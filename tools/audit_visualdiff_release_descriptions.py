#!/usr/bin/env python3
"""Build a read-only remediation queue for nonfinal VisualDiff descriptions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from visualdiff_description_finality import description_release_issue
except ModuleNotFoundError:
    from tools.visualdiff_description_finality import description_release_issue


ACTIVE_PATHS = (
    "eng_bench.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
)

LANES = {
    "placeholder": (
        "machine_evidence_rewrite",
        "Derive a specific English description from the paired images and text layers; "
        "send only ambiguous cases to semantic review.",
        "conditional",
    ),
    "unvalidated_machine_visual": (
        "human_semantic_confirmation",
        "Replace the generic machine description with a specific engineering change and "
        "obtain an independent semantic decision.",
        "yes",
    ),
    "generic_machine_description": (
        "human_semantic_confirmation",
        "Replace the generic machine description with a specific engineering change and "
        "obtain an independent semantic decision.",
        "yes",
    ),
    "non_english": (
        "machine_english_localization",
        "Translate the already reviewed meaning into concise English without adding new "
        "engineering claims; verify against the evidence before promotion.",
        "conditional",
    ),
    "tentative_generator_template": (
        "machine_evidence_resolution",
        "Resolve the tentative wording against paired evidence; request human confirmation "
        "when correspondence or topology remains ambiguous.",
        "conditional",
    ),
    "blank": (
        "machine_evidence_rewrite",
        "Create a specific English description from paired evidence; send ambiguous cases "
        "to semantic review.",
        "conditional",
    ),
}


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def remediation_row(
    row: dict[str, Any],
    unified: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    issue = description_release_issue(row)
    if not issue:
        return None
    reason = issue["reason"]
    lane, action, human_required = LANES[reason]
    split = str(row.get("split") or "unknown")
    unified = unified or {}
    images = unified.get("images") if isinstance(unified.get("images"), list) else []
    evidence = (
        unified.get("evidence")
        if isinstance(unified.get("evidence"), list) else []
    )

    def evidence_bbox(index: int) -> Any:
        if index >= len(evidence) or not isinstance(evidence[index], dict):
            return None
        return evidence[index].get("bbox")

    return {
        "priority": {"test": 1, "dev": 2, "train": 3}.get(split, 4),
        "pair_id": str(row.get("pair_id") or row.get("id") or ""),
        "project_id": str(row.get("project_id") or ""),
        "split": split,
        "reason": reason,
        "kind": issue.get("kind", ""),
        "desc_source": str(row.get("desc_source") or ""),
        "current_description": str(row.get("change_desc_gt") or ""),
        "target_old": issue.get("target_old", ""),
        "target_new": issue.get("target", ""),
        "image_old": str(row.get("image_old") or (images[0] if images else "")),
        "image_new": str(row.get("image_new") or (images[1] if len(images) > 1 else "")),
        "page_index_old": row.get("page_index_old"),
        "page_index_new": row.get("page_index_new"),
        "bbox_old": row.get("bbox_old") or evidence_bbox(0),
        "bbox_new": row.get("bbox_new") or evidence_bbox(1),
        "machine_lane": lane,
        "human_required": human_required,
        "recommended_action": action,
        "promotion_status": "hold_until_repaired_and_validated",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = (
        "priority", "pair_id", "project_id", "split", "reason", "kind",
        "desc_source", "current_description", "target_old", "target_new",
        "image_old", "image_new", "page_index_old", "page_index_new",
        "bbox_old", "bbox_new", "machine_lane", "human_required",
        "recommended_action", "promotion_status",
    )
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row[key], ensure_ascii=False)
                if isinstance(row.get(key), (list, dict)) else row.get(key, "")
                for key in columns
            })


def build_queue(root: Path, output_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    quality_root = (root / "derived" / "quality").resolve()
    if not output_dir.is_relative_to(quality_root) or output_dir.exists():
        raise ValueError("output must be a new directory under root/derived/quality")

    before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    pairs_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    unified_by_pair_id = {
        str(row.get("metadata", {}).get("pair_id") or ""): row
        for row in read_jsonl(root / "eng_bench.jsonl")
        if row.get("task") == "visualdiff" and isinstance(row.get("metadata"), dict)
    }
    rows = [
        item
        for item in (
            remediation_row(row, unified_by_pair_id.get(str(row.get("pair_id") or "")))
            for row in read_jsonl(pairs_path)
        )
        if item
    ]
    rows.sort(key=lambda row: (row["priority"], row["reason"], row["pair_id"]))

    for row in rows:
        row["image_old_exists"] = bool(
            row["image_old"] and (root / row["image_old"]).is_file()
        )
        row["image_new_exists"] = bool(
            row["image_new"] and (root / row["image_new"]).is_file()
        )
        row["bbox_old_present"] = bool(row["bbox_old"])
        row["bbox_new_present"] = bool(row["bbox_new"])
        row["evidence_ready"] = all((
            row["image_old_exists"], row["image_new_exists"],
            row["bbox_old_present"], row["bbox_new_present"],
        ))

    output_dir.mkdir(parents=True)
    jsonl_path = output_dir / "visualdiff_description_remediation_queue.jsonl"
    csv_path = output_dir / "visualdiff_description_remediation_queue.csv"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    write_csv(csv_path, rows)

    after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global",
        "status": "OPEN" if rows else "PASS",
        "total_active_visualdiff_rows": len(read_jsonl(pairs_path)),
        "nonfinal_rows": len(rows),
        "release_final_rows": len(read_jsonl(pairs_path)) - len(rows),
        "by_reason": dict(sorted(Counter(row["reason"] for row in rows).items())),
        "by_split": dict(sorted(Counter(row["split"] for row in rows).items())),
        "by_lane": dict(sorted(Counter(row["machine_lane"] for row in rows).items())),
        "human_requirement": dict(
            sorted(Counter(row["human_required"] for row in rows).items())
        ),
        "evidence_ready_rows": sum(row["evidence_ready"] for row in rows),
        "missing_evidence_rows": sum(not row["evidence_ready"] for row in rows),
        "queue_jsonl": jsonl_path.relative_to(root).as_posix(),
        "queue_csv": csv_path.relative_to(root).as_posix(),
        "queue_jsonl_sha256": file_hash(jsonl_path),
        "queue_csv_sha256": file_hash(csv_path),
        "active_hashes_before": before,
        "active_hashes_after": after,
        "active_gold_modified": before != after,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "limitation": (
            "This queue classifies machine-detectable description debt. It does not "
            "certify semantic correctness or authorize Gold promotion."
        ),
    }
    if report["active_gold_modified"]:
        report["status"] = "FAIL"
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# VisualDiff Description Remediation Queue\n\n"
        "Gold v2.0 Global machine-side work queue. Process rows in priority order. "
        "Rows stay outside promotion until repaired and validated. The CSV is for "
        "triage; the JSONL preserves structured boxes and metadata.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_queue(root, root / args.output_dir)
    print(json.dumps({
        key: report[key]
        for key in (
            "status", "total_active_visualdiff_rows", "release_final_rows",
            "nonfinal_rows", "by_reason", "by_split", "by_lane",
            "human_requirement", "evidence_ready_rows", "missing_evidence_rows",
            "active_gold_modified",
        )
    }, indent=2))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
