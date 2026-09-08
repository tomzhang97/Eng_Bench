#!/usr/bin/env python3
"""Verify a compatible-return processing plan before any command is run."""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any


BLOCKED_TOOL_NAMES = {
    "microtext_merge.py",
    "maap_merge.py",
    "unify_dataset.py",
    "apply_split_policy.py",
}
INPUT_FLAGS = {
    "--handoff-root",
    "--packet-root",
    "--batch-root",
    "--packet-dir",
    "--checklist",
    "--review-jsonl",
    "--reference",
    "--reviewer-a",
    "--reviewer-b",
}
OUTPUT_FLAGS = {
    "--output-json",
    "--output-md",
    "--output-dir",
    "--output",
    "--summary",
    "--adjudication-csv",
    "--adjudication-md",
}
ALLOWED_OUTPUT_PREFIXES = (
    "derived/quality/",
    "derived/human_adjudication/processed_returns/",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def is_under_allowed_output(value: str) -> bool:
    rel = normalize(value)
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in ALLOWED_OUTPUT_PREFIXES)


def parse_command(command: str) -> list[str]:
    return shlex.split(command, posix=False)


def flag_values(parts: list[str], flags: set[str]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part in flags and index + 1 < len(parts):
            values.append((part, parts[index + 1].strip('"')))
            index += 2
            continue
        index += 1
    return values


def verify_step(root: Path, step: dict[str, Any], index: int) -> tuple[list[str], dict[str, int]]:
    issues: list[str] = []
    counts = {"checked_input_paths": 0, "checked_output_paths": 0}
    command = str(step.get("command") or "").strip()
    if not command:
        return [f"step {index}: missing command"], counts
    parts = parse_command(command)
    if len(parts) < 2 or parts[0].lower() != "python":
        issues.append(f"step {index}: command must start with python")
        return issues, counts

    tool_path = parts[1].strip('"').replace("\\", "/")
    tool_name = Path(tool_path).name
    if tool_name in BLOCKED_TOOL_NAMES:
        issues.append(f"step {index}: blocked merge-like command {tool_name}")
    if not (root / tool_path).exists():
        issues.append(f"step {index}: missing tool {tool_path}")

    for flag, value in flag_values(parts, INPUT_FLAGS):
        counts["checked_input_paths"] += 1
        if not resolve(root, value).exists():
            issues.append(f"step {index}: missing input for {flag}: {value}")

    for flag, value in flag_values(parts, OUTPUT_FLAGS):
        counts["checked_output_paths"] += 1
        if not is_under_allowed_output(value):
            issues.append(f"step {index}: unsafe output for {flag}: {value}")
    return issues, counts


def verify_plan(root: Path, plan_path: Path) -> dict[str, Any]:
    plan = load_json(plan_path)
    issues: list[str] = []
    totals = {
        "steps": 0,
        "checked_input_paths": 0,
        "checked_output_paths": 0,
    }

    uncovered = int((plan.get("totals") or {}).get("uncovered_checklists") or 0)
    if uncovered:
        issues.append(f"plan has uncovered_checklists={uncovered}")

    for index, step in enumerate(plan.get("steps") or [], start=1):
        totals["steps"] += 1
        step_issues, step_counts = verify_step(root, step, index)
        issues.extend(step_issues)
        totals["checked_input_paths"] += step_counts["checked_input_paths"]
        totals["checked_output_paths"] += step_counts["checked_output_paths"]

    if totals["steps"] == 0:
        issues.append("plan has no steps")

    return {
        "plan_path": plan_path.as_posix(),
        "valid": not issues,
        "issues": issues,
        "totals": totals,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    report = verify_plan(root, plan_path)
    if args.output_json:
        write_json(Path(args.output_json), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
