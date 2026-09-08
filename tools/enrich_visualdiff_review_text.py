#!/usr/bin/env python3
"""Add exact nearby text evidence and draft descriptions to VisualDiff review rows."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def truncate(value: str, limit: int = 220) -> str:
    value = normalize_text(value)
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def intersects(a: list[float], b: list[float], margin: float) -> bool:
    ax1, ay1, ax2, ay2 = [float(value) for value in a[:4]]
    bx1, by1, bx2, by2 = [float(value) for value in b[:4]]
    return not (
        bx2 < ax1 - margin
        or bx1 > ax2 + margin
        or by2 < ay1 - margin
        or by1 > ay2 + margin
    )


def text_for_bbox(
    spans: list[dict[str, Any]],
    page: int,
    bbox: list[float],
    margin: int,
    limit: int = 14,
) -> str:
    matches: list[tuple[float, float, str]] = []
    for span in spans:
        if int(span.get("page", -1)) != page:
            continue
        span_bbox = span.get("bbox_px") or span.get("bbox")
        text = normalize_text(span.get("text"))
        if not text or not isinstance(span_bbox, list) or len(span_bbox) < 4:
            continue
        if intersects(bbox, span_bbox, margin):
            matches.append((float(span_bbox[1]), float(span_bbox[0]), text))
    selected: list[str] = []
    for _, _, text in sorted(matches):
        if text not in selected:
            selected.append(text)
        if len(selected) >= limit:
            break
    return truncate(" | ".join(selected))


def doc_id_from_image(value: Any) -> str:
    path = Path(str(value or ""))
    if not path.name or not path.parent.name:
        raise ValueError(f"cannot infer document ID from image path: {value!r}")
    return path.parent.name


def description_for(old_text: str, new_text: str, context: str) -> tuple[str, str]:
    if old_text and new_text and old_text != new_text:
        return "text", f'Engineering labels changed from "{truncate(old_text, 150)}" to "{truncate(new_text, 150)}".'
    if old_text and not new_text:
        return "deletion", f'Engineering content was removed: "{truncate(old_text, 180)}".'
    if new_text and not old_text:
        return "addition", f'Engineering content was added: "{truncate(new_text, 180)}".'
    suffix = f' near "{truncate(context, 140)}"' if context else ""
    return "symbol", f"The highlighted schematic symbol or connection changed between revisions{suffix}."


def enrich_rows(root: Path, rows: list[dict[str, Any]], margin: int = 36, context_margin: int = 120) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    cache: dict[str, list[dict[str, Any]]] = {}
    missing_textlayer_docs: set[str] = set()

    def spans(doc_id: str) -> list[dict[str, Any]]:
        if doc_id not in cache:
            path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
            if not path.is_file():
                cache[doc_id] = []
                missing_textlayer_docs.add(doc_id)
            else:
                cache[doc_id] = read_jsonl(path)
        return cache[doc_id]

    output: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    rows_with_text_change = 0
    for original in rows:
        row = dict(original)
        old_doc_id = doc_id_from_image(row.get("image_old"))
        new_doc_id = doc_id_from_image(row.get("image_new"))
        old_bbox = row.get("bbox_old") or []
        new_bbox = row.get("bbox_new") or []
        if len(old_bbox) < 4 or len(new_bbox) < 4:
            raise ValueError(f"row has invalid bboxes: {row.get('pair_id')}")
        old_page = int(row.get("page_old") or 0)
        new_page = int(row.get("page_new") or 0)
        old_text = text_for_bbox(spans(old_doc_id), old_page, old_bbox, margin)
        new_text = text_for_bbox(spans(new_doc_id), new_page, new_bbox, margin)
        old_context = text_for_bbox(spans(old_doc_id), old_page, old_bbox, context_margin)
        new_context = text_for_bbox(spans(new_doc_id), new_page, new_bbox, context_margin)
        context = new_context or old_context
        change_type, description = description_for(old_text, new_text, context)
        textlayer_missing = old_doc_id in missing_textlayer_docs or new_doc_id in missing_textlayer_docs
        if old_text != new_text and (old_text or new_text):
            rows_with_text_change += 1
        type_counts[change_type] = type_counts.get(change_type, 0) + 1
        row.update(
            {
                "old_doc_id": old_doc_id,
                "new_doc_id": new_doc_id,
                "old_text": old_text,
                "new_text": new_text,
                "change_type": change_type,
                "description": description,
                "description_source": (
                    "machine_visual_candidate_missing_textlayer"
                    if textlayer_missing
                    else "machine_exact_svg_textlayer_candidate"
                ),
                "textlayer_status": "missing_one_or_more" if textlayer_missing else "available",
                "review_confidence": "machine_candidate",
                "safe_to_merge_gold": False,
                "notes": (
                    "Machine-enriched review candidate from pinned Git revisions. "
                    + (
                        "One or more exact text layers are unavailable; the draft relies on visual evidence. "
                        if textlayer_missing
                        else "Exact SVG text layers were used where they intersect the evidence region. "
                    )
                    +
                    "Human acceptance and description correction remain required before gold promotion."
                ),
            }
        )
        output.append(row)
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "visualdiff_text_evidence_enrichment",
        "rows": len(output),
        "rows_with_text_change": rows_with_text_change,
        "change_types": dict(sorted(type_counts.items())),
        "textlayer_docs": len(cache),
        "missing_textlayer_docs": sorted(missing_textlayer_docs),
        "missing_textlayer_doc_count": len(missing_textlayer_docs),
        "margin": margin,
        "context_margin": context_margin,
        "active_gold_rows_modified": 0,
        "valid": len(output) == len(rows),
    }
    return output, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--margin", type=int, default=36)
    parser.add_argument("--context-margin", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    output_path = args.output if args.output.is_absolute() else root / args.output
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    rows, report = enrich_rows(root, read_jsonl(input_path), args.margin, args.context_margin)
    write_jsonl(output_path, rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
