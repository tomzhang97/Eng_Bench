#!/usr/bin/env python3
"""Mine textlayer-based hard negatives for Eng_Bench VisualDiff.

Goal: Add YES/NO hard negatives that satisfy Validator Check 4:
- answer.yes == False
- entity appears on the same page in BOTH versions (A and B) according to textlayer
- evidence bboxes exist (we harvest bboxes from textlayer entries)

It assumes the same directory layout as validate_engbench_v2.py:
  <root>/manifest.jsonl
  <root>/derived/textlayer/<doc_id>.jsonl

Usage:
  python mine_hard_negs_textlayer.py \
    --root /path/to/Eng_Bench \
    --input engbench_v1_visualdiff_stage1.jsonl \
    --output engbench_v1_visualdiff_stage2.jsonl \
    --split train --num 35 --seed 13

Notes:
- We prefer entity tokens containing BOTH letters and digits (e.g., R47, X7, BAT1, FIC-101).
- We avoid entities with freq>3 on the page to prevent Check 6 (ambiguous target) unless you later add anchors.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def load_jsonl(path: str) -> List[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_jsonl(path: str, items: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def load_manifest(path: str) -> Dict[str, dict]:
    items = {}
    if not os.path.exists(path):
        return items
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("type") == "pair":
                items[r.get("pair_id")] = r
    return items


def load_textlayer(root: str, doc_id: str) -> Dict[int, List[dict]]:
    pages: Dict[int, List[dict]] = defaultdict(list)
    tl_path = Path(root) / "derived" / "textlayer" / f"{doc_id}.jsonl"
    if not tl_path.exists():
        return pages
    with open(tl_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            pages[int(r.get("page", 0))].append(r)
    return pages


def count_entity_on_page(textlayer: Dict[int, List[dict]], page: int, entity: str) -> int:
    entity_lower = entity.lower()
    c = 0
    for entry in textlayer.get(page, []):
        text = (entry.get("text") or "").lower()
        if entity_lower in text:
            c += 1
    return c


def first_bbox_for_entity(textlayer: Dict[int, List[dict]], page: int, entity: str) -> Optional[List[float]]:
    entity_lower = entity.lower()
    for entry in textlayer.get(page, []):
        text = (entry.get("text") or "").lower()
        if entity_lower in text:
            bbox = entry.get("bbox") or entry.get("box") or entry.get("bbox_px")
            # Accept [x1,y1,x2,y2]
            if isinstance(bbox, list) and len(bbox) == 4:
                return bbox
    return None


def extract_version_ids(evidence: List[dict]) -> Tuple[str, str]:
    """Return (versionA, versionB) from evidence if present, else ('A','B')."""
    vA, vB = "A", "B"
    for ev in evidence or []:
        if ev.get("doc_side") == "A" and ev.get("version_id"):
            vA = ev["version_id"]
        if ev.get("doc_side") == "B" and ev.get("version_id"):
            vB = ev["version_id"]
    return vA, vB


def extract_doc_name(pair_id: str) -> str:
    # pair_id often looks like vdiff__viola__pcbV1.0__to__pcbV1.1
    parts = pair_id.split("__")
    if len(parts) >= 2:
        return parts[1].capitalize()
    return "Document"


def candidate_tokens_from_textlayer(page_entries: List[dict]) -> List[str]:
    """Harvest plausible entity tokens from textlayer.

    We focus on alnum tokens with optional -_/., and require at least one letter and one digit.
    """
    toks = set()
    for entry in page_entries:
        text = entry.get("text") or ""
        for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_/\.]{1,24}", text):
            tl = t.lower()
            if tl.isdigit():
                continue
            # require mixed letter+digit to reduce garbage
            if not (re.search(r"[A-Za-z]", t) and re.search(r"\d", t)):
                continue
            if len(t) < 2:
                continue
            toks.add(t.strip())
    return list(toks)


def safe_qid(existing: set, base: str) -> str:
    q = base
    k = 0
    while q in existing:
        k += 1
        q = f"{base}_{k}"
    existing.add(q)
    return q



def get_page_dims(root: str, doc_id: str, page: int) -> Tuple[int, int]:
    """Return (width, height) of the rendered page image."""
    from PIL import Image
    base = Path(root) / "derived" / "pages_300dpi" / doc_id
    # Try different naming conventions
    candidates = [f"p{page:04d}.png", f"page_{page:03d}.png", f"page_{page:04d}.png"]
    for pat in candidates:
        p = base / pat
        if p.exists():
            return Image.open(p).size
    # Fallback default if not found (shouldn't happen)
    return (2481, 3508)

def rotate_bbox(bbox: List[float], page_w: int, page_h: int) -> List[float]:
    """Rotate bbox 90 degrees CW.
    Original (unrotated) coordinates: x, y
    Image is rotated 90 CW, so:
       W_new = H_old
       H_new = W_old
    Transform (assuming TL origin):
       x_new = H_old - y_old
       y_new = x_old
       x_new = W_new - y_old
    """
    x1, y1, x2, y2 = bbox
    # W_new = page_w, H_new = page_h
    # Transform all points
    nx1 = page_w - y1
    ny1 = x1
    nx2 = page_w - y2
    ny2 = x2
    
    # Re-normalize (min, min, max, max)
    return [
        min(nx1, nx2), min(ny1, ny2),
        max(nx1, nx2), max(ny1, ny2)
    ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Eng_Bench root")
    ap.add_argument("--input", required=True, help="Input JSONL")
    ap.add_argument("--output", required=True, help="Output JSONL")
    ap.add_argument("--split", default="train", help="Which split to add hard negatives to")
    ap.add_argument("--num", type=int, default=35, help="How many hard negatives to add")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    random.seed(args.seed)

    items = load_jsonl(args.input)
    # Filter out old easy negatives (Check 4 failures)
    items = [it for it in items if not (it.get("qid") or "").startswith("neg_")]

    existing_qids = set((it.get("qid") or it.get("question_id")) for it in items)

    manifest = load_manifest(os.path.join(args.root, "manifest.jsonl"))
    if not manifest:
        raise SystemExit("manifest.jsonl not found or empty. Put it under --root.")

    # Preload textlayers for all docs referenced by split items
    doc_textlayers: Dict[str, Dict[int, List[dict]]] = {}

    def get_textlayer(doc_id: str) -> Dict[int, List[dict]]:
        if doc_id not in doc_textlayers:
            doc_textlayers[doc_id] = load_textlayer(args.root, doc_id)
        return doc_textlayers[doc_id]

    # Base pages to mine from: prefer YESNO positives in the target split
    base = [it for it in items if it.get("split") == args.split and it.get("question_type") == "yesno" and it.get("answer", {}).get("yes") is True]
    if not base:
        # Fallback: try test split positives if train has none
        print(f"[WARN] No yesno positives in {args.split}, checking test split for base pages...")
        base = [it for it in items if it.get("question_type") == "yesno" and it.get("answer", {}).get("yes") is True]
    
    if not base:
        raise SystemExit("No yesno positives found anywhere to mine from.")

    random.shuffle(base)

    new_items = []
    attempts = 0

    from PIL import Image # Ensure PIL is available

    for it in base:
        if len(new_items) >= args.num:
            break

        pair_id = it.get("pair_id")
        if not pair_id or pair_id not in manifest:
            continue

        page = it.get("answer", {}).get("page")
        if page is None:
            continue
        page = int(page)

        pair = manifest[pair_id]
        docA = pair.get("from_doc_id")
        docB = pair.get("to_doc_id")
        if not docA or not docB:
            continue
        
        # Check rotation requirement (doc-level)
        rotateA = manifest.get(docA, {}).get("render", {}).get("rotate_cw90", False)
        rotateB = manifest.get(docB, {}).get("render", {}).get("rotate_cw90", False)

        tlA = get_textlayer(docA)
        tlB = get_textlayer(docB)

        # Candidate entities from B page (could also union with A)
        candidates = candidate_tokens_from_textlayer(tlB.get(page, []))
        if not candidates:
            continue

        random.shuffle(candidates)

        vA, vB = extract_version_ids(it.get("evidence", []))
        doc_name = extract_doc_name(pair_id)

        # Get page dims for rotation if needed
        dimsA = get_page_dims(args.root, docA, page) if rotateA else (0,0)
        dimsB = get_page_dims(args.root, docB, page) if rotateB else (0,0)

        # Try a few candidates per base item
        picked = None
        for ent in candidates[:80]:
            # Check hard-neg presence in BOTH
            cA = count_entity_on_page(tlA, page, ent)
            cB = count_entity_on_page(tlB, page, ent)
            if cA == 0 or cB == 0:
                continue
            # Avoid ambiguous targets to satisfy Check 6 without anchors
            if cB > 3:
                continue
            # Need evidence bboxes for both sides
            bbA = first_bbox_for_entity(tlA, page, ent)
            bbB = first_bbox_for_entity(tlB, page, ent)
            if not bbA or not bbB:
                continue
            
            # Apply rotation if needed
            if rotateA:
                bbA = rotate_bbox(bbA, dimsA[0], dimsA[1])
            if rotateB:
                bbB = rotate_bbox(bbB, dimsB[0], dimsB[1])
                
            picked = (ent, bbA, bbB)
            break

        attempts += 1
        if not picked:
            continue

        ent, bbA, bbB = picked

        # Create the hard-negative item
        base_id = (it.get("qid") or it.get("question_id") or "q").replace("q_vdiff__", "")
        qid = safe_qid(existing_qids, f"q_vdiff_hn__{base_id}__p{page:04d}__{ent}")

        q = f"On page {page} of {doc_name}, did component {ent} change between {vA} and {vB}? Answer yes/no and cite the region."

        new_it = {
            "qid": qid,
            "pair_id": pair_id,
            # Keep change_id consistent with the base item when possible
            "change_id": it.get("change_id"),
            "question_type": "yesno",
            "question": q,
            "entity": ent,
            "answer": {"yes": False, "page": page},
            "evidence": [
                {"doc_side": "A", "version_id": vA, "page": page, "bbox": bbA, "note": "hard_negative_anchor"},
                {"doc_side": "B", "version_id": vB, "page": page, "bbox": bbB, "note": "hard_negative_anchor"},
            ],
            "coord_frame": it.get("coord_frame", "aligned"),
            "category": it.get("category", "Reasoning"),
            "type": it.get("type", "Visual Inspection"),
            "difficulty": "Hard",
            "split": args.split,
        }

        new_items.append(new_it)

    if len(new_items) < args.num:
        print(f"[WARN] Only mined {len(new_items)}/{args.num} hard negatives. Consider relaxing filters (freq>3) or mining from more pages.")

    out = items + new_items
    write_jsonl(args.output, out)

    print(f"[OK] Wrote {args.output}")
    print(f"      Removed old negatives: {len(load_jsonl(args.input)) - len(items)}")
    print(f"      Added hard negatives: {len(new_items)} to split={args.split}")


if __name__ == "__main__":
    main()
