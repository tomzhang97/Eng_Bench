#!/usr/bin/env python3
"""Build a maintainer control sheet for the current human handoff."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "zip",
        "assigned_paths",
        "mb",
        "entries",
        "crc_ok",
        "windows_expand_archive",
        "sha256",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_to_or_abs(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def safe_return_commands() -> list[dict[str, str]]:
    plan_json = "derived\\quality\\compatible_return_processing_plan_<return_label>.json"
    plan_md = "derived\\quality\\compatible_return_processing_plan_<return_label>.md"
    verify_json = "derived\\quality\\compatible_return_processing_plan_verification_<return_label>.json"
    dry_run_json = "derived\\quality\\compatible_return_processing_plan_dry_run_<return_label>.json"
    return [
        {
            "kind": "build_return_plan_for_returned_folder",
            "command": (
                "python tools\\build_compatible_return_plan.py --root . "
                "--handoff-root \"<returned_handoff_root>\" --return-label \"<return_label>\" "
                f"--output-json \"{plan_json}\" --output-md \"{plan_md}\""
            ),
        },
        {
            "kind": "verify_return_plan",
            "command": (
                "python tools\\verify_compatible_return_plan.py --root . "
                f"--plan \"{plan_json}\" --output-json \"{verify_json}\""
            ),
        },
        {
            "kind": "dry_run_verified_return_plan",
            "command": (
                "python tools\\run_compatible_return_plan.py --root . "
                f"--plan \"{plan_json}\" --output-json \"{dry_run_json}\""
            ),
        },
    ]


def build_report(
    *,
    root: Path,
    compatible_report: Path,
    split_report: Path,
    return_plan: Path,
) -> dict[str, Any]:
    compatible_report = resolve(root, compatible_report)
    split_report = resolve(root, split_report)
    return_plan = resolve(root, return_plan)
    compatible = load_json(compatible_report)
    split = load_json(split_report)
    plan = load_json(return_plan)
    handoff_dir = compatible.get("handoff_dir") if isinstance(compatible.get("handoff_dir"), dict) else {}
    split_dir = compatible.get("split_dir") if isinstance(compatible.get("split_dir"), dict) else {}
    totals = handoff_dir.get("totals") if isinstance(handoff_dir.get("totals"), dict) else {}
    plan_totals = plan.get("totals") if isinstance(plan.get("totals"), dict) else {}
    zips = split.get("zips") if isinstance(split.get("zips"), list) else []
    commands = [
        {
            "kind": str(step.get("kind") or ""),
            "packet": str(step.get("packet") or ""),
            "command": str(step.get("command") or ""),
        }
        for step in plan.get("steps", [])
        if isinstance(step, dict)
    ]
    ready_to_send = (
        bool(handoff_dir.get("valid"))
        and bool(split.get("all_crc_ok"))
        and bool(split.get("all_windows_expand_archive_ok"))
        and int(plan_totals.get("uncovered_checklists") or 0) == 0
    )
    return {
        "ready_to_send": ready_to_send,
        "handoff_folder": str(handoff_dir.get("path") or compatible.get("handoff_folder") or ""),
        "compatible_report": relative_to_or_abs(compatible_report, root),
        "split_report": relative_to_or_abs(split_report, root),
        "return_plan": relative_to_or_abs(return_plan, root),
        "review_rows": int(handoff_dir.get("worklist_review_rows") or compatible.get("review_rows") or 0),
        "packet_count": int(handoff_dir.get("packet_folders") or compatible.get("packet_folders") or 0),
        "checklist_rows": int(totals.get("checklist_rows") or 0),
        "png_files": int(totals.get("png_files") or 0),
        "split_zip_count": int(split.get("zip_count") or split_dir.get("zip_count") or len(zips)),
        "return_plan_steps": int(plan_totals.get("steps") or len(commands)),
        "return_plan_packets": int(plan_totals.get("packets") or 0),
        "return_plan_processable_packets": int(plan_totals.get("processable_packets") or 0),
        "return_plan_uncovered_checklists": int(plan_totals.get("uncovered_checklists") or 0),
        "zips": zips,
        "returned_handoff_root_placeholder": "<returned_handoff_root>",
        "return_label_placeholder": "<return_label>",
        "safe_return_commands": safe_return_commands(),
        "next_return_commands": commands,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Current Human Handoff Control Sheet",
        "",
        f"- Ready to send: `{str(report['ready_to_send']).lower()}`",
        f"- Handoff folder: `{report['handoff_folder']}`",
        f"- Verified review rows: `{report['review_rows']}`",
        f"- Packets: `{report['packet_count']}`",
        f"- Split ZIPs: `{report['split_zip_count']}`",
        f"- Checklist rows: `{report['checklist_rows']}`",
        f"- PNG evidence files: `{report['png_files']}`",
        f"- Return-plan steps: `{report['return_plan_steps']}`",
        f"- Return-plan uncovered checklists: `{report['return_plan_uncovered_checklists']}`",
        "",
        "## Send These Files",
        "",
        "| ZIP | Assigned Paths | MB | Entries | CRC | Expand-Archive | SHA256 |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in report["zips"]:
        lines.append(
            f"| `{row.get('zip', '')}` | `{row.get('assigned_paths', '')}` | "
            f"{row.get('mb', '')} | {row.get('entries', '')} | "
            f"`{row.get('crc_ok', '')}` | `{row.get('windows_expand_archive', '')}` | "
            f"`{row.get('sha256', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Return Processing",
            "",
            "After the intern returns completed CSVs, do not process the original unfilled handoff folder. Copy or unpack the returned package, then replace `<returned_handoff_root>` with that returned `EB_HV_0615` folder path and replace `<return_label>` with the return date or batch label.",
            "",
            "The plan is staging/preflight only; it does not merge gold rows.",
            "",
        ]
    )
    for index, step in enumerate(report["safe_return_commands"], start=1):
        lines.extend(
            [
                f"{index}. `{step['kind']}`",
                "",
                "```powershell",
                step["command"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Reference Plan Commands",
            "",
            "Do not run the reference commands on the unfilled handoff folder after the intern returns work. They are included only to show the covered processors from the current verified plan.",
            "",
            f"- Reference return plan: `{report['return_plan']}`",
            "",
        ]
    )
    for index, step in enumerate(report["next_return_commands"], start=1):
        lines.extend(
            [
                f"{index}. `{step['kind']}` for `{step['packet']}`",
                "",
                "```powershell",
                step["command"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Source Reports",
            "",
            f"- Compatible handoff verification: `{report['compatible_report']}`",
            f"- Split ZIP verification: `{report['split_report']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--compatible-report", required=True)
    parser.add_argument("--split-report", required=True)
    parser.add_argument("--return-plan", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root=root,
        compatible_report=Path(args.compatible_report),
        split_report=Path(args.split_report),
        return_plan=Path(args.return_plan),
    )
    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(Path(args.output_csv), report["zips"])
    print(json.dumps({key: report[key] for key in ("ready_to_send", "review_rows", "packet_count", "split_zip_count", "return_plan_steps")}, indent=2, sort_keys=True))
    return 0 if report["ready_to_send"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
