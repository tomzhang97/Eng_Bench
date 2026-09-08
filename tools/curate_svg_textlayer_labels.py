#!/usr/bin/env python3
"""Convert explicitly adjudicated SVG text spans into review-only candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def candidate_id(
    doc_id: str,
    version_id: str,
    page_index: int,
    text: str,
    bbox: list[int],
) -> str:
    payload = json.dumps(
        {
            "doc_id": doc_id,
            "version_id": version_id,
            "page_index": page_index,
            "text": text,
            "bbox": bbox,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mtcand__{hashlib.sha256(payload).hexdigest()[:20]}"


def question_for(category: str) -> str:
    return {
        "equipment_tag": "What equipment tag is shown in this region?",
        "instrument_tag": "What instrument tag is shown in this region?",
        "pipe_line_tag": "What pipe, line, or stream tag is shown in this region?",
        "process_value": "What process value is shown in this region?",
    }.get(category, "What text is shown in this small engineering label region?")


def curate_rows(
    textlayer_rows: list[dict[str, Any]],
    decisions: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    doc_id = str(decisions.get("doc_id") or "").strip()
    version_id = str(decisions.get("version_id") or "").strip()
    source_candidate_id = str(decisions.get("source_candidate_id") or "").strip()
    image_path = str(decisions.get("image_path") or "").strip()
    if not all((doc_id, version_id, source_candidate_id, image_path)):
        raise ValueError("decisions require doc_id, version_id, source_candidate_id, and image_path")
    default_decision = str(decisions.get("default_decision") or "").strip().lower()
    if default_decision not in {"keep", "hold"}:
        raise ValueError("default_decision must be keep or hold")
    selection_rows = decisions.get("selections") or []
    if not isinstance(selection_rows, list):
        raise ValueError("selections must be a list")
    selection_by_text: dict[str, dict[str, Any]] = {}
    for selection in selection_rows:
        text = str(selection.get("text") or "").strip()
        if not text or text in selection_by_text:
            raise ValueError(f"selection text must be unique and nonblank: {text!r}")
        selection_by_text[text] = selection

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    observed: Counter[str] = Counter()
    for source_row in textlayer_rows:
        text = str(source_row.get("text") or "").strip()
        if not text:
            continue
        observed[text] += 1
        selection = selection_by_text.get(text, {})
        decision = str(selection.get("decision") or default_decision).strip().lower()
        if decision not in {"keep", "hold"}:
            raise ValueError(f"invalid decision for {text!r}: {decision}")
        category = str(selection.get("category") or "unknown_microtext").strip()
        notes = str(selection.get("notes") or "").strip()
        bbox = [int(value) for value in source_row.get("bbox_px") or []]
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError(f"nondegenerate bbox required for {text!r}: {bbox}")
        row = {
            "doc_id": doc_id,
            "version_id": version_id,
            "page_index": int(source_row.get("page", 0)),
            "bbox": bbox,
            "target_text": text if decision == "keep" else "",
            "proposed_text": text,
            "category": category,
            "source": "legacy_svg_exact_text_candidate",
            "review_status": "needs_review" if decision == "keep" else "machine_held",
            "corrected_text": "",
            "question_text": question_for(category),
            "image_path": image_path,
            "text_context": text,
            "review_notes": notes,
            "candidate_id": candidate_id(doc_id, version_id, int(source_row.get("page", 0)), text, bbox),
            "promotion_state": "unreviewed_candidate" if decision == "keep" else "machine_held",
            "machine_qa_status": "selected_for_human_review" if decision == "keep" else "machine_held",
            "machine_qa_notes": notes,
            "safe_to_merge_gold": False,
            "source_candidate_id": source_candidate_id,
            "source_textlayer_bbox_source": source_row.get("bbox_source", ""),
        }
        if decision == "keep":
            kept.append(row)
        else:
            held.append(row)

    missing = sorted(set(selection_by_text) - set(observed))
    duplicate_texts = sorted(text for text, count in observed.items() if count > 1)
    if missing:
        raise ValueError(f"decision texts missing from textlayer: {', '.join(missing)}")
    if duplicate_texts:
        raise ValueError(f"textlayer contains ambiguous duplicate texts: {', '.join(duplicate_texts)}")
    report = {
        "valid": True,
        "doc_id": doc_id,
        "input_rows": sum(observed.values()),
        "explicit_decision_rows": len(selection_by_text),
        "kept_rows": len(kept),
        "held_rows": len(held),
        "kept_by_category": dict(sorted(Counter(row["category"] for row in kept).items())),
        "all_source_spans_dispositioned": len(kept) + len(held) == sum(observed.values()),
        "active_gold_modified": False,
    }
    return kept, held, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--textlayer", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--kept-output", required=True)
    parser.add_argument("--held-output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rows = read_jsonl(root / args.textlayer)
    decisions = json.loads((root / args.decisions).read_text(encoding="utf-8"))
    kept, held, report = curate_rows(rows, decisions)
    write_jsonl(root / args.kept_output, kept)
    write_jsonl(root / args.held_output, held)
    report_path = root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
