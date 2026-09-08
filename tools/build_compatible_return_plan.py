#!/usr/bin/env python3
"""Build maintainer processing commands for a returned compatibility handoff.

This is a planning/preflight tool only. It does not stage returned rows, merge
gold, or rewrite annotations.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


V1_5_CHECKLISTS = {
    "microtext_caltrans_bridge_standard_details_validation_checklist.csv",
    "microtext_mechanical_drawing_faunce_validation_checklist.csv",
    "microtext_mechanical_drawing_reid_validation_checklist.csv",
    "microtext_v1_5_first20_validation_checklist.csv",
    "visualdiff_rusefi_hellen121vag_validation_checklist.csv",
    "visualdiff_train_description_rewrite_checklist.csv",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def display_path(path: Path) -> str:
    return str(path).replace("/", "\\")


def relative_to_or_abs(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def quote(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def output_dir(return_label: str, packet: str) -> str:
    return f"derived\\human_adjudication\\processed_returns\\{return_label}\\{packet}"


def checklist_paths(packet_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in packet_dir.rglob("*checklist*.csv")
        if path.name.lower() not in {"checklist_index.csv"}
    )


def csv_ids(path: Path, key: str) -> list[str]:
    return [str(row.get(key) or "").strip() for row in read_csv(path) if str(row.get(key) or "").strip()]


def is_duplicate_visualdiff_train_checklist(path: Path, current_dir: Path) -> bool:
    duplicate_name = Path("02_visualdiff_train_pending_descriptions") / "validation_checklist.csv"
    try:
        if path.relative_to(current_dir) != duplicate_name:
            return False
    except ValueError:
        return False
    canonical = current_dir / "visualdiff_train_description_rewrite_checklist.csv"
    if not canonical.exists():
        return False
    candidate_ids = csv_ids(path, "pair_id")
    canonical_ids = csv_ids(canonical, "pair_id")
    return bool(candidate_ids) and candidate_ids == canonical_ids


def auxiliary_microtext_region_source(path: Path) -> str:
    if path.name != "microtext_region_proposals_validation_checklist.csv":
        return ""
    if path.parent.name != "microtext_region_proposals_2026-06-03":
        return ""
    return "microtext\\annotations\\microtext_review_region_proposals_2026-06-03.jsonl"


def auxiliary_apply_step(
    handoff_root: Path,
    path: Path,
    packet: str,
    return_label: str,
) -> dict[str, str] | None:
    source_jsonl = auxiliary_microtext_region_source(path)
    if not source_jsonl:
        return None
    stem = path.parent.name
    out_dir = output_dir(return_label, f"{packet}_aux")
    output = f"{out_dir}\\{stem}_reviewed.jsonl"
    summary = f"{out_dir}\\{stem}_summary.json"
    return {
        "packet": packet,
        "kind": "auxiliary_microtext_apply",
        "command": (
            "python tools\\microtext_review_checklist.py apply --root . "
            f"--review-jsonl {quote(source_jsonl)} "
            f"--checklist {quote(display_path(path))} "
            f"--output {quote(output)} --summary {quote(summary)} --strict"
        ),
    }


def packet_plan(root: Path, handoff_root: Path, row: dict[str, str], return_label: str) -> dict[str, Any]:
    packet = str(row.get("packet") or Path(row.get("folder") or "").name or "packet").strip()
    rel_folder = normalize_rel(row.get("folder") or row.get("assigned_path") or "")
    packet_dir = handoff_root / rel_folder if rel_folder else handoff_root / "01_packets" / packet
    paths = checklist_paths(packet_dir) if packet_dir.exists() else []
    rel_checklists = [relative_to_or_abs(path, handoff_root) for path in paths]
    steps: list[dict[str, str]] = []
    issues: list[str] = []
    processor = "manual_or_future_processing"
    covered: set[str] = set()
    duplicates: list[str] = []
    auxiliary_steps: list[dict[str, str]] = []

    current_dir = packet_dir / "current"
    if current_dir.exists() and any((current_dir / name).exists() for name in V1_5_CHECKLISTS):
        processor = "process_v1_5_human_return"
        covered.update(
            relative_to_or_abs(current_dir / name, handoff_root)
            for name in V1_5_CHECKLISTS
            if (current_dir / name).exists()
        )
        packet_root_arg = display_path(current_dir)
        out_dir = output_dir(return_label, f"{packet}_v1_5")
        steps.append(
            {
                "packet": packet,
                "kind": "process_v1_5",
                "command": (
                    "python tools\\process_v1_5_human_return.py --root . "
                    f"--packet-root {quote(packet_root_arg)} --output-dir {quote(out_dir)} --strict"
                ),
            }
        )

    for path in paths:
        rel = relative_to_or_abs(path, handoff_root)
        if is_duplicate_visualdiff_train_checklist(path, current_dir):
            covered.add(rel)
            duplicates.append(rel)
            continue
        aux_step = auxiliary_apply_step(handoff_root, path, packet, return_label)
        if aux_step:
            covered.add(rel)
            auxiliary_steps.append(aux_step)

    if auxiliary_steps:
        processor = "auxiliary_checklist_apply" if not steps else f"{processor}+auxiliary_checklist_apply"
        steps.extend(auxiliary_steps)

    if (packet_dir / "NEXT_REVIEW_BATCH_MANIFEST.csv").exists():
        processor = "process_next_review_batch_return" if not steps else f"{processor}+process_next_review_batch_return"
        covered.update(rel_checklists)
        batch_root_arg = display_path(packet_dir)
        out_dir = output_dir(return_label, packet)
        steps.append(
            {
                "packet": packet,
                "kind": "process_next_review_batch",
                "command": (
                    "python tools\\process_next_review_batch_return.py --root . "
                    f"--batch-root {quote(batch_root_arg)} --output-dir {quote(out_dir)} --strict"
                ),
            }
        )

    uncovered = [path for path in rel_checklists if path not in covered]
    if uncovered:
        issues.append(f"uncovered checklist files: {len(uncovered)}")
    if not packet_dir.exists():
        issues.append("packet folder missing")
    if not steps and not uncovered:
        issues.append("no processable checklists detected")

    return {
        "packet": packet,
        "packet_id": row.get("packet_id", ""),
        "priority": row.get("priority", ""),
        "folder": rel_folder,
        "review_rows": row.get("review_rows", ""),
        "processor": processor,
        "checklists": rel_checklists,
        "covered_checklists": sorted(covered),
        "duplicate_checklists": duplicates,
        "uncovered_checklists": uncovered,
        "steps": steps,
        "issues": issues,
    }


def agreement_steps(handoff_root: Path, return_label: str) -> list[dict[str, str]]:
    agreement_dir = handoff_root / "02_agreement"
    if not agreement_dir.exists():
        return []
    out_base = f"derived\\human_adjudication\\processed_returns\\{return_label}\\agreement_audit"
    out_json = f"{out_base}\\verification.json"
    out_md = f"{out_base}\\verification.md"
    agreement_json = f"{out_base}\\agreement_report.json"
    agreement_md = f"{out_base}\\agreement_report.md"
    adjudication_csv = f"{out_base}\\adjudication_queue.csv"
    adjudication_md = f"{out_base}\\adjudication_queue.md"
    return [
        {
            "packet": "agreement",
            "kind": "verify_agreement_audit",
            "command": (
                "python tools\\verify_agreement_audit_packet.py "
                f"--packet-dir {quote(display_path(agreement_dir))} "
                f"--output-json {quote(out_json)} "
                f"--output-md {quote(out_md)}"
            ),
        },
        {
            "packet": "agreement",
            "kind": "agreement_audit",
            "command": (
                "python tools\\agreement_audit.py "
                f"--reference {quote(display_path(agreement_dir / 'sample_reference.csv'))} "
                f"--reviewer-a {quote(display_path(agreement_dir / 'reviewer_a_checklist.csv'))} "
                f"--reviewer-b {quote(display_path(agreement_dir / 'reviewer_b_checklist.csv'))} "
                f"--output-json {quote(agreement_json)} --output-md {quote(agreement_md)} "
                f"--adjudication-csv {quote(adjudication_csv)} "
                f"--adjudication-md {quote(adjudication_md)}"
            ),
        },
    ]


def build_plan(root: Path, handoff_root: Path, return_label: str) -> dict[str, Any]:
    worklist_path = handoff_root / "PACKET_WORKLIST.csv"
    worklist = read_csv(worklist_path)
    packets = [packet_plan(root, handoff_root, row, return_label) for row in worklist]
    preflight_json = f"derived\\quality\\returned_handoff_preflight_{return_label}.json"
    preflight_md = f"derived\\quality\\returned_handoff_preflight_{return_label}.md"
    steps = [
        {
            "packet": "all",
            "kind": "preflight",
            "command": (
                "python tools\\review_packet_status.py "
                f"--handoff-root {quote(display_path(handoff_root))} "
                f"--output-json {quote(preflight_json)} --output-md {quote(preflight_md)}"
            ),
        }
    ]
    for packet in packets:
        steps.extend(packet["steps"])
    steps.extend(agreement_steps(handoff_root, return_label))

    totals: Counter[str] = Counter()
    for packet in packets:
        totals["packets"] += 1
        totals["processable_packets"] += int(bool(packet["steps"]))
        totals["uncovered_checklists"] += len(packet["uncovered_checklists"])
        totals["packet_issues"] += len(packet["issues"])
        totals["checklists"] += len(packet["checklists"])
        totals["duplicate_checklists"] += len(packet["duplicate_checklists"])
        totals["auxiliary_steps"] += sum(1 for step in packet["steps"] if step["kind"].startswith("auxiliary_"))
    totals["steps"] = len(steps)

    return {
        "handoff_root": handoff_root.as_posix(),
        "return_label": return_label,
        "worklist": worklist_path.as_posix(),
        "totals": dict(sorted(totals.items())),
        "steps": steps,
        "packets": packets,
        "notes": [
            "Run the preflight step first. Do not run merge tools until blank rows are resolved or intentionally quarantined.",
            "Strict processing commands may return nonzero for incomplete packets; that is expected before human review is complete.",
            "Uncovered checklist files require a dedicated processor or manual maintainer decision before any gold merge.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals", {})
    lines = [
        "# Compatible Return Processing Plan",
        "",
        f"- Handoff root: `{report['handoff_root']}`",
        f"- Return label: `{report['return_label']}`",
        f"- Packets: `{totals.get('packets', 0)}`",
        f"- Processable packets: `{totals.get('processable_packets', 0)}`",
        f"- Uncovered checklists: `{totals.get('uncovered_checklists', 0)}`",
        "",
        "## Commands",
        "",
    ]
    for index, step in enumerate(report.get("steps", []), start=1):
        lines.extend([f"{index}. `{step['kind']}` for `{step['packet']}`", "", "```powershell", step["command"], "```", ""])
    lines.extend(["## Packet Coverage", "", "| Packet | Processor | Checklists | Uncovered | Issues |", "| --- | --- | ---: | ---: | --- |"])
    for packet in report.get("packets", []):
        lines.append(
            f"| `{packet['packet']}` | `{packet['processor']}` | {len(packet['checklists'])} | "
            f"{len(packet['uncovered_checklists'])} | {'; '.join(packet['issues']) or '-'} |"
        )
    if any(packet.get("uncovered_checklists") for packet in report.get("packets", [])):
        lines.extend(["", "## Uncovered Checklists", ""])
        for packet in report["packets"]:
            for path in packet.get("uncovered_checklists", []):
                lines.append(f"- `{packet['packet']}`: `{path}`")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report.get("notes", []))
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a processing plan for a returned compatibility handoff.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--handoff-root", required=True)
    parser.add_argument("--return-label", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    handoff_root = Path(args.handoff_root)
    if not handoff_root.is_absolute():
        handoff_root = root / handoff_root
    report = build_plan(root=root, handoff_root=handoff_root, return_label=args.return_label)
    if args.output_json:
        write_json(Path(args.output_json), report)
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
