#!/usr/bin/env python3
"""Report Eng_Bench question-template diversity."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def normalize_template(question: Any) -> str:
    text = str(question or "").strip().lower()
    text = re.sub(r"pcbv?\d+(?:\.\d+)?", "{version}", text)
    text = re.sub(r"v\d+(?:\.\d+)?", "{version}", text)
    text = re.sub(r"\d+(?:\.\d+)?", "{num}", text)
    text = re.sub(r"\s+", " ", text)
    return text


def summarize_task(rows: list[dict[str, Any]], max_template_share: float) -> dict[str, Any]:
    counts = Counter(normalize_template(row.get("question")) for row in rows)
    total = len(rows)
    most_common = counts.most_common(10)
    max_count = most_common[0][1] if most_common else 0
    max_share = max_count / total if total else 0.0
    return {
        "rows": total,
        "unique_templates": len(counts),
        "max_template_count": max_count,
        "max_template_share": max_share,
        "passes_max_share": max_share <= max_template_share if total else True,
        "top_templates": [
            {"template": template, "count": count, "share": count / total if total else 0.0}
            for template, count in most_common
        ],
    }


def compute_diversity(
    rows: list[dict[str, Any]],
    max_template_share: float = 0.2,
) -> dict[str, Any]:
    by_task_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task_rows[str(row.get("task") or "unknown")].append(row)
    by_task = {
        task: summarize_task(task_rows, max_template_share)
        for task, task_rows in sorted(by_task_rows.items())
    }
    return {
        "total_rows": len(rows),
        "max_template_share_threshold": max_template_share,
        "passes": all(summary["passes_max_share"] for summary in by_task.values()),
        "by_task": by_task,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Eng_Bench Question Diversity Report",
        "",
        f"- Total rows: {report['total_rows']}",
        f"- Max-template-share threshold: {report['max_template_share_threshold']:.2f}",
        f"- Gate passed: {report['passes']}",
        "",
        "| Task | Rows | Unique Templates | Max Count | Max Share | Pass |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for task, summary in report["by_task"].items():
        lines.append(
            f"| {task} | {summary['rows']} | {summary['unique_templates']} | "
            f"{summary['max_template_count']} | {summary['max_template_share']:.4f} | "
            f"{summary['passes_max_share']} |"
        )
    for task, summary in report["by_task"].items():
        lines.extend(["", f"## Top Templates: {task}", ""])
        for row in summary["top_templates"]:
            lines.append(f"- `{row['template']}`: {row['count']} ({row['share']:.2%})")
    lines.append("")
    return "\n".join(lines)


def write_outputs(root: Path, report: dict[str, Any], output_json: str, output_md: str) -> None:
    json_path = root / output_json
    md_path = root / output_md
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build question diversity report.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl")
    parser.add_argument("--max-template-share", type=float, default=0.2)
    parser.add_argument("--output-json", default="derived/quality/question_diversity_report.json")
    parser.add_argument("--output-md", default="derived/quality/question_diversity_report.md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = compute_diversity(
        load_jsonl(root / args.input),
        max_template_share=args.max_template_share,
    )
    write_outputs(root, report, args.output_json, args.output_md)
    print(f"[OK] Wrote {root / args.output_json}")
    print(f"[OK] Wrote {root / args.output_md}")
    print(f"[OK] Gate passed: {report['passes']}")
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
