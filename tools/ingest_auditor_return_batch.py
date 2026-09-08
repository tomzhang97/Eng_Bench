#!/usr/bin/env python3
"""Archive a partial auditor return, retaining complete answers and safety holds.

Issued workbooks and their frozen payload are authoritative. This command never
edits source workbooks, outstanding assignments, or active Gold annotations.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import process_partial_auditor_returns as parser
except ImportError:
    import process_partial_auditor_returns as parser


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def decision_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["reviewer_id"]), parser.row_identifier(row)


def reconcile_history(
    rows: list[dict[str, Any]], history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    previous: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in history:
        previous[decision_key(row)].add(str(row["decision_code"]))
    new, replayed, changed = [], [], []
    for row in rows:
        codes = previous.get(decision_key(row), set())
        if not codes:
            new.append(row)
        elif codes == {row["decision_code"]}:
            replayed.append(row)
        else:
            changed.append({**row, "prior_codes": sorted(codes),
                            "hold_reason": "historical_decision_conflict"})
    return new, replayed, changed


def active_identity_index(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for relative in ("microtext/annotations/microtext_items.jsonl",
                     "visualdiff/annotations/visualdiff_pairs.jsonl"):
        for row in read_jsonl(root / relative):
            keys = ["candidate_id", "item_id", "pair_id", "id"]
            # MicroText promotion changes the public ID but retains this exact
            # row-level provenance link. VisualDiff source IDs can name assets.
            if row.get("item_id"):
                keys.append("source_candidate_id")
            for key in keys:
                if row.get(key):
                    alias = str(row[key])
                    if alias in result and result[alias] != row:
                        raise ValueError(f"ambiguous_active_identity_alias:{alias}")
                    result[alias] = row
    return result


def workbook_comments(path: Path) -> list[dict[str, str]]:
    result = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.startswith("xl/") and "comment" in member.lower() and member.endswith(".xml"):
                doc = ET.fromstring(archive.read(member))
                for node in doc.iter():
                    if node.tag.rsplit("}", 1)[-1] not in {"comment", "threadedComment"}:
                        continue
                    texts = [x.text or "" for x in node.iter()
                             if x.tag.rsplit("}", 1)[-1] in {"t", "text"}]
                    result.append({"part": member, "cell": node.get("ref", ""),
                                   "text": "".join(texts)})
    return result


def require_literal_answers(path: Path, sheet_name: str, count: int) -> None:
    with zipfile.ZipFile(path) as archive:
        sheet_path = parser.workbook_io.workbook_sheet_paths(archive)[sheet_name]
        sheet = ET.fromstring(archive.read(sheet_path))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        answer_cells = {f"D{i}" for i in range(7, 7 + count)}
        for cell in sheet.iter(f"{ns}c"):
            if cell.get("r") in answer_cells and cell.find(f"{ns}f") is not None:
                raise ValueError(f"{path.name}: formula in human answer {cell.get('r')}")


def summarize_actions(
    observations: list[dict[str, Any]], active: dict[str, dict[str, Any]],
    primary: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    actions = []
    for row in observations:
        identifier = row["record_id"]
        code = row["decision_code"]
        is_active = identifier in active
        active_row = active.get(identifier, {})
        direct_keys = ("candidate_id", "item_id", "pair_id", "id")
        match_keys = [key for key in (*direct_keys, "source_candidate_id")
                      if str(active_row.get(key) or "") == identifier]
        reviewed = primary.get(identifier, {})
        primary_status = str(reviewed.get("primary_reviewer_status") or
                             reviewed.get("human_review_status") or
                             reviewed.get("review_status") or "")
        if code == "3":
            action = "active_gold_evidence_recheck" if is_active else "candidate_context_hold"
        elif code == "2":
            action = "active_gold_semantic_recheck" if is_active else "candidate_semantic_hold"
        elif is_active:
            action = "active_gold_audit_support_only"
        elif primary_status in {"accepted", "edited", "valid", "edit"}:
            action = "primary_and_auditor_support_pending_release_gates"
        else:
            action = "auditor_support_pending_primary_and_release_gates"
        actions.append({**row, "action": action,
                        "active_gold_match": is_active,
                        "active_gold_direct_identity_match": any(key in direct_keys for key in match_keys),
                        "active_gold_match_basis": match_keys,
                        "active_gold_identity": str(active_row.get("item_id") or active_row.get("pair_id") or active_row.get("id") or ""),
                        "primary_review_status": primary_status,
                        "primary_review_source": reviewed.get("reconciliation_source", ""),
                        "safe_to_merge_gold": False})
    return actions


def ingest(root: Path, source: Path, payload_path: Path, issued_dir: Path,
           output: Path, primary_paths: list[Path], retry_receipt: Path | None = None) -> dict[str, Any]:
    processed_root = (root / "derived/human_adjudication/processed_returns").resolve()
    if processed_root not in output.resolve().parents:
        raise ValueError("output must be a new directory below processed_returns")
    if output.exists():
        raise FileExistsError(f"return archive already exists: {output}")
    before = parser.active_gold_hashes(root)
    if len(before) != len(parser.ACTIVE_GOLD_PATHS):
        raise ValueError("active release files missing; cannot establish safety snapshot")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    expected = parser.expected_by_number(payload)
    issued_hashes = {n: parser.sha256_file(issued_dir / a["workbook"])
                     for n, a in expected.items()}
    source_hash = parser.sha256_file(source)
    history_paths = sorted(processed_root.glob("**/*auditor_decisions*.jsonl"))
    history = [row for path in history_paths for row in read_jsonl(path)]
    for path in processed_root.glob("**/receipt.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("source_sha256") == source_hash:
            previous_report = path.parent / "processing_report.json"
            retry_allowed = (retry_receipt is not None and path.resolve() == retry_receipt.resolve()
                             and previous_report.is_file()
                             and json.loads(previous_report.read_text(encoding="utf-8")).get("status") == "REVIEW_REQUIRED"
                             and receipt.get("payload_sha256") == parser.sha256_file(payload_path))
            if not retry_allowed:
                raise ValueError(f"this exact ZIP was already received: {path}")
    primary: dict[str, dict[str, Any]] = {}
    for path in primary_paths:
        for row in read_jsonl(path):
            identifier = parser.row_identifier(row)
            if identifier in primary and primary[identifier].get("primary_reviewer_status") != row.get("primary_reviewer_status"):
                raise ValueError(f"conflicting primary evidence for {identifier}")
            primary[identifier] = {**row, "reconciliation_source": path.as_posix()}

    with zipfile.ZipFile(source) as archive:
        members = parser.returned_xlsx_members(archive)
        if archive.testzip():
            raise ValueError("input ZIP failed CRC verification")
        numbers = [parser.reviewer_number(Path(m.filename)) for m in members]
        if len(numbers) != len(set(numbers)) or not set(numbers) <= set(expected):
            raise ValueError("duplicate or unknown returned auditor")
        output.mkdir(parents=True)
        shutil.copy2(source, output / "original_return.zip")
        received = output / "received"
        received.mkdir()
        receipt = {"source": source.as_posix(), "source_sha256": source_hash,
                   "retry_receipt": retry_receipt.as_posix() if retry_receipt else None,
                   "payload": payload_path.as_posix(), "payload_sha256": parser.sha256_file(payload_path),
                   "issued_dir": issued_dir.as_posix(), "workbooks": [], "ignored_members": [
                       m.filename for m in archive.infolist() if m not in members and not m.is_dir()]}
        paths = []
        for member, number in zip(members, numbers):
            path = received / f"AUDITOR_{number:02d}_RETURN.xlsx"
            with archive.open(member) as reader, path.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            paths.append((number, path))
            receipt["workbooks"].append({"auditor": number, "original_member": member.filename,
                                        "archived_file": path.relative_to(output).as_posix(),
                                        "sha256": parser.sha256_file(path)})
        write_json(output / "receipt.json", receipt)

    observations, reports, pending, failures, comments = [], [], [], [], []
    for number, path in sorted(paths):
        auditor = expected[number]
        try:
            require_literal_answers(path, auditor["sheet_name"], len(auditor["rows"]))
            rows, report = parser.validate_workbook(
                path, auditor, allow_incomplete_answers=True,
                canonical_workbook=issued_dir / auditor["workbook"],
            )
            for row in rows:
                row["assignment_payload"] = payload_path.as_posix()
                row["assignment_payload_sha256"] = receipt["payload_sha256"]
                row["return_source_sha256"] = source_hash
            observations.extend(rows)
            reports.append(report)
            pending.extend(report["incomplete_rows"])
            comments.extend({"reviewer_id": f"auditor_{number:02d}", **c} for c in workbook_comments(path))
        except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
            failures.append({"auditor": number, "error": str(error)})
    keys = [decision_key(r) for r in observations]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate reviewer/record decision in validated batch")
    new, replayed, changed = reconcile_history(observations, history)
    active = active_identity_index(root)
    actions = summarize_actions(observations, active, primary)
    holds = [r for r in actions if r["decision_code"] in {"2", "3"}]
    active_holds = [r for r in holds if r["active_gold_direct_identity_match"]]
    missing = sorted(set(expected) - set(numbers))
    after = parser.active_gold_hashes(root)
    issued_after = {n: parser.sha256_file(issued_dir / a["workbook"]) for n, a in expected.items()}
    if before != after or issued_after != issued_hashes:
        raise ValueError("active release or issued assignments changed during processing")

    outputs = {
        "observations.jsonl": observations,
        "normalized_valid_auditor_decisions.jsonl": new,
        "replayed_observations.jsonl": replayed,
        "historical_conflicts.jsonl": changed,
        "candidate_actions.jsonl": actions,
        "audit_holds.jsonl": holds,
        "active_gold_recheck.jsonl": active_holds,
        "pending_answers.jsonl": pending,
    }
    for name, rows in outputs.items():
        parser.write_jsonl(output / name, rows)
    write_json(output / "workbook_comments.json", comments)
    task_codes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in observations:
        task_codes[row["task_type"]][row["decision_code"]] += 1
    counts = dict(Counter(r["decision_code"] for r in observations))
    carried = sum(r["carried_forward_answer"] for r in observations)
    changed_carried = [r for r in observations if r["carried_forward_answer"] and
                       r["preserved_answer_code"] != r["decision_code"]]
    report = {
        "goal": "Gold v2.0 Global", "status": "PASS" if not failures and not changed else "REVIEW_REQUIRED",
        "mode": "independent_audit_archive_and_recheck_holds_not_gold_promotion",
        "assignment_payload": payload_path.as_posix(), "receipt_sha256": parser.sha256_file(output / "receipt.json"),
        "returned_auditors": sorted(numbers), "missing_auditors": missing,
        "missing_assignment_rows": sum(len(expected[n]["rows"]) for n in missing),
        "workbooks": reports, "failed_workbooks": failures,
        "validated_workbooks": len(reports), "completed_workbooks": sum(r["complete"] for r in reports),
        "validated_answer_count": len(observations), "blank_answer_count": len(pending),
        "decision_counts": counts, "task_decision_counts": {k: dict(v) for k, v in task_codes.items()},
        "carried_forward_answers": carried, "carried_answers_changed": len(changed_carried),
        "newly_filled_answer_cells": len(observations) - carried,
        "registered_decisions": len(new), "replayed_decisions": len(replayed), "historical_conflicts": len(changed),
        "history_inputs": [{"path": p.as_posix(), "sha256": parser.sha256_file(p)} for p in history_paths],
        "primary_inputs": [{"path": p.as_posix(), "sha256": parser.sha256_file(p)} for p in primary_paths],
        "pending_answers": pending, "active_gold_recheck_rows": len(active_holds), "audit_hold_rows": len(holds),
        "action_counts": dict(Counter(r["action"] for r in actions)), "workbook_comment_count": len(comments),
        "active_overlap_basis": "direct row IDs and exact MicroText source_candidate_id provenance links; not proof of unchanged evidence or region-level non-overlap",
        "active_gold_hashes_before": before, "active_gold_hashes_after": after,
        "issued_workbook_hashes_before": issued_hashes, "issued_workbook_hashes_after": issued_after,
        "active_gold_modified": False, "outstanding_assignments_modified": False,
        "gold_rows_modified": 0, "safe_to_merge_gold": False,
        "formal_agreement_gate_credit": 0,
        "interpretation": "Codes are human observations, not certified correctness. Blank answers stay pending. "
                          "Prefilled answers are not new independent judgments. Candidate and active-Gold "
                          "negative/uncertain decisions require reconciliation; no vote automatically promotes or deletes Gold.",
        "output_hashes": {name: parser.sha256_file(output / name) for name in outputs},
    }
    write_json(output / "processing_report.json", report)
    lines = ["# Auditor Return Receipt", "", "Goal: Gold v2.0 Global", "",
             f"Assignment: `{payload_path.name}` (not inferred from the ZIP name).",
             f"Validated {len(reports)} returned workbooks and {len(observations)} filled answers.",
             f"Decision codes: 1={counts.get('1', 0)}, 2={counts.get('2', 0)}, 3={counts.get('3', 0)}.",
             f"Carried-forward answers: {carried}; newly filled cells: {len(observations) - carried}.",
             f"Missing auditors: {', '.join(f'{n:02d}' for n in missing) or 'none'}; their assignments are unchanged.", "",
             "## Human Follow-up", ""]
    for row in pending:
        lines.append(f"- {row['reviewer_id']}: sample {row['display_index']}, sheet `{row['sheet_name']}`, "
                     f"cell `{row['answer_cell']}` remains blank. Fill only this answer in the existing workbook and return it.")
    lines += ["", "## Machine Follow-up", "",
              f"- Reconcile {len(holds)} negative/uncertain observations, including {len(active_holds)} active-Gold identities.",
              "- Compare returned evidence to current targets before any repair, retirement, or promotion.",
              "- Reuse recorded answers by reviewer/record identity; do not count resubmissions twice.",
              "- Formal agreement credit is zero until the separate protocol and release gates pass.",
              "", "Active Gold and all issued workbooks are byte-for-byte unchanged.", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    with (output / "auditor_status.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["auditor", "assigned", "completed", "pending", "status"])
        writer.writeheader()
        for number, auditor in expected.items():
            r = next((r for r in reports if r["auditor"] == number), None)
            completed = r["completed_rows"] if r else 0
            writer.writerow({"auditor": f"{number:02d}", "assigned": len(auditor["rows"]),
                             "completed": completed, "pending": len(auditor["rows"]) - completed,
                             "status": "not_returned_unchanged" if number in missing else
                                       "complete" if r and r["complete"] else "needs_completion_or_repair"})
    return report


def main() -> int:
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument("--root", type=Path, default=Path("."))
    args_parser.add_argument("--input", required=True, type=Path)
    args_parser.add_argument("--payload", required=True, type=Path)
    args_parser.add_argument("--issued-dir", required=True, type=Path)
    args_parser.add_argument("--output-dir", required=True, type=Path)
    args_parser.add_argument("--primary-reviewed", type=Path, action="append", default=[])
    args_parser.add_argument("--retry-receipt", type=Path,
                             help="Explicitly revalidate a prior REVIEW_REQUIRED receipt; existing votes are deduplicated.")
    args = args_parser.parse_args()
    root = args.root.resolve()
    resolve = lambda p: p.resolve() if p.is_absolute() else (root / p).resolve()
    report = ingest(root, resolve(args.input), resolve(args.payload), resolve(args.issued_dir),
                    resolve(args.output_dir), [resolve(p) for p in args.primary_reviewed],
                    resolve(args.retry_receipt) if args.retry_receipt else None)
    print(json.dumps({k: report[k] for k in ("status", "returned_auditors", "missing_auditors",
        "validated_answer_count", "blank_answer_count", "decision_counts", "carried_forward_answers",
        "newly_filled_answer_cells", "registered_decisions", "audit_hold_rows", "active_gold_recheck_rows",
        "active_gold_modified")}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
