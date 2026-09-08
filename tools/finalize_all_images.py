#!/usr/bin/env python3
"""
Finalize all active benchmark page images into the canonical images/ layout.

The unified dataset points to images/{doc_id}__{version_id}/page_XXXX.png.
Microtext imports often begin life under derived/pages_300dpi/{doc_id}, so this
tool copies any required active page renders into the public image tree without
removing or rewriting existing files.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from unify_dataset import canonical_image_relpath, image_candidates, load_jsonl


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def required_visualdiff_images(root: Path) -> Iterable[dict[str, Any]]:
    pairs_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    for pair in load_jsonl(pairs_path):
        doc_id = pair.get("doc_id", "")
        pair_id = pair.get("pair_id", "")
        old_page = pair.get("page_index_old", 0)
        new_page = pair.get("page_index_new", old_page)
        yield {
            "task": "visualdiff",
            "row_id": pair_id,
            "doc_id": doc_id,
            "version_id": pair.get("version_id_old", ""),
            "page_index": old_page,
            "side": "old",
            "source_image_path": pair.get("image_old", ""),
        }
        yield {
            "task": "visualdiff",
            "row_id": pair_id,
            "doc_id": doc_id,
            "version_id": pair.get("version_id_new", ""),
            "page_index": new_page,
            "side": "new",
            "source_image_path": pair.get("image_new", ""),
        }


def required_microtext_images(root: Path) -> Iterable[dict[str, Any]]:
    items_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    for item in load_jsonl(items_path):
        yield {
            "task": "microtext",
            "row_id": item.get("item_id", ""),
            "doc_id": item.get("doc_id", ""),
            "version_id": item.get("version_id", "unknown"),
            "page_index": item.get("page_index", 0),
        }


def required_images(root: Path) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, int]] = set()
    required: list[dict[str, Any]] = []
    for row in list(required_visualdiff_images(root)) + list(required_microtext_images(root)):
        key = (str(row["doc_id"]), str(row["version_id"] or "unknown"), int(row["page_index"] or 0))
        if key in seen:
            continue
        seen.add(key)
        required.append(row)
    return required


def source_candidates(root: Path, row: dict[str, Any]) -> list[Path]:
    explicit = str(row.get("source_image_path") or "").strip()
    candidates = image_candidates(
        root,
        row.get("doc_id", ""),
        row.get("version_id", "unknown"),
        row.get("page_index", 0),
    )
    result = candidates[1:] + candidates[:1]
    if explicit:
        explicit_path = Path(explicit)
        result.insert(0, explicit_path if explicit_path.is_absolute() else root / explicit_path)
    return result


def finalize_images(root: Path, report_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    stats: dict[str, Any] = {
        "required": 0,
        "existing": 0,
        "copied": 0,
        "missing": 0,
        "missing_rows": [],
    }

    for row in required_images(root):
        stats["required"] += 1
        canonical = root / canonical_image_relpath(
            row.get("doc_id", ""),
            row.get("version_id", "unknown"),
            row.get("page_index", 0),
        )
        if canonical.exists():
            stats["existing"] += 1
            continue

        source = next((candidate for candidate in source_candidates(root, row) if candidate.exists()), None)
        if source is None:
            stats["missing"] += 1
            stats["missing_rows"].append(
                {
                    "task": row.get("task"),
                    "row_id": row.get("row_id"),
                    "doc_id": row.get("doc_id"),
                    "version_id": row.get("version_id", "unknown"),
                    "page_index": row.get("page_index", 0),
                    "expected": canonical.relative_to(root).as_posix(),
                }
            )
            continue

        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, canonical)
        stats["copied"] += 1

    if report_path is not None:
        write_json(report_path, stats)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize active Eng_Bench images into images/.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--report",
        default="results/health/image_finalization_report.json",
        help="JSON report path relative to root",
    )
    args = parser.parse_args()

    root = Path(args.root)
    report_path = root / args.report if args.report else None
    stats = finalize_images(root, report_path=report_path)

    print("[*] Finalized active image assets")
    print(f"    Required: {stats['required']}")
    print(f"    Existing: {stats['existing']}")
    print(f"    Copied:   {stats['copied']}")
    print(f"    Missing:  {stats['missing']}")
    if report_path is not None:
        print(f"    Report:   {report_path}")

    return 1 if stats["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
