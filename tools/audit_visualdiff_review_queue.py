#!/usr/bin/env python3
"""Audit raw VisualDiff review rows before they enter a human handoff."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageStat


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _bbox(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        raise ValueError("bbox must contain four coordinates")
    x1, y1, x2, y2 = (int(round(float(item))) for item in value[:4])
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox has no positive area")
    return x1, y1, x2, y2


def _crop(root: Path, image_value: Any, bbox_value: Any, pad_px: int) -> Image.Image:
    image_path = Path(str(image_value or ""))
    if not image_path.is_absolute():
        image_path = root / image_path
    if not image_path.is_file():
        raise FileNotFoundError(f"missing image: {image_path}")
    x1, y1, x2, y2 = _bbox(bbox_value)
    with Image.open(image_path) as image:
        source = image.convert("RGB")
        left = max(0, x1 - pad_px)
        top = max(0, y1 - pad_px)
        right = min(source.width, x2 + pad_px)
        bottom = min(source.height, y2 + pad_px)
        if right <= left or bottom <= top:
            raise ValueError(f"bbox is outside image: {image_path}")
        return source.crop((left, top, right, bottom))


def _difference_metrics(old: Image.Image, new: Image.Image) -> dict[str, Any]:
    width = max(old.width, new.width)
    height = max(old.height, new.height)
    old_canvas = Image.new("RGB", (width, height), "white")
    new_canvas = Image.new("RGB", (width, height), "white")
    old_canvas.paste(old, (0, 0))
    new_canvas.paste(new, (0, 0))
    difference = ImageChops.difference(old_canvas, new_canvas)
    exact_match = old.size == new.size and difference.getbbox() is None
    mean_absolute_delta = sum(ImageStat.Stat(difference).mean) / 3.0

    channels = difference.split()
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    histogram = mask.histogram()
    total = width * height
    changed = sum(histogram[17:])

    normalized_old = old.resize((256, 256), Image.Resampling.LANCZOS)
    normalized_new = new.resize((256, 256), Image.Resampling.LANCZOS)
    normalized_difference = ImageChops.difference(normalized_old, normalized_new)
    normalized_mean_delta = sum(ImageStat.Stat(normalized_difference).mean) / 3.0
    normalized_channels = normalized_difference.split()
    normalized_mask = ImageChops.lighter(
        ImageChops.lighter(normalized_channels[0], normalized_channels[1]),
        normalized_channels[2],
    )
    normalized_histogram = normalized_mask.histogram()
    normalized_changed = sum(normalized_histogram[17:])

    return {
        "old_crop_width": old.width,
        "old_crop_height": old.height,
        "new_crop_width": new.width,
        "new_crop_height": new.height,
        "pixel_exact_match": exact_match,
        "mean_absolute_delta": round(mean_absolute_delta, 6),
        "changed_pixel_ratio_gt16": round(changed / total if total else 0.0, 8),
        "normalized_mean_absolute_delta": round(normalized_mean_delta, 6),
        "normalized_changed_pixel_ratio_gt16": round(normalized_changed / 65536, 8),
    }


def _text_relation(row: dict[str, Any]) -> str:
    old_text = str(row.get("old_text") or "").strip()
    new_text = str(row.get("new_text") or "").strip()
    if not old_text and not new_text:
        return "both_blank"
    if not old_text or not new_text:
        return "one_side_blank"
    if old_text == new_text:
        return "same"
    return "changed"


def audit_row(root: Path, row: dict[str, Any], pad_px: int = 24) -> dict[str, Any]:
    audited = dict(row)
    flags: list[str] = []
    relation = _text_relation(row)
    if relation == "same":
        flags.append("same_old_new_text")
    elif relation == "one_side_blank":
        flags.append("one_side_text_blank")
    elif relation == "both_blank":
        flags.append("both_text_blank")

    try:
        old_crop = _crop(root, row.get("image_old"), row.get("bbox_old"), pad_px)
        new_crop = _crop(root, row.get("image_new"), row.get("bbox_new"), pad_px)
        metrics = _difference_metrics(old_crop, new_crop)
        error = ""
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        metrics = {
            "old_crop_width": None,
            "old_crop_height": None,
            "new_crop_width": None,
            "new_crop_height": None,
            "pixel_exact_match": False,
            "mean_absolute_delta": None,
            "changed_pixel_ratio_gt16": None,
            "normalized_mean_absolute_delta": None,
            "normalized_changed_pixel_ratio_gt16": None,
        }
        error = str(exc)

    if error:
        disposition = "hold_missing_or_invalid_evidence"
        flags.append("evidence_error")
    elif metrics["pixel_exact_match"]:
        disposition = "hold_pixel_identical"
        flags.append("pixel_identical")
    else:
        disposition = "needs_human_review"
        if float(metrics["normalized_mean_absolute_delta"]) < 2.0:
            flags.append("near_identical_normalized")
        if float(metrics["normalized_changed_pixel_ratio_gt16"]) < 0.01:
            flags.append("very_low_changed_area")

    audited["machine_audit"] = {
        "disposition": disposition,
        "text_relation": relation,
        "flags": flags,
        "error": error,
        **metrics,
    }
    return audited


def build_audit(
    root: Path,
    rows: list[dict[str, Any]],
    pad_px: int = 24,
    mark_review_ready: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    audited_rows = [audit_row(root, row, pad_px=pad_px) for row in rows]
    review_rows: list[dict[str, Any]] = []
    held_rows: list[dict[str, Any]] = []
    dispositions: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    families: Counter[str] = Counter()
    for row in audited_rows:
        audit = row["machine_audit"]
        disposition = str(audit["disposition"])
        dispositions[disposition] += 1
        flags.update(audit["flags"])
        families[str(row.get("project_id") or "unknown")] += 1
        if disposition == "needs_human_review":
            if mark_review_ready:
                row["machine_qa_status"] = "visualdiff_queue_audit_pass"
                row["review_status"] = "needs_review"
                row["safe_to_merge_gold"] = False
                bucket = str(row.get("review_bucket") or "")
                if bucket.endswith("_unverified"):
                    row["review_bucket"] = bucket[: -len("_unverified")] + "_machine_passing"
            review_rows.append(row)
        else:
            if mark_review_ready:
                row["machine_qa_status"] = disposition
                row["review_status"] = "machine_hold"
                row["safe_to_merge_gold"] = False
            held_rows.append(row)

    report = {
        "input_rows": len(rows),
        "review_ready_rows": len(review_rows),
        "machine_held_rows": len(held_rows),
        "pad_px": pad_px,
        "marked_review_ready": mark_review_ready,
        "dispositions": dict(sorted(dispositions.items())),
        "flags": dict(sorted(flags.items())),
        "families": dict(sorted(families.items())),
        "policy": {
            "automatic_hold": ["pixel-exact crop pairs", "missing or invalid crop evidence"],
            "human_required": "All non-identical pairs, including near-identical and alignment-sensitive rows.",
        },
    }
    return review_rows, held_rows, report


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "pair_id",
        "project_id",
        "change_type",
        "old_text",
        "new_text",
        "disposition",
        "text_relation",
        "flags",
        "error",
        "old_crop_width",
        "old_crop_height",
        "new_crop_width",
        "new_crop_height",
        "pixel_exact_match",
        "mean_absolute_delta",
        "changed_pixel_ratio_gt16",
        "normalized_mean_absolute_delta",
        "normalized_changed_pixel_ratio_gt16",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            audit = row["machine_audit"]
            output = {field: audit.get(field, row.get(field, "")) for field in fields}
            output["flags"] = ";".join(audit["flags"])
            writer.writerow(output)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# VisualDiff Queue Audit",
        "",
        f"- Input rows: `{report['input_rows']}`",
        f"- Human-review rows: `{report['review_ready_rows']}`",
        f"- Machine-held rows: `{report['machine_held_rows']}`",
        f"- Crop padding: `{report['pad_px']}` pixels",
        "- Automatic holds are limited to pixel-exact pairs and missing/invalid evidence.",
        "- Near-identical and alignment-sensitive rows remain human decisions.",
        "",
        "## Dispositions",
        "",
        "| Disposition | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in report["dispositions"].items())
    lines.extend(["", "## Review Flags", "", "| Flag | Rows |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in report["flags"].items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--review-output", required=True)
    parser.add_argument("--hold-output", required=True)
    parser.add_argument("--report-prefix", required=True)
    parser.add_argument("--pad-px", type=int, default=24)
    parser.add_argument("--mark-review-ready", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    rows = read_jsonl(root / args.input)
    review_rows, held_rows, report = build_audit(
        root,
        rows,
        pad_px=args.pad_px,
        mark_review_ready=args.mark_review_ready,
    )
    write_jsonl(root / args.review_output, review_rows)
    write_jsonl(root / args.hold_output, held_rows)
    prefix = root / args.report_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    write_csv(prefix.with_suffix(".csv"), review_rows + held_rows)
    write_markdown(prefix.with_suffix(".md"), report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
