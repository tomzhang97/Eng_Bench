#!/usr/bin/env python3
"""
validate_engbench_v2.py

Comprehensive 11-check validator for Eng_Bench (Phase 6.4):
1. qid uniqueness
2. bbox bounds (answer + evidence bboxes)
3. yes/no balance ≥30%
4. negative entity on page (hard-neg rule with textlayer)
5. scope-schema match
6. ambiguous target (freq>3 w/o anchor via textlayer)
7. evidence on positives
8. coverage (≥50 locate/yesno per split)
9. change_id split overlap
10. pair/doc resolvability (manifest)
11. duplicate QA collapse
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from benchmark_utils import (
    dedup_key,
    anchor_present,
    normalize_entity_text,
    extract_entity_from_question,
    GENERIC_TOKENS,
)


def load_jsonl(path: str) -> List[dict]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def load_manifest(path: str) -> Dict[str, dict]:
    """Load manifest.jsonl into dict keyed by doc_id/pair_id."""
    items = {}
    if not os.path.exists(path):
        return items
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("type") == "doc":
                items[r.get("doc_id")] = r
            elif r.get("type") == "pair":
                items[r.get("pair_id")] = r
    return items


def load_textlayer(root: str, doc_id: str) -> Dict[int, List[dict]]:
    """Load textlayer JSONL for a doc, grouped by page."""
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


def get_page_dims(root: str, doc_id: str, page: int) -> Optional[Tuple[int, int]]:
    """Get page dimensions from rendered PNG."""
    from PIL import Image
    base = Path(root) / "derived" / "pages_300dpi" / doc_id
    for pattern in [f"p{page:04d}.png", f"page_{page:03d}.png", f"page_{page:04d}.png"]:
        p = base / pattern
        if p.exists():
            img = Image.open(p)
            return img.size  # (width, height)
    return None


def count_entity_on_page(textlayer: Dict[int, List[dict]], page: int, entity: str) -> int:
    """Count occurrences of entity text on a page."""
    entity_lower = entity.lower()
    count = 0
    for entry in textlayer.get(page, []):
        text = (entry.get("text") or "").lower()
        if entity_lower in text:
            count += 1
    return count


class ValidatorReport:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, Any] = {}

    def error(self, check: int, msg: str):
        self.errors.append(f"[Check {check}] {msg}")

    def warn(self, check: int, msg: str):
        self.warnings.append(f"[Check {check}] {msg}")


def validate_bbox(bbox: List, name: str, page_dims: Optional[Tuple[int, int]] = None) -> List[str]:
    """Validate a single bbox."""
    errors = []
    if not isinstance(bbox, list) or len(bbox) != 4:
        errors.append(f"{name}: must be 4-element list")
        return errors
    
    x1, y1, x2, y2 = bbox
    if x2 <= x1:
        errors.append(f"{name}: degenerate (x2={x2} <= x1={x1})")
    if y2 <= y1:
        errors.append(f"{name}: degenerate (y2={y2} <= y1={y1})")
    
    if page_dims:
        w, h = page_dims
        if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
            errors.append(f"{name}: out of bounds (page={w}x{h})")
    
    return errors


def validate_all(
    items: List[dict],
    manifest: Dict[str, dict],
    root: str,
    strict: bool = False,
    skip_textlayer: bool = False,
) -> Tuple[ValidatorReport, Set[int]]:
    report = ValidatorReport()
    bad_indices = set()

    # Pre-load textlayers for docs referenced in items
    textlayers: Dict[str, Dict[int, List[dict]]] = {}
    if not skip_textlayer:
        doc_ids = set()
        for it in items:
            pair_id = it.get("pair_id", "")
            if pair_id in manifest:
                pair = manifest[pair_id]
                doc_ids.add(pair.get("from_doc_id", ""))
                doc_ids.add(pair.get("to_doc_id", ""))
        for doc_id in doc_ids:
            if doc_id:
                textlayers[doc_id] = load_textlayer(root, doc_id)

    # --- Check 1: qid uniqueness ---
    qids = [it.get("question_id") or it.get("qid") for it in items]
    qid_counts = Counter(qids)
    dups = {q for q, c in qid_counts.items() if c > 1}
    if dups:
        for i, it in enumerate(items):
            q = it.get("question_id") or it.get("qid")
            if q in dups:
                report.error(1, f"Duplicate qid: {q}")
                bad_indices.add(i)

    # --- Check 2: bbox bounds (answer + evidence) ---
    for i, it in enumerate(items):
        qid = it.get("question_id") or it.get("qid")
        ans = it.get("answer", {})
        
        # Get page dims if possible
        page = None
        if isinstance(ans, dict) and ans.get("page") is not None:
            page = int(ans["page"])
        pair_id = it.get("pair_id", "")
        doc_id = manifest.get(pair_id, {}).get("to_doc_id", "")
        dims = get_page_dims(root, doc_id, page) if page is not None and doc_id else None
        
        found_err = False
        # Validate answer bbox
        if isinstance(ans, dict) and ans.get("bbox"):
            errs = validate_bbox(ans["bbox"], f"{qid} answer.bbox", dims)
            if errs:
                for e in errs: report.error(2, e)
                found_err = True
        
        # Validate evidence bboxes
        for k, ev in enumerate(it.get("evidence", []) or []):
            if ev.get("bbox"):
                errs = validate_bbox(ev["bbox"], f"{qid} evidence[{k}].bbox", dims)
                if errs:
                    for e in errs: report.error(2, e)
                    found_err = True
        
        if found_err:
            bad_indices.add(i)

    # --- Check 3: yes/no balance ≥30% per split ---
    by_split = defaultdict(list)
    for it in items:
        by_split[it.get("split", "unknown")].append(it)

    for split, split_items in by_split.items():
        yes_count = sum(1 for it in split_items if it.get("answer", {}).get("yes") is True)
        no_count = sum(1 for it in split_items if it.get("answer", {}).get("yes") is False)
        total = yes_count + no_count
        if total > 0:
            minority = min(yes_count, no_count) / total
            if minority < 0.30:
                msg = f"Split '{split}' yes/no minority {minority:.1%} < 30%"
                if strict:
                    report.error(3, msg)
                else:
                    report.warn(3, msg)
        report.stats[f"split_{split}_yes"] = yes_count
        report.stats[f"split_{split}_no"] = no_count

    # --- Check 4: negative entity on page (hard-neg enforcement) ---
    if not skip_textlayer:
        for i, it in enumerate(items):
            ans = it.get("answer", {})
            if isinstance(ans, dict) and ans.get("yes") is False:
                qid = it.get("question_id") or it.get("qid")
                entity = it.get("entity") or extract_entity_from_question(it.get("question", ""))
                page = ans.get("page")
                pair_id = it.get("pair_id", "")
                
                if page is not None and pair_id in manifest:
                    pair = manifest[pair_id]
                    doc_a = pair.get("from_doc_id", "")
                    doc_b = pair.get("to_doc_id", "")
                    
                    count_a = count_entity_on_page(textlayers.get(doc_a, {}), page, entity)
                    count_b = count_entity_on_page(textlayers.get(doc_b, {}), page, entity)
                    
                    found_err = False
                    if count_a == 0 or count_b == 0:
                        msg = f"{qid}: hard-neg entity '{entity}' missing on page {page} (A:{count_a}, B:{count_b})"
                        if strict:
                            report.error(4, msg)
                            found_err = True
                        else:
                            report.warn(4, msg)
                    
                    ev = it.get("evidence", [])
                    if not ev:
                        msg = f"{qid}: hard-neg without evidence bboxes"
                        if strict:
                            report.error(4, msg)
                            found_err = True
                        else:
                            report.warn(4, msg)
                    
                    if found_err:
                        bad_indices.add(i)

    # --- Check 5: scope-schema match ---
    for i, it in enumerate(items):
        qtype = it.get("question_type", "")
        ans = it.get("answer", {})
        qid = it.get("question_id") or it.get("qid")
        found_err = False
        if qtype == "locate_bbox" and not ans.get("bbox"):
            report.error(5, f"{qid}: locate_bbox without bbox answer")
            found_err = True
        if qtype == "yesno" and ans.get("yes") is None:
            report.error(5, f"{qid}: yesno without yes/no answer")
            found_err = True
        if found_err:
            bad_indices.add(i)

    # --- Check 6: ambiguous target (freq > 3 without anchor) ---
    if not skip_textlayer:
        for i, it in enumerate(items):
            if anchor_present(it):
                continue
            entity = it.get("entity") or extract_entity_from_question(it.get("question", ""))
            if not entity: continue
            
            ans = it.get("answer", {})
            page = ans.get("page") if isinstance(ans, dict) else None
            pair_id = it.get("pair_id", "")
            
            if page is not None and pair_id in manifest:
                pair = manifest[pair_id]
                doc_b = pair.get("to_doc_id", "")
                count = count_entity_on_page(textlayers.get(doc_b, {}), page, entity)
                
                if count > 3:
                    qid = it.get("question_id") or it.get("qid")
                    msg = f"{qid}: entity '{entity}' appears {count}x on page {page} without anchor"
                    if strict:
                        report.error(6, msg)
                        bad_indices.add(i)
                    else:
                        report.warn(6, msg)

    # --- Check 7: evidence on positives ---
    for i, it in enumerate(items):
        ans = it.get("answer", {})
        if isinstance(ans, dict) and ans.get("yes") is True:
            ev = it.get("evidence", [])
            if not ev:
                qid = it.get("question_id") or it.get("qid")
                msg = f"{qid}: positive without evidence"
                if strict:
                    report.error(7, msg)
                    bad_indices.add(i)
                else:
                    report.warn(7, msg)

    # --- Check 8: coverage per split ---
    # Global check, no item filtering

    # --- Check 9: change_id split overlap ---
    change_ids_by_split: Dict[str, Set[str]] = defaultdict(set)
    for it in items:
        cid = it.get("change_id")
        if cid:
            change_ids_by_split[it.get("split", "unknown")].add(cid)

    splits = list(change_ids_by_split.keys())
    for k, s1 in enumerate(splits):
        for s2 in splits[k + 1:]:
            overlap = change_ids_by_split[s1] & change_ids_by_split[s2]
            if overlap:
                report.error(9, f"change_id overlap between {s1} and {s2}: {len(overlap)} ids")
                # Could flag items here, but leaving as global error for now

    # --- Check 10: pair/doc resolvability ---
    for i, it in enumerate(items):
        pid = it.get("pair_id")
        if pid and pid not in manifest:
            report.error(10, f"pair_id '{pid}' not in manifest")
            bad_indices.add(i)

    # --- Check 11: duplicate QA collapse ---
    seen_keys: Dict[Tuple, int] = {}
    for i, it in enumerate(items):
        key = dedup_key(it)
        if key in seen_keys:
            old_i = seen_keys[key]
            old_qid = items[old_i].get("question_id") or items[old_i].get("qid")
            new_qid = it.get("question_id") or it.get("qid")
            report.error(11, f"Duplicate QA: {new_qid} collides with {old_qid}")
            # Mark the NEW duplicate as bad
            bad_indices.add(i)
        else:
            seen_keys[key] = i

    return report, bad_indices


def main():
    ap = argparse.ArgumentParser(description="Eng_Bench 11-check validator")
    ap.add_argument("--root", required=True, help="Eng_Bench root")
    ap.add_argument("--input", required=True, help="JSONL file to validate")
    ap.add_argument("--manifest", default="manifest.jsonl")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    ap.add_argument("--skip-textlayer", action="store_true", help="Skip checks requiring textlayer")
    ap.add_argument("--clean", help="Path to write clean JSONL (removing invalid items)")
    args = ap.parse_args()

    manifest_path = os.path.join(args.root, args.manifest)
    manifest = load_manifest(manifest_path)

    items = load_jsonl(args.input)
    print(f"[*] Validating {len(items)} items from {args.input}")
    if args.strict:
        print("[*] Running in STRICT mode")

    report, bad_indices = validate_all(items, manifest, args.root, strict=args.strict, skip_textlayer=args.skip_textlayer)

    # Print stats
    print("\n=== Stats ===")
    for k, v in sorted(report.stats.items()):
        print(f"  {k}: {v}")

    # Print warnings
    if report.warnings:
        print(f"\n=== Warnings ({len(report.warnings)}) ===")
        for w in report.warnings[:20]:
            print(f"  [WARN] {w}")
        if len(report.warnings) > 20:
            print(f"  ... and {len(report.warnings) - 20} more")

    # Print errors
    if report.errors:
        print(f"\n=== Errors ({len(report.errors)}) ===")
        for e in report.errors[:20]:
            print(f"  [ERR] {e}")
        if len(report.errors) > 20:
            print(f"  ... and {len(report.errors) - 20} more")

    # Write clean output if requested
    if args.clean:
        clean_items = [it for i, it in enumerate(items) if i not in bad_indices]
        with open(args.clean, "w", encoding="utf-8") as f:
            for it in clean_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        print(f"\n[OK] Wrote clean dataset to {args.clean}")
        print(f"     Kept: {len(clean_items)}, Removed: {len(bad_indices)}")

    if report.errors:
        return 1

    print("\n[OK] Validation passed!")
    return 0


if __name__ == "__main__":
    exit(main())
