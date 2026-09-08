#!/usr/bin/env python3
"""Preflight release-ready active Gold capacity for a formal agreement packet."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from build_agreement_audit_packet import (
        active_audit_identities,
        filter_quality_ready_rows,
        filter_release_ready_rows,
        provenance_documents,
        read_jsonl,
        read_source_inventory,
    )
except ModuleNotFoundError:
    from tools.build_agreement_audit_packet import (
        active_audit_identities,
        filter_quality_ready_rows,
        filter_release_ready_rows,
        provenance_documents,
        read_jsonl,
        read_source_inventory,
    )


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    root: Path,
    input_path: Path,
    provenance_path: Path,
    *,
    microtext_target: int,
    visualdiff_target: int,
    date_label: str,
    audit_holds_path: Path | None = None,
    require_quality_ready: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    input_path = resolve(root, input_path)
    provenance_path = resolve(root, provenance_path)
    if require_quality_ready and audit_holds_path is None:
        raise ValueError("require_quality_ready requires an audit_holds_path")
    audit_holds_path = (
        resolve(root, audit_holds_path) if audit_holds_path is not None else None
    )
    rows = read_jsonl(input_path)
    dev_test = [
        row
        for row in rows
        if row.get("split") in {"dev", "test"}
        and row.get("task") in {"microtext", "visualdiff"}
    ]
    source_inventory = read_source_inventory(root / "SOURCE_INVENTORY.csv")
    visualdiff_pairs = {
        str(row.get("pair_id") or ""): row
        for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
        if str(row.get("pair_id") or "")
    }
    visualdiff_manifest_docs = [
        row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("task") == "visualdiff" and row.get("type", "doc") == "doc"
    ]
    eligible, contexts, exclusions = filter_release_ready_rows(
        dev_test,
        source_inventory=source_inventory,
        visualdiff_pairs=visualdiff_pairs,
        visualdiff_manifest_docs=visualdiff_manifest_docs,
        provenance_docs=provenance_documents(provenance_path),
    )
    release_available = Counter(str(row.get("task") or "") for row in eligible)
    audit_ids = active_audit_identities(audit_holds_path) if audit_holds_path else set()
    eligible, quality_contexts, quality_exclusions = filter_quality_ready_rows(
        eligible,
        active_audit_ids=audit_ids,
    )
    available = Counter(str(row.get("task") or "") for row in eligible)
    total = Counter(str(row.get("task") or "") for row in dev_test)
    blocked = Counter()
    for row in dev_test:
        identifier = str(row.get("id") or "")
        if not bool((contexts.get(identifier) or {}).get("source_release_ready")):
            blocked[str(row.get("task") or "")] += 1
    targets = {"microtext": microtext_target, "visualdiff": visualdiff_target}
    deficits = {task: max(0, target - available[task]) for task, target in targets.items()}
    issues = [f"insufficient_{task}_rows:{available[task]}/{target}" for task, target in targets.items() if available[task] < target]
    packet_ready = not issues
    return {
        "schema": "eng_bench_formal_agreement_packet_readiness_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "status": "PASS" if packet_ready else "OPEN",
        "packet_ready": packet_ready,
        "active_gold_modified": False,
        "targets": targets,
        "available_release_ready": dict(sorted(available.items())),
        "available_release_ready_before_quality_filter": dict(sorted(release_available.items())),
        "available_release_and_quality_ready": dict(sorted(available.items())),
        "active_dev_test_rows": dict(sorted(total.items())),
        "rights_or_provenance_blocked": dict(sorted(blocked.items())),
        "deficits": deficits,
        "issues": issues,
        "release_filter_exclusions": exclusions,
        "quality_filter_exclusions": quality_exclusions,
        "quality_hold_filter_required": require_quality_ready,
        "active_audit_filter_enabled": audit_holds_path is not None,
        "inputs": {
            "active_gold": relative(root, input_path),
            "active_gold_sha256": file_sha256(input_path),
            "provenance_report": relative(root, provenance_path),
            "provenance_report_sha256": file_sha256(provenance_path),
            "audit_holds_jsonl": relative(root, audit_holds_path) if audit_holds_path else "",
            "audit_holds_jsonl_sha256": file_sha256(audit_holds_path) if audit_holds_path else "",
        },
        "next_action": (
            "Build the formal two-reviewer packet from active Gold."
            if packet_ready
            else "Promote enough accepted release-safe dev/test rows, then rerun this preflight before packet generation."
        ),
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Formal Agreement Packet Readiness",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Status: `{report['status']}`",
        f"- Packet ready: `{str(report['packet_ready']).lower()}`",
        f"- Release-ready MicroText: `{report['available_release_ready'].get('microtext', 0)}/{report['targets']['microtext']}`",
        f"- Release-ready VisualDiff: `{report['available_release_ready'].get('visualdiff', 0)}/{report['targets']['visualdiff']}`",
        f"- Rights/provenance-blocked VisualDiff dev/test rows: `{report['rights_or_provenance_blocked'].get('visualdiff', 0)}`",
        "",
        "## Next Action",
        "",
        report["next_action"],
        "",
        "## Issues",
        "",
    ]
    lines.extend(f"- `{issue}`" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- None.")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", default="eng_bench.jsonl")
    parser.add_argument("--provenance-report", required=True)
    parser.add_argument("--microtext-target", type=int, default=95)
    parser.add_argument("--visualdiff-target", type=int, default=90)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--audit-holds-jsonl")
    parser.add_argument("--require-quality-ready", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    report = build_report(
        root,
        Path(args.input),
        Path(args.provenance_report),
        microtext_target=args.microtext_target,
        visualdiff_target=args.visualdiff_target,
        date_label=args.date_label,
        audit_holds_path=(
            Path(args.audit_holds_jsonl) if args.audit_holds_jsonl else None
        ),
        require_quality_ready=args.require_quality_ready,
    )
    output = resolve(root.resolve(), args.output_json)
    write_report(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.require_ready and not report["packet_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
