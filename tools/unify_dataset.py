#!/usr/bin/env python3
"""
unify_dataset.py

Consolidates visualdiff and microtext into a single unified JSONL file
following standard benchmark conventions (HotpotQA, Musique).

Output Schema:
{
    "id": str,           # Unique question ID
    "task": str,         # "visualdiff" or "microtext"
    "question": str,     # The query text
    "answer": str,       # Ground truth answer
    "images": [str],     # List of relative image paths
    "evidence": [dict],  # Bboxes or other supporting evidence
    "split": str,        # "train" or "test"
    "metadata": dict     # Task-specific metadata
}
"""
import argparse
import json
import os
from pathlib import Path


def relpath(path: Path, root: Path) -> str:
    """Return a POSIX-style path relative to the benchmark root."""
    return path.relative_to(root).as_posix()


def canonical_image_relpath(doc_id, version_id, page_index) -> str:
    """Canonical public image path for a document page."""
    clean_doc = str(doc_id or "")
    clean_version = str(version_id or "unknown")
    page_idx = int(page_index or 0)
    return f"images/{clean_doc}__{clean_version}/page_{page_idx:04d}.png"


def image_candidates(root: Path, doc_id, version_id, page_index) -> list[Path]:
    """Candidate page-image locations in preferred public-to-derived order."""
    page_idx = int(page_index or 0)
    clean_doc = str(doc_id or "")
    candidates = [root / canonical_image_relpath(clean_doc, version_id, page_idx)]
    derived_dir = root / "derived" / "pages_300dpi" / clean_doc
    candidates.extend(
        [
            derived_dir / f"page_{page_idx:04d}.png",
            derived_dir / f"page_{page_idx:03d}.png",
            derived_dir / f"p{page_idx:04d}.png",
        ]
    )
    return candidates


def resolve_image_path(root: Path, doc_id, version_id, page_index) -> str:
    """
    Resolve a page image to an existing path when possible.

    The canonical image path remains preferred, but derived render locations are
    accepted as a fallback so packaging gaps are visible and testable before
    finalization copies them into images/.
    """
    candidates = image_candidates(root, doc_id, version_id, page_index)
    for candidate in candidates:
        if candidate.exists():
            return relpath(candidate, root)
    return relpath(candidates[0], root)


def resolve_recorded_image_path(root: Path, recorded_path) -> str | None:
    """Return an existing explicit row image path relative to the benchmark root."""
    value = str(recorded_path or "").strip()
    if not value:
        return None
    candidate = Path(value)
    full_path = candidate if candidate.is_absolute() else root / candidate
    if not full_path.exists():
        return None
    try:
        return relpath(full_path.resolve(), root.resolve())
    except ValueError:
        return full_path.as_posix()


def load_jsonl(path):
    """Load JSONL file."""
    items = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
    return items


def write_jsonl(path, rows):
    """Write canonical JSONL matching the reviewed-promotion transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

def process_visualdiff(root, pairs_path=None, questions_path=None):
    """Process visualdiff pairs and questions into unified format."""
    pairs_path = pairs_path or root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    questions_path = questions_path or root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl"
    
    pairs = {p["pair_id"]: p for p in load_jsonl(pairs_path)}
    questions = load_jsonl(questions_path)
    
    unified = []
    for q in questions:
        pair = pairs.get(q["pair_id"], {})
        
        # Build image paths
        page_idx_old = pair.get("page_index_old", 0)
        page_idx_new = pair.get("page_index_new", page_idx_old)
        img_old = resolve_recorded_image_path(root, pair.get("image_old"))
        if not img_old:
            img_old = resolve_image_path(
                root,
                pair.get("doc_id", ""),
                pair.get("version_id_old", ""),
                page_idx_old,
            )
        img_new = resolve_recorded_image_path(root, pair.get("image_new"))
        if not img_new:
            img_new = resolve_image_path(
                root,
                pair.get("doc_id", ""),
                pair.get("version_id_new", ""),
                page_idx_new,
            )
        
        # Build evidence
        evidence = []
        if pair.get("bbox_old"):
            evidence.append({"bbox": pair["bbox_old"], "image_index": 0})
        if pair.get("bbox_new"):
            evidence.append({"bbox": pair["bbox_new"], "image_index": 1})
        
        unified.append({
            "id": q.get("question_id", q["pair_id"]),
            "task": "visualdiff",
            "question": q.get("query_text", ""),
            "answer": q.get("answer_text", ""),
            "images": [img_old, img_new],
            "evidence": evidence,
            "split": pair.get("split", "test"),
            "metadata": {
                "pair_id": q["pair_id"],
                "doc_id": pair.get("doc_id"),
                "change_type": pair.get("change_type", []),
            }
        })
    
    return unified

def process_microtext(root, items_path=None, questions_path=None):
    """Process microtext items and questions into unified format."""
    items_path = items_path or root / "microtext" / "annotations" / "microtext_items.jsonl"
    questions_path = questions_path or root / "microtext" / "annotations" / "microtext_questions.jsonl"
    
    items = {i["item_id"]: i for i in load_jsonl(items_path)}
    questions = load_jsonl(questions_path)
    
    # If no questions file, generate from items directly
    if not questions:
        questions = [{"item_id": i["item_id"], "query_text": f"Read the text at this location.", "answer_text": i.get("text_gt", "")} for i in items.values()]
    
    unified = []
    for q in questions:
        item_ids = q.get("item_ids") or []
        item_id = q.get("item_id") or (item_ids[0] if item_ids else "")
        item = items.get(item_id, {})
        
        # Build image path
        page_idx = item.get("page_index", 0)
        img_path = resolve_image_path(
            root,
            item.get("doc_id", ""),
            item.get("version_id", "unknown"),
            page_idx,
        )
        
        # Build evidence
        evidence = []
        if item.get("bbox"):
            evidence.append({"bbox": item["bbox"], "image_index": 0})

        item_metadata = {
            "item_id": item_id,
            "item_ids": item_ids or ([item_id] if item_id else []),
            "doc_id": item.get("doc_id"),
            "category": item.get("category", ""),
        }
        certification_fields = (
            "review_source",
            "human_reviewed",
            "certification_method",
            "certification_tier",
            "certification_policy_version",
            "certification_date",
            "certification_eligibility_report_sha256",
            "certification_calibration_attestation_sha256",
            "machine_certification_evidence_sha256",
        )
        has_machine_certification = (
            item.get("review_source") == "machine_certification_policy"
            or any(
                item.get(field) not in (None, "")
                for field in certification_fields
                if field not in {"review_source", "human_reviewed"}
            )
        )
        if has_machine_certification:
            for field in certification_fields:
                if item.get(field) not in (None, ""):
                    item_metadata[field] = item[field]
        
        unified.append({
            "id": q.get("question_id", q.get("item_id", "")),
            "task": "microtext",
            "question": q.get("query_text", ""),
            "answer": q.get("answer_text", item.get("text_gt", "")),
            "images": [img_path],
            "evidence": evidence,
            "split": item.get("split", "test"),
            "metadata": item_metadata,
        })
    
    return unified

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eng_bench.jsonl"),
        help="Output JSONL path, relative to --root unless absolute.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = args.root.resolve()
    output_path = args.output if args.output.is_absolute() else root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print("[*] Processing Visual Diff...")
    vdiff_data = process_visualdiff(root)
    print(f"    Found {len(vdiff_data)} Visual Diff questions.")
    
    print("[*] Processing Microtext...")
    micro_data = process_microtext(root)
    print(f"    Found {len(micro_data)} Microtext questions.")
    
    all_data = vdiff_data + micro_data
    
    print(f"[*] Writing unified dataset to {output_path}...")
    write_jsonl(output_path, all_data)
    
    # Stats
    split_counts = {}
    for row in all_data:
        split_counts[row["split"]] = split_counts.get(row["split"], 0) + 1
    
    print(f"[OK] Unified dataset created:")
    print(f"     Total: {len(all_data)}")
    for split, count in sorted(split_counts.items()):
        print(f"     {split.title()}: {count}")
    print(f"     Visual Diff: {len(vdiff_data)}")
    print(f"     Microtext:   {len(micro_data)}")

if __name__ == "__main__":
    main()
