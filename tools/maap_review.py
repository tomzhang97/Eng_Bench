#!/usr/bin/env python3
"""Review and accept MAAP draft annotations."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
    return items


def save_jsonl(items: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def get_image_paths(pair: dict[str, Any], root: Path) -> tuple[Path, Path]:
    images_dir = root / "images"
    doc_id = pair.get("doc_id", "")
    v_old = pair.get("version_id_old", "")
    v_new = pair.get("version_id_new", "")
    page_old = int(pair.get("page_index_old", 0))
    page_new = int(pair.get("page_index_new", 0))
    return (
        images_dir / f"{doc_id}__{v_old}" / f"page_{page_old:04d}.png",
        images_dir / f"{doc_id}__{v_new}" / f"page_{page_new:04d}.png",
    )


def textlayer_candidates(root: Path, pair: dict[str, Any], version_id: str) -> list[Path]:
    doc_id = str(pair.get("doc_id", ""))
    names = [f"{doc_id}__{version_id}.jsonl", f"{doc_id}_{version_id}.jsonl"]
    if doc_id == "viola" and str(version_id).startswith("pcbV"):
        names.append(f"toradex__viola__datasheet__{version_id}.jsonl")
        names.append(f"toradex_viola_v{str(version_id).removeprefix('pcbV')}.jsonl")
    if doc_id == "bbb":
        names.append(f"bbb_schematic_rev{version_id}.jsonl")
    return [root / "derived" / "textlayer" / name for name in names]


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def nearby_text(
    root: Path,
    pair: dict[str, Any],
    version_id: str,
    page_index: int,
    bbox: list[float],
    limit: int = 6,
) -> list[str]:
    if not bbox:
        return []
    cx, cy = bbox_center(bbox)
    matches: list[tuple[float, str]] = []
    for path in textlayer_candidates(root, pair, version_id):
        if not path.exists():
            continue
        for row in load_jsonl(path):
            if int(row.get("page", -1)) != int(page_index):
                continue
            text_bbox = row.get("bbox_px") or row.get("bbox")
            text = (row.get("text") or "").strip()
            if not text or not text_bbox:
                continue
            tx, ty = bbox_center(text_bbox)
            distance = math.hypot(tx - cx, ty - cy)
            if distance <= 800:
                matches.append((distance, text))
        break
    matches.sort(key=lambda item: item[0])
    return [text for _, text in matches[:limit]]


def select_review_items(
    drafts: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    split: str | None = None,
    bucket: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None and limit <= 0:
        return []
    reviewed_ids = {row["pair_id"] for row in reviewed}
    selected: list[dict[str, Any]] = []
    for draft in drafts:
        if draft.get("pair_id") in reviewed_ids:
            continue
        if split and draft.get("split") != split:
            continue
        if bucket and draft.get("review_bucket") != bucket:
            continue
        selected.append(draft)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def print_pair(pair: dict[str, Any], idx: int, total: int, root: Path) -> None:
    print("\n" + "=" * 60)
    print(f"[{idx + 1}/{total}] {pair['pair_id']}")
    print("=" * 60)
    print(f"Document: {pair.get('doc_id', 'N/A')}")
    print(f"Versions: {pair.get('version_id_old', '?')} -> {pair.get('version_id_new', '?')}")
    print(f"Split: {pair.get('split', 'N/A')} | Bucket: {pair.get('review_bucket', 'N/A')}")
    print(f"Page: {pair.get('page_index_old', 0)}")
    print(f"Change Type: {pair.get('change_type', [])}")
    print(f"BBox Old: {pair.get('bbox_old', [])}")
    print(f"BBox New: {pair.get('bbox_new', [])}")
    img_old, img_new = get_image_paths(pair, root)
    print(f"Image Old: {img_old}")
    print(f"Image New: {img_new}")

    old_text = nearby_text(
        root,
        pair,
        str(pair.get("version_id_old", "")),
        int(pair.get("page_index_old", 0)),
        pair.get("bbox_old", []),
    )
    new_text = nearby_text(
        root,
        pair,
        str(pair.get("version_id_new", "")),
        int(pair.get("page_index_new", 0)),
        pair.get("bbox_new", []),
    )
    if old_text or new_text:
        print("-" * 60)
        print("NEARBY TEXT:")
        if old_text:
            print(f"  old: {' | '.join(old_text)}")
        if new_text:
            print(f"  new: {' | '.join(new_text)}")
    print("-" * 60)
    print("DRAFT ANNOTATION:")
    print(f"  {pair.get('change_desc_gt', 'N/A')}")
    print("-" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review and accept MAAP draft annotations")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--input",
        default="visualdiff/annotations/visualdiff_pairs_DRAFT.jsonl",
        help="Draft or review-queue JSONL",
    )
    parser.add_argument(
        "--output",
        default="visualdiff/annotations/visualdiff_pairs_REVIEWED.jsonl",
        help="Reviewed output JSONL",
    )
    parser.add_argument("--split", choices=["train", "dev", "test"], default=None)
    parser.add_argument("--bucket", default=None, help="Filter by review_bucket")
    parser.add_argument("--limit", type=int, default=None, help="Maximum rows to review")
    args = parser.parse_args()

    root = Path(args.root)
    draft_path = root / args.input
    reviewed_path = root / args.output

    print("[*] MAAP Review Interface")
    print(f"    Input file: {draft_path}")
    print(f"    Output: {reviewed_path}")

    if not draft_path.exists():
        print(f"\nERROR: draft file not found: {draft_path}")
        print("Run maap_annotate.py first or pass --input visualdiff/annotations/visualdiff_review_queue.jsonl.")
        return 1

    drafts = load_jsonl(draft_path)
    reviewed = load_jsonl(reviewed_path)
    to_review = select_review_items(
        drafts,
        reviewed,
        split=args.split,
        bucket=args.bucket,
        limit=args.limit,
    )

    print(f"    Loaded: {len(drafts)}")
    print(f"    Already reviewed: {len({row['pair_id'] for row in reviewed})}")
    print(f"    Pending review: {len(to_review)}")

    if not to_review:
        print("\n[OK] All matching drafts have been reviewed.")
        return 0

    print("\nCommands: [a]ccept | [e]dit | [s]kip | [f]lag | [q]uit")
    print("=" * 60)

    accepted = 0
    edited = 0
    skipped = 0
    flagged = 0

    for i, pair in enumerate(to_review):
        print_pair(pair, i, len(to_review), root)

        while True:
            choice = input("\nAction [a/e/s/f/q]: ").strip().lower()

            if choice == "a":
                pair["annotation_status"] = "accepted"
                pair.setdefault("desc_source", "human_from_llm_draft")
                pair["reviewed_at"] = datetime.now().isoformat()
                reviewed.append(pair)
                save_jsonl(reviewed, reviewed_path)
                accepted += 1
                print("  [OK] Accepted")
                break

            if choice == "e":
                print("\nEnter corrected description (or press Enter to cancel):")
                new_desc = input("> ").strip()
                if new_desc:
                    pair["change_desc_gt"] = new_desc
                    pair["annotation_status"] = "edited"
                    pair["desc_source"] = "human"
                    pair["reviewed_at"] = datetime.now().isoformat()
                    reviewed.append(pair)
                    save_jsonl(reviewed, reviewed_path)
                    edited += 1
                    print("  [OK] Edited and saved")
                    break
                print("  Cancelled edit")
                continue

            if choice == "s":
                skipped += 1
                print("  [SKIP] Skipped")
                break

            if choice == "f":
                pair["annotation_status"] = "flagged"
                pair["reviewed_at"] = datetime.now().isoformat()
                reason = input("Reason for flagging: ").strip()
                pair["flag_reason"] = reason
                reviewed.append(pair)
                save_jsonl(reviewed, reviewed_path)
                flagged += 1
                print("  [FLAG] Flagged")
                break

            if choice == "q":
                print("\n" + "=" * 60)
                print("SESSION SUMMARY")
                print("=" * 60)
                print(f"Accepted: {accepted}")
                print(f"Edited:   {edited}")
                print(f"Skipped:  {skipped}")
                print(f"Flagged:  {flagged}")
                print(f"Remaining: {len(to_review) - i}")
                print("=" * 60)
                return 0

            print("  Invalid. Use: a/e/s/f/q")

    print("\n" + "=" * 60)
    print("REVIEW COMPLETE")
    print("=" * 60)
    print(f"Accepted: {accepted}")
    print(f"Edited:   {edited}")
    print(f"Skipped:  {skipped}")
    print(f"Flagged:  {flagged}")
    print(f"Total reviewed: {len(reviewed)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
