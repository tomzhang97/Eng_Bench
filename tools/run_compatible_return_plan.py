#!/usr/bin/env python3
"""Run or dry-run a verified compatible-return processing plan."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from tools import verify_compatible_return_plan
except ImportError:  # pragma: no cover - script execution path
    import verify_compatible_return_plan


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_parts(command: str) -> list[str]:
    parts = verify_compatible_return_plan.parse_command(command)
    return [part.strip('"') for part in parts]


def run_step(root: Path, step: dict[str, Any]) -> dict[str, Any]:
    command = str(step.get("command") or "")
    result = subprocess.run(
        command_parts(command),
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "packet": step.get("packet", ""),
        "kind": step.get("kind", ""),
        "command": command,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def run_plan(
    root: Path,
    plan_path: Path,
    *,
    execute: bool,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    verification = verify_compatible_return_plan.verify_plan(root, plan_path)
    if not verification["valid"]:
        return {
            "plan_path": plan_path.as_posix(),
            "valid": False,
            "executed": False,
            "verification": verification,
            "steps": [],
        }

    plan = load_json(plan_path)
    step_reports: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not execute:
            step_reports.append(
                {
                    "packet": step.get("packet", ""),
                    "kind": step.get("kind", ""),
                    "command": step.get("command", ""),
                    "status": "planned",
                    "returncode": None,
                }
            )
            continue
        report = run_step(root, step)
        step_reports.append(report)
        if report["returncode"] != 0 and not continue_on_error:
            break

    return {
        "plan_path": plan_path.as_posix(),
        "valid": True,
        "executed": execute,
        "complete": all(step.get("returncode") in (None, 0) for step in step_reports),
        "verification": verification,
        "steps": step_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--execute", action="store_true", help="Actually run commands. Default is dry-run.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    report = run_plan(
        root,
        plan_path,
        execute=args.execute,
        continue_on_error=args.continue_on_error,
    )
    if args.output_json:
        write_json(Path(args.output_json), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["valid"]:
        return 1
    if args.execute and not report.get("complete"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
