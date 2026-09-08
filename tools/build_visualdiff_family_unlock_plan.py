#!/usr/bin/env python3
"""Build a VisualDiff family unlock report from ready review packs."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "priority",
    "family",
    "active_gold",
    "candidate_rows",
    "ready_packet_count",
    "selected_by_next_tranche",
    "selected_packet_count",
    "suggested_rows_in_next_tranche",
    "packet_ids",
    "selected_packet_ids",
    "source_candidate_ids",
    "project_ids",
    "top_change_types",
    "next_step",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def visualdiff_family(pair_id: str) -> str:
    parts = pair_id.split("__")
    if len(parts) >= 3:
        return "__".join(parts[:3])
    return pair_id.rsplit("__", 1)[0]


def row_family(row: dict[str, Any]) -> str:
    pair_id = str(row.get("pair_id") or row.get("id") or "").strip()
    project_id = str(row.get("project_id") or "").strip()
    seed = pair_id or project_id
    return visualdiff_family(seed) if seed else ""


def active_visualdiff_families(root: Path) -> set[str]:
    rows = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    families = set()
    for row in rows:
        family = row_family(row)
        if family:
            families.add(family)
    return families


def read_tranche_csv(path: Path | None) -> dict[str, int]:
    if path is None or not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as f:
        return {
            str(row.get("packet_id") or ""): as_int(row.get("suggested_rows"))
            for row in csv.DictReader(f)
            if row.get("packet_id")
        }


def eligible_visualdiff_pack(row: dict[str, Any]) -> bool:
    if str(row.get("kind") or "") != "visualdiff":
        return False
    if not as_bool(row.get("ready_to_send")):
        return False
    if as_bool(row.get("human_complete")):
        return False
    if as_int(row.get("blank_rows")) <= 0:
        return False
    if as_int(row.get("missing_evidence_refs")) > 0:
        return False
    if as_int(row.get("invalid_rows")) > 0:
        return False
    return True


def pack_manifest_path(root: Path, pack_row: dict[str, Any]) -> Path:
    folder = Path(str(pack_row.get("folder_path") or ""))
    if not folder.is_absolute():
        folder = root / folder
    return folder / "manifest.jsonl"


def sorted_join(values: set[str]) -> str:
    return ";".join(sorted(v for v in values if v))


def build_report(
    *,
    root: Path,
    packet_index: Path,
    tranche_csv: Path | None = None,
    date_label: str = "manual",
    target_families: int = 30,
) -> dict[str, Any]:
    root = root.resolve()
    packet_index = packet_index if packet_index.is_absolute() else root / packet_index
    selected_rows_by_packet = read_tranche_csv(
        tranche_csv if tranche_csv is None or tranche_csv.is_absolute() else root / tranche_csv
    )
    active_families = active_visualdiff_families(root)
    index = read_json(packet_index)

    family_data: dict[str, dict[str, Any]] = {}
    skipped = []

    for pack in index.get("packs", []):
        if not eligible_visualdiff_pack(pack):
            continue
        packet_id = str(pack.get("packet_id") or "")
        manifest_path = pack_manifest_path(root, pack)
        manifest_rows = read_jsonl(manifest_path)
        if not manifest_rows:
            skipped.append(
                {
                    "packet_id": packet_id,
                    "reason": "missing_or_empty_manifest",
                    "manifest_path": str(manifest_path.relative_to(root)).replace("\\", "/")
                    if manifest_path.is_relative_to(root)
                    else str(manifest_path),
                }
            )
            continue

        family_counts: Counter[str] = Counter()
        family_order: list[str] = []
        family_source_ids: dict[str, set[str]] = defaultdict(set)
        family_project_ids: dict[str, set[str]] = defaultdict(set)
        family_change_types: dict[str, Counter[str]] = defaultdict(Counter)
        for manifest_row in manifest_rows:
            family = row_family(manifest_row)
            if not family:
                continue
            if family not in family_counts:
                family_order.append(family)
            family_counts[family] += 1
            family_source_ids[family].add(str(manifest_row.get("source_candidate_id") or ""))
            family_project_ids[family].add(str(manifest_row.get("project_id") or ""))
            family_change_types[family][str(manifest_row.get("change_type") or "unknown")] += 1

        selected_budget = selected_rows_by_packet.get(packet_id, 0)
        remaining_selected_budget = selected_budget
        selected_allocations: dict[str, int] = {}
        for family in family_order:
            if remaining_selected_budget <= 0:
                selected_allocations[family] = 0
                continue
            allocation = min(family_counts[family], remaining_selected_budget)
            selected_allocations[family] = allocation
            remaining_selected_budget -= allocation

        for family in family_order:
            row = family_data.setdefault(
                family,
                {
                    "family": family,
                    "active_gold": family in active_families,
                    "candidate_rows": 0,
                    "packet_ids": set(),
                    "selected_packet_ids": set(),
                    "suggested_rows_in_next_tranche": 0,
                    "source_candidate_ids": set(),
                    "project_ids": set(),
                    "change_types": Counter(),
                },
            )
            row["candidate_rows"] += family_counts[family]
            row["packet_ids"].add(packet_id)
            row["source_candidate_ids"].update(family_source_ids[family])
            row["project_ids"].update(family_project_ids[family])
            row["change_types"].update(family_change_types[family])
            if selected_allocations.get(family, 0) > 0:
                row["selected_packet_ids"].add(packet_id)
                row["suggested_rows_in_next_tranche"] += selected_allocations[family]

    candidate_families = []
    for family, row in family_data.items():
        selected = bool(row["selected_packet_ids"])
        next_step = (
            "already_active_gold_family"
            if row["active_gold"]
            else "human_review_then_promote_if_accepted"
            if selected
            else "queue_for_future_human_review"
        )
        candidate_families.append(
            {
                "priority": 0,
                "family": family,
                "active_gold": row["active_gold"],
                "candidate_rows": row["candidate_rows"],
                "ready_packet_count": len(row["packet_ids"]),
                "selected_by_next_tranche": selected,
                "selected_packet_count": len(row["selected_packet_ids"]),
                "suggested_rows_in_next_tranche": row["suggested_rows_in_next_tranche"],
                "packet_ids": sorted_join(row["packet_ids"]),
                "selected_packet_ids": sorted_join(row["selected_packet_ids"]),
                "source_candidate_ids": sorted_join(row["source_candidate_ids"]),
                "project_ids": sorted_join(row["project_ids"]),
                "top_change_types": ";".join(
                    f"{name}:{count}" for name, count in row["change_types"].most_common(5)
                ),
                "next_step": next_step,
            }
        )

    candidate_families.sort(
        key=lambda row: (
            row["active_gold"],
            not row["selected_by_next_tranche"],
            -row["suggested_rows_in_next_tranche"],
            -row["candidate_rows"],
            row["family"],
        )
    )
    for idx, row in enumerate(candidate_families, start=1):
        row["priority"] = idx

    new_ready = [row for row in candidate_families if not row["active_gold"]]
    selected_new = [row for row in new_ready if row["selected_by_next_tranche"]]
    return {
        "date_label": date_label,
        "source_index": str(packet_index.relative_to(root)).replace("\\", "/")
        if packet_index.is_relative_to(root)
        else str(packet_index),
        "tranche_csv": (
            str((tranche_csv if tranche_csv.is_absolute() else root / tranche_csv).relative_to(root)).replace("\\", "/")
            if tranche_csv
            and (tranche_csv if tranche_csv.is_absolute() else root / tranche_csv).is_relative_to(root)
            else str(tranche_csv)
            if tranche_csv
            else ""
        ),
        "target_family_count": target_families,
        "candidate_families": candidate_families,
        "skipped_packs": skipped,
        "totals": {
            "active_family_count": len(active_families),
            "target_family_count": target_families,
            "missing_families_to_target": max(0, target_families - len(active_families)),
            "ready_candidate_family_count": len(candidate_families),
            "new_ready_candidate_family_count": len(new_ready),
            "selected_new_candidate_family_count": len(selected_new),
            "projected_family_count_if_selected_new_families_promoted": len(active_families)
            + len(selected_new),
            "projected_family_count_if_all_new_ready_families_promoted": len(active_families)
            + len(new_ready),
            "new_families_still_needed_after_selected": max(
                0,
                target_families - (len(active_families) + len(selected_new)),
            ),
            "candidate_rows": sum(row["candidate_rows"] for row in candidate_families),
            "new_candidate_rows": sum(row["candidate_rows"] for row in new_ready),
            "suggested_visualdiff_rows_in_next_tranche": sum(
                row["suggested_rows_in_next_tranche"] for row in candidate_families
            ),
            "skipped_pack_count": len(skipped),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# VisualDiff Family Unlock Plan",
        "",
        f"- date label: `{report['date_label']}`",
        f"- source index: `{report['source_index']}`",
        f"- active gold families: `{totals['active_family_count']}` / `{totals['target_family_count']}`",
        f"- ready new candidate families: `{totals['new_ready_candidate_family_count']}`",
        f"- selected new candidate families: `{totals['selected_new_candidate_family_count']}`",
        f"- projected families if selected new families are accepted: `{totals['projected_family_count_if_selected_new_families_promoted']}`",
        f"- new families still needed after selected tranche: `{totals['new_families_still_needed_after_selected']}`",
        "",
        "## Selected New Families",
        "",
        "| Priority | Family | Rows | Selected Rows | Packets | Next Step |",
        "| ---: | --- | ---: | ---: | ---: | --- |",
    ]
    selected_rows = [
        row
        for row in report["candidate_families"]
        if row["selected_by_next_tranche"] and not row["active_gold"]
    ]
    if selected_rows:
        for row in selected_rows:
            lines.append(
                f"| {row['priority']} | `{row['family']}` | {row['candidate_rows']} | "
                f"{row['suggested_rows_in_next_tranche']} | {row['ready_packet_count']} | `{row['next_step']}` |"
            )
    else:
        lines.append("| - | none | 0 | 0 | 0 | - |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This report is a planning/control artifact only. It does not promote unreviewed VisualDiff rows into gold. A family counts toward the v2.0 gate only after human-reviewed rows are accepted and merged through the normal validators.",
            "",
        ]
    )
    if report.get("skipped_packs"):
        lines.extend(["## Skipped Packs", ""])
        for row in report["skipped_packs"]:
            lines.append(f"- `{row['packet_id']}`: {row['reason']}")
        lines.append("")
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_md: Path,
    output_csv: Path,
) -> None:
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["candidate_families"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--packet-index", type=Path, required=True)
    parser.add_argument("--tranche-csv", type=Path)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--target-families", type=int, default=30)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        root=args.root,
        packet_index=args.packet_index,
        tranche_csv=args.tranche_csv,
        date_label=args.date_label,
        target_families=args.target_families,
    )
    write_outputs(
        report,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
    )
    totals = report["totals"]
    print(
        json.dumps(
            {
                "active_family_count": totals["active_family_count"],
                "new_ready_candidate_family_count": totals["new_ready_candidate_family_count"],
                "selected_new_candidate_family_count": totals["selected_new_candidate_family_count"],
                "projected_family_count_if_selected_new_families_promoted": totals[
                    "projected_family_count_if_selected_new_families_promoted"
                ],
                "output_json": str(args.output_json),
                "output_md": str(args.output_md),
                "output_csv": str(args.output_csv),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
