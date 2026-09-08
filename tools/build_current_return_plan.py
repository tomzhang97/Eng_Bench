#!/usr/bin/env python3
"""Build safe processing commands for the current intern handoff returns."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


PLAN_FIELDS = [
    "step_index",
    "packet_id",
    "checklist_name",
    "pack_name",
    "review_jsonl",
    "returned_checklist",
    "output_jsonl",
    "summary_json",
    "kind",
    "command",
]
ALLOWED_POST_RETURN_GATES = [
    "python tools\\review_packet_status.py --packet-root \"<returned_packet_root>\" --output-json \"derived\\quality\\returned_packet_status_<return_label>.json\" --output-md \"derived\\quality\\returned_packet_status_<return_label>.md\"",
    "python tools\\audit_unmerged_reviewed_rows.py --root .",
    "python tools\\audit_microtext_quality.py --root .",
    "python tools\\audit_question_leakage.py --root .",
    "python splits\\leakage_check.py --root .",
    "python tools\\audit_v2_0_gate.py --root .",
    "python tools\\unify_dataset.py",
    "python tools\\validate_engbench_v2.py --root . --input eng_bench.jsonl --manifest manifest.jsonl --skip-textlayer",
]


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] = PLAN_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def display_path(path: str) -> str:
    return path.replace("/", "\\")


def quote(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def pack_name_from_checklist(path: Path) -> str:
    name = path.name
    suffix = "_checklist.csv"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return path.stem


def checklist_paths(packet_root: Path) -> list[Path]:
    return sorted(path for path in packet_root.glob("*checklist.csv") if path.is_file())


def batch_manifest_path(packet_root: Path) -> Path:
    return packet_root / "NEXT_REVIEW_BATCH_MANIFEST.csv"


def build_batch_return_step(
    *,
    packet_id: str,
    packet_root_rel: str,
    return_label: str,
    returned_root: str,
    step_index: int,
) -> dict[str, Any]:
    returned_batch_root = f"{returned_root}\\{Path(packet_root_rel).name}"
    output_dir = f"derived\\human_adjudication\\processed_returns\\{return_label}\\{packet_id}"
    summary_json = f"{output_dir}\\processing_summary.json"
    command = (
        "python tools\\process_next_review_batch_return.py --root . "
        f"--batch-root {quote(returned_batch_root)} "
        f"--output-dir {quote(output_dir)} --strict"
    )
    return {
        "step_index": step_index,
        "packet_id": packet_id,
        "checklist_name": "NEXT_REVIEW_BATCH_MANIFEST.csv",
        "pack_name": "__batch__",
        "review_jsonl": "",
        "returned_checklist": returned_batch_root,
        "output_jsonl": "",
        "summary_json": summary_json,
        "kind": "process_next_review_batch_return",
        "command": command,
    }


def build_step(
    *,
    root: Path,
    packet_id: str,
    packet_root_rel: str,
    checklist: Path,
    pack_name: str,
    return_label: str,
    returned_root: str,
    step_index: int,
) -> tuple[dict[str, Any] | None, str | None]:
    packet_root = resolve(root, packet_root_rel)
    manifest = packet_root / "review_packs" / pack_name / "manifest.jsonl"
    if not manifest.exists():
        return None, f"{packet_id}: missing manifest for {checklist.name}: {rel(manifest, root)}"
    checklist_rel = rel(checklist, packet_root)
    returned_checklist = f"{returned_root}\\{Path(packet_root_rel).name}\\{display_path(checklist_rel)}"
    output_base = f"derived\\human_adjudication\\processed_returns\\{return_label}\\{packet_id}"
    output_jsonl = f"{output_base}\\{pack_name}_reviewed.jsonl"
    summary_json = f"{output_base}\\{pack_name}_summary.json"
    review_jsonl = display_path(rel(manifest, root))
    command = (
        "python tools\\microtext_review_checklist.py apply --root . "
        f"--review-jsonl {quote(review_jsonl)} "
        f"--checklist {quote(returned_checklist)} "
        f"--output {quote(output_jsonl)} "
        f"--summary {quote(summary_json)} --strict"
    )
    return (
        {
            "step_index": step_index,
            "packet_id": packet_id,
            "checklist_name": checklist.name,
            "pack_name": pack_name,
            "review_jsonl": review_jsonl,
            "returned_checklist": returned_checklist,
            "output_jsonl": output_jsonl,
            "summary_json": summary_json,
            "kind": "microtext_checklist_apply",
            "command": command,
        },
        None,
    )


def packet_plan(
    *,
    root: Path,
    packet: dict[str, Any],
    return_label: str,
    returned_root: str,
    start_index: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    packet_id = str(packet.get("packet_id") or "").strip()
    packet_root_rel = str(packet.get("packet_root") or packet.get("folder_path") or "").strip()
    packet_root = resolve(root, packet_root_rel)
    checklists = checklist_paths(packet_root) if packet_root.exists() else []
    has_batch_manifest = packet_root.exists() and batch_manifest_path(packet_root).exists()
    steps: list[dict[str, Any]] = []
    issues: list[str] = []
    if has_batch_manifest:
        steps.append(
            build_batch_return_step(
                packet_id=packet_id,
                packet_root_rel=packet_root_rel,
                return_label=return_label,
                returned_root=returned_root,
                step_index=start_index,
            )
        )
    for checklist in checklists:
        pack_name = pack_name_from_checklist(checklist)
        step, issue = build_step(
            root=root,
            packet_id=packet_id,
            packet_root_rel=packet_root_rel,
            checklist=checklist,
            pack_name=pack_name,
            return_label=return_label,
            returned_root=returned_root,
            step_index=start_index + len(steps),
        )
        if step:
            steps.append(step)
        if issue:
            issues.append(issue)
    if not packet_root.exists():
        issues.append(f"{packet_id}: missing packet_root {packet_root_rel}")
    return (
        steps,
        issues,
        {
            "packet_id": packet_id,
            "packet_root": packet_root_rel,
            "checklists": len(checklists) + int(has_batch_manifest),
            "processable_checklists": len(steps),
            "uncovered_checklists": max(0, len(checklists) + int(has_batch_manifest) - len(steps)),
            "issues": issues,
        },
    )


def build_report(
    root: str | Path,
    *,
    index_json: str | Path,
    return_label: str | None = None,
    returned_root: str = "<returned_handoff_root>",
) -> dict[str, Any]:
    root = Path(root)
    index_path = resolve(root, index_json)
    index = read_json(index_path)
    label = return_label or f"return_{date.today().isoformat()}"
    packets = index.get("packets") if isinstance(index.get("packets"), list) else []
    all_steps: list[dict[str, Any]] = []
    all_issues: list[str] = []
    packet_rows: list[dict[str, Any]] = []
    skipped_unready_packets = 0
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        if packet.get("ready_to_send") is False:
            skipped_unready_packets += 1
            continue
        steps, issues, packet_row = packet_plan(
            root=root,
            packet=packet,
            return_label=label,
            returned_root=returned_root,
            start_index=len(all_steps) + 1,
        )
        all_steps.extend(steps)
        all_issues.extend(issues)
        packet_rows.append(packet_row)
    totals = Counter()
    totals["packets"] = len(packet_rows)
    totals["checklists"] = sum(int(row["checklists"]) for row in packet_rows)
    totals["processable_checklists"] = len(all_steps)
    totals["uncovered_checklists"] = sum(int(row["uncovered_checklists"]) for row in packet_rows)
    totals["issues"] = len(all_issues)
    totals["skipped_unready_packets"] = skipped_unready_packets
    return {
        "date_label": index.get("date_label", ""),
        "index_json": rel(index_path, root),
        "return_label": label,
        "returned_root": returned_root,
        "totals": dict(sorted(totals.items())),
        "steps": all_steps,
        "packets": packet_rows,
        "issues": all_issues,
        "post_return_gates": ALLOWED_POST_RETURN_GATES,
        "notes": [
            "This is a planning artifact only. It does not apply returned CSVs and does not merge gold.",
            "Replace <returned_handoff_root> with the folder containing the intern-returned packet folders.",
            "Run apply commands into derived/human_adjudication/processed_returns first, then review summaries before any merge.",
            "Do not merge returned rows into gold until provenance, leakage, validator, and benchmark health gates pass.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Current Return Processing Plan",
        "",
        f"- Source index: `{report['index_json']}`",
        f"- Return label: `{report['return_label']}`",
        f"- Returned root placeholder: `{report['returned_root']}`",
        f"- Packets: `{totals.get('packets', 0)}`",
        f"- Checklists: `{totals.get('checklists', 0)}`",
        f"- Processable checklists: `{totals.get('processable_checklists', 0)}`",
        f"- Uncovered checklists: `{totals.get('uncovered_checklists', 0)}`",
        f"- Issues: `{totals.get('issues', 0)}`",
        "- Plan only: no gold merge.",
        "",
        "## Apply Commands",
        "",
    ]
    for step in report["steps"]:
        lines.extend(
            [
                f"{step['step_index']}. `{step['packet_id']}` `{step['checklist_name']}`",
                "",
                "```powershell",
                step["command"],
                "```",
                "",
            ]
        )
    lines.extend(["## Packet Coverage", "", "| Packet | Checklists | Processable | Uncovered | Issues |", "| --- | ---: | ---: | ---: | --- |"])
    for packet in report["packets"]:
        lines.append(
            f"| `{packet['packet_id']}` | {packet['checklists']} | {packet['processable_checklists']} | "
            f"{packet['uncovered_checklists']} | {'; '.join(packet['issues']) or '-'} |"
        )
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.extend(["", "## Post-Return Gates", ""])
    for command in report["post_return_gates"]:
        lines.extend(["```powershell", command, "```", ""])
    lines.extend(["## Notes", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a current return processing plan.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--index-json", default="derived/quality/current_handoff_index_2026-06-16.json")
    parser.add_argument("--return-label", default=f"return_{date.today().isoformat()}")
    parser.add_argument("--returned-root", default="<returned_handoff_root>")
    parser.add_argument("--output-json", default="derived/quality/current_return_processing_plan_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/current_return_processing_plan_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/current_return_processing_plan_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        index_json=args.index_json,
        return_label=args.return_label,
        returned_root=args.returned_root,
    )
    output_json = resolve(root, args.output_json)
    output_md = resolve(root, args.output_md)
    output_csv = resolve(root, args.output_csv)
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["steps"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0 if report["totals"].get("uncovered_checklists", 0) == 0 and not report["issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
