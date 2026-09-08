#!/usr/bin/env python3
"""Preflight and stage simplified Eng_Bench primary-plus-auditor returns.

The tool never mutates active gold. It verifies returned workbook identity,
embedded evidence, immutable cells, and human completion; normalizes primary
and auditor decisions; compares the 120 assigned overlaps; writes rework and
adjudication queues; and stages only eligible primary rows as pending the
normal provenance, split, leakage, duplicate, and strict-v2 release gates.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from . import verify_multi_reviewer_handoff as workbook_io
    from . import verify_simple_multi_reviewer_handoff as package_io
except ImportError:  # Direct script execution from tools/.
    import verify_multi_reviewer_handoff as workbook_io
    import verify_simple_multi_reviewer_handoff as package_io


MICRO_SHEET = "MicroText"
VISUAL_SHEET = "VisualDiff"

MICRO_HEADERS = {
    "#",
    "机器建议（不一定对）",
    "你的结论",
    "正确文字（仅 edited）",
    "正确类别（仅 edited）",
    "原因/备注",
    "整页路径（不确定时复制打开）",
    "candidate_id",
    "primary_index",
}
VISUAL_HEADERS = {
    "#",
    "机器提示（不一定对）",
    "你的结论",
    "变化描述（edit 必填）",
    "原因/备注",
    "旧版整页路径",
    "新版整页路径",
    "pair_id",
    "primary_index",
}

MICRO_FINAL_STATUSES = {"accepted", "edited", "rejected"}
VISUAL_FINAL_STATUSES = {"edit", "reject_unclear"}
MICRO_CATEGORIES = {
    "component_value",
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
    "gdandt_symbol",
    "unknown_microtext",
}

DECISION_FIELDS = [
    "reviewer_id",
    "role",
    "workbook",
    "task",
    "row_number",
    "display_index",
    "primary_index",
    "record_id",
    "pack_name",
    "status",
    "decision_class",
    "effective_text",
    "effective_category",
    "description",
    "notes",
    "human_complete",
    "integrity_ok",
    "ready",
    "blocking_reasons",
    "evidence_path",
    "page_path",
    "old_page_path",
    "new_page_path",
]

COMPARISON_FIELDS = [
    "record_id",
    "task",
    "pack_name",
    "primary_index",
    "auditor_id",
    "primary_status",
    "auditor_status",
    "primary_class",
    "auditor_class",
    "primary_text",
    "auditor_text",
    "primary_category",
    "auditor_category",
    "primary_description",
    "auditor_description",
    "pair_complete",
    "class_agreement",
    "content_agreement",
    "exact_outcome_agreement",
    "conflict_reasons",
    "recommended_action",
    "evidence_path",
    "page_path",
    "old_page_path",
    "new_page_path",
    "primary_notes",
    "auditor_notes",
]

ADJUDICATION_FIELDS = COMPARISON_FIELDS + [
    "adjudicator_status",
    "adjudicated_text",
    "adjudicated_category",
    "adjudicated_description",
    "adjudication_notes",
]

REWORK_FIELDS = [
    "reviewer_id",
    "workbook",
    "task",
    "row_number",
    "record_id",
    "status",
    "reasons",
    "action_zh",
]

HOLD_FIELDS = [
    "record_id",
    "task",
    "primary_index",
    "primary_status",
    "review_depth",
    "hold_reasons",
    "auditor_id",
    "source_resolution",
    "source_path",
]

SOURCE_FIELDS = [
    "record_id",
    "task",
    "status",
    "source_resolution",
    "source_resolution_reason",
    "source_match_count",
    "source_path",
    "match_paths",
]

FROZEN_SOURCE_FIELDS = [
    "record_id",
    "task",
    "pack_name",
    "status",
    "source_path",
    "source_match_count",
    "control_match",
    "control_mismatch_fields",
]


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def scalar(value: Any) -> str:
    result = text(value)
    if re.fullmatch(r"-?\d+\.0", result):
        return result[:-2]
    return result


def normalized(value: Any) -> str:
    value = unicodedata.normalize("NFKC", text(value)).casefold()
    return " ".join(value.split())


def normalized_path(value: Any) -> str:
    return text(value).replace("\\", "/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_frozen_source_index(
    batch_dir: Path,
) -> dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]]:
    result: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]] = {
        "microtext": defaultdict(list),
        "visualdiff": defaultdict(list),
    }
    for manifest in sorted((batch_dir / "review_packs").glob("*/manifest.jsonl")):
        relative = manifest.relative_to(batch_dir)
        for row in load_jsonl(manifest):
            candidate_id = text(row.get("candidate_id"))
            pair_id = text(row.get("pair_id"))
            if candidate_id:
                result["microtext"][candidate_id].append((relative, row))
            elif pair_id:
                result["visualdiff"][pair_id].append((relative, row))
    return result


def frozen_resolution(
    index: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
    task: str,
    identifier: str,
) -> dict[str, Any]:
    matches = index[task].get(identifier, [])
    if not matches:
        return {
            "status": "unresolved",
            "match_count": 0,
            "source_path": "",
            "match_paths": [],
            "row": None,
        }
    if len(matches) > 1:
        return {
            "status": "duplicate_frozen_sources",
            "match_count": len(matches),
            "source_path": matches[0][0].as_posix(),
            "match_paths": [path.as_posix() for path, _ in matches],
            "row": None,
        }
    path, row = matches[0]
    return {
        "status": "resolved",
        "resolution_reason": "unique_frozen_review_pack_manifest_row",
        "match_count": 1,
        "source_path": path.as_posix(),
        "match_paths": [path.as_posix()],
        "row": dict(row),
    }


def primary_control_rows(batch_dir: Path) -> list[tuple[str, dict[str, str]]]:
    source_root = batch_dir / "03_MACHINE_CONTROL" / "source_tables"
    return [
        *(('microtext', row) for row in read_csv(source_root / "PRIMARY_MICROTEXT_SOURCE.csv")),
        *(('visualdiff', row) for row in read_csv(source_root / "PRIMARY_VISUALDIFF_SOURCE.csv")),
    ]


def audit_frozen_sources(
    batch_dir: Path,
    index: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task, control in primary_control_rows(batch_dir):
        identifier_key = "candidate_id" if task == "microtext" else "pair_id"
        identifier = control[identifier_key]
        resolution = frozen_resolution(index, task, identifier)
        mismatch_fields: list[str] = []
        source = resolution.get("row")
        if isinstance(source, dict):
            if task == "microtext":
                pairs = (
                    ("doc_id", "doc_id"),
                    ("version_id", "version_id"),
                    ("page_index", "page_index"),
                    ("category", "category"),
                    ("proposed_text", "proposed_text"),
                )
            else:
                pairs = (
                    ("project_id", "project_id"),
                    ("split", "split"),
                    ("page_old", "page_old"),
                    ("page_new", "page_new"),
                    ("change_type", "change_type"),
                    ("current_description", "description"),
                    ("old_text", "old_text"),
                    ("new_text", "new_text"),
                )
            for control_field, source_field in pairs:
                if scalar(control.get(control_field)) != scalar(source.get(source_field)):
                    mismatch_fields.append(control_field)
        control_match = resolution["status"] == "resolved" and not mismatch_fields
        rows.append(
            {
                "record_id": identifier,
                "task": task,
                "pack_name": control.get("pack_name", ""),
                "status": resolution["status"],
                "source_path": resolution["source_path"],
                "source_match_count": resolution["match_count"],
                "control_match": control_match,
                "control_mismatch_fields": ";".join(mismatch_fields),
            }
        )
    return rows


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def collect_return_workbooks(source: Path, extract_dir: Path) -> tuple[list[Path], dict[str, Any]]:
    source = source.resolve()
    if source.is_dir():
        files = sorted(path for path in source.rglob("*.xlsx") if not path.name.startswith("~$"))
        return files, {"kind": "directory", "source": source.as_posix(), "issues": []}
    if not source.is_file():
        return [], {"kind": "missing", "source": source.as_posix(), "issues": ["returns source missing"]}
    if source.suffix.lower() == ".xlsx":
        return [source], {"kind": "workbook", "source": source.as_posix(), "issues": []}
    if source.suffix.lower() != ".zip":
        return [], {
            "kind": "unsupported",
            "source": source.as_posix(),
            "issues": ["returns source must be a directory, .xlsx, or .zip"],
        }
    issues: list[str] = []
    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            corrupt = archive.testzip()
            unsafe = [name for name in names if unsafe_zip_name(name)]
            if corrupt:
                issues.append(f"return ZIP CRC failure: {corrupt}")
            if unsafe:
                issues.append(f"unsafe return ZIP paths: {unsafe[:10]}")
            if issues:
                return [], {"kind": "zip", "source": source.as_posix(), "issues": issues}
            archive.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        return [], {"kind": "zip", "source": source.as_posix(), "issues": [str(exc)]}
    files = sorted(path for path in extract_dir.rglob("*.xlsx") if not path.name.startswith("~$"))
    return files, {
        "kind": "zip",
        "source": source.as_posix(),
        "xlsx_files": len(files),
        "issues": issues,
    }


def role_control_paths(batch_dir: Path, manifest: dict[str, str]) -> tuple[Path, Path]:
    source_root = batch_dir / "03_MACHINE_CONTROL" / "source_tables"
    if manifest["role"] == "primary":
        return (
            source_root / "PRIMARY_MICROTEXT_SOURCE.csv",
            source_root / "PRIMARY_VISUALDIFF_SOURCE.csv",
        )
    reviewer = manifest["assignee"]
    number = reviewer[-2:]
    return (
        source_root / reviewer / f"AUDITOR_{number}_MICROTEXT_SOURCE.csv",
        source_root / reviewer / f"AUDITOR_{number}_VISUALDIFF_SOURCE.csv",
    )


def expected_micro_suggestion(row: dict[str, str]) -> str:
    return f"{row.get('proposed_text', '')}\n类别：{row.get('category', '')}"


def expected_visual_suggestion(row: dict[str, str]) -> str:
    old = row.get("old_text") or "(空)"
    new = row.get("new_text") or "(空)"
    return f"{row.get('change_type', '')}\n机器文字：{old} -> {new}"


def micro_human_decision(row: dict[str, str], control: dict[str, str]) -> dict[str, Any]:
    status = normalized(row.get("你的结论"))
    corrected_text = text(row.get("正确文字（仅 edited）"))
    corrected_category = text(row.get("正确类别（仅 edited）"))
    notes = text(row.get("原因/备注"))
    reasons: list[str] = []
    if not status:
        reasons.append("missing_status")
    elif status == "needs_full_page":
        reasons.append("needs_full_page_not_final")
    elif status not in MICRO_FINAL_STATUSES:
        reasons.append("invalid_status")
    elif status == "accepted" and (corrected_text or corrected_category):
        reasons.append("accepted_has_correction_fields")
    elif status == "edited" and not (corrected_text or corrected_category):
        reasons.append("edited_requires_text_or_category")
    elif status == "rejected" and not notes:
        reasons.append("rejected_requires_reason")
    if corrected_category and corrected_category not in MICRO_CATEGORIES:
        reasons.append("invalid_corrected_category")

    proposed_text = text(control.get("proposed_text"))
    proposed_category = text(control.get("category"))
    effective_text = corrected_text if status == "edited" and corrected_text else proposed_text
    effective_category = (
        corrected_category if status == "edited" and corrected_category else proposed_category
    )
    decision_class = "keep" if status in {"accepted", "edited"} else "reject" if status == "rejected" else ""
    return {
        "status": status,
        "decision_class": decision_class,
        "effective_text": effective_text,
        "effective_category": effective_category,
        "description": "",
        "notes": notes,
        "human_complete": not reasons,
        "human_reasons": reasons,
    }


def visual_human_decision(row: dict[str, str], control: dict[str, str]) -> dict[str, Any]:
    del control
    status = normalized(row.get("你的结论"))
    description = text(row.get("变化描述（edit 必填）"))
    notes = text(row.get("原因/备注"))
    reasons: list[str] = []
    if not status:
        reasons.append("missing_status")
    elif status == "needs_full_page":
        reasons.append("needs_full_page_not_final")
    elif status == "valid":
        reasons.append("legacy_valid_not_allowed_for_todo_description")
    elif status not in VISUAL_FINAL_STATUSES:
        reasons.append("invalid_status")
    elif status == "edit" and not description:
        reasons.append("edit_requires_description")
    elif status == "reject_unclear" and not notes:
        reasons.append("reject_unclear_requires_reason")
    if status == "reject_unclear" and description:
        reasons.append("reject_unclear_has_description")
    decision_class = "keep" if status == "edit" else "reject" if status == "reject_unclear" else ""
    return {
        "status": status,
        "decision_class": decision_class,
        "effective_text": "",
        "effective_category": "",
        "description": description,
        "notes": notes,
        "human_complete": not reasons,
        "human_reasons": reasons,
    }


def immutable_reasons(
    task: str,
    row: dict[str, str],
    control: dict[str, str],
    primary: bool,
) -> list[str]:
    reasons: list[str] = []
    display_expected = control.get("primary_index") if primary else control.get("audit_index")
    identifier = "candidate_id" if task == "microtext" else "pair_id"
    suggestion_header = "机器建议（不一定对）" if task == "microtext" else "机器提示（不一定对）"
    suggestion_expected = (
        expected_micro_suggestion(control) if task == "microtext" else expected_visual_suggestion(control)
    )
    if scalar(row.get("#")) != scalar(display_expected):
        reasons.append("display_index_changed")
    if text(row.get(identifier)) != text(control.get(identifier)):
        reasons.append(f"{identifier}_changed")
    if scalar(row.get("primary_index")) != scalar(control.get("primary_index")):
        reasons.append("primary_index_changed")
    if text(row.get(suggestion_header)).replace("\r\n", "\n") != suggestion_expected:
        reasons.append("machine_suggestion_changed")
    if task == "microtext":
        if normalized_path(row.get("整页路径（不确定时复制打开）")) != normalized_path(
            control.get("page_path")
        ):
            reasons.append("page_path_changed")
    else:
        if normalized_path(row.get("旧版整页路径")) != normalized_path(control.get("old_page_path")):
            reasons.append("old_page_path_changed")
        if normalized_path(row.get("新版整页路径")) != normalized_path(control.get("new_page_path")):
            reasons.append("new_page_path_changed")
    return reasons


def normalize_rows(
    workbook: Path,
    reviewer_id: str,
    role: str,
    task: str,
    actual: list[dict[str, str]],
    expected: list[dict[str, str]],
    workbook_reasons: list[str],
) -> list[dict[str, Any]]:
    primary = role == "primary"
    identifier = "candidate_id" if task == "microtext" else "pair_id"
    rows: list[dict[str, Any]] = []
    for index, control in enumerate(expected):
        row = actual[index] if index < len(actual) else {}
        human = (
            micro_human_decision(row, control)
            if task == "microtext"
            else visual_human_decision(row, control)
        )
        integrity = immutable_reasons(task, row, control, primary) if row else ["missing_row"]
        all_integrity = sorted(set(workbook_reasons + integrity))
        all_reasons = sorted(set(human["human_reasons"] + all_integrity))
        rows.append(
            {
                "reviewer_id": reviewer_id,
                "role": role,
                "workbook": workbook.name,
                "task": task,
                "row_number": index + 2,
                "display_index": control.get("primary_index") if primary else control.get("audit_index"),
                "primary_index": control.get("primary_index", ""),
                "record_id": control.get(identifier, ""),
                "pack_name": control.get("pack_name", ""),
                "status": human["status"],
                "decision_class": human["decision_class"],
                "effective_text": human["effective_text"],
                "effective_category": human["effective_category"],
                "description": human["description"],
                "notes": human["notes"],
                "human_complete": human["human_complete"],
                "integrity_ok": not all_integrity,
                "ready": not all_reasons,
                "blocking_reasons": ";".join(all_reasons),
                "evidence_path": control.get("crop_path") if task == "microtext" else control.get("panel_path"),
                "page_path": control.get("page_path", ""),
                "old_page_path": control.get("old_page_path", ""),
                "new_page_path": control.get("new_page_path", ""),
                "control": control,
            }
        )
    return rows


def choose_workbook(candidates: list[Path]) -> tuple[Path | None, list[str], list[str]]:
    if not candidates:
        return None, ["missing_workbook"], []
    hashes = defaultdict(list)
    for path in candidates:
        hashes[sha256(path)].append(path)
    if len(hashes) > 1:
        return None, ["conflicting_duplicate_workbooks"], [path.as_posix() for path in candidates]
    selected = min(candidates, key=lambda path: (len(path.parts), path.as_posix().lower()))
    notes = [] if len(candidates) == 1 else [f"identical_duplicate_copies:{len(candidates)}"]
    return selected, [], notes


def process_role_workbook(
    batch_dir: Path,
    manifest: dict[str, str],
    candidates: list[Path],
    snapshot_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviewer_id = manifest["assignee"]
    expected_name = manifest["return_file"]
    selected, selection_reasons, notes = choose_workbook(candidates)
    inventory: dict[str, Any] = {
        "reviewer_id": reviewer_id,
        "role": manifest["role"],
        "expected_file": expected_name,
        "found_copies": len(candidates),
        "selected_path": selected.as_posix() if selected else "",
        "sha256": sha256(selected) if selected else "",
        "structural_valid": False,
        "human_complete": False,
        "decision_rows": 0,
        "issues": selection_reasons,
        "notes": notes,
    }
    if selected is None:
        return inventory, []

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / expected_name
    shutil.copy2(selected, snapshot)
    inventory["snapshot_path"] = snapshot.as_posix()

    micro_path, visual_path = role_control_paths(batch_dir, manifest)
    expected_micro = read_csv(micro_path)
    expected_visual = read_csv(visual_path)
    template = batch_dir / manifest["folder"] / expected_name
    workbook_reasons: list[str] = []
    try:
        actual_micro, micro_meta = workbook_io.table_records(selected, MICRO_SHEET)
        actual_visual, visual_meta = workbook_io.table_records(selected, VISUAL_SHEET)
    except (KeyError, ValueError, zipfile.BadZipFile, OSError) as exc:
        inventory["issues"] = inventory["issues"] + [f"unreadable_workbook:{exc}"]
        return inventory, []

    if not actual_micro or not MICRO_HEADERS.issubset(actual_micro[0]):
        workbook_reasons.append("microtext_headers_changed")
    if not actual_visual or not VISUAL_HEADERS.issubset(actual_visual[0]):
        workbook_reasons.append("visualdiff_headers_changed")
    if len(actual_micro) != len(expected_micro):
        workbook_reasons.append("microtext_row_count_changed")
    if len(actual_visual) != len(expected_visual):
        workbook_reasons.append("visualdiff_row_count_changed")
    if [row.get("candidate_id", "") for row in actual_micro] != [
        row.get("candidate_id", "") for row in expected_micro
    ]:
        workbook_reasons.append("microtext_id_order_changed")
    if [row.get("pair_id", "") for row in actual_visual] != [
        row.get("pair_id", "") for row in expected_visual
    ]:
        workbook_reasons.append("visualdiff_id_order_changed")
    if micro_meta["formula_count"] < len(expected_micro):
        workbook_reasons.append("microtext_completion_formulas_missing")
    if visual_meta["formula_count"] < len(expected_visual):
        workbook_reasons.append("visualdiff_completion_formulas_missing")
    if not micro_meta["data_validation"]:
        workbook_reasons.append("microtext_dropdown_missing")
    if not visual_meta["data_validation"]:
        workbook_reasons.append("visualdiff_dropdown_missing")
    try:
        if package_io.workbook_media_hashes(selected) != package_io.workbook_media_hashes(template):
            workbook_reasons.append("embedded_evidence_changed")
    except (zipfile.BadZipFile, OSError) as exc:
        workbook_reasons.append(f"embedded_evidence_unreadable:{exc}")

    decisions = normalize_rows(
        selected,
        reviewer_id,
        manifest["role"],
        "microtext",
        actual_micro,
        expected_micro,
        workbook_reasons,
    ) + normalize_rows(
        selected,
        reviewer_id,
        manifest["role"],
        "visualdiff",
        actual_visual,
        expected_visual,
        workbook_reasons,
    )
    inventory["issues"] = sorted(set(inventory["issues"] + workbook_reasons))
    inventory["structural_valid"] = not workbook_reasons and not selection_reasons
    inventory["human_complete"] = bool(decisions) and all(row["human_complete"] for row in decisions)
    inventory["decision_rows"] = len(decisions)
    inventory["ready_rows"] = sum(bool(row["ready"]) for row in decisions)
    return inventory, decisions


def cohen_kappa(left: list[str], right: list[str]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in set(left_counts) | set(right_counts)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def compare_pair(
    assignment: dict[str, str],
    primary: dict[str, Any] | None,
    auditor: dict[str, Any] | None,
) -> dict[str, Any]:
    task = assignment["task"]
    reasons: list[str] = []
    pair_complete = bool(primary and auditor and primary["ready"] and auditor["ready"])
    if not primary:
        reasons.append("primary_decision_missing")
    elif not primary["ready"]:
        reasons.append("primary_decision_incomplete_or_invalid")
    if not auditor:
        reasons.append("auditor_decision_missing")
    elif not auditor["ready"]:
        reasons.append("auditor_decision_incomplete_or_invalid")

    class_agreement = False
    content_agreement = False
    if pair_complete and primary and auditor:
        class_agreement = primary["decision_class"] == auditor["decision_class"]
        if not class_agreement:
            reasons.append("decision_class_disagreement")
        elif primary["decision_class"] == "reject":
            content_agreement = True
        elif task == "microtext":
            text_agrees = normalized(primary["effective_text"]) == normalized(auditor["effective_text"])
            category_agrees = primary["effective_category"] == auditor["effective_category"]
            content_agreement = text_agrees and category_agrees
            if not text_agrees:
                reasons.append("text_disagreement")
            if not category_agrees:
                reasons.append("category_disagreement")
        else:
            content_agreement = normalized(primary["description"]) == normalized(auditor["description"])
            if not content_agreement:
                reasons.append("description_disagreement")
    exact = pair_complete and class_agreement and content_agreement
    if not pair_complete:
        action = "return_to_assigned_reviewer"
    elif not exact:
        action = "human_adjudication"
    else:
        action = "no_conflict"
    evidence_source = primary or auditor or {}
    return {
        "record_id": assignment["record_id"],
        "task": task,
        "pack_name": assignment.get("pack_name", ""),
        "primary_index": assignment.get("primary_index", ""),
        "auditor_id": assignment["auditor_id"],
        "primary_status": primary.get("status", "") if primary else "",
        "auditor_status": auditor.get("status", "") if auditor else "",
        "primary_class": primary.get("decision_class", "") if primary else "",
        "auditor_class": auditor.get("decision_class", "") if auditor else "",
        "primary_text": primary.get("effective_text", "") if primary else "",
        "auditor_text": auditor.get("effective_text", "") if auditor else "",
        "primary_category": primary.get("effective_category", "") if primary else "",
        "auditor_category": auditor.get("effective_category", "") if auditor else "",
        "primary_description": primary.get("description", "") if primary else "",
        "auditor_description": auditor.get("description", "") if auditor else "",
        "pair_complete": pair_complete,
        "class_agreement": class_agreement,
        "content_agreement": content_agreement,
        "exact_outcome_agreement": exact,
        "conflict_reasons": ";".join(reasons),
        "recommended_action": action,
        "evidence_path": evidence_source.get("evidence_path", ""),
        "page_path": evidence_source.get("page_path", ""),
        "old_page_path": evidence_source.get("old_page_path", ""),
        "new_page_path": evidence_source.get("new_page_path", ""),
        "primary_notes": primary.get("notes", "") if primary else "",
        "auditor_notes": auditor.get("notes", "") if auditor else "",
    }


def summarize_comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for task in ("overall", "microtext", "visualdiff"):
        subset = rows if task == "overall" else [row for row in rows if row["task"] == task]
        complete = [row for row in subset if row["pair_complete"]]
        left = [row["primary_class"] for row in complete]
        right = [row["auditor_class"] for row in complete]
        result[task] = {
            "assigned_rows": len(subset),
            "complete_pairs": len(complete),
            "class_agreement": (
                sum(bool(row["class_agreement"]) for row in complete) / len(complete)
                if complete
                else 0.0
            ),
            "exact_outcome_agreement": (
                sum(bool(row["exact_outcome_agreement"]) for row in complete) / len(complete)
                if complete
                else 0.0
            ),
            "decision_class_kappa": cohen_kappa(left, right),
        }
    return result


def rework_action(reasons: str) -> str:
    values = set(reasons.split(";"))
    if "missing_status" in values:
        return "请选择最终结论。"
    if "needs_full_page_not_final" in values:
        return "请查看整页后改成最终结论，不能保留 needs_full_page。"
    if "edited_requires_text_or_category" in values:
        return "edited 至少填写正确文字或正确类别。"
    if "edit_requires_description" in values:
        return "edit 必须填写准确的变化描述。"
    if "rejected_requires_reason" in values or "reject_unclear_requires_reason" in values:
        return "拒绝时必须填写原因/备注。"
    if "legacy_valid_not_allowed_for_todo_description" in values:
        return "本批没有现成描述可确认；有真实变化请选择 edit 并填写描述。"
    if any("changed" in value or "missing" in value for value in values):
        return "请使用原始工作簿重新填写，不要修改灰色字段、ID、顺序、图片或公式。"
    return "请按“开始”工作表规则修正这一行。"


def make_rework_queue(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        if decision["ready"]:
            continue
        reasons = decision["blocking_reasons"]
        rows.append(
            {
                "reviewer_id": decision["reviewer_id"],
                "workbook": decision["workbook"],
                "task": decision["task"],
                "row_number": decision["row_number"],
                "record_id": decision["record_id"],
                "status": decision["status"],
                "reasons": reasons,
                "action_zh": rework_action(reasons),
            }
        )
    return rows


def build_staging(
    primary_decisions: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    date_label: str,
    frozen_index: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
    frozen_ready_ids: set[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    comparison_by_id = {row["record_id"]: row for row in comparisons}
    audited_ids = set(comparison_by_id)
    micro_staged: list[dict[str, Any]] = []
    visual_staged: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []

    for decision in primary_decisions:
        identifier = decision["record_id"]
        comparison = comparison_by_id.get(identifier)
        review_depth = "double_review" if identifier in audited_ids else "single_review"
        hold_reasons: list[str] = []
        if not decision["ready"]:
            hold_reasons.append("primary_incomplete_or_invalid")
        elif decision["decision_class"] == "reject":
            hold_reasons.append("primary_reviewer_rejected")
        elif comparison and not comparison["pair_complete"]:
            hold_reasons.append("auditor_return_incomplete")
        elif comparison and not comparison["exact_outcome_agreement"]:
            hold_reasons.append("independent_review_conflict")
        elif identifier not in frozen_ready_ids:
            hold_reasons.append("frozen_source_preflight_failed")

        if hold_reasons:
            holds.append(
                {
                    "record_id": identifier,
                    "task": decision["task"],
                    "primary_index": decision["primary_index"],
                    "primary_status": decision["status"],
                    "review_depth": review_depth,
                    "hold_reasons": ";".join(hold_reasons),
                    "auditor_id": comparison.get("auditor_id", "") if comparison else "",
                    "source_resolution": "not_attempted",
                    "source_path": "",
                }
            )
            continue

        kind = decision["task"]
        resolution = frozen_resolution(frozen_index, kind, identifier)
        resolutions.append(
            {
                "record_id": identifier,
                "task": kind,
                "status": decision["status"],
                "source_resolution": resolution["status"],
                "source_resolution_reason": resolution.get("resolution_reason", ""),
                "source_match_count": resolution["match_count"],
                "source_path": resolution["source_path"],
                "match_paths": " | ".join(resolution["match_paths"]),
            }
        )
        if resolution["status"] != "resolved":
            holds.append(
                {
                    "record_id": identifier,
                    "task": kind,
                    "primary_index": decision["primary_index"],
                    "primary_status": decision["status"],
                    "review_depth": review_depth,
                    "hold_reasons": f"source_{resolution['status']}",
                    "auditor_id": comparison.get("auditor_id", "") if comparison else "",
                    "source_resolution": resolution["status"],
                    "source_path": resolution["source_path"],
                }
            )
            continue

        source_row = dict(resolution["row"])
        source_row.update(
            {
                "review_status": decision["status"],
                "human_completion_date_label": date_label,
                "human_completion_source": "simple_primary_plus_independent_auditors",
                "human_completion_source_path": resolution["source_path"],
                "primary_reviewer_id": decision["reviewer_id"],
                "independent_auditor_id": comparison.get("auditor_id", "") if comparison else "",
                "review_depth": (
                    "double_review_agree" if comparison else "single_review_complete"
                ),
                "promotion_state": "human_reviewed_pending_release_gates",
                "safe_to_merge_gold": False,
            }
        )
        if kind == "microtext":
            source_row["candidate_id"] = identifier
            source_row["target_text"] = decision["effective_text"]
            source_row["proposed_text"] = decision["effective_text"]
            source_row["category"] = decision["effective_category"]
            source_row["corrected_text"] = (
                decision["effective_text"] if decision["status"] == "edited" else ""
            )
            source_row["corrected_category"] = (
                decision["effective_category"] if decision["status"] == "edited" else ""
            )
            source_row["review_notes"] = decision["notes"]
            micro_staged.append(source_row)
        else:
            source_row["pair_id"] = identifier
            source_row["human_review_status"] = "edit"
            source_row["human_description"] = decision["description"]
            source_row["human_review_notes"] = decision["notes"]
            source_row["description"] = decision["description"]
            source_row["change_desc_gt"] = decision["description"]
            source_row["desc_source"] = "human"
            visual_staged.append(source_row)
    return micro_staged, visual_staged, holds, resolutions


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    agreement = report["agreement_metrics"]["overall"]
    return "\n".join(
        [
            "# Simplified Multi-reviewer Return Processing",
            "",
            f"- Goal: `Gold v2.0 Global`",
            f"- Processing complete: `{str(report['complete']).lower()}`",
            f"- Safe to merge gold: `false`",
            f"- Expected/found workbooks: `{totals['expected_workbooks']}/{totals['found_workbooks']}`",
            f"- Ready primary decisions: `{totals['ready_primary_rows']}/498`",
            f"- Ready auditor decisions: `{totals['ready_auditor_rows']}/120`",
            f"- Complete overlap pairs: `{agreement['complete_pairs']}/120`",
            f"- Class agreement: `{agreement['class_agreement']:.4f}`",
            f"- Exact outcome agreement: `{agreement['exact_outcome_agreement']:.4f}`",
            f"- Adjudication rows: `{totals['adjudication_rows']}`",
            f"- Rework rows: `{totals['rework_rows']}`",
            f"- Frozen source rows ready: `{totals['frozen_source_ready_rows']}/498`",
            f"- Staged pending-gate rows: `{totals['staged_rows']}`",
            f"- Gold rows modified: `0`",
            "",
            "This report is supplemental review evidence. It does not replace or update the existing 185-row release agreement gate.",
            "",
        ]
    )


def process_returns(
    root: Path,
    batch_dir: Path,
    returns_source: Path,
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    batch_dir = batch_dir if batch_dir.is_absolute() else root / batch_dir
    returns_source = returns_source if returns_source.is_absolute() else root / returns_source
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = batch_dir / "03_MACHINE_CONTROL" / "return_manifest.csv"
    manifests = read_csv(manifest_path)
    assignments = read_csv(batch_dir / "03_MACHINE_CONTROL" / "audit_assignments.csv")
    with tempfile.TemporaryDirectory() as temporary:
        workbooks, source_report = collect_return_workbooks(returns_source, Path(temporary))
        by_name: dict[str, list[Path]] = defaultdict(list)
        for workbook in workbooks:
            by_name[workbook.name.casefold()].append(workbook)

        inventories: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for manifest in manifests:
            inventory, role_decisions = process_role_workbook(
                batch_dir,
                manifest,
                by_name.get(manifest["return_file"].casefold(), []),
                output_dir / "returned_workbook_snapshots",
            )
            inventories.append(inventory)
            decisions.extend(role_decisions)

    primary_decisions = [row for row in decisions if row["role"] == "primary"]
    auditor_decisions = [row for row in decisions if row["role"] != "primary"]
    primary_by_id = {row["record_id"]: row for row in primary_decisions}
    auditor_by_key = {(row["reviewer_id"], row["record_id"]): row for row in auditor_decisions}
    comparisons = [
        compare_pair(
            assignment,
            primary_by_id.get(assignment["record_id"]),
            auditor_by_key.get((assignment["auditor_id"], assignment["record_id"])),
        )
        for assignment in assignments
    ]
    adjudication = [
        {**row, **{field: "" for field in ADJUDICATION_FIELDS if field not in row}}
        for row in comparisons
        if row["pair_complete"] and not row["exact_outcome_agreement"]
    ]
    rework = make_rework_queue(decisions)
    frozen_index = load_frozen_source_index(batch_dir)
    frozen_source_audit = audit_frozen_sources(batch_dir, frozen_index)
    frozen_ready_ids = {
        row["record_id"] for row in frozen_source_audit if row["control_match"]
    }
    micro_staged, visual_staged, holds, resolutions = build_staging(
        primary_decisions,
        comparisons,
        date_label,
        frozen_index,
        frozen_ready_ids,
    )
    metrics = summarize_comparisons(comparisons)

    structural_issues = list(source_report.get("issues", []))
    structural_issues.extend(
        f"{row['reviewer_id']}:{issue}"
        for row in inventories
        for issue in row.get("issues", [])
    )
    expected_workbooks = len(manifests)
    found_workbooks = sum(bool(row.get("selected_path")) for row in inventories)
    ready_primary = sum(bool(row["ready"]) for row in primary_decisions)
    ready_auditor = sum(bool(row["ready"]) for row in auditor_decisions)
    source_blocked = sum(row["source_resolution"] != "resolved" for row in resolutions)
    frozen_source_blocked = len(frozen_source_audit) - len(frozen_ready_ids)
    complete = (
        expected_workbooks == 11
        and found_workbooks == 11
        and not structural_issues
        and len(primary_decisions) == 498
        and len(auditor_decisions) == 120
        and ready_primary == 498
        and ready_auditor == 120
        and metrics["overall"]["complete_pairs"] == 120
        and source_blocked == 0
        and frozen_source_blocked == 0
    )
    ready_for_promotion_review = complete and not adjudication
    totals = {
        "expected_workbooks": expected_workbooks,
        "found_workbooks": found_workbooks,
        "primary_decision_rows": len(primary_decisions),
        "auditor_decision_rows": len(auditor_decisions),
        "ready_primary_rows": ready_primary,
        "ready_auditor_rows": ready_auditor,
        "rework_rows": len(rework),
        "assigned_overlap_rows": len(comparisons),
        "complete_overlap_pairs": metrics["overall"]["complete_pairs"],
        "adjudication_rows": len(adjudication),
        "staged_microtext_rows": len(micro_staged),
        "staged_visualdiff_rows": len(visual_staged),
        "staged_rows": len(micro_staged) + len(visual_staged),
        "hold_rows": len(holds),
        "source_resolution_rows": len(resolutions),
        "source_blocked_rows": source_blocked,
        "frozen_source_rows": len(frozen_source_audit),
        "frozen_source_ready_rows": len(frozen_ready_ids),
        "frozen_source_blocked_rows": frozen_source_blocked,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "complete": bool(complete),
        "ready_for_maintainer_promotion_review": bool(ready_for_promotion_review),
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "release_agreement_gate_updated": False,
        "agreement_scope": "supplemental 120-row overlap; does not replace the existing 185-row release gate",
        "root": root.as_posix(),
        "batch_dir": batch_dir.as_posix(),
        "returns_source": returns_source.as_posix(),
        "output_dir": output_dir.as_posix(),
        "date_label": date_label,
        "return_source_report": source_report,
        "workbooks": inventories,
        "totals": totals,
        "agreement_metrics": metrics,
        "structural_issues": structural_issues,
        "outputs": {
            "workbook_inventory": (output_dir / "returned_workbook_inventory.csv").as_posix(),
            "primary_decisions": (output_dir / "normalized_primary_decisions.csv").as_posix(),
            "auditor_decisions": (output_dir / "normalized_auditor_decisions.csv").as_posix(),
            "agreement_comparison": (output_dir / "agreement_comparison.csv").as_posix(),
            "adjudication_queue": (output_dir / "adjudication_queue.csv").as_posix(),
            "rework_queue": (output_dir / "reviewer_rework_queue.csv").as_posix(),
            "staged_microtext": (output_dir / "staged_microtext_pending_release_gates.jsonl").as_posix(),
            "staged_visualdiff": (output_dir / "staged_visualdiff_pending_release_gates.jsonl").as_posix(),
            "holds": (output_dir / "promotion_holds.csv").as_posix(),
            "source_resolution": (output_dir / "source_resolution.csv").as_posix(),
            "frozen_source_preflight": (output_dir / "frozen_source_preflight.csv").as_posix(),
        },
    }

    inventory_fields = [
        "reviewer_id",
        "role",
        "expected_file",
        "found_copies",
        "selected_path",
        "sha256",
        "structural_valid",
        "human_complete",
        "decision_rows",
        "ready_rows",
        "snapshot_path",
        "issues",
        "notes",
    ]
    inventory_rows = [
        {
            **row,
            "issues": ";".join(row.get("issues", [])),
            "notes": ";".join(row.get("notes", [])),
        }
        for row in inventories
    ]
    write_csv(output_dir / "returned_workbook_inventory.csv", inventory_rows, inventory_fields)
    write_csv(output_dir / "normalized_primary_decisions.csv", primary_decisions, DECISION_FIELDS)
    write_csv(output_dir / "normalized_auditor_decisions.csv", auditor_decisions, DECISION_FIELDS)
    write_csv(output_dir / "agreement_comparison.csv", comparisons, COMPARISON_FIELDS)
    write_csv(output_dir / "adjudication_queue.csv", adjudication, ADJUDICATION_FIELDS)
    write_csv(output_dir / "reviewer_rework_queue.csv", rework, REWORK_FIELDS)
    write_jsonl(output_dir / "staged_microtext_pending_release_gates.jsonl", micro_staged)
    write_jsonl(output_dir / "staged_visualdiff_pending_release_gates.jsonl", visual_staged)
    write_csv(output_dir / "promotion_holds.csv", holds, HOLD_FIELDS)
    write_csv(output_dir / "source_resolution.csv", resolutions, SOURCE_FIELDS)
    write_csv(
        output_dir / "frozen_source_preflight.csv",
        frozen_source_audit,
        FROZEN_SOURCE_FIELDS,
    )
    write_json(output_dir / "processing_summary.json", report)
    (output_dir / "processing_summary.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--returns", required=True, help="Returned folder, .zip, or .xlsx")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    report = process_returns(
        Path(args.root),
        Path(args.batch_dir),
        Path(args.returns),
        Path(args.output_dir),
        args.date_label,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
