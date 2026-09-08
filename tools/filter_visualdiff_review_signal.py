#!/usr/bin/env python3
"""Keep VisualDiff review rows with explicit text or one-sided graphic signal."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def semantic_text_signal(row: dict[str, Any]) -> tuple[bool, str]:
    category = str(row.get("change_type") or "").strip()
    old_text = normalized_text(row.get("old_text"))
    new_text = normalized_text(row.get("new_text"))
    if category == "addition+text":
        return (not old_text and bool(new_text), "explicit_text_addition")
    if category == "deletion+text":
        return (bool(old_text) and not new_text, "explicit_text_deletion")
    if category == "text":
        return (
            bool(old_text) and bool(new_text) and old_text != new_text,
            "explicit_text_replacement",
        )
    return False, "unsupported_or_inconsistent_text_signal"


def resolve_image(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def crop_ink_metrics(
    root: Path,
    row: dict[str, Any],
    *,
    threshold: int,
) -> dict[str, float | int]:
    counts: list[int] = []
    areas: list[int] = []
    for image_key, bbox_key in (("image_old", "bbox_old"), ("image_new", "bbox_new")):
        path = resolve_image(root, row.get(image_key))
        bbox = row.get(bbox_key)
        if not path.is_file() or not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"missing or invalid {image_key}/{bbox_key}")
        x1, y1, x2, y2 = (int(value) for value in bbox)
        with Image.open(path) as source:
            gray = source.convert("L")
            if not (0 <= x1 < x2 <= gray.width and 0 <= y1 < y2 <= gray.height):
                raise ValueError(f"out-of-bounds {bbox_key}")
            crop = gray.crop((x1, y1, x2, y2))
        histogram = crop.histogram()
        counts.append(sum(histogram[:threshold]))
        areas.append(max(1, crop.width * crop.height))

    maximum_ink = max(counts)
    minimum_ink = min(counts)
    return {
        "old_ink_pixels": counts[0],
        "new_ink_pixels": counts[1],
        "maximum_ink_ratio": max(count / area for count, area in zip(counts, areas)),
        "ink_asymmetry": abs(counts[0] - counts[1]) / max(1, maximum_ink),
        "minimum_ink_pixels": minimum_ink,
    }


def filter_rows(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    ink_threshold: int = 180,
    min_symbol_asymmetry: float = 0.9,
    min_symbol_ink_ratio: float = 0.01,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    dispositions: Counter[str] = Counter()

    for source_row in rows:
        row = dict(source_row)
        category = str(row.get("change_type") or "").strip()
        keep = False
        reason = ""
        metrics: dict[str, float | int] = {}

        if category in {"addition+text", "deletion+text", "text"}:
            keep, reason = semantic_text_signal(row)
        elif category in {"symbol", "addition", "deletion"}:
            try:
                metrics = crop_ink_metrics(root, row, threshold=ink_threshold)
                keep = (
                    float(metrics["ink_asymmetry"]) >= min_symbol_asymmetry
                    and float(metrics["maximum_ink_ratio"]) >= min_symbol_ink_ratio
                )
                reason = (
                    "one_sided_graphic_signal"
                    if keep
                    else "weak_or_two_sided_graphic_signal"
                )
            except (OSError, TypeError, ValueError) as exc:
                reason = f"invalid_graphic_evidence:{exc}"
        else:
            reason = "unsupported_change_type"

        row["review_signal_filter"] = {
            "decision": "keep" if keep else "hold",
            "reason": reason,
            "ink_threshold": ink_threshold,
            "min_symbol_asymmetry": min_symbol_asymmetry,
            "min_symbol_ink_ratio": min_symbol_ink_ratio,
            **metrics,
        }
        row["safe_to_merge_gold"] = False
        dispositions[reason] += 1
        if keep:
            row["machine_qa_status"] = "review_signal_passing"
            row["review_status"] = "needs_review"
            passing.append(row)
        else:
            row["machine_qa_status"] = "machine_held_low_review_signal"
            row["review_status"] = "machine_held"
            held.append(row)

    report = {
        "input_rows": len(rows),
        "passing_rows": len(passing),
        "held_rows": len(held),
        "passing_by_change_type": dict(sorted(Counter(row.get("change_type", "") for row in passing).items())),
        "held_by_change_type": dict(sorted(Counter(row.get("change_type", "") for row in held).items())),
        "dispositions": dict(sorted(dispositions.items())),
        "policy": {
            "ink_threshold": ink_threshold,
            "min_symbol_asymmetry": min_symbol_asymmetry,
            "min_symbol_ink_ratio": min_symbol_ink_ratio,
            "human_review_required": True,
            "safe_to_merge_gold": False,
        },
    }
    return passing, held, report


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Review Signal Filter",
        "",
        f"- Input rows: `{report['input_rows']}`",
        f"- Passing rows: `{report['passing_rows']}`",
        f"- Held rows: `{report['held_rows']}`",
        "- Gold rows modified: `0`",
        "- Human review is still required: `yes`",
        "",
        "## Passing Categories",
        "",
    ]
    for name, count in report["passing_by_change_type"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(["", "## Dispositions", ""])
    for name, count in report["dispositions"].items():
        lines.append(f"- `{name}`: `{count}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--ink-threshold", type=int, default=180)
    parser.add_argument("--min-symbol-asymmetry", type=float, default=0.9)
    parser.add_argument("--min-symbol-ink-ratio", type=float, default=0.01)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    rows = read_jsonl(input_path)
    passing, held, report = filter_rows(
        root,
        rows,
        ink_threshold=max(1, min(255, args.ink_threshold)),
        min_symbol_asymmetry=max(0.0, min(1.0, args.min_symbol_asymmetry)),
        min_symbol_ink_ratio=max(0.0, min(1.0, args.min_symbol_ink_ratio)),
    )
    report["input"] = input_path.relative_to(root).as_posix()
    report["input_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()

    output = args.output if args.output.is_absolute() else root / args.output
    hold_output = args.hold_output if args.hold_output.is_absolute() else root / args.hold_output
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_jsonl(output, passing)
    write_jsonl(hold_output, held)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"passing_rows": len(passing), "held_rows": len(held)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
