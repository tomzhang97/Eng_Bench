#!/usr/bin/env python3
"""Validate and normalize any returned subset of auditor workbooks.

The hidden assignment identities and embedded evidence are checked against the
canonical payload. Results are archival QC only and never modify gold data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import build_ultrafast_human_audit_delivery as delivery
    from . import normalize_easiest_human_audit_returns as normalizer
    from . import prepare_ultrafast_human_audit_payload as payload_io
    from . import verify_multi_reviewer_handoff as workbook_io
except ImportError:  # Direct script execution from tools/.
    import build_ultrafast_human_audit_delivery as delivery
    import normalize_easiest_human_audit_returns as normalizer
    import prepare_ultrafast_human_audit_payload as payload_io
    import verify_multi_reviewer_handoff as workbook_io


AUDITOR_PATTERN = re.compile(r"AUDITOR_(\d{2})", re.IGNORECASE)
MAX_RETURN_WORKBOOKS = 64
MAX_RETURN_WORKBOOK_BYTES = 50 * 1024 * 1024
MAX_RETURN_TOTAL_BYTES = 500 * 1024 * 1024
ACTIVE_GOLD_PATHS = (
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "eng_bench.jsonl",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_gold_hashes(root: Path) -> dict[str, str]:
    return {
        relative: sha256_file(root / relative)
        for relative in ACTIVE_GOLD_PATHS
        if (root / relative).is_file()
    }


def returned_xlsx_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = [
        member
        for member in archive.infolist()
        if not member.is_dir()
        and Path(member.filename).suffix.lower() == ".xlsx"
        and not Path(member.filename).name.startswith("~$")
        and "__MACOSX" not in Path(member.filename).parts
    ]
    if not members:
        raise ValueError("return ZIP contains no XLSX workbooks")
    if len(members) > MAX_RETURN_WORKBOOKS:
        raise ValueError(f"return ZIP contains too many workbooks: {len(members)}")
    names = [Path(member.filename).name for member in members]
    folded = [name.casefold() for name in names]
    if len(folded) != len(set(folded)):
        raise ValueError("return ZIP contains duplicate workbook basenames")
    oversized = [
        Path(member.filename).name
        for member in members
        if member.file_size > MAX_RETURN_WORKBOOK_BYTES
    ]
    if oversized:
        raise ValueError(f"return workbook exceeds size limit: {oversized[0]}")
    total = sum(member.file_size for member in members)
    if total > MAX_RETURN_TOTAL_BYTES:
        raise ValueError(f"return ZIP workbook payload exceeds size limit: {total}")
    return members


def expand_return_inputs(
    inputs: list[Path], temporary_root: Path
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Discover XLSX files and safely flatten ZIP members into temporary storage."""
    workbooks: list[Path] = []
    sources: list[dict[str, Any]] = []
    for index, source in enumerate(inputs, 1):
        source = source.resolve()
        if source.is_dir():
            found = sorted(
                path
                for path in source.rglob("*.xlsx")
                if not path.name.startswith("~$")
            )
            if not found:
                raise ValueError(f"return directory contains no XLSX workbooks: {source}")
            if len(found) > MAX_RETURN_WORKBOOKS:
                raise ValueError(f"return directory contains too many workbooks: {len(found)}")
            oversized = [path for path in found if path.stat().st_size > MAX_RETURN_WORKBOOK_BYTES]
            if oversized:
                raise ValueError(f"return workbook exceeds size limit: {oversized[0].name}")
            total = sum(path.stat().st_size for path in found)
            if total > MAX_RETURN_TOTAL_BYTES:
                raise ValueError(f"return directory workbook payload exceeds size limit: {total}")
            workbooks.extend(found)
            sources.append(
                {"path": source.as_posix(), "kind": "directory", "workbooks": len(found)}
            )
            continue
        if not source.is_file():
            raise FileNotFoundError(f"return input does not exist: {source}")
        if source.suffix.lower() == ".xlsx":
            if source.name.startswith("~$"):
                raise ValueError(f"temporary Excel lock file is not a return: {source.name}")
            workbooks.append(source)
            sources.append(
                {
                    "path": source.as_posix(),
                    "kind": "xlsx",
                    "sha256": sha256_file(source),
                    "workbooks": 1,
                }
            )
            continue
        if source.suffix.lower() != ".zip":
            raise ValueError(f"unsupported return input: {source}")
        destination = temporary_root / f"zip_{index:02d}"
        destination.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(source, "r") as archive:
            members = returned_xlsx_members(archive)
            for member in members:
                target = destination / Path(member.filename).name
                with archive.open(member, "r") as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
                workbooks.append(target)
        sources.append(
            {
                "path": source.as_posix(),
                "kind": "zip",
                "sha256": sha256_file(source),
                "workbooks": len(members),
            }
        )
    if not workbooks:
        raise ValueError("no returned auditor workbooks discovered")
    return workbooks, sources


def reviewer_number(path: Path) -> int:
    match = AUDITOR_PATTERN.search(path.stem)
    if not match:
        raise ValueError(f"unrecognized auditor filename: {path.name}")
    return int(match.group(1))


def row_identifier(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("record_id") or "")


def expected_by_number(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    auditors = payload.get("auditors")
    if not isinstance(auditors, list):
        raise ValueError("payload auditors must be a list")
    result: dict[int, dict[str, Any]] = {}
    for auditor in auditors:
        number = int(auditor["number"])
        if number in result:
            raise ValueError(f"duplicate auditor in payload: {number:02d}")
        result[number] = auditor
    return result


def embedded_evidence_hashes(
    path: Path,
    sheet_name: str = delivery.AUDIT_SHEET,
    row_count: int = 12,
    layout_report: dict[str, Any] | None = None,
) -> list[str]:
    """Return evidence hashes ordered by their visible Excel row."""
    def row_bounds(excel_row: int, heights: dict[int, float], default_height: float) -> tuple[int, int]:
        top = int(sum(heights.get(i, default_height) for i in range(1, excel_row)) * 12700)
        return top, top + int(heights.get(excel_row, default_height) * 12700)

    def geometric_row(top: int, extent: int, heights: dict[int, float],
                      default_height: float, final_row: int) -> tuple[int, float]:
        center = top + extent // 2
        for excel_row in range(1, final_row + 1):
            row_top, row_bottom = row_bounds(excel_row, heights, default_height)
            if row_top <= center < row_bottom:
                overlap = max(0, min(top + extent, row_bottom) - max(top, row_top))
                return excel_row, overlap / extent if extent else 0.0
        return 0, 0.0

    with zipfile.ZipFile(path, "r") as archive:
        sheets = workbook_io.workbook_sheet_paths(archive)
        sheet_path = sheets.get(sheet_name)
        if not sheet_path:
            raise ValueError(f"{path.name}: missing audit sheet")
        sheet = ET.fromstring(archive.read(sheet_path))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        format_node = sheet.find(f"{ns}sheetFormatPr")
        default_height = float(format_node.get("defaultRowHeight", "15")) if format_node is not None else 15
        heights = {int(r.get("r", "0")): float(r.get("ht", str(default_height)))
                   for r in sheet.findall(f"{ns}sheetData/{ns}row")}
        data_top = sum(heights.get(i, default_height) for i in range(1, 7)) * 12700
        sheet_relations = payload_io.relation_targets(archive, sheet_path)
        drawing_paths = sorted(
            target
            for target in sheet_relations.values()
            if "/drawings/" in target and target.endswith(".xml")
        )
        anchors: list[tuple[int, str]] = []
        header_duplicates: list[str] = []
        adjusted_anchors: list[dict[str, Any]] = []
        for drawing_path in drawing_paths:
            drawing_relations = payload_io.relation_targets(archive, drawing_path)
            drawing = ET.fromstring(archive.read(drawing_path))
            for anchor in list(drawing):
                picture = anchor.find(f"{{{payload_io.DRAWING_NS}}}pic")
                start = anchor.find(f"{{{payload_io.DRAWING_NS}}}from")
                if picture is None or start is None:
                    continue
                row_node = start.find(f"{{{payload_io.DRAWING_NS}}}row")
                blip = picture.find(f".//{{{payload_io.DRAWINGML_NS}}}blip")
                if row_node is None or blip is None:
                    continue
                relation_id = blip.get(f"{{{payload_io.OFFICE_REL_NS}}}embed", "")
                media_path = drawing_relations.get(relation_id, "")
                if media_path not in archive.namelist():
                    raise ValueError(f"{path.name}: missing embedded evidence {media_path}")
                encoded_row = int(row_node.text or "0") + 1
                evidence_hash = sha256_bytes(archive.read(media_path))
                extent = anchor.find(f"{{{payload_io.DRAWING_NS}}}ext")
                offset = int(start.findtext(f"{{{payload_io.DRAWING_NS}}}rowOff", "0"))
                encoded_top, _ = row_bounds(encoded_row, heights, default_height)
                top = encoded_top + offset
                cy = int(extent.get("cy", "0")) if extent is not None else 0
                excel_row, containment = geometric_row(
                    top, cy, heights, default_height, 6 + row_count
                )
                if not excel_row or not cy:
                    raise ValueError(f"{path.name}: invalid evidence geometry")
                if excel_row < 7:
                    if top + cy > data_top:
                        raise ValueError(f"{path.name}: extra image overlaps review rows")
                    header_duplicates.append(evidence_hash)
                else:
                    if containment < 0.90:
                        raise ValueError(
                            f"{path.name}: evidence image is not contained in one assigned row"
                        )
                    if excel_row != encoded_row:
                        adjusted_anchors.append({
                            "encoded_row": encoded_row,
                            "geometric_row": excel_row,
                            "containment": round(containment, 6),
                            "evidence_sha256": evidence_hash,
                        })
                    anchors.append((excel_row, evidence_hash))
    anchors.sort()
    if [row for row, _ in anchors] != list(range(7, 7 + row_count)):
        raise ValueError(f"{path.name}: evidence anchors do not match assigned rows")
    if not set(header_duplicates) <= {value for _, value in anchors}:
        raise ValueError(f"{path.name}: unassigned extra image in header")
    if layout_report is not None:
        layout_report["nonoverlapping_header_duplicate_images"] = len(header_duplicates)
        layout_report["geometrically_recovered_anchors"] = adjusted_anchors
    return [value for _, value in anchors]


def normalize_decision(task: str, code: str) -> str:
    if code not in {"1", "2", "3"}:
        raise ValueError(f"invalid auditor decision {code!r}")
    if task == "microtext":
        return {"1": "pass", "2": "issue", "3": "needs_context"}[code]
    if task == "visualdiff":
        return {"1": "has_engineering_change", "2": "no_engineering_change", "3": "unclear"}[code]
    raise ValueError(f"unknown task: {task!r}")


def normalize_display_index(value: Any) -> str:
    text = str(value or "").strip()
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        return text
    if numeric == numeric.to_integral_value():
        return str(int(numeric))
    return text


def normalize_decision_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    normalized = normalize_display_index(text)
    return normalized if normalized in {"1", "2", "3"} else text


def normalize_machine_suggestion(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def validate_return_sheet_states(
    path: Path,
    states: dict[str, str],
    audit_sheet: str = delivery.AUDIT_SHEET,
    machine_sheet: str = delivery.MACHINE_SHEET,
) -> str:
    """Accept hidden-sheet downgrades caused by Excel/WPS return saves."""
    if set(states) != {audit_sheet, machine_sheet}:
        raise ValueError(f"{path.name}: unexpected sheet states {states}")
    if states.get(audit_sheet) != "visible":
        raise ValueError(f"{path.name}: audit sheet is not visible")
    machine_state = states.get(machine_sheet, "missing")
    if machine_state not in {"hidden", "veryHidden"}:
        raise ValueError(
            f"{path.name}: machine sheet must remain hidden, found {machine_state}"
        )
    return machine_state


def validate_workbook(
    path: Path,
    auditor: dict[str, Any],
    *,
    allow_incomplete_answers: bool = False,
    canonical_workbook: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    number = int(auditor["number"])
    expected_rows = list(auditor.get("rows") or [])
    expected_count = len(expected_rows)
    if expected_count <= 0:
        raise ValueError(f"auditor {number:02d}: payload has no rows")
    sheet_name = str(auditor.get("sheet_name") or delivery.AUDIT_SHEET)
    machine_sheet_name = str(
        auditor.get("machine_sheet_name") or delivery.MACHINE_SHEET
    )

    states = delivery.sheet_states(path)
    machine_sheet_state = validate_return_sheet_states(
        path, states, sheet_name, machine_sheet_name
    )
    with zipfile.ZipFile(path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"{path.name}: XLSX CRC failure at {corrupt}")

    machine_rows = normalizer._machine_rows(path)
    if len(machine_rows) != expected_count:
        raise ValueError(f"{path.name}: expected {expected_count} machine rows")
    expected_identity = [
        (
            str(row["primary_index"]),
            str(row["task"]),
            row_identifier(row),
            str(row["display_index"]),
            normalize_machine_suggestion(row["machine_suggestion"]),
        )
        for row in expected_rows
    ]
    actual_identity = [
        (
            str(row.get("primary_index", "")),
            str(row.get("task", "")),
            str(row.get("record_id", "")),
            str(row.get("display_index", "")),
            normalize_machine_suggestion(row.get("machine_suggestion", "")),
        )
        for row in machine_rows
    ]
    if actual_identity != expected_identity:
        raise ValueError(f"{path.name}: hidden assignment identities/source/order changed")

    visible_rows = normalizer._auditor_records(path, sheet_name, None)
    if len(visible_rows) != expected_count:
        raise ValueError(f"{path.name}: expected {expected_count} visible rows")
    layout_report: dict[str, Any] = {}
    embedded = embedded_evidence_hashes(path, sheet_name, expected_count, layout_report)
    expected_evidence = []
    for row in expected_rows:
        evidence_path = Path(str(row.get("evidence_path") or ""))
        if not evidence_path.is_file():
            raise FileNotFoundError(f"{path.name}: missing canonical evidence {evidence_path}")
        evidence_hash = sha256_file(evidence_path)
        frozen_hash = str(row.get("evidence_sha256") or "")
        if frozen_hash and frozen_hash != evidence_hash:
            raise ValueError(f"{path.name}: canonical evidence changed since assignment")
        expected_evidence.append(evidence_hash)
    if embedded != expected_evidence:
        raise ValueError(f"{path.name}: embedded evidence differs from the assignment payload")

    if canonical_workbook is not None:
        canonical_machine = normalizer._machine_rows(canonical_workbook)
        def stable_machine(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
            numeric = {"primary_index", "canonical_position", "display_index"}
            return [{k: (normalize_display_index(v) if k in numeric else normalize_machine_suggestion(v))
                     for k, v in row.items()} for row in rows]

        if stable_machine(machine_rows) != stable_machine(canonical_machine):
            raise ValueError(f"{path.name}: immutable machine cells changed")
        canonical_visible = normalizer._auditor_records(canonical_workbook, sheet_name, None)
        if len(canonical_visible) != expected_count:
            raise ValueError(f"{path.name}: canonical visible row count mismatch")
        editable = {normalizer.ULTRA_VISUAL_DECISION,
                    normalizer.ULTRA_VISUAL_DECISION_LEGACY, "\u72b6\u6001"}
        for offset, (visible, issued) in enumerate(zip(visible_rows, canonical_visible), 7):
            if set(visible) != set(issued):
                raise ValueError(f"{path.name} row {offset}: visible columns changed")
            for key, value in issued.items():
                normalize = normalize_display_index if key == "#" else normalize_machine_suggestion
                if key not in editable and normalize(visible[key]) != normalize(value):
                    raise ValueError(f"{path.name} row {offset}: immutable visible cells changed")

    normalized: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    incomplete: list[dict[str, Any]] = []
    workbook_hash = sha256_file(path)
    for offset, (visible, expected) in enumerate(zip(visible_rows, expected_rows), start=7):
        if normalize_display_index(visible.get("#", "")) != str(expected["display_index"]):
            raise ValueError(f"{path.name} row {offset}: display index changed")
        code = normalize_decision_code(
            normalizer._first_value(
                visible,
                normalizer.ULTRA_VISUAL_DECISION,
                normalizer.ULTRA_VISUAL_DECISION_LEGACY,
            )
        )
        if not code and allow_incomplete_answers:
            incomplete.append({
                "reviewer_id": f"auditor_{number:02d}",
                "record_id": row_identifier(expected),
                "display_index": int(expected["display_index"]),
                "sheet_name": sheet_name,
                "answer_cell": f"D{offset}",
                "reason": "blank_answer",
                "safe_to_merge_gold": False,
            })
            continue
        decision = normalize_decision(str(expected["task"]), code)
        decisions[code] += 1
        identifier = row_identifier(expected)
        normalized.append(
            {
                "goal": "Gold v2.0 Global",
                "reviewer_id": f"auditor_{number:02d}",
                "reviewer_role": "independent_auditor",
                "task_type": expected["task"],
                "primary_index": int(expected["primary_index"]),
                "record_id": identifier,
                "candidate_id": identifier if expected["task"] == "microtext" else "",
                "pair_id": identifier if expected["task"] == "visualdiff" else "",
                "decision_code": code,
                "decision": decision,
                "source_workbook": path.name,
                "source_workbook_sha256": workbook_hash,
                "display_index": int(expected["display_index"]),
                "sheet_name": sheet_name,
                "answer_cell": f"D{offset}",
                "evidence_sha256": expected_evidence[offset - 7],
                "assignment_origin": expected.get("assignment_origin", ""),
                "preserved_answer_code": expected.get("preserved_answer_code", ""),
                "carried_forward_answer": bool(expected.get("preserved_answer_code")),
                "safe_to_merge_gold": False,
                "gold_rows_modified": 0,
            }
        )
    return normalized, {
        "auditor": number,
        "workbook": path.name,
        "sha256": workbook_hash,
        "rows": expected_count,
        "completed_rows": len(normalized),
        "incomplete_rows": incomplete,
        "complete": not incomplete,
        "immutable_visible_match": canonical_workbook is not None,
        "decision_counts": dict(sorted(decisions.items())),
        "identity_match": True,
        "evidence_match": True,
        "machine_sheet_state": machine_sheet_state,
        "machine_sheet_state_compatible": True,
        "embedded_images": len(embedded),
        **layout_report,
        "valid": True,
    }


def process(
    inputs: list[Path],
    payload_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    expected = expected_by_number(payload)
    rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    seen_reviewers: set[int] = set()
    seen_records: set[str] = set()
    for path in sorted(inputs, key=lambda value: value.name):
        number = reviewer_number(path)
        if number in seen_reviewers:
            raise ValueError(f"duplicate returned auditor: {number:02d}")
        if number not in expected:
            raise ValueError(f"auditor {number:02d} is not present in the payload")
        seen_reviewers.add(number)
        normalized, workbook_report = validate_workbook(path, expected[number])
        for row in normalized:
            identifier = row["record_id"]
            if identifier in seen_records:
                raise ValueError(f"duplicate audit record across returns: {identifier}")
            seen_records.add(identifier)
        rows.extend(normalized)
        reports.append(workbook_report)

    decision_counts = Counter(row["decision_code"] for row in rows)
    task_counts = Counter(row["task_type"] for row in rows)
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "partial_auditor_return_archive_only",
        "payload": payload_path.resolve().as_posix(),
        "returned_auditors": sorted(seen_reviewers),
        "workbook_count": len(reports),
        "rows": len(rows),
        "unique_record_ids": len(seen_records),
        "decision_counts": dict(sorted(decision_counts.items())),
        "task_counts": dict(sorted(task_counts.items())),
        "workbooks": reports,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "valid": bool(reports)
        and len(rows) == sum(report["rows"] for report in reports),
        "interpretation": (
            "Independent auditor results are archived for QC only. A primary decision and "
            "all release gates are still required before any gold promotion."
        ),
    }
    return rows, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output-jsonl", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--require-complete-set", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    resolve = lambda value: value.resolve() if value.is_absolute() else (root / value).resolve()
    requested_inputs = [resolve(value) for value in args.inputs]
    payload_path = resolve(args.payload)
    output_path = resolve(args.output_jsonl)
    report_path = resolve(args.report_json)
    if output_path.exists() or report_path.exists():
        raise FileExistsError("return output already exists")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    active_before = active_gold_hashes(root)
    failure: Exception | None = None
    rows: list[dict[str, Any]] = []
    report: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="engbench_auditor_returns_") as temporary:
        try:
            inputs, sources = expand_return_inputs(
                requested_inputs, Path(temporary)
            )
            rows, report = process(inputs, payload_path)
            report["return_sources"] = sources
            expected_workbooks = len(expected_by_number(json.loads(payload_path.read_text(encoding="utf-8"))))
            if args.require_complete_set and report["workbook_count"] != expected_workbooks:
                report.setdefault("issues", []).append(
                    f"complete_return_set_required:{report['workbook_count']}:{expected_workbooks}"
                )
                report["valid"] = False
        except Exception as error:  # The report is the fail-closed audit trail.
            failure = error
            report = {
                "goal": "Gold v2.0 Global",
                "mode": "partial_auditor_return_archive_only",
                "valid": False,
                "issues": [f"{type(error).__name__}:{error}"],
                "return_sources": [path.as_posix() for path in requested_inputs],
                "safe_to_merge_gold": False,
                "gold_rows_modified": 0,
            }

    active_after = active_gold_hashes(root)
    active_modified = active_before != active_after
    if active_modified:
        report.setdefault("issues", []).append("active_gold_changed_during_return_processing")
        report["valid"] = False
    report.update(
        {
            "status": "PASS" if report.get("valid") else "FAIL",
            "payload": payload_path.as_posix(),
            "payload_sha256": sha256_file(payload_path) if payload_path.is_file() else "",
            "active_gold_modified": active_modified,
            "active_gold_hashes_before": active_before,
            "active_gold_hashes_after": active_after,
        }
    )
    if report.get("valid") and failure is None:
        write_jsonl(output_path, rows)
        report["output_jsonl"] = output_path.as_posix()
        report["output_sha256"] = sha256_file(output_path)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "workbooks": report.get("workbook_count", 0),
                "rows": report.get("rows", 0),
                "active_gold_modified": report["active_gold_modified"],
                "issues": report.get("issues", []),
                "report": report_path.as_posix(),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
