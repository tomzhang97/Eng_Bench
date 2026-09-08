#!/usr/bin/env python3
"""Audit release-ready dev/test capacity for the final agreement sample."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_agreement_audit_packet as packet


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_capacity(
    eligible_rows: list[dict[str, Any]],
    task_targets: dict[str, int],
) -> dict[str, Any]:
    task_counts = Counter(str(row.get("task") or "") for row in eligible_rows)
    task_split_counts = Counter(
        f"{row.get('task')}:{row.get('split')}" for row in eligible_rows
    )
    stratum_counts = Counter(packet.stratum_for(row) for row in eligible_rows)
    task_capacity = {
        task: {
            "current": task_counts.get(task, 0),
            "target": target,
            "gap": max(0, target - task_counts.get(task, 0)),
            "passes": task_counts.get(task, 0) >= target,
        }
        for task, target in task_targets.items()
    }
    return {
        "eligible_rows": len(eligible_rows),
        "task_counts": dict(sorted(task_counts.items())),
        "task_split_counts": dict(sorted(task_split_counts.items())),
        "stratum_counts": dict(sorted(stratum_counts.items())),
        "task_capacity": task_capacity,
        "exact_sample_feasible": all(
            value["passes"] for value in task_capacity.values()
        ),
    }


def build_report(
    root: Path,
    *,
    provenance_path: Path,
    task_targets: dict[str, int],
) -> dict[str, Any]:
    root = root.resolve()
    provenance_path = (
        provenance_path
        if provenance_path.is_absolute()
        else root / provenance_path
    )
    gold_path = root / "eng_bench.jsonl"
    rows = packet.read_jsonl(gold_path)
    dev_test_rows = [
        row
        for row in rows
        if row.get("split") in {"dev", "test"}
        and row.get("task") in {"microtext", "visualdiff"}
    ]
    source_inventory = packet.read_source_inventory(root / "SOURCE_INVENTORY.csv")
    visualdiff_pairs = {
        str(row.get("pair_id") or ""): row
        for row in packet.read_jsonl(
            root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
        )
        if str(row.get("pair_id") or "")
    }
    visualdiff_manifest_docs = [
        row
        for row in packet.read_jsonl(root / "manifest.jsonl")
        if row.get("task") == "visualdiff" and row.get("type", "doc") == "doc"
    ]
    eligible, _, exclusions = packet.filter_release_ready_rows(
        dev_test_rows,
        source_inventory=source_inventory,
        visualdiff_pairs=visualdiff_pairs,
        visualdiff_manifest_docs=visualdiff_manifest_docs,
        provenance_docs=packet.provenance_documents(provenance_path),
    )
    summary = summarize_capacity(eligible, task_targets)
    eligible_sources = {
        str(context.get("source_doc_ids") or "")
        for row in eligible
        if (
            context := packet.source_context_for(
                row,
                source_inventory,
                visualdiff_pairs,
                visualdiff_manifest_docs,
            )
        ).get("source_doc_ids")
    }
    sample_size = sum(task_targets.values())
    return {
        "goal": "Gold v2.0 Global",
        "active_gold_rows": len(rows),
        "dev_test_rows": len(dev_test_rows),
        **summary,
        "eligible_source_sets": len(eligible_sources),
        "release_filter_exclusions": exclusions,
        "target_sample_rows": sample_size,
        "target_task_rows": task_targets,
        "migration_required_before_final_packet": not summary["exact_sample_feasible"],
        "next_action": (
            "Complete and strictly promote the one-for-one provenance replacements, "
            "then rerun this audit and build the frozen dev/test agreement packet."
            if not summary["exact_sample_feasible"]
            else "Freeze and build the final two-reviewer agreement packet."
        ),
        "build_command_after_capacity_passes": (
            "python tools\\build_agreement_audit_packet.py --root . "
            "--input eng_bench.jsonl --sample-size "
            f"{sample_size} --microtext-rows {task_targets['microtext']} "
            f"--visualdiff-rows {task_targets['visualdiff']} "
            "--provenance-report <latest-active-provenance.json> "
            "--require-release-ready --min-per-stratum 1 "
            "--seed engbench-agreement-v2-release"
        ),
        "inputs": {
            "eng_bench": gold_path.as_posix(),
            "eng_bench_sha256": file_sha256(gold_path),
            "provenance": provenance_path.as_posix(),
            "provenance_sha256": file_sha256(provenance_path),
        },
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agreement Rebuild Capacity",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Release-ready dev/test rows: `{report['eligible_rows']}`",
        f"- Target sample rows: `{report['target_sample_rows']}`",
        f"- Exact sample feasible: `{str(report['exact_sample_feasible']).lower()}`",
        "",
        "## Task Capacity",
        "",
    ]
    for task, values in report["task_capacity"].items():
        lines.append(
            f"- `{task}`: `{values['current']}/{values['target']}`; "
            f"gap `{values['gap']}`; pass `{str(values['passes']).lower()}`"
        )
    lines.extend(
        [
            "",
            "## Required Action",
            "",
            report["next_action"],
            "",
            "This report is preflight evidence only and does not modify Gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--microtext-rows", type=int, default=95)
    parser.add_argument("--visualdiff-rows", type=int, default=90)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = build_report(
        root,
        provenance_path=args.provenance,
        task_targets={
            "microtext": args.microtext_rows,
            "visualdiff": args.visualdiff_rows,
        },
    )
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "eligible_rows": report["eligible_rows"],
                "task_capacity": report["task_capacity"],
                "exact_sample_feasible": report["exact_sample_feasible"],
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report["exact_sample_feasible"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
