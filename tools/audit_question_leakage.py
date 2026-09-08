#!/usr/bin/env python3
"""Audit whether benchmark questions accidentally reveal their answers."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


VERSION_TOKEN_RE = re.compile(r"\b(?:v|rev|pcb)?\d+(?:\.\d+)*[a-z]?\b", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def compact(text: str) -> str:
    return NON_ALNUM_RE.sub("", text.lower())


def tokens(text: str) -> list[str]:
    return [token for token in NON_ALNUM_RE.split(text.lower()) if token]


def visualdiff_answer_phrase(answer: str) -> str:
    answer = VERSION_TOKEN_RE.sub("", answer)
    answer = re.sub(r"\b(?:from|to|between|old|new|highlighted|region|changed|change)\b", " ", answer, flags=re.I)
    return compact(answer)


def row_leaks_answer(row: dict[str, Any]) -> bool:
    question = str(row.get("question", ""))
    answer = str(row.get("answer", ""))
    if not question or not answer:
        return False
    q_norm = compact(question)
    if row.get("task") == "microtext":
        answer_norm = compact(answer)
        if len(answer_norm) <= 2:
            return answer_norm in tokens(question)
        return len(answer_norm) >= 3 and answer_norm in q_norm
    phrase = visualdiff_answer_phrase(answer)
    return len(phrase) >= 12 and phrase in q_norm


def audit(rows: list[dict[str, Any]], sample_limit: int = 50) -> dict[str, Any]:
    leaks = []
    by_task: dict[str, int] = {}
    for row in rows:
        task = str(row.get("task", "unknown"))
        if row_leaks_answer(row):
            by_task[task] = by_task.get(task, 0) + 1
            if len(leaks) < sample_limit:
                leaks.append(
                    {
                        "id": row.get("id") or row.get("question_id"),
                        "task": task,
                        "split": row.get("split"),
                        "question": row.get("question"),
                        "answer": row.get("answer"),
                    }
                )
    return {
        "total_rows": len(rows),
        "leak_count": sum(by_task.values()),
        "critical_failures": sum(by_task.values()),
        "by_task": dict(sorted(by_task.items())),
        "samples": leaks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Question Answer-Leakage Audit",
        "",
        f"- Total rows: {report['total_rows']}",
        f"- Leak count: {report['leak_count']}",
        f"- Critical failures: {report['critical_failures']}",
        f"- By task: {report['by_task']}",
        "",
        "## Samples",
        "",
    ]
    if not report["samples"]:
        lines.append("- None")
    else:
        for sample in report["samples"]:
            lines.append(
                f"- `{sample['id']}` ({sample['task']}/{sample['split']}): "
                f"question=`{sample['question']}` answer=`{sample['answer']}`"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit question text for accidental answer leakage.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl")
    parser.add_argument("--output-json", default="derived/quality/question_leakage_audit.json")
    parser.add_argument("--output-md", default="derived/quality/question_leakage_audit.md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = audit(load_jsonl(root / args.input))
    output_json = root / args.output_json
    output_md = root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    if report["critical_failures"]:
        print(f"[ERR] Question answer leakage rows: {report['critical_failures']}")
    return 0 if report["critical_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
