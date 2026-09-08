#!/usr/bin/env python3
"""Bind double-pass agreement candidates to reviewed rows without promoting Gold."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_provenance_replacement_plan import candidate_evidence_fingerprint


FINAL_MICROTEXT = {"accepted", "edited"}
FINAL_VISUALDIFF = {"accepted", "edited", "valid", "edit"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def identity(row: dict[str, Any]) -> str:
    return next(
        (
            str(row.get(field) or "").strip()
            for field in ("record_id", "candidate_id", "pair_id", "item_id", "id")
            if str(row.get(field) or "").strip()
        ),
        "",
    )


def unique_map(rows: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = identity(row)
        if not row_id:
            raise ValueError(f"{label}:missing_identity")
        if row_id in result:
            raise ValueError(f"{label}:duplicate_identity:{row_id}")
        result[row_id] = row
    return result


def review_status(row: dict[str, Any], task: str) -> str:
    fields = (
        ("review_status", "human_review_status", "status")
        if task == "microtext"
        else ("human_review_status", "review_status", "status")
    )
    return next(
        (
            str(row.get(field) or "").strip().lower()
            for field in fields
            if str(row.get(field) or "").strip()
        ),
        "",
    )


def build_cohort(
    root: Path,
    pass_ledger_path: Path,
    microtext_reviewed_path: Path,
    visualdiff_reviewed_path: Path,
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    paths = [
        pass_ledger_path,
        microtext_reviewed_path,
        visualdiff_reviewed_path,
        output_dir,
    ]
    pass_ledger_path, microtext_reviewed_path, visualdiff_reviewed_path, output_dir = [
        path if path.is_absolute() else root / path for path in paths
    ]
    active_gold = root / "eng_bench.jsonl"
    active_hash_before = file_sha256(active_gold)

    pass_rows = read_jsonl(pass_ledger_path)
    reviewed = {
        "microtext": unique_map(read_jsonl(microtext_reviewed_path), "microtext_reviewed"),
        "visualdiff": unique_map(read_jsonl(visualdiff_reviewed_path), "visualdiff_reviewed"),
    }
    ready: dict[str, list[dict[str, Any]]] = {"microtext": [], "visualdiff": []}
    holds: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    seen: set[str] = set()

    for ledger_row in pass_rows:
        row_id = identity(ledger_row)
        task = str(ledger_row.get("task") or "").strip().lower()
        reasons: list[str] = []
        if not row_id:
            reasons.append("missing_ledger_identity")
        elif row_id in seen:
            reasons.append("duplicate_pass_ledger_identity")
        else:
            seen.add(row_id)
        if task not in reviewed:
            reasons.append("unsupported_task")
        candidate = reviewed.get(task, {}).get(row_id)
        if candidate is None:
            reasons.append("reviewed_row_not_found")
        if ledger_row.get("candidate_pair_pass") is not True:
            reasons.append("candidate_pair_not_pass")
        if ledger_row.get("candidate_dual_check_complete") is not True:
            reasons.append("candidate_dual_check_incomplete")
        if str(ledger_row.get("candidate_screen_decision_code") or "") != "1":
            reasons.append("candidate_screen_not_pass")
        if str(ledger_row.get("primary_decision_code") or "") != "1":
            reasons.append("primary_decision_not_pass")

        if candidate is not None:
            status = review_status(candidate, task)
            allowed = FINAL_MICROTEXT if task == "microtext" else FINAL_VISUALDIFF
            if status not in allowed:
                reasons.append(f"reviewed_row_nonfinal_status:{status or 'blank'}")
            if str(candidate.get("reserved_split") or "").strip().lower() != str(
                ledger_row.get("reserved_split") or ""
            ).strip().lower():
                reasons.append("reserved_split_mismatch")
            ledger_fingerprint = str(
                ledger_row.get("agreement_evidence_fingerprint") or ""
            ).strip().lower()
            candidate_fingerprint = str(
                candidate.get("replacement_evidence_fingerprint")
                or candidate.get("agreement_evidence_fingerprint")
                or ""
            ).strip().lower()
            if not candidate_fingerprint:
                candidate_fingerprint, fingerprint_status = candidate_evidence_fingerprint(
                    root, candidate
                )
                candidate_fingerprint = str(candidate_fingerprint or "").strip().lower()
                if fingerprint_status != "pixel_crop_sha256":
                    reasons.append(
                        f"evidence_fingerprint_recheck_unavailable:{fingerprint_status}"
                    )
            if not ledger_fingerprint or candidate_fingerprint != ledger_fingerprint:
                reasons.append("evidence_fingerprint_mismatch")

        reasons = sorted(set(reasons))
        if reasons:
            reason_counts.update(reasons)
            holds.append(
                {
                    "record_id": row_id,
                    "task": task,
                    "hold_reasons": reasons,
                    "agreement_ledger_row": ledger_row,
                    "reviewed_row": candidate,
                }
            )
            continue

        prepared = dict(candidate)
        prepared.update(
            {
                "agreement_candidate_contract_index": ledger_row.get(
                    "agreement_contract_index"
                ),
                "agreement_candidate_contract_sha256": ledger_row.get(
                    "agreement_contract_sha256"
                ),
                "agreement_candidate_screen_decision_code": "1",
                "agreement_candidate_screen_reviewer_id": ledger_row.get(
                    "candidate_screen_reviewer_id"
                ),
                "agreement_candidate_pair_outcome": "pass",
                "agreement_primary_decision_code": "1",
                "agreement_pre_promotion_status": (
                    "dual_candidate_pass_pending_release_gates"
                ),
                "formal_detailed_agreement_complete": False,
                "review_depth": "primary_plus_independent_candidate_screen",
                "safe_to_merge_gold": False,
            }
        )
        ready[task].append(prepared)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "microtext": output_dir / "agreement_pass_microtext_pending_gates.jsonl",
        "visualdiff": output_dir / "agreement_pass_visualdiff_pending_gates.jsonl",
        "holds": output_dir / "agreement_pass_holds.jsonl",
        "report": output_dir / "pre_promotion_cohort_report.json",
        "markdown": output_dir / "pre_promotion_cohort_report.md",
    }
    write_jsonl(output_paths["microtext"], ready["microtext"])
    write_jsonl(output_paths["visualdiff"], ready["visualdiff"])
    write_jsonl(output_paths["holds"], holds)
    active_hash_after = file_sha256(active_gold)
    report = {
        "schema": "eng_bench_agreement_pre_promotion_cohort_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": active_hash_before != active_hash_after,
        "active_gold_sha256_before": active_hash_before,
        "active_gold_sha256_after": active_hash_after,
        "safe_to_merge_gold": False,
        "formal_detailed_agreement_complete": False,
        "counts": {
            "pass_ledger_rows": len(pass_rows),
            "ready_rows": len(ready["microtext"]) + len(ready["visualdiff"]),
            "ready_microtext_rows": len(ready["microtext"]),
            "ready_visualdiff_rows": len(ready["visualdiff"]),
            "held_rows": len(holds),
        },
        "hold_reason_counts": dict(sorted(reason_counts.items())),
        "inputs": {
            "pass_ledger": str(pass_ledger_path),
            "pass_ledger_sha256": file_sha256(pass_ledger_path),
            "microtext_reviewed": str(microtext_reviewed_path),
            "microtext_reviewed_sha256": file_sha256(microtext_reviewed_path),
            "visualdiff_reviewed": str(visualdiff_reviewed_path),
            "visualdiff_reviewed_sha256": file_sha256(visualdiff_reviewed_path),
        },
        "artifacts": {
            name: {"path": str(path), "sha256": file_sha256(path)}
            for name, path in output_paths.items()
            if name not in {"report", "markdown"}
        },
        "interpretation": (
            "Rows have a matching primary decision and independent candidate screen. "
            "They remain outside Gold until provenance, split, leakage, duplicate, "
            "strict validation, and final agreement requirements are satisfied."
        ),
    }
    write_json(output_paths["report"], report)
    output_paths["markdown"].write_text(
        "\n".join(
            [
                "# Agreement Pre-Promotion Cohort",
                "",
                "- Goal: **Gold v2.0 Global**",
                f"- Double-pass ledger rows: **{len(pass_rows)}**",
                f"- Bound and ready for release-gate audit: **{report['counts']['ready_rows']}**",
                f"- Held on binding mismatch: **{len(holds)}**",
                "- Formal detailed agreement complete: **false**",
                "- Gold modified: **false**",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pass-ledger", type=Path, required=True)
    parser.add_argument("--microtext-reviewed", type=Path, required=True)
    parser.add_argument("--visualdiff-reviewed", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    report = build_cohort(
        args.root,
        args.pass_ledger,
        args.microtext_reviewed,
        args.visualdiff_reviewed,
        args.output_dir,
        args.date_label,
    )
    print(json.dumps(report["counts"], indent=2))
    if args.require_complete and (
        report["active_gold_modified"] or report["counts"]["held_rows"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
