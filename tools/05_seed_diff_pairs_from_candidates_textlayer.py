#!/usr/bin/env python3
"""
05_seed_diff_pairs_from_candidates_textlayer.py

Generate Eng-Bench visualdiff seed annotations (diff_pairs.seed.jsonl)
from:
  - derived/align/<pair_id>/candidates_pXXXX.json (bbox candidates)
  - derived/textlayer/<doc_id>.jsonl             (vector text layer)

This follows the Eng-Bench plan: visual_diff/annotations/diff_pairs.jsonl
but produces a *seed* file to accelerate human labeling.

Outputs:
  visualdiff/annotations/diff_pairs.seed.jsonl (append/overwrite)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import difflib
from collections import defaultdict
from typing import Dict, List, Any, Tuple

from PIL import Image


def load_manifest(path: str) -> List[dict]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def load_textlayer_jsonl(path: str) -> Dict[int, List[dict]]:
    pages: Dict[int, List[dict]] = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            pages[int(r["page"])].append(r)

    for p in pages:
        pages[p].sort(key=lambda e: (e["bbox_px"][1], e["bbox_px"][0]))
    return pages


def texts_in_bbox(entries: List[dict], bbox_xyxy: List[int]) -> str:
    x1, y1, x2, y2 = bbox_xyxy
    hits = []
    for e in entries:
        bx1, by1, bx2, by2 = e["bbox_px"]
        cx = (bx1 + bx2) / 2.0
        cy = (by1 + by2) / 2.0
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            t = (e.get("text") or "").strip()
            if t:
                hits.append((cy, cx, t))
    hits.sort()
    return " ".join([h[2] for h in hits])


def ndiff_snippet(a: str, b: str, max_tokens: int = 40) -> str:
    if not a and not b:
        return ""
    diff = list(difflib.ndiff(a.split(), b.split()))
    kept = [d for d in diff if d.startswith(("+ ", "- "))]
    return " ".join(kept[:max_tokens])


def iter_candidate_files(align_dir: str) -> List[Tuple[int, str]]:
    """Enumerate candidate JSON files with robust filename matching."""
    # Matches: candidates_p0003.json, candidates_page_003.json, candidates_3.json
    CAND_RE = re.compile(r"^candidates_(?:page_|p)?(\d+)\.json$")
    
    files = []
    for p in sorted(os.listdir(align_dir)):
        m = CAND_RE.match(p)
        if m:
            page_idx = int(m.group(1))
            files.append((page_idx, os.path.join(align_dir, p)))
    return files


def resolve_page_png(pages_dir: str, page: int) -> str:
    """Find page PNG supporting multiple naming conventions."""
    from pathlib import Path
    pages_path = Path(pages_dir)
    
    # Support both naming conventions used across the repo
    candidates = [
        pages_path / f"p{page:04d}.png",        # p0000.png
        pages_path / f"page_{page:03d}.png",    # page_000.png
        pages_path / f"page_{page:04d}.png",    # page_0000.png
        pages_path / f"{page}.png",             # fallback
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    
    # Last-resort glob if naming drifted
    hits = sorted(pages_path.glob(f"*{page}*.png"))
    if hits:
        return str(hits[0])
    
    raise FileNotFoundError(f"Could not find page PNG for page={page} in {pages_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Eng_Bench root folder")
    ap.add_argument("--pair_id", required=True)
    ap.add_argument("--out_jsonl", default="visualdiff/annotations/diff_pairs.seed.jsonl")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--min_area", type=int, default=500)
    ap.add_argument("--max_area_ratio", type=float, default=0.06, help="drop boxes larger than this ratio of page area")
    ap.add_argument("--max_per_page", type=int, default=60)
    ap.add_argument("--drop_border_margin", type=int, default=8)
    args = ap.parse_args()

    manifest = load_manifest(os.path.join(args.root, "manifest.jsonl"))
    pairs = [m for m in manifest if m.get("type") == "pair" and m.get("pair_id") == args.pair_id]
    if not pairs:
        raise SystemExit(f"pair_id not found in manifest: {args.pair_id}")
    pair = pairs[0]
    docA = pair["from_doc_id"]
    docB = pair["to_doc_id"]

    align_dir = os.path.join(args.root, "derived", "align", args.pair_id)
    if not os.path.isdir(align_dir):
        raise SystemExit(f"missing align dir: {align_dir}")

    tlA = load_textlayer_jsonl(os.path.join(args.root, "derived", "textlayer", f"{docA}.jsonl"))
    tlB = load_textlayer_jsonl(os.path.join(args.root, "derived", "textlayer", f"{docB}.jsonl"))

    out_path = os.path.join(args.root, args.out_jsonl)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if args.overwrite:
        mode = "w"
    else:
        mode = "a"

    with open(out_path, mode, encoding="utf-8") as out:
        for page, cf in iter_candidate_files(align_dir):
            pages_dir = os.path.join(args.root, "derived", "pages_300dpi", docA)
            try:
                img_path = resolve_page_png(pages_dir, page)
            except FileNotFoundError:
                continue
            w, h = Image.open(img_path).size
            page_area = w * h

            data = json.load(open(cf, "r", encoding="utf-8"))
            # Support both 'boxes' (legacy) and 'candidates' (current) keys
            raw_list = data.get("boxes", []) or data.get("candidates", [])
            boxes = [b["bbox"] for b in raw_list]

            filtered = []
            for bb in boxes:
                x1, y1, x2, y2 = bb
                area = max(0, x2 - x1) * max(0, y2 - y1)
                if area < args.min_area:
                    continue
                if area > args.max_area_ratio * page_area:
                    continue
                m = args.drop_border_margin
                if x1 <= m or y1 <= m or x2 >= w - m or y2 >= h - m:
                    continue
                filtered.append(bb)

            filtered.sort(key=lambda b: (b[2]-b[0])*(b[3]-b[1]), reverse=True)
            filtered = filtered[: args.max_per_page]

            for j, bb in enumerate(filtered):
                from_text = texts_in_bbox(tlA.get(page, []), bb)
                to_text = texts_in_bbox(tlB.get(page, []), bb)

                if from_text == to_text and not from_text:
                    auto_type = "SYMBOL_CHANGE"
                elif from_text and to_text and from_text != to_text:
                    auto_type = "TEXT_EDIT"
                elif (not from_text) and to_text:
                    auto_type = "ADD_TEXT"
                elif from_text and (not to_text):
                    auto_type = "REMOVE_TEXT"
                else:
                    auto_type = "UNKNOWN"

                change_id = f"{args.pair_id}__p{page:04d}__{j:03d}"
                rec = {
                    "pair_id": args.pair_id,
                    "page": page,
                    "change_id": change_id,
                    "bbox_xyxy": bb,
                    "units": "px@300dpi",
                    "auto_type": auto_type,
                    "from_text": from_text,
                    "to_text": to_text,
                    "text_delta": ndiff_snippet(from_text, to_text),
                    "source": "auto_seed_candidates+textlayer",
                    "confidence": 0.35,
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Wrote seeds to: {out_path}")


if __name__ == "__main__":
    main()
