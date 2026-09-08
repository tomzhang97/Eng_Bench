#!/usr/bin/env python3
"""Stage a returned mixed Eng_Bench human-completion workbook.

This tool is intentionally conservative: it never mutates active gold JSONL and
never marks staged rows as safe to merge. It validates the returned workbook,
resolves accepted/edited rows back to source review JSONL, writes merge-candidate
staging files, and sends incomplete, rejected, ambiguous, or unresolved rows to
CSV queues for maintainer review.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

OLD_GOLD_SHEET = "OLD GOLD CHECK"
MICROTEXT_SHEETS = ("NEW MICROTEXT", "SEMANTIC REDO")
VISUALDIFF_SHEET = "NEW VISUALDIFF"
REQUIRED_SHEETS = (OLD_GOLD_SHEET, "NEW MICROTEXT", VISUALDIFF_SHEET, "SEMANTIC REDO")

MICROTEXT_MERGEABLE = {"accepted", "edited"}
MICROTEXT_HOLD = {"rejected", "needs_full_page"}
MICROTEXT_ALLOWED = MICROTEXT_MERGEABLE | MICROTEXT_HOLD

VISUALDIFF_MERGEABLE = {"valid", "edit"}
VISUALDIFF_HOLD = {"reject_unclear", "needs_full_page"}
VISUALDIFF_ALLOWED = VISUALDIFF_MERGEABLE | VISUALDIFF_HOLD
VISUALDIFF_STATUS_ALIASES = {
    "accept": "valid",
    "accepted": "valid",
    "valid": "valid",
    "ok": "valid",
    "edit": "edit",
    "edited": "edit",
    "reject": "reject_unclear",
    "rejected": "reject_unclear",
    "reject_unclear": "reject_unclear",
    "unclear": "reject_unclear",
    "full_page": "needs_full_page",
    "needs_full_page": "needs_full_page",
    "need_full_page": "needs_full_page",
    "needs full page": "needs_full_page",
}
TODO_DESCRIPTION = "CHANGE_DESC_GT_TODO"

FALLBACK_SEARCH_ROOTS = (
    "derived/human_adjudication",
    "derived/review_queues",
    "derived/review_packs",
)


def text(value: Any) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    if re.fullmatch(r"-?\d+\.0", result):
        return result[:-2]
    return result


def lower_text(value: Any) -> str:
    return text(value).lower()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


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
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        raise ValueError(f"invalid cell reference: {reference}")
    result = 0
    for char in letters.group(0):
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


def read_sheet(archive: zipfile.ZipFile, target: str, strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(target))
    result: list[list[str]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            values[column_index(cell.attrib.get("r", "A1"))] = cell_value(cell, strings)
        width = max(values, default=-1) + 1
        result.append([values.get(index, "") for index in range(width)])
    return result


def read_review_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    with zipfile.ZipFile(path) as archive:
        strings = shared_strings(archive)
        targets = sheet_targets(archive)
        result: dict[str, list[dict[str, str]]] = {}
        for sheet_name, target in targets.items():
            matrix = read_sheet(archive, target, strings)
            if not matrix:
                result[sheet_name] = []
                continue
            headers = [text(value) for value in matrix[0]]
            rows: list[dict[str, str]] = []
            for values in matrix[1:]:
                row = {
                    header: text(values[index] if index < len(values) else "")
                    for index, header in enumerate(headers)
                    if header
                }
                if any(row.values()):
                    rows.append(row)
            result[sheet_name] = rows
        return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def row_identifier(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = text(row.get(key))
        if value:
            return value
    return ""


def source_priority(path: Path) -> tuple[int, str]:
    normalized = path.as_posix().lower()
    if normalized.startswith("microtext/annotations/") or normalized.startswith("visualdiff/annotations/"):
        return (0, normalized)
    if normalized.startswith("derived/human_adjudication/"):
        return (50, normalized)
    if normalized.startswith("derived/review_queues/"):
        return (60, normalized)
    if normalized.startswith("derived/review_packs/"):
        return (70, normalized)
    return (100, normalized)


SOURCE_RESOLUTION_IGNORED_FIELDS = {
    "review_status",
}


def populated(value: Any) -> bool:
    return value not in (None, "", [], {})


def source_rows_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return true when duplicate source rows have no conflicting facts.

    Candidate and review JSONL often carry the same row at different enrichment
    stages. Missing fields are allowed, but two populated values for the same
    field must agree. Review workflow state is intentionally ignored because it
    does not change the source candidate itself.
    """
    for key in set(left) & set(right):
        if key in SOURCE_RESOLUTION_IGNORED_FIELDS:
            continue
        left_value = left.get(key)
        right_value = right.get(key)
        if populated(left_value) and populated(right_value) and left_value != right_value:
            return False
    return True


def compatible_source_group(matches: list[tuple[Path, dict[str, Any]]]) -> bool:
    return all(
        source_rows_compatible(left_row, right_row)
        for index, (_, left_row) in enumerate(matches)
        for _, right_row in matches[index + 1 :]
    )


def source_richness(item: tuple[Path, dict[str, Any]]) -> tuple[int, str]:
    path, row = item
    return (-sum(populated(value) for value in row.values()), path.as_posix().lower())


def index_jsonl_folder(
    root: Path,
    folder: Path,
    id_keys: tuple[str, ...],
) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    result: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    if not folder.exists():
        return result
    for path in sorted(folder.glob("*.jsonl")):
        relative = path.relative_to(root)
        for row in load_jsonl(path):
            identifier = row_identifier(row, id_keys)
            if identifier:
                result[identifier].append((relative, row))
    return result


def scan_fallback_sources(root: Path, identifier: str, id_keys: tuple[str, ...]) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for folder_name in FALLBACK_SEARCH_ROOTS:
        folder = root / folder_name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.jsonl")):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            for row in load_jsonl(path):
                if row_identifier(row, id_keys) == identifier:
                    matches.append((relative, row))
    return matches


def build_source_indexes(root: Path) -> dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]]:
    return {
        "microtext": index_jsonl_folder(
            root,
            root / "microtext" / "annotations",
            ("candidate_id", "id"),
        ),
        "visualdiff": index_jsonl_folder(
            root,
            root / "visualdiff" / "annotations",
            ("pair_id", "id"),
        ),
    }


def resolve_source_row(
    root: Path,
    indexes: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
    kind: str,
    identifier: str,
) -> dict[str, Any]:
    id_keys = ("candidate_id", "id") if kind == "microtext" else ("pair_id", "id")
    matches = list(indexes[kind].get(identifier, []))
    if not matches:
        matches = scan_fallback_sources(root, identifier, id_keys)
    matches.sort(key=lambda item: source_priority(item[0]))
    if not matches:
        return {
            "status": "unresolved",
            "match_count": 0,
            "source_path": "",
            "row": None,
            "match_paths": [],
        }
    best_priority = source_priority(matches[0][0])[0]
    best_matches = [item for item in matches if source_priority(item[0])[0] == best_priority]
    if len(best_matches) > 1:
        if compatible_source_group(best_matches):
            selected_path, selected_row = min(best_matches, key=source_richness)
            return {
                "status": "resolved",
                "resolution_reason": "equivalent_duplicate_rows",
                "match_count": len(matches),
                "equivalent_match_count": len(best_matches),
                "source_path": str(selected_path),
                "row": dict(selected_row),
                "match_paths": [str(path) for path, _ in matches[:20]],
            }
        return {
            "status": "ambiguous",
            "resolution_reason": "conflicting_duplicate_rows",
            "match_count": len(matches),
            "equivalent_match_count": 0,
            "source_path": str(best_matches[0][0]),
            "row": None,
            "match_paths": [str(path) for path, _ in matches[:20]],
        }
    return {
        "status": "resolved",
        "resolution_reason": "unique_best_priority_row",
        "match_count": len(matches),
        "equivalent_match_count": 1,
        "source_path": str(matches[0][0]),
        "row": dict(matches[0][1]),
        "match_paths": [str(path) for path, _ in matches[:20]],
    }


def yes_no(value: Any) -> str:
    normalized = lower_text(value)
    aliases = {
        "y": "yes",
        "true": "yes",
        "1": "yes",
        "n": "no",
        "false": "no",
        "0": "no",
    }
    return aliases.get(normalized, normalized)


def process_old_gold_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    queue: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for row in rows:
        identifier = text(row.get("id") or row.get("qid") or row.get("question_id"))
        answer_correct = yes_no(row.get("answer_correct"))
        bbox_correct = yes_no(row.get("bbox_correct"))
        accept_reject = lower_text(row.get("accept_reject") or row.get("status"))
        ambiguity = yes_no(row.get("ambiguity"))
        rights_concern = yes_no(row.get("rights_concern"))
        corrected_answer = text(row.get("corrected_answer"))
        corrected_evidence = text(row.get("corrected_evidence_json"))
        notes = text(row.get("notes") or row.get("review_notes"))
        reasons: list[str] = []

        if answer_correct not in {"yes", "no"}:
            reasons.append("missing_or_invalid_answer_correct")
        if bbox_correct not in {"yes", "no"}:
            reasons.append("missing_or_invalid_bbox_correct")
        if accept_reject not in {"accept", "reject"}:
            reasons.append("missing_or_invalid_accept_reject")
        if ambiguity not in {"yes", "no"}:
            reasons.append("missing_or_invalid_ambiguity")
        if rights_concern not in {"yes", "no"}:
            reasons.append("missing_or_invalid_rights_concern")
        if answer_correct == "no" and not corrected_answer and not notes:
            reasons.append("answer_marked_wrong_without_correction_or_note")
        if bbox_correct == "no" and not corrected_evidence and not notes:
            reasons.append("bbox_marked_wrong_without_evidence_or_note")
        if accept_reject == "reject":
            reasons.append("reviewer_rejected_existing_gold")
        if ambiguity == "yes":
            reasons.append("reviewer_marked_ambiguous")
        if rights_concern == "yes":
            reasons.append("reviewer_marked_rights_concern")

        if reasons:
            if any(reason.startswith("missing_or_invalid") or reason.endswith("without_correction_or_note") for reason in reasons):
                stats["incomplete_old_gold_rows"] += 1
            stats["old_gold_adjudication_rows"] += 1
            queue.append(
                {
                    "sheet": OLD_GOLD_SHEET,
                    "id": identifier,
                    "task": text(row.get("task")),
                    "answer_correct": answer_correct,
                    "corrected_answer": corrected_answer,
                    "bbox_correct": bbox_correct,
                    "corrected_evidence_json": corrected_evidence,
                    "accept_reject": accept_reject,
                    "ambiguity": ambiguity,
                    "rights_concern": rights_concern,
                    "notes": notes,
                    "hold_reason": "; ".join(reasons),
                }
            )
        else:
            stats["old_gold_ok_rows"] += 1
    return queue, stats


def normalize_microtext_status(value: Any) -> str:
    return lower_text(value)


def normalize_visualdiff_status(value: Any) -> str:
    normalized = lower_text(value)
    return VISUALDIFF_STATUS_ALIASES.get(normalized, normalized)


def proposed_microtext_text(row: dict[str, str], source: dict[str, Any] | None) -> str:
    value = text(row.get("proposed_text"))
    if value:
        return value
    if source:
        return text(source.get("proposed_text") or source.get("target_text"))
    return ""


def hold_row(sheet: str, identifier: str, kind: str, status: str, reason: str, row: dict[str, str]) -> dict[str, Any]:
    return {
        "sheet": sheet,
        "kind": kind,
        "id": identifier,
        "status": status,
        "hold_reason": reason,
        "review_notes": text(row.get("review_notes") or row.get("human_notes") or row.get("notes")),
    }


def process_microtext_rows(
    root: Path,
    indexes: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
    rows_by_sheet: dict[str, list[dict[str, str]]],
    date_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str], list[str]]:
    staged: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    errors: list[str] = []

    for sheet in MICROTEXT_SHEETS:
        for row in rows_by_sheet.get(sheet, []):
            identifier = text(row.get("candidate_id") or row.get("id"))
            status = normalize_microtext_status(row.get("review_status"))
            stats[f"{sheet.lower().replace(' ', '_')}_rows"] += 1
            if not identifier:
                stats["microtext_missing_id_rows"] += 1
                holds.append(hold_row(sheet, "", "microtext", status, "missing_candidate_id", row))
                continue
            if status not in MICROTEXT_ALLOWED:
                stats["microtext_invalid_status_rows"] += 1
                errors.append(f"{sheet}:{identifier}: invalid review_status={status!r}")
                holds.append(hold_row(sheet, identifier, "microtext", status, "invalid_review_status", row))
                continue

            resolution = resolve_source_row(root, indexes, "microtext", identifier)
            resolutions.append(
                {
                    "sheet": sheet,
                    "kind": "microtext",
                    "id": identifier,
                    "status": status,
                    "source_resolution": resolution["status"],
                    "source_resolution_reason": resolution.get("resolution_reason", ""),
                    "source_match_count": resolution["match_count"],
                    "equivalent_source_rows": resolution.get("equivalent_match_count", 0),
                    "source_path": resolution["source_path"],
                    "match_paths": " | ".join(resolution["match_paths"]),
                }
            )
            if status in MICROTEXT_HOLD:
                stats["rejected_or_hold_rows"] += 1
                holds.append(hold_row(sheet, identifier, "microtext", status, "reviewer_hold_status", row))
                continue
            if resolution["status"] != "resolved":
                holds.append(hold_row(sheet, identifier, "microtext", status, f"source_{resolution['status']}", row))
                continue

            source_row = resolution["row"]
            assert isinstance(source_row, dict)
            proposed = proposed_microtext_text(row, source_row)
            corrected = text(row.get("corrected_text"))
            corrected_category = text(row.get("corrected_category") or row.get("proposed_category"))
            notes = text(row.get("review_notes") or row.get("notes"))
            if status == "accepted" and not proposed:
                stats["microtext_accepted_missing_text"] += 1
                errors.append(f"{sheet}:{identifier}: accepted row has no proposed text")
                holds.append(hold_row(sheet, identifier, "microtext", status, "accepted_missing_proposed_text", row))
                continue
            if status == "edited" and not corrected and not corrected_category:
                stats["microtext_edited_missing_correction"] += 1
                errors.append(
                    f"{sheet}:{identifier}: edited row requires corrected_text "
                    "and/or corrected_category"
                )
                holds.append(
                    hold_row(
                        sheet,
                        identifier,
                        "microtext",
                        status,
                        "edited_missing_correction",
                        row,
                    )
                )
                continue

            staged_row = dict(source_row)
            staged_row["candidate_id"] = identifier
            staged_row["review_status"] = status
            staged_row["corrected_text"] = corrected
            staged_row["review_notes"] = notes
            staged_row["human_completion_sheet"] = sheet
            staged_row["human_completion_date_label"] = date_label
            staged_row["human_completion_source_path"] = resolution["source_path"]
            staged_row["human_completion_source_match_count"] = resolution["match_count"]
            if corrected_category:
                staged_row["category"] = corrected_category
            if status == "edited" and corrected:
                staged_row["target_text"] = corrected
            elif proposed:
                staged_row.setdefault("target_text", proposed)
                staged_row.setdefault("proposed_text", proposed)
            staged.append(staged_row)
            stats["new_microtext_mergeable_rows"] += 1
    return staged, holds, resolutions, stats, errors


def current_visualdiff_description(row: dict[str, Any]) -> str:
    return text(row.get("description") or row.get("change_desc_gt"))


def process_visualdiff_rows(
    root: Path,
    indexes: dict[str, dict[str, list[tuple[Path, dict[str, Any]]]]],
    rows: list[dict[str, str]],
    date_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str], list[str]]:
    staged: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    errors: list[str] = []

    for row in rows:
        identifier = text(row.get("pair_id") or row.get("id"))
        status = normalize_visualdiff_status(row.get("human_status"))
        stats["new_visualdiff_rows"] += 1
        if not identifier:
            stats["visualdiff_missing_id_rows"] += 1
            holds.append(hold_row(VISUALDIFF_SHEET, "", "visualdiff", status, "missing_pair_id", row))
            continue
        if status not in VISUALDIFF_ALLOWED:
            stats["visualdiff_invalid_status_rows"] += 1
            errors.append(f"{VISUALDIFF_SHEET}:{identifier}: invalid human_status={status!r}")
            holds.append(hold_row(VISUALDIFF_SHEET, identifier, "visualdiff", status, "invalid_human_status", row))
            continue

        resolution = resolve_source_row(root, indexes, "visualdiff", identifier)
        resolutions.append(
            {
                "sheet": VISUALDIFF_SHEET,
                "kind": "visualdiff",
                "id": identifier,
                "status": status,
                    "source_resolution": resolution["status"],
                    "source_resolution_reason": resolution.get("resolution_reason", ""),
                    "source_match_count": resolution["match_count"],
                    "equivalent_source_rows": resolution.get("equivalent_match_count", 0),
                    "source_path": resolution["source_path"],
                    "match_paths": " | ".join(resolution["match_paths"]),
            }
        )
        if status in VISUALDIFF_HOLD:
            stats["rejected_or_hold_rows"] += 1
            holds.append(hold_row(VISUALDIFF_SHEET, identifier, "visualdiff", status, "reviewer_hold_status", row))
            continue
        if resolution["status"] != "resolved":
            holds.append(hold_row(VISUALDIFF_SHEET, identifier, "visualdiff", status, f"source_{resolution['status']}", row))
            continue

        source_row = resolution["row"]
        assert isinstance(source_row, dict)
        existing = current_visualdiff_description(source_row) or text(row.get("current_description"))
        description = text(row.get("human_description"))
        notes = text(row.get("human_notes") or row.get("review_notes") or row.get("notes"))
        if status == "edit" and not description:
            stats["visualdiff_edit_missing_description"] += 1
            errors.append(f"{VISUALDIFF_SHEET}:{identifier}: edit row requires human_description")
            holds.append(hold_row(VISUALDIFF_SHEET, identifier, "visualdiff", status, "edit_missing_human_description", row))
            continue
        if status == "valid" and (not existing or existing == TODO_DESCRIPTION):
            if not description:
                stats["visualdiff_valid_todo_missing_description"] += 1
                errors.append(f"{VISUALDIFF_SHEET}:{identifier}: valid TODO row requires human_description")
                holds.append(hold_row(VISUALDIFF_SHEET, identifier, "visualdiff", status, "valid_todo_missing_human_description", row))
                continue
            status = "edit"

        staged_row = dict(source_row)
        staged_row["pair_id"] = identifier
        staged_row["review_status"] = status
        staged_row["human_review_status"] = status
        staged_row["human_review_notes"] = notes
        staged_row["human_description"] = description
        staged_row["human_completion_sheet"] = VISUALDIFF_SHEET
        staged_row["human_completion_date_label"] = date_label
        staged_row["human_completion_source_path"] = resolution["source_path"]
        staged_row["human_completion_source_match_count"] = resolution["match_count"]
        if status == "edit":
            staged_row["description"] = description
            staged_row["change_desc_gt"] = description
            staged_row["desc_source"] = "human"
        staged.append(staged_row)
        stats["new_visualdiff_mergeable_rows"] += 1
    return staged, holds, resolutions, stats, errors


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Human Completion Workbook Processing",
        "",
        f"- Complete: `{str(report['complete']).lower()}`",
        f"- Safe to merge gold: `{str(report['safe_to_merge_gold']).lower()}`",
        f"- Workbook: `{report['workbook']}`",
        f"- Output dir: `{report['output_dir']}`",
        "",
        "## Totals",
        "",
    ]
    for key in sorted(totals):
        lines.append(f"- `{key}`: `{totals[key]}`")
    if report["errors"]:
        lines.extend(["", "## Blocking/Validation Notes", ""])
        lines.extend(f"- {error}" for error in report["errors"][:100])
    lines.extend(
        [
            "",
            "## Maintainer Rule",
            "",
            "This tool stages rows only. Do not merge staged rows into gold until the adjudication queue and source-resolution report are reviewed.",
            "",
        ]
    )
    return "\n".join(lines)


def copy_workbook_snapshot(workbook: Path, output_dir: Path) -> dict[str, str]:
    destination = output_dir / "returned_workbook_snapshot.xlsx"
    if destination.exists():
        return {"path": str(destination), "status": "already_exists"}
    try:
        shutil.copy2(workbook, destination)
        return {"path": str(destination), "status": "copied"}
    except PermissionError as exc:
        return {"path": str(destination), "status": f"copy_skipped_permission_error: {exc}"}


def process_workbook(
    root: Path,
    workbook: Path,
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    workbook = workbook if workbook.is_absolute() else root / workbook
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows_by_sheet = read_review_workbook(workbook)
    missing_sheets = [sheet for sheet in REQUIRED_SHEETS if sheet not in rows_by_sheet]
    indexes = build_source_indexes(root)

    old_queue, old_stats = process_old_gold_rows(rows_by_sheet.get(OLD_GOLD_SHEET, []))
    microtext_staged, microtext_holds, microtext_resolutions, microtext_stats, microtext_errors = process_microtext_rows(
        root,
        indexes,
        rows_by_sheet,
        date_label,
    )
    visualdiff_staged, visualdiff_holds, visualdiff_resolutions, visualdiff_stats, visualdiff_errors = process_visualdiff_rows(
        root,
        indexes,
        rows_by_sheet.get(VISUALDIFF_SHEET, []),
        date_label,
    )

    holds = microtext_holds + visualdiff_holds
    resolutions = microtext_resolutions + visualdiff_resolutions
    errors = list(microtext_errors) + list(visualdiff_errors)
    errors.extend(f"missing workbook sheet: {sheet}" for sheet in missing_sheets)

    totals: Counter[str] = Counter()
    totals.update(old_stats)
    totals.update(microtext_stats)
    totals.update(visualdiff_stats)
    totals["workbook_rows"] = sum(len(rows) for rows in rows_by_sheet.values())
    totals["old_gold_rows"] = len(rows_by_sheet.get(OLD_GOLD_SHEET, []))
    totals["rejected_or_hold_rows"] = len(holds)
    totals["source_resolution_rows"] = len(resolutions)
    totals["unresolved_source_rows"] += sum(1 for row in resolutions if row["source_resolution"] == "unresolved")
    totals["ambiguous_source_rows"] += sum(1 for row in resolutions if row["source_resolution"] == "ambiguous")
    totals["missing_sheets"] = len(missing_sheets)
    totals["errors"] = len(errors)
    snapshot = copy_workbook_snapshot(workbook, output_dir)

    complete = (
        totals["missing_sheets"] == 0
        and totals["incomplete_old_gold_rows"] == 0
        and totals["unresolved_source_rows"] == 0
        and totals["ambiguous_source_rows"] == 0
        and totals["errors"] == 0
    )
    report = {
        "complete": bool(complete),
        "safe_to_merge_gold": False,
        "root": str(root),
        "workbook": str(workbook),
        "output_dir": str(output_dir),
        "date_label": date_label,
        "sheet_rows": {sheet: len(rows) for sheet, rows in sorted(rows_by_sheet.items())},
        "totals": dict(sorted(totals.items())),
        "outputs": {
            "new_microtext_reviewed_jsonl": str(output_dir / "new_microtext_reviewed.jsonl"),
            "new_visualdiff_reviewed_jsonl": str(output_dir / "new_visualdiff_reviewed.jsonl"),
            "old_gold_adjudication_queue_csv": str(output_dir / "old_gold_adjudication_queue.csv"),
            "rejected_or_hold_rows_csv": str(output_dir / "rejected_or_hold_rows.csv"),
            "source_resolution_report_csv": str(output_dir / "source_resolution_report.csv"),
            "returned_workbook_snapshot": snapshot["path"],
        },
        "snapshot_status": snapshot["status"],
        "errors": errors,
    }

    write_jsonl(output_dir / "new_microtext_reviewed.jsonl", microtext_staged)
    write_jsonl(output_dir / "new_visualdiff_reviewed.jsonl", visualdiff_staged)
    write_csv(
        output_dir / "old_gold_adjudication_queue.csv",
        old_queue,
        [
            "sheet",
            "id",
            "task",
            "answer_correct",
            "corrected_answer",
            "bbox_correct",
            "corrected_evidence_json",
            "accept_reject",
            "ambiguity",
            "rights_concern",
            "notes",
            "hold_reason",
        ],
    )
    write_csv(
        output_dir / "rejected_or_hold_rows.csv",
        holds,
        ["sheet", "kind", "id", "status", "hold_reason", "review_notes"],
    )
    write_csv(
        output_dir / "source_resolution_report.csv",
        resolutions,
        [
            "sheet",
            "kind",
            "id",
            "status",
            "source_resolution",
            "source_resolution_reason",
            "source_match_count",
            "equivalent_source_rows",
            "source_path",
            "match_paths",
        ],
    )
    write_json(output_dir / "processing_summary.json", report)
    (output_dir / "processing_summary.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    report = process_workbook(
        root=Path(args.root),
        workbook=Path(args.workbook),
        output_dir=Path(args.output_dir),
        date_label=args.date_label,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if args.strict and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
