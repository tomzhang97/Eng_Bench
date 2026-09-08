#!/usr/bin/env python3
"""Create missing CSV checklist scaffolds for existing review packs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.microtext_review_checklist import export_rows as export_microtext_rows
from tools.microtext_review_checklist import write_csv as write_microtext_csv
from tools.export_review_packs import write_microtext_index
from tools.export_review_packs import write_visualdiff_index
from tools.visualdiff_review_checklist import export_rows as export_visualdiff_rows
from tools.visualdiff_review_checklist import write_csv as write_visualdiff_csv


def root_path(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def infer_kind(rows: list[dict[str, Any]]) -> str:
    fields: set[str] = set()
    for row in rows[:10]:
        fields.update(row)
    if "pair_id" in fields or "old_crop_path" in fields or "new_crop_path" in fields:
        return "visualdiff"
    if "candidate_id" in fields or "proposed_text" in fields or "target_text" in fields:
        return "microtext"
    return "unknown"


def pack_dirs(review_pack_root: Path) -> list[Path]:
    if not review_pack_root.exists():
        return []
    return sorted(path for path in review_pack_root.iterdir() if path.is_dir() and (path / "manifest.jsonl").exists())


def checklist_path_for(pack_dir: Path) -> Path:
    return pack_dir / f"{pack_dir.name}_validation_checklist.csv"


def repair_missing_checklists(
    *,
    root: Path,
    review_pack_root: Path,
    dry_run: bool = False,
    create_indexes: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    review_pack_root = root_path(root, review_pack_root)
    summary: dict[str, Any] = {
        "review_pack_root": str(review_pack_root.relative_to(root) if review_pack_root.is_relative_to(root) else review_pack_root),
        "packs_seen": 0,
        "created": 0,
        "created_indexes": 0,
        "dry_run_created": 0,
        "dry_run_created_indexes": 0,
        "skipped_existing": 0,
        "skipped_existing_index": 0,
        "skipped_unknown_kind": 0,
        "skipped_empty_manifest": 0,
        "created_files": [],
        "created_index_files": [],
        "skipped": [],
    }

    for pack_dir in pack_dirs(review_pack_root):
        summary["packs_seen"] += 1
        rows = load_jsonl(pack_dir / "manifest.jsonl")
        if not rows:
            summary["skipped_empty_manifest"] += 1
            summary["skipped"].append({"pack": pack_dir.name, "reason": "empty_manifest"})
            continue

        kind = infer_kind(rows)
        if kind == "unknown":
            summary["skipped_unknown_kind"] += 1
            summary["skipped"].append({"pack": pack_dir.name, "reason": "unknown_kind"})
            continue

        existing_csvs = sorted(pack_dir.glob("*.csv"))
        if existing_csvs:
            summary["skipped_existing"] += 1
            summary["skipped"].append(
                {
                    "pack": pack_dir.name,
                    "reason": "existing_csv",
                    "files": [path.name for path in existing_csvs],
                }
            )
        else:
            output_path = checklist_path_for(pack_dir)
            if kind == "microtext":
                checklist_rows = export_microtext_rows(rows)
                writer = write_microtext_csv
            else:
                checklist_rows = export_visualdiff_rows(rows)
                writer = write_visualdiff_csv

            rel_output = str(output_path.relative_to(root) if output_path.is_relative_to(root) else output_path)
            if dry_run:
                summary["dry_run_created"] += 1
            else:
                writer(output_path, checklist_rows)
                summary["created"] += 1
            summary["created_files"].append(
                {
                    "pack": pack_dir.name,
                    "kind": kind,
                    "rows": len(checklist_rows),
                    "path": rel_output.replace("\\", "/"),
                    "dry_run": dry_run,
                }
            )

        index_path = pack_dir / "index.html"
        if not create_indexes:
            continue
        if index_path.exists():
            summary["skipped_existing_index"] += 1
            continue
        rel_index = str(index_path.relative_to(root) if index_path.is_relative_to(root) else index_path)
        if dry_run:
            summary["dry_run_created_indexes"] += 1
        else:
            if kind == "microtext":
                write_microtext_index(pack_dir, rows)
            else:
                write_visualdiff_index(pack_dir, rows)
            summary["created_indexes"] += 1
        summary["created_index_files"].append(
            {
                "pack": pack_dir.name,
                "kind": kind,
                "rows": len(rows),
                "path": rel_index.replace("\\", "/"),
                "dry_run": dry_run,
            }
        )

    return summary


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--review-pack-root", default="derived/review_packs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-indexes", action="store_true", help="Also create missing index.html files")
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    summary = repair_missing_checklists(
        root=root,
        review_pack_root=Path(args.review_pack_root),
        dry_run=args.dry_run,
        create_indexes=args.create_indexes,
    )
    if args.output_json:
        write_summary(root_path(root.resolve(), Path(args.output_json)), summary)
        print(f"[OK] Wrote {args.output_json}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
