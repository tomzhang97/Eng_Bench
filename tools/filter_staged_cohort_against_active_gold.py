#!/usr/bin/env python3
"""Remove active-Gold and within-cohort collisions from a staged review cohort."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged_capacity
from build_canonical_staged_capacity import (
    add_geometry,
    add_payload_alias_geometry,
    add_payload_alias_label,
    alias_identities,
    near_overlap_origin,
    payload_alias_geometry,
    payload_alias_label_key,
    payload_alias_label_overlap_origin,
    payload_alias_overlap_origin,
)
from payload_alias_regions import load_payload_alias_map


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def sanitize_cohort(
    root: Path,
    input_rows: list[dict[str, Any]],
    *,
    payload_alias_report: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    active_rows = staged_capacity.read_rows(
        root / "microtext" / "annotations" / "microtext_items.jsonl"
    ) + staged_capacity.read_rows(
        root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    )
    active_ids = {
        staged_capacity.capacity_identity(row)
        for row in active_rows
        if staged_capacity.capacity_identity(row)
    }
    active_aliases = {
        alias for row in active_rows for alias in alias_identities(row)
    }

    alias_map: dict[str, str] = {}
    alias_groups = 0
    if payload_alias_report:
        alias_map, alias_groups = load_payload_alias_map(payload_alias_report)

    geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    payload_geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    for row in active_rows:
        add_geometry(geometry_by_page, row, "active_gold")
        add_payload_alias_geometry(
            payload_geometry_by_page, root, row, alias_map, "active_gold"
        )
    payload_alias_labels: dict[tuple[str, int, str, str], str] = {}
    for row in active_rows:
        add_payload_alias_label(
            payload_alias_labels, row, alias_map, "active_gold"
        )

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    kept_ids: set[str] = set()
    kept_aliases: set[str] = set()
    reasons: Counter[str] = Counter()

    for source_row in input_rows:
        row = dict(source_row)
        identity = staged_capacity.capacity_identity(row)
        aliases = alias_identities(row)
        reason = ""
        if not identity:
            reason = "missing_capacity_identity"
        elif identity in active_ids or aliases & active_aliases:
            reason = "active_gold_identity_overlap"
        elif identity in kept_ids or aliases & kept_aliases:
            reason = "within_cohort_identity_overlap"
        else:
            overlap = near_overlap_origin(geometry_by_page, row)
            if overlap == "active_gold":
                reason = "active_gold_near_region_overlap"
            elif overlap == "current_active_safe":
                reason = "within_cohort_near_region_overlap"

        doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
        if not reason and alias_map:
            alias_overlap = payload_alias_overlap_origin(
                payload_geometry_by_page, root, row, alias_map
            )
            if alias_overlap == "active_gold":
                reason = "active_gold_payload_alias_region_overlap"
            elif alias_overlap == "current_active_safe":
                reason = "within_cohort_payload_alias_region_overlap"

        if not reason and alias_map:
            label_overlap = payload_alias_label_overlap_origin(
                payload_alias_labels, row, alias_map
            )
            if label_overlap == "active_gold":
                reason = "active_gold_payload_alias_text_category_overlap"
            elif label_overlap == "current_active_safe":
                reason = "within_cohort_payload_alias_text_category_overlap"
            elif (
                doc_id in alias_map
                and not payload_alias_geometry(root, row, alias_map)
                and not payload_alias_label_key(row, alias_map)
            ):
                reason = "payload_alias_region_unresolved"

        if reason:
            row["current_cohort_status"] = "excluded"
            row["current_cohort_hold_reason"] = reason
            row["safe_to_merge_gold"] = False
            held.append(row)
            reasons[reason] += 1
            continue

        row["current_cohort_status"] = "active_safe"
        row["safe_to_merge_gold"] = False
        kept.append(row)
        kept_ids.add(identity)
        kept_aliases.update(aliases)
        add_geometry(geometry_by_page, row, "current_active_safe")
        add_payload_alias_geometry(
            payload_geometry_by_page, root, row, alias_map, "current_active_safe"
        )
        add_payload_alias_label(
            payload_alias_labels, row, alias_map, "current_active_safe"
        )

    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(input_rows),
        "active_gold_rows": len(active_rows),
        "kept_rows": len(kept),
        "held_rows": len(held),
        "hold_reasons": dict(sorted(reasons.items())),
        "unique_kept_identities": len(kept_ids),
        "payload_alias_groups": alias_groups,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "valid": len(kept) + len(held) == len(input_rows)
        and len(kept_ids) == len(kept),
        "interpretation": (
            "Passing rows are active-safe review capacity only. They remain "
            "unreviewed and are not authorized for Gold promotion."
        ),
    }
    return kept, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Active-Gold Cohort Filter",
        "",
        f"- Input rows: `{report['input_rows']}`",
        f"- Active-safe rows: `{report['kept_rows']}`",
        f"- Held rows: `{report['held_rows']}`",
        f"- Unique kept identities: `{report['unique_kept_identities']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "- Active Gold modified: `false`",
        "",
        "## Hold Reasons",
        "",
    ]
    for reason, count in report["hold_reasons"].items():
        lines.append(f"- `{reason}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--payload-alias-report", type=Path)
    parser.add_argument("--kept-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    input_path = resolve(args.input)
    alias_path = resolve(args.payload_alias_report) if args.payload_alias_report else None
    kept, held, report = sanitize_cohort(
        root,
        staged_capacity.read_rows(input_path),
        payload_alias_report=alias_path,
    )
    kept_path = resolve(args.kept_output)
    held_path = resolve(args.held_output)
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    write_jsonl(kept_path, kept)
    write_jsonl(held_path, held)
    report.update(
        {
            "input": display_path(root, input_path),
            "input_sha256": sha256(input_path),
            "kept_output": display_path(root, kept_path),
            "kept_sha256": sha256(kept_path),
            "held_output": display_path(root, held_path),
            "held_sha256": sha256(held_path),
            "payload_alias_report": display_path(root, alias_path) if alias_path else "",
        }
    )
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "input_rows": report["input_rows"],
        "kept_rows": report["kept_rows"],
        "held_rows": report["held_rows"],
        "hold_reasons": report["hold_reasons"],
        "valid": report["valid"],
    }, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
