#!/usr/bin/env python3
"""Build a compact Markdown table from Eng_Bench baseline reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation_registry import baseline_registry


IGNORED_NAME_MARKERS = ("oracle", "smoke")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metric(payload: dict[str, Any], *keys: str) -> float | None:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return float(current) if isinstance(current, (int, float)) else None


def format_value(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}"


def report_row(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    return {
        "model": payload.get("model_name") or path.name.replace("_report.json", ""),
        "task": payload.get("task") or "all",
        "split": payload.get("split") or "all",
        "rows": int(payload.get("rows_scored") or 0),
        "missing": int(payload.get("missing_predictions") or 0),
        "micro_norm_em": metric(payload, "microtext", "normalized_exact_match"),
        "micro_cer": metric(payload, "microtext", "character_error_rate"),
        "micro_iou_0_5": metric(payload, "microtext", "evidence_iou_0_5"),
        "vdiff_iou_0_3": metric(payload, "visualdiff", "evidence_recall_iou_0_3"),
        "vdiff_iou_0_5": metric(payload, "visualdiff", "evidence_recall_iou_0_5"),
        "vdiff_side_hit": metric(payload, "visualdiff", "old_new_side_hit_rate"),
        "description_f1": metric(payload, "visualdiff", "normalized_description_f1"),
        "path": str(path),
    }


def collect_reports(root: str | Path) -> list[dict[str, Any]]:
    root = Path(root)
    rows = []
    for item in baseline_registry(root)["counted"]:
        path = root / item["report_path"]
        rows.append(report_row(path))
    return rows


def render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Eng_Bench Baseline Table",
        "",
        "| Model | Task | Split | Rows | Missing | Norm EM | CER | Micro IoU@0.5 | VDiff Recall@0.3 | VDiff Recall@0.5 | Side Hit | Desc F1 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(rows, key=lambda item: (item["task"], item["model"], item["split"])):
        lines.append(
            f"| {row['model']} | {row['task']} | {row['split']} | "
            f"{row['rows']} | {row['missing']} | "
            f"{format_value(row['micro_norm_em'])} | {format_value(row['micro_cer'])} | "
            f"{format_value(row['micro_iou_0_5'])} | {format_value(row['vdiff_iou_0_3'])} | "
            f"{format_value(row['vdiff_iou_0_5'])} | {format_value(row['vdiff_side_hit'])} | "
            f"{format_value(row['description_f1'])} |"
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- Rows with fewer than 100 examples are diagnostic, not headline.",
            "- Smoke and oracle reports are intentionally excluded from this table.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Eng_Bench baseline Markdown table.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--output",
        default="results/baselines/BASELINE_TABLE.md",
        help="Markdown output path",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = collect_reports(root)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_table(rows), encoding="utf-8")
    print(f"[OK] Wrote {output}")
    print(f"[OK] Baseline reports: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
