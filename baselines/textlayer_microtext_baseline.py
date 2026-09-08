#!/usr/bin/env python3
"""Text-layer heuristic microtext baseline for unified Eng_Bench rows."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOM_WORDS = {
    "bath",
    "bedroom",
    "closet",
    "dining",
    "garage",
    "hall",
    "kitchen",
    "laundry",
    "living",
    "porch",
    "room",
    "storage",
    "toilet",
}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not Path(path).exists():
        return rows
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else None


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def page_index_from_row(row: dict[str, Any]) -> int | None:
    metadata = row_metadata(row)
    if metadata.get("page_index") is not None:
        try:
            return int(metadata["page_index"])
        except (TypeError, ValueError):
            pass
    images = row.get("images") or []
    if isinstance(images, list) and images:
        match = re.search(r"page_(\d+)\.png", str(images[0]))
        if match:
            return int(match.group(1))
    return None


def normalize_bbox(raw: Any) -> list[int] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return [int(round(float(value))) for value in raw]
    except (TypeError, ValueError):
        return None


def span_from_jsonl(row: dict[str, Any]) -> dict[str, Any] | None:
    bbox = normalize_bbox(row.get("bbox_px") or row.get("bbox"))
    text = str(row.get("text") or "").strip()
    if not text or bbox is None:
        return None
    return {"page": int(row.get("page", row.get("page_index", 0))), "text": text, "bbox": bbox}


def load_spans(root: Path, doc_id: str, page_index: int) -> list[dict[str, Any]]:
    jsonl_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    spans = [
        span
        for span in (span_from_jsonl(row) for row in load_jsonl(jsonl_path))
        if span and span["page"] == page_index
    ]
    if spans:
        return spans

    page_json = root / "derived" / "textlayer" / doc_id / f"page_{page_index:03d}.json"
    if not page_json.exists():
        return []
    payload = json.loads(page_json.read_text(encoding="utf-8"))
    page_spans = payload.get("spans") if isinstance(payload, dict) else []
    return [
        span
        for span in (span_from_jsonl({**raw, "page": page_index}) for raw in page_spans)
        if span
    ]


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def score_span(span: dict[str, Any], category: str) -> tuple[int, int, int]:
    text = clean_text(str(span.get("text") or ""))
    lower = text.lower()
    compact = re.sub(r"[^A-Za-z0-9/\"'.+-]", "", text)
    has_digit = any(ch.isdigit() for ch in text)
    uppercase_ratio = sum(ch.isupper() for ch in text) / max(1, sum(ch.isalpha() for ch in text))

    score = 0
    if category == "room_label":
        if lower in ROOM_WORDS or any(word in lower.split() for word in ROOM_WORDS):
            score += 10
        if uppercase_ratio > 0.8:
            score += 2
    elif category in {"pin_label", "equipment_tag", "instrument_tag"}:
        if re.fullmatch(r"[A-Z]{1,4}[- ]?\d+[A-Z0-9.-]*", compact.upper()):
            score += 10
        if has_digit and uppercase_ratio > 0.5:
            score += 3
    elif category == "pipe_line_tag":
        if re.search(r"\d+[-/][A-Z]{1,5}[-/]\d+", compact.upper()):
            score += 10
        if has_digit and "-" in text:
            score += 3
    elif category in {"dimension_value", "tolerance_value", "process_value"}:
        if has_digit:
            score += 6
        if any(token in text for token in ('"', "'", "/", "+/-", "±", ".", ",")):
            score += 3
    else:
        if has_digit:
            score += 2
        if uppercase_ratio > 0.8:
            score += 1

    bbox = span.get("bbox") or [0, 0, 0, 0]
    area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return score, -abs(len(text) - 8), -area


def pick_span(spans: list[dict[str, Any]], category: str) -> dict[str, Any] | None:
    candidates = [span for span in spans if clean_text(str(span.get("text") or ""))]
    if not candidates:
        return None
    scored = sorted(
        candidates,
        key=lambda span: score_span(span, category),
        reverse=True,
    )
    best = scored[0]
    if score_span(best, category)[0] <= 0:
        counter = Counter(clean_text(str(span.get("text") or "")) for span in candidates)
        common_text, _ = counter.most_common(1)[0]
        for span in candidates:
            if clean_text(str(span.get("text") or "")) == common_text:
                return span
    return best


def pick_smallest_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Size-prior selection: smallest readable span, assuming microtext is tiny."""
    candidates = []
    for span in spans:
        text = clean_text(str(span.get("text") or ""))
        if len(text) < 2 or not any(ch.isalnum() for ch in text):
            continue
        bbox = span.get("bbox") or [0, 0, 0, 0]
        area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        candidates.append((area, bbox[1], bbox[0], text, span))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:4])
    return candidates[0][4]


def readable_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = []
    for span in spans:
        text = clean_text(str(span.get("text") or ""))
        if len(text) < 2 or not any(ch.isalnum() for ch in text):
            continue
        keep.append({**span, "text": text})
    return keep


def pick_center_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Location-prior selection: readable span nearest the page centroid."""
    candidates = readable_spans(spans)
    if not candidates:
        return None
    xs = [(span["bbox"][0] + span["bbox"][2]) / 2.0 for span in candidates]
    ys = [(span["bbox"][1] + span["bbox"][3]) / 2.0 for span in candidates]
    page_cx = (min(span["bbox"][0] for span in candidates) + max(span["bbox"][2] for span in candidates)) / 2.0
    page_cy = (min(span["bbox"][1] for span in candidates) + max(span["bbox"][3] for span in candidates)) / 2.0
    scored = sorted(
        zip(candidates, xs, ys),
        key=lambda item: ((item[1] - page_cx) ** 2 + (item[2] - page_cy) ** 2, item[2], item[1]),
    )
    return scored[0][0]


def pick_most_frequent_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Repetition-prior selection: first instance of the most repeated text."""
    candidates = readable_spans(spans)
    if not candidates:
        return None
    counter = Counter(span["text"] for span in candidates)
    best_text, _count = max(counter.items(), key=lambda item: (item[1], -len(item[0]), item[0]))
    instances = [span for span in candidates if span["text"] == best_text]
    instances.sort(key=lambda span: (span["bbox"][1], span["bbox"][0]))
    return instances[0]


def predict_row(
    root: Path,
    row: dict[str, Any],
    mode: str = "category_heuristic",
) -> tuple[dict[str, Any] | None, str | None]:
    if row.get("task") != "microtext":
        return None, None
    identifier = row_id(row)
    metadata = row_metadata(row)
    doc_id = metadata.get("doc_id") or row.get("doc_id")
    page_index = page_index_from_row(row)
    if identifier is None or not doc_id or page_index is None:
        return None, "missing_schema"
    category = str(metadata.get("category") or row.get("category") or "unknown")
    spans = load_spans(root, str(doc_id), page_index)
    if mode == "smallest_span":
        span = pick_smallest_span(spans)
        model_metadata = {
            "model": "textlayer_smallest_span",
            "method": "textlayer_smallest_span_size_prior",
            "category": category,
        }
    elif mode == "center_span":
        span = pick_center_span(spans)
        model_metadata = {
            "model": "textlayer_center_span",
            "method": "textlayer_center_span_location_prior",
            "category": category,
        }
    elif mode == "page_frequency":
        span = pick_most_frequent_span(spans)
        model_metadata = {
            "model": "textlayer_page_frequency",
            "method": "textlayer_most_frequent_text_repetition_prior",
            "category": category,
        }
    else:
        span = pick_span(spans, category)
        model_metadata = {
            "model": "textlayer_heuristic",
            "method": "textlayer_category_heuristic",
            "category": category,
        }
    if span is None:
        return None, "missing_textlayer"
    return (
        {
            "id": identifier,
            "answer": clean_text(str(span["text"])),
            "evidence": [{"image_index": 0, "bbox": span["bbox"]}],
            "metadata": model_metadata,
        },
        None,
    )


def predict_rows(
    root: str | Path,
    rows: list[dict[str, Any]],
    mode: str = "category_heuristic",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = Path(root)
    predictions: list[dict[str, Any]] = []
    stats = {
        "rows": len(rows),
        "microtext_rows": 0,
        "predictions": 0,
        "abstentions": 0,
        "missing_schema": 0,
        "missing_textlayer": 0,
    }
    for row in rows:
        if row.get("task") != "microtext":
            continue
        stats["microtext_rows"] += 1
        prediction, error = predict_row(root, row, mode=mode)
        if prediction is None:
            if error:
                stats[error] += 1
            identifier = row_id(row)
            if identifier is None:
                continue
            prediction = {
                "id": identifier,
                "answer": "",
                "evidence": [],
                "metadata": {
                    "model": f"textlayer_{mode}",
                    "method": "explicit_abstention",
                    "abstained": True,
                    "abstention_reason": error or "unavailable",
                },
            }
            stats["abstentions"] += 1
        predictions.append(prediction)
    stats["predictions"] = len(predictions)
    return predictions, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate text-layer heuristic microtext predictions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified Eng_Bench JSONL")
    parser.add_argument("--output", required=True, help="Output unified prediction JSONL")
    parser.add_argument("--split", default="all", help="Optional split filter")
    parser.add_argument(
        "--mode",
        choices=("category_heuristic", "smallest_span", "center_span", "page_frequency"),
        default="category_heuristic",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    if args.split != "all":
        rows = [row for row in rows if row.get("split") == args.split]
    predictions, stats = predict_rows(root, rows, mode=args.mode)
    write_jsonl(root / args.output, predictions)
    print(f"[OK] Microtext rows: {stats['microtext_rows']}")
    print(f"[OK] Predictions: {stats['predictions']}")
    print(f"[OK] Explicit abstentions: {stats['abstentions']}")
    if stats["missing_schema"] or stats["missing_textlayer"]:
        print(f"[WARN] Missing schema rows: {stats['missing_schema']}")
        print(f"[WARN] Missing textlayer rows: {stats['missing_textlayer']}")
    print(f"[OK] Wrote {root / args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
