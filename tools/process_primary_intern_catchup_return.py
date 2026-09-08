#!/usr/bin/env python3
"""Validate and stage a returned primary-review catch-up workbook.

This tool never mutates active Gold and never marks a staged row safe to merge.
It validates workbook identity against the frozen payload and source pool, then
separates merge candidates, reviewer holds, and rows that still need work.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

REQUIRED_SHEETS = (
    "00说明",
    "主审_MicroText",
    "主审_VisualDiff",
    "工程任务清单",
    "机器数据_勿改",
)
MICRO_CATEGORIES = {
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "component_value",
    "pipe_line_tag",
    "process_value",
    "process_label",
    "room_label",
    "tolerance_value",
    "unknown_microtext",
}
VISUAL_TYPES = {
    "text_change",
    "dimension_change",
    "symbol_component_change",
    "connection_wiring_change",
    "addition",
    "deletion",
    "geometry_change",
}
VISUAL_TYPE_ALIASES = {
    "text": "text_change",
    "text_change_candidate": "text_change",
    "dimension_change_candidate": "dimension_change",
    "symbol": "symbol_component_change",
    "text_added_candidate": "addition",
    "addition": "addition",
    "addition+text": "addition",
    "text_removed_candidate": "deletion",
    "deletion": "deletion",
    "deletion+text": "deletion",
    "geometry_change_candidate": "geometry_change",
}
GENERIC_ENGINEERING_NOTES = {
    "对",
    "正确",
    "没问题",
    "无",
    "同意",
    "是",
    "否",
    "ok",
    "yes",
    "no",
    "correct",
}


def text(value: Any) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    if re.fullmatch(r"-?\d+\.0", result):
        return result[:-2]
    return result


def normalized(value: Any) -> str:
    return " ".join(text(value).casefold().split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def record_id(row: dict[str, Any]) -> str:
    return text(row.get("candidate_id") or row.get("pair_id") or row.get("record_id"))


def load_additional_overlap_ids(paths: list[Path]) -> set[str]:
    identities: set[str] = set()
    for path in paths:
        for row in read_jsonl(path):
            identity = record_id(row)
            if identity:
                identities.add(identity)
    return identities


def row_task(row: dict[str, Any]) -> str:
    explicit = text(row.get("task") or row.get("replacement_for_task"))
    if explicit:
        return explicit
    if text(row.get("candidate_id")):
        return "microtext"
    if text(row.get("pair_id")):
        return "visualdiff"
    return ""


def comparable(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text(value).replace("\\", "/")


def replacement_alignment_issues(
    source: dict[str, Any],
    replacement: dict[str, Any],
) -> list[str]:
    task = row_task(replacement)
    issues: list[str] = []
    if record_id(source) != record_id(replacement):
        issues.append("record_id_mismatch")
    if row_task(source) != task:
        issues.append("task_mismatch")
    if text(source.get("reserved_split")) != text(replacement.get("replacement_for_split")):
        issues.append("replacement_split_mismatch")
    if text(replacement.get("reserved_split")) != text(replacement.get("replacement_for_split")):
        issues.append("candidate_split_mismatch")
    if replacement.get("provenance_replacement_candidate") is not True:
        issues.append("replacement_flag_missing")
    if text(replacement.get("replacement_rights_check")) != "release_safe_status":
        issues.append("replacement_rights_check_invalid")
    if text(replacement.get("replacement_evidence_fingerprint_status")) != "pixel_crop_sha256":
        issues.append("replacement_evidence_status_invalid")
    if not text(replacement.get("replacement_evidence_fingerprint")):
        issues.append("replacement_evidence_fingerprint_missing")
    evidence_fields = (
        ("doc_id", "image_path", "page_index", "bbox")
        if task == "microtext"
        else ("project_id", "image_old", "image_new", "bbox_old", "bbox_new")
    )
    for field in evidence_fields:
        if comparable(source.get(field)) != comparable(replacement.get(field)):
            issues.append(f"{field}_mismatch")
    return sorted(set(issues))


def replacement_metadata(replacement: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "task",
        "source_candidate_id",
        "reserved_split",
        "split_reservation_plan",
        "split_reservation_id",
        "provenance_replacement_candidate",
        "provenance_replacement_date_label",
        "replacement_for_task",
        "replacement_for_split",
        "replacement_for_category",
        "replacement_match_level",
        "replacement_origin_phase",
        "replacement_rights_check",
        "replacement_source_unit",
        "replacement_evidence_fingerprint",
        "replacement_evidence_fingerprint_status",
    )
    return {field: replacement.get(field) for field in fields}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def build_assignment_capacity(
    payload_rows: list[dict[str, Any]],
    pool_by_id: dict[str, dict[str, Any]],
    date_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Restore full source geometry for the issued primary assignment."""
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for payload_row in payload_rows:
        identity = text(payload_row.get("record_id"))
        source = pool_by_id.get(identity)
        if source is None:
            issues.append(
                {
                    "record_id": identity,
                    "reason": "assignment_capacity_source_missing",
                }
            )
            continue
        task = text(payload_row.get("task"))
        if row_task(source) != task:
            issues.append(
                {
                    "record_id": identity,
                    "reason": "assignment_capacity_task_mismatch",
                    "expected": task,
                    "actual": row_task(source),
                }
            )
            continue
        required = (
            ("candidate_id", "doc_id", "page_index", "bbox")
            if task == "microtext"
            else ("pair_id", "project_id")
        )
        missing = [field for field in required if source.get(field) in (None, "", [])]
        if missing:
            issues.append(
                {
                    "record_id": identity,
                    "reason": "assignment_capacity_evidence_missing",
                    "field": ";".join(missing),
                }
            )
            continue

        assigned = dict(source)
        assigned.update(
            {
                "record_id": identity,
                "task": task,
                "primary_index": payload_row.get("primary_index"),
                "reserved_split": text(payload_row.get("reserved_split")),
                "source_group": text(payload_row.get("source_group")),
                "capacity_cohort": text(payload_row.get("capacity_cohort")),
                "engineering_required": bool(payload_row.get("engineering_required")),
                "engineering_index": payload_row.get("engineering_index", ""),
                "auditor_overlap": bool(payload_row.get("auditor_overlap")),
                "retained_from_previous_primary": bool(
                    payload_row.get("retained_from_previous_primary")
                ),
                "current_assignment_date_label": date_label,
                "current_assignment_status": "issued_primary_pending_return",
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "safe_to_merge_gold": False,
            }
        )
        rows.append(assigned)
    return rows, issues


def column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference.upper())
    if not match:
        raise ValueError(f"invalid cell reference: {reference}")
    result = 0
    for char in match.group(0):
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def sheet_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    result: dict[str, str] = {}
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        return result
    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
        relation_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
        target = targets[relation_id].replace("\\", "/")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = str(PurePosixPath("xl") / target)
        result[sheet.attrib["name"]] = target
    return result


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if kind == "s" and raw:
        return strings[int(raw)]
    return text(raw)


def read_sheet_rows(
    archive: zipfile.ZipFile,
    target: str,
    strings: list[str],
) -> dict[int, dict[int, str]]:
    root = ET.fromstring(archive.read(target))
    result: dict[int, dict[int, str]] = {}
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        row_number = int(row.attrib.get("r") or 0)
        cells: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            cells[column_index(cell.attrib.get("r", "A1"))] = cell_value(cell, strings)
        result[row_number] = cells
    return result


def read_workbook(
    path: Path,
    required_sheets: tuple[str, ...] = REQUIRED_SHEETS,
) -> tuple[dict[str, dict[int, dict[int, str]]], list[str]]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                return {}, [f"zip_crc_error:{bad}"]
            strings = shared_strings(archive)
            targets = sheet_targets(archive)
            missing = [name for name in required_sheets if name not in targets]
            issues.extend(f"missing_sheet:{name}" for name in missing)
            rows = {
                name: read_sheet_rows(archive, target, strings)
                for name, target in targets.items()
                if name in required_sheets
            }
            return rows, issues
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as error:
        return {}, [f"workbook_parse_error:{type(error).__name__}:{error}"]


def as_bool(value: Any) -> bool | None:
    value = normalized(value)
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    return None


def original_visual_type(value: Any) -> str:
    normalized_type = normalized(value)
    if normalized_type in VISUAL_TYPES:
        return normalized_type
    return VISUAL_TYPE_ALIASES.get(normalized_type, "")


def engineering_note_valid(value: Any) -> bool:
    note = normalized(value)
    compact = re.sub(r"\s+", "", note)
    return len(compact) >= 6 and note not in GENERIC_ENGINEERING_NOTES


def decision_code(value: Any) -> int | None:
    value = text(value)
    if value in {"1", "2", "3", "4"}:
        return int(value)
    return None


def interpret_decision(payload_row: dict[str, Any], cells: dict[int, str]) -> dict[str, Any]:
    task = payload_row["task"]
    code = decision_code(cells.get(4))
    engineering_basis = text(cells.get(8))
    reasons: list[str] = []
    if code is None:
        reasons.append("missing_or_invalid_decision")
    if payload_row["engineering_required"] and not engineering_note_valid(engineering_basis):
        reasons.append("engineering_basis_missing_or_too_generic")
    mandatory_description_rewrite = bool(payload_row.get("mandatory_description_rewrite"))

    result: dict[str, Any] = {
        "primary_index": payload_row["primary_index"],
        "record_id": payload_row["record_id"],
        "task": task,
        "decision_code": code or "",
        "engineering_required": bool(payload_row["engineering_required"]),
        "engineering_reason": payload_row.get("engineering_reason", ""),
        "engineering_basis": engineering_basis,
        "mandatory_description_rewrite": mandatory_description_rewrite,
        "status": "",
        "decision_class": "",
        "blocking_reasons": reasons,
        "ready": False,
    }

    if task == "microtext":
        corrected_text = text(cells.get(5))
        corrected_category = text(cells.get(6))
        original_text = text(payload_row.get("proposed_text"))
        original_category = text(payload_row.get("category"))
        if code == 1:
            result.update(status="accepted", decision_class="keep")
            if corrected_text or corrected_category:
                reasons.append("accepted_has_corrections")
        elif code == 2:
            result.update(status="edited", decision_class="keep")
            if not corrected_text and not corrected_category:
                reasons.append("edited_requires_text_or_category")
        elif code == 3:
            result.update(status="rejected", decision_class="reject")
            if corrected_text or corrected_category:
                reasons.append("rejected_has_corrections")
        elif code == 4:
            result.update(status="needs_context", decision_class="hold")
            if corrected_text or corrected_category:
                reasons.append("needs_context_has_corrections")
        if corrected_category and corrected_category not in MICRO_CATEGORIES:
            reasons.append("invalid_corrected_category")
        effective_text = corrected_text if code == 2 and corrected_text else original_text
        effective_category = corrected_category if code == 2 and corrected_category else original_category
        if code in {1, 2} and not effective_text:
            reasons.append("merge_candidate_missing_text")
        if code in {1, 2} and effective_category not in MICRO_CATEGORIES:
            reasons.append("merge_candidate_invalid_category")
        if code in {1, 2} and effective_category == "unknown_microtext":
            reasons.append("merge_candidate_unknown_category")
        result.update(
            corrected_text=corrected_text,
            corrected_category=corrected_category,
            effective_text=effective_text,
            effective_category=effective_category,
        )
    else:
        corrected_type = text(cells.get(5))
        corrected_description = text(cells.get(6))
        original_type = original_visual_type(payload_row.get("change_type"))
        original_description = text(payload_row.get("change_description"))
        if code == 1:
            result.update(status="valid", decision_class="keep")
            if corrected_type or corrected_description:
                reasons.append("valid_has_corrections")
        elif code == 2:
            result.update(status="edit", decision_class="keep")
            if not corrected_type and not corrected_description:
                reasons.append("edit_requires_type_or_description")
        elif code == 3:
            result.update(status="rejected_no_change", decision_class="reject")
            if corrected_type or corrected_description:
                reasons.append("rejected_has_corrections")
        elif code == 4:
            result.update(status="needs_context", decision_class="hold")
            if corrected_type or corrected_description:
                reasons.append("needs_context_has_corrections")
        if corrected_type and corrected_type not in VISUAL_TYPES:
            reasons.append("invalid_corrected_change_type")
        if mandatory_description_rewrite and code in {1, 2}:
            if code != 2:
                reasons.append("mandatory_description_rewrite_requires_code_2")
            if code == 2 and not corrected_type:
                reasons.append("mandatory_description_rewrite_requires_type")
            if code == 2 and not corrected_description:
                reasons.append("mandatory_description_rewrite_requires_description")
        effective_type = corrected_type if code == 2 and corrected_type else original_type
        effective_description = (
            corrected_description if code == 2 and corrected_description else original_description
        )
        if code in {1, 2} and not effective_type:
            reasons.append("merge_candidate_ambiguous_change_type")
        if code in {1, 2} and not effective_description:
            reasons.append("merge_candidate_missing_description")
        result.update(
            corrected_change_type=corrected_type,
            corrected_description=corrected_description,
            effective_change_type=effective_type,
            effective_description=effective_description,
        )
    result["blocking_reasons"] = sorted(set(reasons))
    result["ready"] = not result["blocking_reasons"]
    return result


def cell(cells: dict[int, str], index: int) -> str:
    return text(cells.get(index))


def validate_machine_sheet(
    rows: dict[int, dict[int, str]],
    payload_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_headers = [
        "primary_index", "task", "record_id", "reserved_split", "source_group",
        "capacity_cohort", "evidence_path", "engineering_required", "engineering_index",
        "auditor_overlap", "retained_from_previous_primary", "safe_to_merge_gold",
    ]
    include_extension_fields = any(
        any(
            field in row
            for field in (
                "carried_from_previous_workbook",
                "provenance_replacement",
                "mandatory_description_rewrite",
            )
        )
        for row in payload_rows
    )
    if include_extension_fields:
        expected_headers.extend(
            [
                "carried_from_previous_workbook",
                "provenance_replacement",
                "mandatory_description_rewrite",
            ]
        )
    header = rows.get(1, {})
    for index, expected in enumerate(expected_headers):
        if cell(header, index) != expected:
            issues.append({"sheet": "机器数据_勿改", "row": 1, "field": expected, "reason": "header_changed"})
    for offset, expected in enumerate(payload_rows, 2):
        actual = rows.get(offset, {})
        comparisons = {
            "primary_index": (cell(actual, 0), text(expected["primary_index"])),
            "task": (cell(actual, 1), text(expected["task"])),
            "record_id": (cell(actual, 2), text(expected["record_id"])),
            "reserved_split": (cell(actual, 3), text(expected["reserved_split"])),
            "source_group": (cell(actual, 4), text(expected["source_group"])),
            "capacity_cohort": (cell(actual, 5), text(expected["capacity_cohort"])),
            "evidence_path": (cell(actual, 6).replace("\\", "/"), text(expected["evidence_path"]).replace("\\", "/")),
            "engineering_index": (cell(actual, 8), text(expected.get("engineering_index", ""))),
        }
        for field, (value, expected_value) in comparisons.items():
            if value != expected_value:
                issues.append({"sheet": "机器数据_勿改", "row": offset, "record_id": expected["record_id"], "field": field, "reason": "immutable_value_changed", "expected": expected_value, "actual": value})
        bool_fields = {
            "engineering_required": (7, bool(expected["engineering_required"])),
            "auditor_overlap": (9, bool(expected["auditor_overlap"])),
            "retained_from_previous_primary": (10, bool(expected["retained_from_previous_primary"])),
            "safe_to_merge_gold": (11, False),
        }
        if include_extension_fields:
            bool_fields.update(
                {
                    "carried_from_previous_workbook": (
                        12,
                        bool(expected.get("carried_from_previous_workbook")),
                    ),
                    "provenance_replacement": (
                        13,
                        bool(expected.get("provenance_replacement")),
                    ),
                    "mandatory_description_rewrite": (
                        14,
                        bool(expected.get("mandatory_description_rewrite")),
                    ),
                }
            )
        for field, (index, expected_value) in bool_fields.items():
            if as_bool(actual.get(index)) is not expected_value:
                issues.append({"sheet": "机器数据_勿改", "row": offset, "record_id": expected["record_id"], "field": field, "reason": "immutable_value_changed"})
    extra = [number for number, values in rows.items() if number > len(payload_rows) + 1 and any(text(value) for value in values.values())]
    for number in extra:
        issues.append({"sheet": "机器数据_勿改", "row": number, "field": "row", "reason": "unexpected_extra_row"})
    return issues


def validate_main_identity(
    sheet_name: str,
    rows: dict[int, dict[int, str]],
    payload_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_headers = [
        "#",
        "证据图片" if sheet_name == "主审_MicroText" else "OLD / NEW 证据图片",
        "机器内容 + 审核问题",
        "机器类别" if sheet_name == "主审_MicroText" else "机器变化类型",
        "判断 1/2/3/4",
        "正确文字（仅 2）" if sheet_name == "主审_MicroText" else "正确变化类型（仅 2）",
        "正确类别（仅 2）" if sheet_name == "主审_MicroText" else "正确变化描述（仅 2）",
        "工程深审原因",
        "工程依据/说明",
        "完成状态",
    ]
    header = rows.get(6, {})
    for index, expected_header in enumerate(expected_headers):
        if cell(header, index) != expected_header:
            issues.append(
                {
                    "sheet": sheet_name,
                    "row": 6,
                    "field": expected_header,
                    "reason": "header_changed",
                }
            )
    for offset, expected in enumerate(payload_rows, 7):
        actual = rows.get(offset, {})
        checks = {
            "primary_index": (cell(actual, 0), text(expected["primary_index"])),
            "machine_type": (
                cell(actual, 3),
                text(expected.get("category") if expected["task"] == "microtext" else expected.get("change_type")),
            ),
            "engineering_reason": (cell(actual, 7), text(expected.get("engineering_reason"))),
        }
        for field, (value, expected_value) in checks.items():
            if value != expected_value:
                issues.append({"sheet": sheet_name, "row": offset, "record_id": expected["record_id"], "field": field, "reason": "immutable_value_changed", "expected": expected_value, "actual": value})
    last_expected = len(payload_rows) + 6
    for number, values in rows.items():
        if number > last_expected and any(text(value) for value in values.values()):
            issues.append({"sheet": sheet_name, "row": number, "field": "row", "reason": "unexpected_extra_row"})
    return issues


def rework_action(reasons: list[str], task: str) -> str:
    values = set(reasons)
    if "missing_or_invalid_decision" in values:
        return "在黄色判断列填写 1、2、3 或 4。"
    if "engineering_basis_missing_or_too_generic" in values:
        return "这是工程深审行；请填写至少 6 个有效字符的工程判断依据，不能只写“对/正确”。"
    if "edited_requires_text_or_category" in values:
        return "选择 2 后，至少填写正确文字或正确类别。"
    if any(value.startswith("mandatory_description_rewrite_") for value in values):
        return "这是强制工程描述重写行：必须选择 2，并同时填写规范变化类型、具体变化描述和工程依据。"
    if "edit_requires_type_or_description" in values:
        return "选择 2 后，至少填写正确变化类型或正确变化描述。"
    if "merge_candidate_missing_description" in values:
        return "该 VisualDiff 没有可用机器描述；请选择 2 并填写完整的真实变化描述。"
    if "merge_candidate_unknown_category" in values:
        return "unknown_microtext 不能直接进入候选集；有效样本请选择 2 并填写规范类别，无效选 3，证据不足选 4。"
    if any("changed" in value for value in values):
        return "请使用原始工作簿重新填写，不要修改序号、机器类别/类型、工程原因或机器数据页。"
    return "按 00说明 修正本行；不确定时选择 4 并写工程依据（如该行为工程深审）。"


def stage_row(
    source: dict[str, Any],
    payload_row: dict[str, Any],
    decision: dict[str, Any],
    date_label: str,
    workbook_sha256: str,
    provenance_replacement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    staged = dict(source)
    staged.update(
        {
            "human_completion_date_label": date_label,
            "human_completion_source": "primary_intern_catchup_superseding_workbook",
            "human_completion_workbook_sha256": workbook_sha256,
            "primary_reviewer_decision_code": decision["decision_code"],
            "primary_reviewer_status": decision["status"],
            "engineering_review_required": decision["engineering_required"],
            "engineering_review_reason": decision["engineering_reason"],
            "engineering_review_basis": decision["engineering_basis"],
            "review_depth": "single_review_primary",
            "auditor_overlap_reserved": bool(payload_row["auditor_overlap"]),
            "promotion_state": "human_reviewed_pending_release_gates",
            "safe_to_merge_gold": False,
        }
    )
    if payload_row["task"] == "microtext":
        staged.update(
            {
                "candidate_id": payload_row["record_id"],
                "review_status": decision["status"],
                "human_review_status": decision["status"],
                "target_text": decision["effective_text"],
                "proposed_text": decision["effective_text"],
                "category": decision["effective_category"],
                "corrected_text": decision["corrected_text"],
                "corrected_category": decision["corrected_category"],
            }
        )
    else:
        staged.update(
            {
                "pair_id": payload_row["record_id"],
                "review_status": decision["status"],
                "human_review_status": "edit",
                "change_type": decision["effective_change_type"],
                "human_description": decision["effective_description"],
                "description": decision["effective_description"],
                "change_desc_gt": decision["effective_description"],
                "desc_source": "human",
            }
        )
    if provenance_replacement is not None:
        staged.update(replacement_metadata(provenance_replacement))
        staged.update(
            {
                "provenance_replacement_review_status": "human_keep_pending_atomic_migration_gates",
                "provenance_replacement_safe_to_retire_active_row": False,
            }
        )
    return staged


def normalize_overlap_decision(
    payload_row: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    if payload_row["task"] == "microtext":
        semantic = {
            "keep": ("1", "pass"),
            "reject": ("2", "issue"),
            "hold": ("3", "needs_context"),
        }
    else:
        semantic = {
            "keep": ("1", "has_engineering_change"),
            "reject": ("2", "no_engineering_change"),
            "hold": ("3", "unclear"),
        }
    auditor_code, auditor_decision = semantic[decision["decision_class"]]
    return {
        "record_id": decision["record_id"],
        "candidate_id": decision["record_id"] if decision["task"] == "microtext" else "",
        "pair_id": decision["record_id"] if decision["task"] == "visualdiff" else "",
        "task_type": decision["task"],
        "primary_index": decision["primary_index"],
        "reviewer_id": "primary_reviewer",
        "reviewer_role": "primary",
        "decision_code": auditor_code,
        "decision": auditor_decision,
        "primary_detailed_status": decision["status"],
        "effective_text": decision.get("effective_text", ""),
        "effective_category": decision.get("effective_category", ""),
        "effective_change_type": decision.get("effective_change_type", ""),
        "effective_description": decision.get("effective_description", ""),
        "engineering_review_required": decision["engineering_required"],
        "safe_to_merge_gold": False,
    }


def build_replacement_coverage(
    replacement_rows: list[dict[str, Any]],
    payload_by_id: dict[str, dict[str, Any]],
    decisions_by_id: dict[str, dict[str, Any]],
    alignment_by_id: dict[str, list[str]],
    specialist_by_id: dict[str, dict[str, Any]] | None = None,
    evidence_aliases_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    specialist_by_id = specialist_by_id or {}
    evidence_aliases_by_id = evidence_aliases_by_id or {}
    coverage: list[dict[str, Any]] = []
    for replacement in replacement_rows:
        identity = record_id(replacement)
        direct_payload_row = payload_by_id.get(identity)
        specialist_row = specialist_by_id.get(identity)
        alias = evidence_aliases_by_id.get(identity)
        review_via_record_id = str((alias or {}).get("review_via_record_id") or "")
        payload_row = direct_payload_row or payload_by_id.get(review_via_record_id)
        decision = decisions_by_id.get(identity) or decisions_by_id.get(review_via_record_id)
        alignment = alignment_by_id.get(identity, [])
        if direct_payload_row is not None:
            coverage_mode = "direct_primary"
        elif specialist_row is not None:
            coverage_mode = "engineering_specialist"
        elif alias is not None and payload_row is not None:
            coverage_mode = "exact_evidence_alias"
        else:
            coverage_mode = "unassigned"
        if coverage_mode == "engineering_specialist":
            status = "specialist_assignment_pending_separate_return_processing"
        elif payload_row is None:
            status = "not_in_primary_workbook"
        elif alignment:
            status = "replacement_metadata_alignment_issue"
        elif not decision or not decision["ready"]:
            status = (
                "exact_evidence_alias_primary_incomplete_or_rework"
                if coverage_mode == "exact_evidence_alias"
                else "primary_incomplete_or_rework"
            )
        elif decision["decision_class"] == "keep":
            status = (
                "exact_evidence_alias_primary_keep_pending_atomic_migration_gates"
                if coverage_mode == "exact_evidence_alias"
                else "primary_keep_pending_atomic_migration_gates"
            )
        elif decision["decision_class"] == "reject":
            status = (
                "exact_evidence_alias_primary_rejected_need_alternate"
                if coverage_mode == "exact_evidence_alias"
                else "primary_rejected_need_alternate"
            )
        else:
            status = (
                "exact_evidence_alias_primary_needs_context"
                if coverage_mode == "exact_evidence_alias"
                else "primary_needs_context"
            )
        coverage.append(
            {
                "record_id": identity,
                "task": replacement.get("replacement_for_task", ""),
                "split": replacement.get("replacement_for_split", ""),
                "category": replacement.get("replacement_for_category", ""),
                "source_unit": replacement.get("replacement_source_unit", ""),
                "coverage_mode": coverage_mode,
                "review_via_record_id": review_via_record_id,
                "in_primary_workbook": direct_payload_row is not None,
                "has_review_assignment": coverage_mode != "unassigned",
                "primary_index": payload_row.get("primary_index", "") if payload_row else "",
                "primary_decision_status": decision.get("status", "") if decision else "",
                "coverage_status": status,
                "alignment_issues": ";".join(alignment),
                "safe_to_retire_active_row": False,
            }
        )
    return coverage


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    replacement = report["provenance_replacement"]
    return "\n".join(
        [
            "# Primary Intern Catch-up Return Processing",
            "",
            "- Goal: **Gold v2.0 Global**",
            f"- Processing complete: **{str(report['complete']).lower()}**",
            "- Safe to merge Gold: **false**",
            f"- Workbook rows: **{totals['expected_rows']}**",
            f"- Ready decisions: **{totals['ready_decisions']}/{totals['expected_rows']}**",
            f"- Engineering decisions complete: **{totals['engineering_ready']}/{totals['engineering_expected']}**",
            f"- Auditor-overlap decisions ready: **{totals['auditor_overlap_ready']}/{totals['auditor_overlap_expected']}**",
            f"- Staged pending-gate rows: **{totals['staged_rows']}**",
            f"- Reviewer holds: **{totals['review_holds']}**",
            f"- Rework rows: **{totals['rework_rows']}**",
            f"- Identity/structure issues: **{totals['structural_issues']}**",
            f"- Exact provenance replacements with review coverage: **{replacement['candidates_with_review_assignment']}/{replacement['exact_replacement_candidates']}**",
            f"- Direct primary / specialist / exact-evidence alias: **{replacement['direct_primary_assignments']} / {replacement['specialist_assignments']} / {replacement['exact_evidence_alias_assignments']}**",
            f"- Accepted replacements pending atomic migration gates: **{replacement['primary_keep_pending_atomic_migration_gates']}**",
            "- Gold rows modified: **0**",
            "",
            "Staged rows still require provenance, evidence, split, leakage, duplicate, agreement, and strict promotion gates.",
            "",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument(
        "--provenance-replacement-candidates",
        default="derived/review_queues/v2_0_provenance_replacement_candidates_2026-08-13-wave164.jsonl",
    )
    parser.add_argument(
        "--additional-overlap-ids",
        action="append",
        default=[],
        help="JSONL identities with completed or reserved independent-auditor coverage",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    workbook = Path(args.workbook)
    if not workbook.is_absolute():
        workbook = (root / workbook).resolve()
    payload_path = Path(args.payload)
    if not payload_path.is_absolute():
        payload_path = (root / payload_path).resolve()
    replacement_path = Path(args.provenance_replacement_candidates)
    if not replacement_path.is_absolute():
        replacement_path = (root / replacement_path).resolve()
    additional_overlap_paths = [
        (Path(value).resolve() if Path(value).is_absolute() else (root / value).resolve())
        for value in args.additional_overlap_ids
    ]
    missing_overlap_paths = [str(path) for path in additional_overlap_paths if not path.is_file()]
    if missing_overlap_paths:
        raise FileNotFoundError(f"additional overlap JSONL missing: {missing_overlap_paths}")
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (root / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload_rows = payload["rows"]
    specialist_payload = payload.get("specialist") or {}
    specialist_rows = list(specialist_payload.get("visualdiff_english") or []) + list(
        specialist_payload.get("microtext_balance") or []
    )
    specialist_by_id = {
        str(row.get("record_id") or ""): row
        for row in specialist_rows
        if str(row.get("record_id") or "")
    }
    evidence_aliases = list(payload.get("provenance_replacement_aliases") or [])
    evidence_aliases_by_id = {
        str(row.get("record_id") or ""): row
        for row in evidence_aliases
        if str(row.get("record_id") or "")
    }
    structural: list[dict[str, Any]] = []
    expected_total = int(payload.get("counts", {}).get("total") or 0)
    if expected_total <= 0 or len(payload_rows) != expected_total:
        structural.append(
            {
                "reason": "payload_row_count_mismatch",
                "expected": expected_total,
                "actual": len(payload_rows),
            }
        )
    if len({row["record_id"] for row in payload_rows}) != len(payload_rows):
        structural.append({"reason": "payload_duplicate_record_ids"})
    if len(specialist_by_id) != len(specialist_rows):
        structural.append({"reason": "payload_specialist_duplicate_or_blank_record_ids"})
    if len(evidence_aliases_by_id) != len(evidence_aliases):
        structural.append({"reason": "payload_evidence_alias_duplicate_or_blank_record_ids"})
    payload_ids = {str(row["record_id"]) for row in payload_rows}
    additional_overlap_ids = load_additional_overlap_ids(additional_overlap_paths)
    additional_overlap_in_payload = additional_overlap_ids & payload_ids
    additional_overlap_outside_payload = additional_overlap_ids - payload_ids
    effective_overlap_ids = {
        str(row["record_id"]) for row in payload_rows if row.get("auditor_overlap")
    } | additional_overlap_in_payload
    for alias_id, alias in evidence_aliases_by_id.items():
        owner_id = str(alias.get("review_via_record_id") or "")
        if alias_id in payload_ids or alias_id in specialist_by_id:
            structural.append(
                {"record_id": alias_id, "reason": "payload_evidence_alias_overlaps_assigned_id"}
            )
        if owner_id not in payload_ids:
            structural.append(
                {"record_id": alias_id, "reason": "payload_evidence_alias_owner_missing"}
            )
        if alias.get("coverage_basis") != "exact_rendered_evidence_sha256":
            structural.append(
                {"record_id": alias_id, "reason": "payload_evidence_alias_basis_invalid"}
            )

    pool_path = Path(payload["inputs"]["pool"])
    if not pool_path.is_absolute():
        pool_path = root / pool_path
    if not pool_path.is_file():
        structural.append({"reason": "source_pool_missing", "path": str(pool_path)})
        pool_rows: list[dict[str, Any]] = []
    else:
        pool_hash = sha256_file(pool_path)
        if pool_hash != payload["inputs"]["pool_sha256"]:
            structural.append({"reason": "source_pool_hash_mismatch", "actual": pool_hash})
        pool_rows = read_jsonl(pool_path)
    pool_by_id = {
        text(row.get("candidate_id") or row.get("pair_id")): row
        for row in pool_rows
    }
    missing_pool = [row["record_id"] for row in payload_rows if row["record_id"] not in pool_by_id]
    if missing_pool:
        structural.append({"reason": "payload_rows_missing_from_source_pool", "count": len(missing_pool), "examples": missing_pool[:10]})
    missing_alias_pool = sorted(set(evidence_aliases_by_id) - set(pool_by_id))
    if missing_alias_pool:
        structural.append(
            {
                "reason": "payload_evidence_aliases_missing_from_source_pool",
                "count": len(missing_alias_pool),
                "examples": missing_alias_pool[:10],
            }
        )
    assignment_capacity, assignment_capacity_issues = build_assignment_capacity(
        payload_rows,
        pool_by_id,
        args.date_label,
    )
    structural.extend(assignment_capacity_issues)

    replacement_contract_issues: list[str] = []
    if not replacement_path.is_file():
        replacement_rows: list[dict[str, Any]] = []
        replacement_contract_issues.append("provenance_replacement_candidates_missing")
    else:
        replacement_rows = read_jsonl(replacement_path)
    replacement_ids = [record_id(row) for row in replacement_rows]
    if any(not identity for identity in replacement_ids):
        replacement_contract_issues.append("provenance_replacement_candidate_missing_id")
    if len(set(replacement_ids)) != len(replacement_ids):
        replacement_contract_issues.append("provenance_replacement_duplicate_ids")
    replacement_by_id = {
        record_id(row): row for row in replacement_rows if record_id(row)
    }
    replacement_alignment_by_id: dict[str, list[str]] = {}
    for identity, replacement in replacement_by_id.items():
        source = pool_by_id.get(identity)
        if source is not None and (identity in payload_ids or identity in evidence_aliases_by_id):
            issues = replacement_alignment_issues(source, replacement)
            if issues:
                replacement_alignment_by_id[identity] = issues

    workbook_sha = sha256_file(workbook)
    workbook_rows, workbook_issues = read_workbook(workbook)
    structural.extend({"reason": issue} for issue in workbook_issues)
    if workbook_rows:
        structural.extend(validate_machine_sheet(workbook_rows.get("机器数据_勿改", {}), payload_rows))
        micro_payload = [row for row in payload_rows if row["task"] == "microtext"]
        visual_payload = [row for row in payload_rows if row["task"] == "visualdiff"]
        structural.extend(validate_main_identity("主审_MicroText", workbook_rows.get("主审_MicroText", {}), micro_payload))
        structural.extend(validate_main_identity("主审_VisualDiff", workbook_rows.get("主审_VisualDiff", {}), visual_payload))
    else:
        micro_payload = [row for row in payload_rows if row["task"] == "microtext"]
        visual_payload = [row for row in payload_rows if row["task"] == "visualdiff"]

    structural_by_record: dict[str, list[str]] = {}
    global_structural_reasons: list[str] = []
    for issue in structural:
        issue_record_id = text(issue.get("record_id"))
        if issue_record_id:
            structural_by_record.setdefault(issue_record_id, []).append(issue["reason"])
        else:
            global_structural_reasons.append(issue["reason"])

    decisions: list[dict[str, Any]] = []
    for task_name, task_rows, sheet_name in (
        ("microtext", micro_payload, "主审_MicroText"),
        ("visualdiff", visual_payload, "主审_VisualDiff"),
    ):
        sheet = workbook_rows.get(sheet_name, {})
        for offset, payload_row in enumerate(task_rows):
            excel_row = 7 + offset
            decision = interpret_decision(payload_row, sheet.get(excel_row, {}))
            identity_reasons = global_structural_reasons + structural_by_record.get(payload_row["record_id"], [])
            if identity_reasons:
                decision["blocking_reasons"] = sorted(set(decision["blocking_reasons"] + identity_reasons))
                decision["ready"] = False
            decision["sheet"] = sheet_name
            decision["excel_row"] = excel_row
            decisions.append(decision)

    staged_micro: list[dict[str, Any]] = []
    staged_visual: list[dict[str, Any]] = []
    overlap_decisions: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    rework: list[dict[str, Any]] = []
    engineering: list[dict[str, Any]] = []
    payload_by_id = {row["record_id"]: row for row in payload_rows}
    decisions_by_id = {row["record_id"]: row for row in decisions}
    for decision in decisions:
        payload_row = payload_by_id[decision["record_id"]]
        if decision["engineering_required"]:
            engineering.append(
                {
                    "engineering_index": payload_row.get("engineering_index", ""),
                    "primary_index": decision["primary_index"],
                    "record_id": decision["record_id"],
                    "task": decision["task"],
                    "decision_code": decision["decision_code"],
                    "engineering_reason": decision["engineering_reason"],
                    "engineering_basis": decision["engineering_basis"],
                    "ready": decision["ready"],
                    "blocking_reasons": ";".join(decision["blocking_reasons"]),
                }
            )
        if not decision["ready"]:
            rework.append(
                {
                    "sheet": decision["sheet"],
                    "excel_row": decision["excel_row"],
                    "primary_index": decision["primary_index"],
                    "record_id": decision["record_id"],
                    "task": decision["task"],
                    "decision_code": decision["decision_code"],
                    "reasons": ";".join(decision["blocking_reasons"]),
                    "action_zh": rework_action(decision["blocking_reasons"], decision["task"]),
                }
            )
            continue
        if decision["record_id"] in effective_overlap_ids:
            overlap_decisions.append(normalize_overlap_decision(payload_row, decision))
        if decision["decision_class"] != "keep":
            holds.append(
                {
                    "primary_index": decision["primary_index"],
                    "record_id": decision["record_id"],
                    "task": decision["task"],
                    "status": decision["status"],
                    "decision_code": decision["decision_code"],
                    "engineering_basis": decision["engineering_basis"],
                    "hold_reason": "primary_reviewer_rejected" if decision["decision_class"] == "reject" else "primary_reviewer_needs_context",
                }
            )
            continue
        source = pool_by_id.get(decision["record_id"])
        if source is None:
            continue
        replacement = replacement_by_id.get(decision["record_id"])
        if replacement_alignment_by_id.get(decision["record_id"]):
            replacement = None
        payload_row_for_stage = dict(payload_row)
        payload_row_for_stage["auditor_overlap"] = decision["record_id"] in effective_overlap_ids
        staged = stage_row(
            source,
            payload_row_for_stage,
            decision,
            args.date_label,
            workbook_sha,
            provenance_replacement=replacement,
        )
        (staged_micro if decision["task"] == "microtext" else staged_visual).append(staged)

    for alias_id, alias in evidence_aliases_by_id.items():
        owner_id = str(alias.get("review_via_record_id") or "")
        owner_payload = payload_by_id.get(owner_id)
        owner_decision = decisions_by_id.get(owner_id)
        source = pool_by_id.get(alias_id)
        replacement = replacement_by_id.get(alias_id)
        if (
            owner_payload is None
            or owner_decision is None
            or not owner_decision["ready"]
            or owner_decision["decision_class"] != "keep"
            or source is None
            or replacement is None
            or replacement_alignment_by_id.get(alias_id)
        ):
            continue
        if str(alias.get("task") or "") != str(owner_payload.get("task") or ""):
            continue
        alias_payload = dict(owner_payload)
        alias_payload.update(
            {
                "record_id": alias_id,
                "auditor_overlap": False,
                "provenance_replacement": True,
            }
        )
        alias_decision = dict(owner_decision)
        alias_decision["record_id"] = alias_id
        staged = stage_row(
            source,
            alias_payload,
            alias_decision,
            args.date_label,
            workbook_sha,
            provenance_replacement=replacement,
        )
        staged.update(
            {
                "human_review_reused_via_exact_evidence_alias": True,
                "human_review_alias_owner_record_id": owner_id,
                "human_review_alias_evidence_sha256": alias.get("evidence_sha256", ""),
                "review_depth": "single_review_primary_exact_evidence_alias",
            }
        )
        (staged_micro if alias_payload["task"] == "microtext" else staged_visual).append(staged)

    replacement_coverage = build_replacement_coverage(
        replacement_rows,
        payload_by_id,
        decisions_by_id,
        replacement_alignment_by_id,
        specialist_by_id,
        evidence_aliases_by_id,
    )
    replacement_status_counts = Counter(row["coverage_status"] for row in replacement_coverage)
    replacement_mode_counts = Counter(row["coverage_mode"] for row in replacement_coverage)
    replacement_summary = {
        "goal": "Gold v2.0 Global",
        "candidate_path": str(replacement_path),
        "candidate_sha256": sha256_file(replacement_path) if replacement_path.is_file() else "",
        "exact_replacement_candidates": len(replacement_rows),
        "candidates_in_primary_workbook": sum(bool(row["in_primary_workbook"]) for row in replacement_coverage),
        "candidates_not_in_primary_workbook": sum(not bool(row["in_primary_workbook"]) for row in replacement_coverage),
        "candidates_with_review_assignment": sum(
            bool(row["has_review_assignment"]) for row in replacement_coverage
        ),
        "candidates_without_review_assignment": sum(
            not bool(row["has_review_assignment"]) for row in replacement_coverage
        ),
        "direct_primary_assignments": replacement_mode_counts.get("direct_primary", 0),
        "specialist_assignments": replacement_mode_counts.get("engineering_specialist", 0),
        "exact_evidence_alias_assignments": replacement_mode_counts.get("exact_evidence_alias", 0),
        "primary_keep_pending_atomic_migration_gates": replacement_status_counts.get("primary_keep_pending_atomic_migration_gates", 0),
        "exact_evidence_alias_keep_pending_atomic_migration_gates": replacement_status_counts.get(
            "exact_evidence_alias_primary_keep_pending_atomic_migration_gates", 0
        ),
        "metadata_alignment_issues": len(replacement_alignment_by_id),
        "contract_issues": replacement_contract_issues,
        "coverage_mode_counts": dict(sorted(replacement_mode_counts.items())),
        "status_counts": dict(sorted(replacement_status_counts.items())),
        "safe_to_retire_active_rows": False,
        "gold_rows_modified": 0,
        "interpretation": "Primary-review overlap can satisfy part of the exact replacement plan after human acceptance, but active rows remain until the atomic migration audit and all release gates pass.",
    }
    status_counts = Counter(decision["status"] or "incomplete" for decision in decisions)
    engineering_ready = sum(bool(row["ready"]) for row in engineering)
    complete = (
        not structural
        and not rework
        and engineering_ready == len(engineering)
        and not replacement_contract_issues
        and not replacement_alignment_by_id
        and replacement_summary["candidates_without_review_assignment"] == 0
    )
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "complete": complete,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "workbook": str(workbook),
        "workbook_sha256": workbook_sha,
        "payload": str(payload_path),
        "payload_sha256": sha256_file(payload_path),
        "source_pool": str(pool_path),
        "source_pool_sha256": sha256_file(pool_path) if pool_path.is_file() else "",
        "additional_overlap_inputs": [
            {"path": str(path), "sha256": sha256_file(path)}
            for path in additional_overlap_paths
        ],
        "additional_overlap_unique_ids": len(additional_overlap_ids),
        "additional_overlap_ids_in_payload": len(additional_overlap_in_payload),
        "additional_overlap_ids_outside_payload": len(additional_overlap_outside_payload),
        "effective_overlap_ids": len(effective_overlap_ids),
        "current_assignment_capacity": {
            "path": str(output_dir / "current_assignment_capacity.jsonl"),
            "rows": len(assignment_capacity),
            "issues": len(assignment_capacity_issues),
            "safe_to_merge_gold": False,
        },
        "provenance_replacement": replacement_summary,
        "totals": {
            "expected_rows": len(payload_rows),
            "ready_decisions": sum(bool(row["ready"]) for row in decisions),
            "staged_microtext": len(staged_micro),
            "staged_visualdiff": len(staged_visual),
            "staged_rows": len(staged_micro) + len(staged_visual),
            "review_holds": len(holds),
            "rework_rows": len(rework),
            "engineering_expected": len(engineering),
            "engineering_ready": engineering_ready,
            "auditor_overlap_expected": len(effective_overlap_ids),
            "auditor_overlap_ready": len(overlap_decisions),
            "structural_issues": len(structural),
        },
        "decision_status_counts": dict(sorted(status_counts.items())),
        "interpretation": "Review output only. No active Gold file was modified; staged rows require all release gates before promotion.",
    }

    write_jsonl(output_dir / "microtext_reviewed_pending_gates.jsonl", staged_micro)
    write_jsonl(output_dir / "visualdiff_reviewed_pending_gates.jsonl", staged_visual)
    write_jsonl(output_dir / "normalized_primary_overlap_decisions.jsonl", overlap_decisions)
    write_jsonl(output_dir / "current_assignment_capacity.jsonl", assignment_capacity)
    report["current_assignment_capacity"]["sha256"] = sha256_file(
        output_dir / "current_assignment_capacity.jsonl"
    )
    write_csv(
        output_dir / "provenance_replacement_coverage.csv",
        replacement_coverage,
        [
            "record_id", "task", "split", "category", "source_unit",
            "coverage_mode", "review_via_record_id", "in_primary_workbook",
            "has_review_assignment", "primary_index", "primary_decision_status",
            "coverage_status", "alignment_issues", "safe_to_retire_active_row",
        ],
    )
    write_json(output_dir / "provenance_replacement_coverage_summary.json", replacement_summary)
    write_csv(
        output_dir / "review_holds.csv",
        holds,
        ["primary_index", "record_id", "task", "status", "decision_code", "engineering_basis", "hold_reason"],
    )
    write_csv(
        output_dir / "resume_rework.csv",
        rework,
        ["sheet", "excel_row", "primary_index", "record_id", "task", "decision_code", "reasons", "action_zh"],
    )
    write_csv(
        output_dir / "engineering_review_audit.csv",
        engineering,
        ["engineering_index", "primary_index", "record_id", "task", "decision_code", "engineering_reason", "engineering_basis", "ready", "blocking_reasons"],
    )
    write_csv(
        output_dir / "identity_structure_issues.csv",
        structural,
        ["sheet", "row", "record_id", "field", "reason", "expected", "actual", "count", "path"],
    )
    write_json(output_dir / "processing_summary.json", report)
    (output_dir / "processing_summary.md").write_text(render_markdown(report), encoding="utf-8")
    if not args.no_snapshot:
        shutil.copy2(workbook, output_dir / "returned_workbook_snapshot.xlsx")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not complete:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
