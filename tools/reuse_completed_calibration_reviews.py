#!/usr/bin/env python3
"""Reuse exact completed human reviews in a frozen machine-calibration checklist.

The tool is read-only with respect to active Gold. It fills a calibration row
only when a completed primary-review record has the same candidate identity,
an explicit accepted decision, and exact agreement with the frozen proposed
text and category. All other rows remain blank for independent calibration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ACTIVE_FILES = (
    "eng_bench.jsonl",
    "manifest.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [
            {str(key): str(value or "").strip() for key, value in row.items()}
            for row in reader
        ]
    return fields, rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def active_hashes(root: Path) -> dict[str, str]:
    return {
        relative: sha256(root / relative)
        for relative in ACTIVE_FILES
        if (root / relative).is_file()
    }


def reviewed_candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("source_candidate_id") or "").strip()


def normalized_category(row: dict[str, Any]) -> str:
    return str(row.get("corrected_category") or row.get("category") or "").strip()


def normalized_text(row: dict[str, Any]) -> str:
    return str(row.get("corrected_text") or row.get("target_text") or "").strip()


def is_explicit_accept(row: dict[str, Any]) -> bool:
    statuses = {
        str(row.get("human_review_status") or "").strip().lower(),
        str(row.get("primary_reviewer_status") or "").strip().lower(),
        str(row.get("review_status") or "").strip().lower(),
    }
    decision = str(row.get("primary_reviewer_decision_code") or "").strip()
    return "accepted" in statuses and decision in {"1", "1.0"}


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    return "\n".join(
        [
            "# Machine Calibration Review Reuse",
            "",
            f"- Goal: **{report['goal']}**",
            f"- Frozen calibration rows: `{counts['calibration_rows']}`",
            f"- Completed human reviews reused: `{counts['reused_correct_rows']}`",
            f"- Human calibration rows remaining: `{counts['remaining_rows']}`",
            f"- Conflicting completed reviews: `{counts['conflict_rows']}`",
            f"- Ready for machine finalization: `{str(report['ready_for_finalization']).lower()}`",
            f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
            "",
            "Only explicit accepted primary decisions with exact text and category",
            "agreement were reused. The remaining rows are still blank and must be",
            "independently reviewed before machine certification can finalize.",
            "",
        ]
    )


def build_reuse(
    *,
    root: Path,
    cohort_dir: Path,
    reviewed_jsonls: list[Path],
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    cohort_dir = resolve(root, cohort_dir)
    output_dir = resolve(root, output_dir)
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    quality_root = (root / "derived" / "quality").resolve()
    if output_dir != quality_root and quality_root not in output_dir.parents:
        raise ValueError("output-dir must be under derived/quality")

    checklist_path = cohort_dir / "calibration" / "machine_certification_calibration_checklist.csv"
    eligibility_path = cohort_dir / "eligibility_report.json"
    if not checklist_path.is_file() or not eligibility_path.is_file():
        raise FileNotFoundError("frozen checklist or eligibility report is missing")

    active_before = active_hashes(root)
    eligibility = json.loads(eligibility_path.read_text(encoding="utf-8"))
    fields, checklist = read_csv(checklist_path)
    sample = eligibility.get("calibration_sample") or {}
    expected_ids = [str(value) for value in sample.get("candidate_ids") or []]
    checklist_ids = [row.get("candidate_id", "") for row in checklist]
    if checklist_ids != expected_ids:
        raise ValueError("frozen checklist candidate order does not match eligibility report")
    expected_ids_sha = hashlib.sha256("\n".join(checklist_ids).encode("utf-8")).hexdigest()
    if expected_ids_sha != str(sample.get("candidate_ids_sha256") or ""):
        raise ValueError("frozen calibration candidate hash does not match eligibility report")
    if len(checklist_ids) != len(set(checklist_ids)) or any(not value for value in checklist_ids):
        raise ValueError("frozen calibration candidate identities are invalid")

    eligible_artifact = (eligibility.get("artifacts") or {}).get("auto_eligible") or {}
    eligible_value = str(eligible_artifact.get("path") or "").strip()
    if not eligible_value:
        raise ValueError("eligibility report does not name the auto-eligible artifact")
    eligible_path = resolve(root, Path(eligible_value))
    eligible_rows = read_jsonl(eligible_path)
    if sha256(eligible_path).lower() != str(eligible_artifact.get("sha256") or "").lower():
        raise ValueError("auto-eligible artifact hash does not match eligibility report")
    eligible_ids = [reviewed_candidate_id(row) for row in eligible_rows]
    if len(eligible_ids) != len(set(eligible_ids)) or any(not value for value in eligible_ids):
        raise ValueError("auto-eligible candidate identities are invalid")

    active_items_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    active_items = read_jsonl(active_items_path) if active_items_path.is_file() else []
    active_items_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active_items:
        for key in ("candidate_id", "source_candidate_id"):
            value = str(row.get(key) or "").strip()
            if value:
                active_items_by_candidate[value].append(row)
    already_active_eligible = [
        row for row in eligible_rows if reviewed_candidate_id(row) in active_items_by_candidate
    ]
    current_pending_eligible = [
        row for row in eligible_rows if reviewed_candidate_id(row) not in active_items_by_candidate
    ]
    current_pending_pin = sum(
        str(row.get("category") or "").strip() == "pin_label"
        for row in current_pending_eligible
    )
    current_pending_nonpin = len(current_pending_eligible) - current_pending_pin

    source_reports: list[dict[str, Any]] = []
    reviews_by_candidate: dict[str, list[tuple[dict[str, Any], Path]]] = defaultdict(list)
    reviewed_rows_scanned = 0
    for value in reviewed_jsonls:
        path = resolve(root, value)
        rows = read_jsonl(path)
        reviewed_rows_scanned += len(rows)
        source_reports.append(
            {"path": display(root, path), "sha256": sha256(path), "rows": len(rows)}
        )
        for row in rows:
            candidate = reviewed_candidate_id(row)
            if candidate in expected_ids:
                reviews_by_candidate[candidate].append((row, path))

    reasons: Counter[str] = Counter()
    reuse_by_candidate: dict[str, tuple[dict[str, Any], Path]] = {}
    conflicts: list[dict[str, Any]] = []
    checklist_by_id = {row["candidate_id"]: row for row in checklist}
    for candidate, entries in reviews_by_candidate.items():
        frozen = checklist_by_id[candidate]
        exact = [
            (row, path)
            for row, path in entries
            if is_explicit_accept(row)
            and normalized_text(row) == frozen.get("proposed_text", "")
            and normalized_category(row) == frozen.get("category", "")
            and str(row.get("task") or "microtext").strip().lower() == "microtext"
            and str(row.get("reserved_split") or "train").strip().lower() == "train"
        ]
        active_exact = [
            row
            for row in active_items_by_candidate.get(candidate, [])
            if str(row.get("text_gt") or "").strip() == frozen.get("proposed_text", "")
            and str(row.get("category") or "").strip() == frozen.get("category", "")
            and str(row.get("split") or "").strip().lower() == "train"
            and str(row.get("review_status") or "").strip().lower() == "accepted"
        ]
        signatures = {
            (normalized_text(row), normalized_category(row), is_explicit_accept(row))
            for row, _ in entries
        }
        if len(signatures) > 1:
            conflicts.append({"candidate_id": candidate, "review_count": len(entries)})
            reasons["conflicting_completed_reviews"] += 1
        elif exact and active_exact:
            reuse_by_candidate[candidate] = exact[0]
            reasons["exact_accepted_reused"] += 1
        elif exact:
            reasons["exact_review_not_verified_in_active_gold"] += 1
        else:
            reasons["review_not_exact_accepted_train_microtext"] += 1

    lineage_fields = [
        "reused_human_review_source",
        "reused_human_review_source_sha256",
        "reused_human_completion_workbook_sha256",
        "reused_human_completion_source",
        "reused_human_completion_date_label",
        "reused_primary_index",
    ]
    output_fields = fields + [field for field in lineage_fields if field not in fields]
    prefilled: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for frozen in checklist:
        output = dict(frozen)
        entry = reuse_by_candidate.get(frozen["candidate_id"])
        if entry:
            review, path = entry
            output.update(
                {
                    "reviewer_decision": "correct",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "Reused exact accepted primary review; see hash-bound reuse report.",
                    "reused_human_review_source": display(root, path),
                    "reused_human_review_source_sha256": sha256(path),
                    "reused_human_completion_workbook_sha256": str(
                        review.get("human_completion_workbook_sha256") or ""
                    ),
                    "reused_human_completion_source": str(
                        review.get("human_completion_source") or ""
                    ),
                    "reused_human_completion_date_label": str(
                        review.get("human_completion_date_label") or ""
                    ),
                    "reused_primary_index": str(review.get("primary_index") or ""),
                }
            )
            reused.append(output)
        else:
            remaining.append(dict(frozen))
        prefilled.append(output)

    output_dir.mkdir(parents=True)
    prefilled_path = output_dir / "prefilled_calibration_checklist_300.csv"
    reused_path = output_dir / "reused_completed_reviews.csv"
    remaining_path = output_dir / "remaining_calibration_checklist.csv"
    conflicts_path = output_dir / "conflicts.jsonl"
    current_pending_path = output_dir / "current_pending_auto_eligible.jsonl"
    already_active_path = output_dir / "already_active_auto_eligible.jsonl"
    write_csv(prefilled_path, output_fields, prefilled)
    write_csv(reused_path, output_fields, reused)
    write_csv(remaining_path, fields, remaining)
    with conflicts_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in conflicts:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_jsonl(current_pending_path, current_pending_eligible)
    write_jsonl(already_active_path, already_active_eligible)

    active_after = active_hashes(root)
    report = {
        "schema": "eng_bench_machine_calibration_review_reuse_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": active_before != active_after,
        "ready_for_finalization": len(remaining) == 0 and not conflicts,
        "safe_to_merge_gold": False,
        "cohort": {
            "directory": display(root, cohort_dir),
            "eligibility_report": display(root, eligibility_path),
            "eligibility_report_sha256": sha256(eligibility_path),
            "calibration_checklist": display(root, checklist_path),
            "calibration_checklist_sha256": sha256(checklist_path),
            "candidate_ids_sha256": expected_ids_sha,
        },
        "review_sources": source_reports,
        "counts": {
            "calibration_rows": len(checklist),
            "reviewed_rows_scanned": reviewed_rows_scanned,
            "sample_rows_with_review_evidence": len(reviews_by_candidate),
            "reused_correct_rows": len(reused),
            "remaining_rows": len(remaining),
            "conflict_rows": len(conflicts),
            "eligible_rows_original": len(eligible_rows),
            "eligible_rows_already_active": len(already_active_eligible),
            "eligible_rows_current_pending": len(current_pending_eligible),
            "eligible_rows_current_pending_nonpin": current_pending_nonpin,
            "eligible_rows_current_pending_pin": current_pending_pin,
        },
        "reason_counts": dict(sorted(reasons.items())),
        "artifacts": {
            "prefilled_checklist": {"path": display(root, prefilled_path), "sha256": sha256(prefilled_path)},
            "reused_reviews": {"path": display(root, reused_path), "sha256": sha256(reused_path)},
            "remaining_checklist": {"path": display(root, remaining_path), "sha256": sha256(remaining_path)},
            "conflicts": {"path": display(root, conflicts_path), "sha256": sha256(conflicts_path)},
            "current_pending_auto_eligible": {"path": display(root, current_pending_path), "sha256": sha256(current_pending_path)},
            "already_active_auto_eligible": {"path": display(root, already_active_path), "sha256": sha256(already_active_path)},
        },
        "active_file_hashes_before": active_before,
        "active_file_hashes_after": active_after,
        "interpretation": (
            "Exact completed human decisions reduce duplicate calibration work but do not authorize machine certification. "
            "Every remaining frozen row still requires an independent correct, incorrect, or unclear decision."
        ),
    }
    write_json(output_dir / "reuse_report.json", report)
    (output_dir / "reuse_report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort-dir", type=Path, required=True)
    parser.add_argument("--reviewed-jsonl", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args(argv)
    report = build_reuse(
        root=args.root,
        cohort_dir=args.cohort_dir,
        reviewed_jsonls=args.reviewed_jsonl,
        output_dir=args.output_dir,
        date_label=args.date_label,
    )
    print(
        json.dumps(
            {
                "calibration_rows": report["counts"]["calibration_rows"],
                "reused_correct_rows": report["counts"]["reused_correct_rows"],
                "remaining_rows": report["counts"]["remaining_rows"],
                "active_gold_modified": report["active_gold_modified"],
                "ready_for_finalization": report["ready_for_finalization"],
            },
            indent=2,
        )
    )
    return 0 if not report["active_gold_modified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
