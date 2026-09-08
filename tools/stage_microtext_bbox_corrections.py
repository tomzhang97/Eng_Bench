#!/usr/bin/env python3
"""Stage exact-box repairs without changing human votes or treating new boxes as reviewed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

try:
    from . import preview_reviewed_gold_promotion as preview
except ImportError:
    import preview_reviewed_gold_promotion as preview


def corrected_row(row: dict, correction: dict, page_size: tuple[int, int], page_sha256: str) -> dict:
    before = row.get("bbox")
    if before != correction["original_bbox"]:
        raise ValueError("original bbox changed since visual inspection")
    after = correction["bbox"]
    if (not isinstance(after, list) or len(after) != 4
            or any(type(value) is not int for value in after)
            or not (0 <= after[0] < after[2] <= page_size[0] and 0 <= after[1] < after[3] <= page_size[1])):
        raise ValueError("invalid corrected bbox")
    if not correction.get("reason") or before == after:
        raise ValueError("correction requires changed coordinates and a reason")
    updated = {**row, "bbox": after, "crop_path": "", "safe_to_merge_gold": False,
               "prior_human_review": {key: row[key] for key in (
                   "review_status", "human_review_status", "human_completion_workbook_sha256",
                   "human_completion_source", "human_completion_date_label", "independent_audit_support",
               ) if key in row},
               "machine_bbox_repair": {
                   "original_bbox": before, "bbox": after, "reason": correction["reason"],
                   "image_path": row["image_path"], "image_sha256": page_sha256,
                   "semantics_changed": False, "new_human_vote": False,
               },
               "review_status": "needs_machine_bbox_reconciliation",
               "human_review_status": "needs_machine_bbox_reconciliation",
               "promotion_state": "held_for_machine_bbox_reconciliation"}
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), (args.root / args.output_dir).resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    before = preview.active_hashes(root)
    input_path, corrections_path = root / args.input, root / args.corrections
    config = json.loads(corrections_path.read_text(encoding="utf-8"))
    if preview.file_sha256(input_path) != config["input_sha256"]:
        raise ValueError("reviewed input changed since correction selection")
    rows = preview.read_jsonl(input_path)
    mapping = {row["candidate_id"]: row for row in config["corrections"]}
    if len(mapping) != len(config["corrections"]) or set(mapping) - {row["candidate_id"] for row in rows}:
        raise ValueError("duplicate or unknown correction candidate")
    proposals, unchanged, evidence = [], [], {}
    for row in rows:
        if row["candidate_id"] not in mapping:
            unchanged.append(row)
            continue
        image_path = root / row["image_path"]
        with Image.open(image_path) as image:
            size = image.size
        digest = preview.file_sha256(image_path)
        evidence[row["image_path"]] = digest
        proposals.append(corrected_row(row, mapping[row["candidate_id"]], size, digest))
    if before != preview.active_hashes(root):
        raise ValueError("active release changed during staging")
    output.mkdir(parents=True)
    preview.write_jsonl(output / "bbox_repair_proposals.jsonl", proposals)
    preview.write_jsonl(output / "unchanged_reviewed.jsonl", unchanged)
    report = {"goal": "Gold v2.0 Global", "status": "PENDING_MACHINE_BBOX_RECONCILIATION",
              "proposals": len(proposals), "unchanged": len(unchanged),
              "original_review_input": args.input.as_posix(), "original_review_input_sha256": config["input_sha256"],
              "corrections_sha256": preview.file_sha256(corrections_path), "source_image_hashes": evidence,
              "original_human_votes_modified": False, "active_gold_modified": False, "safe_to_merge_gold": False,
              "output_hashes": {name: preview.file_sha256(output / name) for name in (
                  "bbox_repair_proposals.jsonl", "unchanged_reviewed.jsonl")}}
    preview.write_json(output / "report.json", report)
    print(json.dumps({key: report[key] for key in ("status", "proposals", "unchanged")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
