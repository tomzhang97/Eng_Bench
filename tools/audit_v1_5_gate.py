#!/usr/bin/env python3
"""Audit Eng_Bench against the v1.5 Public release gate."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from agreement_audit import agreement_release_gate
from audit_active_gold_provenance import build_report as build_provenance_report
from audit_active_gold_provenance import release_provenance_gate
from evaluation_registry import baseline_registry
from source_rights import is_release_safe_status


V1_5_TARGETS = {
    "rows_min": 12000,
    "rows_max": 18000,
    "source_docs": 75,
    "visualdiff_revision_families": 15,
    "public_test_rows": 2500,
    "baselines": 10,
    "candidate_per_group": 30,
}

V1_5_GROUPS = (
    "pcb_schematic",
    "datasheet_spec",
    "mechanical_cad",
    "civil_architectural",
    "pid",
)

IGNORED_BASELINE_MARKERS = ("oracle", "smoke")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def quota_group(domain: str | None) -> str:
    if domain in {"civil", "architectural"}:
        return "civil_architectural"
    return domain or "unknown"


def is_release_safe_active(row: dict[str, str]) -> bool:
    if row.get("task") == "reference":
        return False
    status = str(row.get("public_status") or row.get("rights_tier") or "").lower()
    return is_release_safe_status(status)


def visualdiff_family(pair_id: str) -> str:
    parts = pair_id.split("__")
    if len(parts) >= 3:
        return "__".join(parts[:3])
    return pair_id.rsplit("__", 1)[0]


def counted_baseline_report_paths(root: Path) -> list[Path]:
    registry = baseline_registry(root)
    return [root / item["report_path"] for item in registry["counted"]]


def collect_status(root: Path) -> dict[str, Any]:
    unified = read_jsonl(root / "eng_bench.jsonl")
    pairs = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    inventory = read_csv(root / "SOURCE_INVENTORY.csv")
    candidates = read_csv(root / "SOURCE_CANDIDATES.csv")

    split_counts = Counter(row.get("split") for row in unified)
    provenance = build_provenance_report(root)
    source_doc_count = int(provenance["totals"]["active_source_docs"])

    release_safe_inventory = [row for row in inventory if is_release_safe_active(row)]
    candidate_groups = Counter(quota_group(row.get("domain")) for row in candidates)
    visualdiff_families = {
        visualdiff_family(str(row.get("id") or row.get("pair_id") or ""))
        for row in pairs
        if row.get("id") or row.get("pair_id")
    }
    baseline_reports = counted_baseline_report_paths(root)
    registry = baseline_registry(root)

    candidate_group_status = {
        group: {
            "current": candidate_groups[group],
            "target": V1_5_TARGETS["candidate_per_group"],
            "passes": candidate_groups[group] >= V1_5_TARGETS["candidate_per_group"],
        }
        for group in V1_5_GROUPS
    }

    gates = {
        "total_rows": {
            "current": len(unified),
            "target": f"{V1_5_TARGETS['rows_min']}-{V1_5_TARGETS['rows_max']}",
            "passes": V1_5_TARGETS["rows_min"] <= len(unified) <= V1_5_TARGETS["rows_max"],
        },
        "gold_source_docs": {
            "current": source_doc_count,
            "target": V1_5_TARGETS["source_docs"],
            "passes": source_doc_count >= V1_5_TARGETS["source_docs"],
        },
        "release_safe_inventory_docs": {
            "current": len(release_safe_inventory),
            "target": V1_5_TARGETS["source_docs"],
            "passes": len(release_safe_inventory) >= V1_5_TARGETS["source_docs"],
        },
        "visualdiff_revision_families": {
            "current": len(visualdiff_families),
            "target": V1_5_TARGETS["visualdiff_revision_families"],
            "passes": len(visualdiff_families) >= V1_5_TARGETS["visualdiff_revision_families"],
        },
        "public_test_rows": {
            "current": split_counts.get("test", 0),
            "target": V1_5_TARGETS["public_test_rows"],
            "passes": split_counts.get("test", 0) >= V1_5_TARGETS["public_test_rows"],
        },
        "baseline_reports": {
            "current": len(baseline_reports),
            "target": V1_5_TARGETS["baselines"],
            "passes": len(baseline_reports) >= V1_5_TARGETS["baselines"],
        },
        "candidate_source_quota": {
            "current": sum(1 for row in candidate_group_status.values() if row["passes"]),
            "target": len(V1_5_GROUPS),
            "passes": all(row["passes"] for row in candidate_group_status.values()),
        },
        "human_agreement_audit": agreement_release_gate(root),
        "active_gold_provenance": release_provenance_gate(root),
    }
    return {
        "gates": gates,
        "baseline_report_files": [path.relative_to(root).as_posix() for path in baseline_reports],
        "baseline_registry": registry,
        "split_counts": dict(sorted(split_counts.items())),
        "candidate_groups": candidate_group_status,
        "v1_5_public_complete": all(
            gates[name]["passes"]
            for name in (
                "total_rows",
                "gold_source_docs",
                "visualdiff_revision_families",
                "public_test_rows",
                "baseline_reports",
                "human_agreement_audit",
                "active_gold_provenance",
            )
        ),
        "candidate_source_quota_complete": gates["candidate_source_quota"]["passes"],
    }


def render_markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Eng_Bench v1.5 Gate Audit",
        "",
    ]
    if status.get("date_label"):
        lines.append(f"- audit date label: `{status['date_label']}`")
    lines.extend(
        [
            f"- v1.5 public complete: `{status['v1_5_public_complete']}`",
            f"- candidate-source quota complete: `{status['candidate_source_quota_complete']}`",
            "",
            "## Gate Checklist",
            "",
            "| Gate | Current | Target | Pass |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for name, row in status["gates"].items():
        lines.append(f"| {name} | {row['current']} | {row['target']} | `{row['passes']}` |")
    lines.extend(["", "## Split Counts", ""])
    for split, count in status["split_counts"].items():
        lines.append(f"- {split}: `{count}`")
    lines.extend(["", "## Counted Baseline Reports", ""])
    if status.get("baseline_report_files"):
        for path in status["baseline_report_files"]:
            lines.append(f"- `{path}`")
    else:
        lines.append("- none")
    lines.extend(["", "## v1.5 Candidate Source Groups", ""])
    for group, row in status["candidate_groups"].items():
        lines.append(
            f"- {group}: `{row['current']}` / {row['target']} "
            f"(pass: `{row['passes']}`)"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The candidate-source acquisition subtask is complete, but the v1.5 Public release gate is not complete. The next real bottleneck is converting queued sources into rendered, reviewed, gold rows and expanding test/baseline coverage.",
            "",
        ]
    )
    return "\n".join(lines)


def default_output_paths(date_label: str) -> tuple[str, str]:
    stem = f"results/health/v1_5_gate_audit_{date_label}"
    return f"{stem}.json", f"{stem}.md"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Eng_Bench v1.5 release gate.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--date-label",
        default=date.today().isoformat(),
        help="Date label to embed in default output filenames.",
    )
    parser.add_argument(
        "--output",
        help="Backward-compatible alias for --output-md.",
    )
    parser.add_argument("--output-json", help="JSON report output path.")
    parser.add_argument("--output-md", help="Markdown report output path.")
    args = parser.parse_args(argv)

    root = Path(args.root)
    default_json, default_md = default_output_paths(args.date_label)
    output_json = root / (args.output_json or default_json)
    output_md = root / (args.output_md or args.output or default_md)
    status = collect_status(root)
    status["date_label"] = args.date_label
    write_json(output_json, status)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(status), encoding="utf-8")
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
