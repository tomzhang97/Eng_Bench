#!/usr/bin/env python3
"""
08_microtext_seed_queries_from_textlayer.py

Generate Eng-Bench microtext seed annotations from vector text layer.

Two modes:
  - instrument_tags: regex extraction (e.g., TIC-101 / PCV-01)
  - tolerance_values: numeric extraction (table cells)

Outputs JSONL in microtext/annotations/*.jsonl

Note: This is a *seed* generator; you should review / downsample before training.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import defaultdict
from typing import Dict, List, Any

def load_textlayer(path: str) -> Dict[int, List[dict]]:
    pages = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                pages[int(r["page"])].append(r)
    return pages

def bbox_to_xyxy(b: List[float]) -> List[int]:
    return [int(round(b[0])), int(round(b[1])), int(round(b[2])), int(round(b[3]))]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--doc_id", required=True)
    ap.add_argument("--mode", choices=["instrument_tags", "tolerance_values"], required=True)
    ap.add_argument("--max_items", type=int, default=200)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--out_jsonl", required=True)
    ap.add_argument("--pages", default="all", help="all or 0-3 or 0,1,2")
    args = ap.parse_args()

    random.seed(args.seed)
    tl_path = os.path.join(args.root, "derived", "textlayer", f"{args.doc_id}.jsonl")
    pages = load_textlayer(tl_path)

    # page selection
    if args.pages == "all":
        sel_pages = sorted(pages.keys())
    elif "-" in args.pages:
        a,b = args.pages.split("-",1)
        sel_pages = list(range(int(a), int(b)+1))
    else:
        sel_pages = [int(x) for x in args.pages.split(",") if x.strip()]

    items = []

    if args.mode == "instrument_tags":
        # conservative: 2-4 letters + '-' + 1-4 digits + optional letters
        pat = re.compile(r"\b[A-Z]{2,4}-\d{1,4}[A-Z]?\b")
        for p in sel_pages:
            for e in pages.get(p, []):
                t = (e.get("text") or "").strip()
                if not t:
                    continue
                if pat.fullmatch(t):
                    items.append({
                        "doc_id": args.doc_id,
                        "page": p,
                        "query": f"Locate tag {t}",
                        "target_text": t,
                        "bbox_xyxy": bbox_to_xyxy(e["bbox_px"]),
                        "units": "px@300dpi",
                        "hard": False,
                        "source": "auto_seed_textlayer"
                    })

    elif args.mode == "tolerance_values":
        # Enhanced patterns for tolerance values: ranges, ±, +/-, inequalities, standalone numbers
        NUM = r"[+-]?\d+(?:\.\d+)?"
        RANGE = rf"{NUM}\s*[-–—]\s*{NUM}"          # 0.1-0.3, 0.1 – 0.3
        PLUSMINUS = rf"[±\u00B1]\s*{NUM}"          # ±0.05
        PM_SLASH = rf"\+{NUM}\s*/\s*-{NUM}"        # +0.10/-0.05
        INEQ = rf"[<>]=?\s*{NUM}"                  # <=0.1, >0.2
        
        # Combined pattern - try complex patterns first, then simple numbers
        TOL_PAT = re.compile(rf"(?:{PM_SLASH}|{PLUSMINUS}|{RANGE}|{INEQ}|{NUM})")
        
        for p in sel_pages:
            for e in pages.get(p, []):
                t = (e.get("text") or "").strip()
                if not t:
                    continue
                # Use search instead of fullmatch to find patterns within text
                m = TOL_PAT.search(t)
                if m:
                    items.append({
                        "doc_id": args.doc_id,
                        "page": p,
                        "target_text": t,
                        "bbox_xyxy": bbox_to_xyxy(e["bbox_px"]),
                        "units": "px@300dpi",
                        "source": "auto_seed_textlayer"
                    })

    random.shuffle(items)
    items = items[: args.max_items]

    out_path = os.path.join(args.root, args.out_jsonl)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in items:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote {len(items)} seed items -> {out_path}")

if __name__ == "__main__":
    main()
