#!/usr/bin/env python3
"""Text-layer span-diff visualdiff baseline for unified Eng_Bench rows.

Instead of comparing pixels, this baseline diffs the exact embedded text
layers of the old and new page, localizes the largest cluster of changed
spans on each side, and answers with a template naming the changed texts.
It exercises a different input modality than the pixel baselines and is
expected to be strong on text-value changes but blind to purely graphical
edits.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# Canonical images/ directory names ("{doc}__{version}") to internal
# text-layer document IDs. Identity is assumed when a name is absent.
IMAGE_DIR_TO_TEXTLAYER_DOC = {
    "viola__pcbV1.0": "toradex_viola_v1.0",
    "viola__pcbV1.1": "toradex_viola_v1.1",
    "viola__pcbV1.2": "toradex_viola_v1.2",
    "bbb__C": "bbb_schematic_revC",
    "bbb__C3": "bbb_schematic_revC3",
    "tolerances_table_iso__iso": "tolerances_table_iso",
}

NO_TEXT_CHANGE_ANSWER = "No embedded text change detected; any difference is graphical."


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


def parse_image_ref(image_path: str) -> tuple[str, int] | None:
    match = re.search(r"images/([^/]+)/page_(\d+)\.png$", str(image_path).replace("\\", "/"))
    if not match:
        return None
    image_dir = match.group(1)
    doc_id = IMAGE_DIR_TO_TEXTLAYER_DOC.get(image_dir, image_dir)
    return doc_id, int(match.group(2))


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_page_spans(root: Path, doc_id: str, page_index: int) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for raw in load_jsonl(root / "derived" / "textlayer" / f"{doc_id}.jsonl"):
        if int(raw.get("page", -1)) != page_index:
            continue
        text = normalize_text(raw.get("text"))
        bbox = raw.get("bbox_px") or raw.get("bbox")
        if not text or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        spans.append({"text": text, "bbox": [int(round(float(v))) for v in bbox]})
    return spans


def largest_changed_cluster(spans: list[dict[str, Any]], merge_gap_px: int = 48) -> tuple[list[int], list[str]] | None:
    """Cluster changed spans by proximity and return the largest cluster."""
    clusters: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        merged = False
        for cluster in clusters:
            box = cluster["bbox"]
            gap_x = max(span["bbox"][0] - box[2], box[0] - span["bbox"][2], 0)
            gap_y = max(span["bbox"][1] - box[3], box[1] - span["bbox"][3], 0)
            if gap_x <= merge_gap_px and gap_y <= merge_gap_px:
                cluster["bbox"] = [
                    min(box[0], span["bbox"][0]),
                    min(box[1], span["bbox"][1]),
                    max(box[2], span["bbox"][2]),
                    max(box[3], span["bbox"][3]),
                ]
                cluster["texts"].append(span["text"])
                merged = True
                break
        if not merged:
            clusters.append({"bbox": list(span["bbox"]), "texts": [span["text"]]})
    if not clusters:
        return None
    best = max(
        clusters,
        key=lambda cluster: (
            len(cluster["texts"]),
            (cluster["bbox"][2] - cluster["bbox"][0]) * (cluster["bbox"][3] - cluster["bbox"][1]),
        ),
    )
    return best["bbox"], best["texts"]


def predict_row(root: Path, row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if row.get("task") != "visualdiff":
        return None, None
    identifier = row_id(row)
    images = row.get("images") or []
    if identifier is None or not isinstance(images, list) or len(images) < 2:
        return None, "missing_schema"
    old_ref = parse_image_ref(images[0])
    new_ref = parse_image_ref(images[1])
    if old_ref is None or new_ref is None:
        return None, "missing_schema"
    old_spans = load_page_spans(root, *old_ref)
    new_spans = load_page_spans(root, *new_ref)
    if not old_spans and not new_spans:
        return None, "missing_textlayer"

    old_freq = Counter(span["text"] for span in old_spans)
    new_freq = Counter(span["text"] for span in new_spans)
    removed = [span for span in old_spans if not new_freq.get(span["text"])]
    added = [span for span in new_spans if not old_freq.get(span["text"])]

    removed_cluster = largest_changed_cluster(removed)
    added_cluster = largest_changed_cluster(added)

    def page_bbox(spans: list[dict[str, Any]]) -> list[int]:
        if not spans:
            return [0, 0, 1, 1]
        return [
            min(span["bbox"][0] for span in spans),
            min(span["bbox"][1] for span in spans),
            max(span["bbox"][2] for span in spans),
            max(span["bbox"][3] for span in spans),
        ]

    old_bbox = removed_cluster[0] if removed_cluster else (added_cluster[0] if added_cluster else page_bbox(old_spans))
    new_bbox = added_cluster[0] if added_cluster else (removed_cluster[0] if removed_cluster else page_bbox(new_spans))

    if removed_cluster or added_cluster:
        removed_texts = ", ".join(f"'{text}'" for text in (removed_cluster[1][:3] if removed_cluster else []))
        added_texts = ", ".join(f"'{text}'" for text in (added_cluster[1][:3] if added_cluster else []))
        parts = []
        if removed_texts:
            parts.append(f"removed {removed_texts}")
        if added_texts:
            parts.append(f"added {added_texts}")
        answer = f"Embedded text changed: {'; '.join(parts)}."
    else:
        answer = NO_TEXT_CHANGE_ANSWER

    return (
        {
            "id": identifier,
            "answer": answer,
            "evidence": [
                {"image_index": 0, "bbox": old_bbox},
                {"image_index": 1, "bbox": new_bbox},
            ],
            "metadata": {
                "model": "textlayer_diff",
                "method": "textlayer_span_frequency_diff_largest_cluster",
                "removed_span_count": len(removed),
                "added_span_count": len(added),
            },
        },
        None,
    )


def predict_rows(
    root: str | Path,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = Path(root)
    predictions: list[dict[str, Any]] = []
    stats = {
        "rows": len(rows),
        "visualdiff_rows": 0,
        "predictions": 0,
        "abstentions": 0,
        "missing_schema": 0,
        "missing_textlayer": 0,
    }
    for row in rows:
        if row.get("task") != "visualdiff":
            continue
        stats["visualdiff_rows"] += 1
        prediction, error = predict_row(root, row)
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
                    "model": "textlayer_diff",
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
    parser = argparse.ArgumentParser(description="Generate text-layer span-diff visualdiff predictions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified Eng_Bench JSONL")
    parser.add_argument("--output", required=True, help="Output unified prediction JSONL")
    parser.add_argument("--split", default="all", help="Optional split filter")
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    if args.split != "all":
        rows = [row for row in rows if row.get("split") == args.split]
    predictions, stats = predict_rows(root, rows)
    write_jsonl(root / args.output, predictions)
    print(f"[OK] Visualdiff rows: {stats['visualdiff_rows']}")
    print(f"[OK] Predictions: {stats['predictions']}")
    print(f"[OK] Explicit abstentions: {stats['abstentions']}")
    if stats["missing_schema"] or stats["missing_textlayer"]:
        print(f"[WARN] Missing schema rows: {stats['missing_schema']}")
        print(f"[WARN] Missing textlayer rows: {stats['missing_textlayer']}")
    print(f"[OK] Wrote {root / args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
