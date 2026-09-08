#!/usr/bin/env python3
"""Select fresh, release-safe VisualDiff rows that unlock new families."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness
import audit_v2_0_gate
import review_queue_inventory


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def release_candidate_ids(root: Path) -> set[str]:
    path = root / "SOURCE_CANDIDATE_VALIDATION.csv"
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    allowed: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        posture = str(row.get("release_posture") or "").strip().lower()
        next_action = " ".join(
            str(row.get(field) or "").strip().lower()
            for field in ("validation_next_action", "next_action", "next_step")
        )
        if candidate_id and "release_candidate" in posture and not any(
            marker in next_action for marker in ("rights_hold", "rights_review", "reject", "blocked")
        ):
            allowed.add(candidate_id)
    return allowed


def active_families(root: Path) -> set[str]:
    families: set[str] = set()
    for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"):
        pair_id = str(row.get("pair_id") or row.get("id") or "")
        if pair_id:
            families.add(audit_v2_0_gate.visualdiff_family(pair_id))
    return families


def families_from_packet_manifests(root: Path, paths: list[str]) -> set[str]:
    families: set[str] = set()
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        for row in read_jsonl(path):
            pair_id = str(row.get("pair_id") or row.get("id") or "")
            if pair_id:
                families.add(audit_v2_0_gate.visualdiff_family(pair_id))
    return families


def identities_from_row_files(root: Path, paths: list[str]) -> set[str]:
    """Load pair identities without excluding the rest of each family."""
    identities: set[str] = set()
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        for row in read_jsonl(path):
            identity = source_readiness.row_identity(row, "visualdiff")
            if identity:
                identities.add(identity)
    return identities


def preference_score(path: Path, row: dict[str, Any]) -> tuple[int, int, int, float, str]:
    curated = int("curated" in path.name.lower())
    machine_keep = int(str(row.get("machine_curation_status") or "").strip().lower() in {"keep", "accepted"})
    text_delta = int(
        bool(str(row.get("old_text") or row.get("from_text") or "").strip())
        and str(row.get("old_text") or row.get("from_text") or "").strip()
        != str(row.get("new_text") or row.get("to_text") or "").strip()
    )
    try:
        confidence = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = {"high": 1.0, "medium": 0.5, "low": 0.0}.get(
            str(row.get("confidence") or "").strip().lower(),
            0.0,
        )
    return curated, machine_keep, text_delta, confidence, str(row.get("pair_id") or "")


def select_family_rows(
    root: Path,
    *,
    packet_date_label: str,
    target_new_families: int,
    rows_per_family: int,
    excluded_source_candidate_ids: set[str] | None = None,
    excluded_families: set[str] | None = None,
    excluded_row_identities: set[str] | None = None,
    require_release_candidate: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    excluded_source_candidate_ids = excluded_source_candidate_ids or set()
    excluded_families = excluded_families or set()
    excluded_row_identities = excluded_row_identities or set()
    packet_keys = source_readiness.active_packet_row_keys(root, packet_date_label)
    resolved_keys = source_readiness.active_gold_row_keys(root) | source_readiness.terminal_reviewed_row_keys(root)
    gold_families = active_families(root)
    allowed_candidates = release_candidate_ids(root)
    counters: Counter[str] = Counter()
    best_by_identity: dict[str, tuple[tuple[int, int, int, float, str], Path, dict[str, Any]]] = {}

    queue_root = root / "visualdiff" / "annotations"
    for path in sorted(queue_root.glob("visualdiff_review*.jsonl")):
        for row in read_jsonl(path):
            counters["input_rows"] += 1
            if (
                review_queue_inventory.status_for(row) not in review_queue_inventory.OPEN_STATUSES
                or source_readiness.review_exclusion_reason(row)
            ):
                counters["excluded_non_open"] += 1
                continue
            pair_id = str(row.get("pair_id") or row.get("id") or "")
            family = audit_v2_0_gate.visualdiff_family(pair_id)
            if not pair_id or not family:
                counters["excluded_missing_identity"] += 1
                continue
            if family in gold_families:
                counters["excluded_active_family"] += 1
                continue
            if family in excluded_families:
                counters["excluded_family"] += 1
                continue
            identity = source_readiness.row_identity(row, "visualdiff")
            if identity in excluded_row_identities:
                counters["excluded_row_identity"] += 1
                continue
            if identity in packet_keys:
                counters["excluded_active_packet"] += 1
                continue
            if identity in resolved_keys:
                counters["excluded_resolved"] += 1
                continue
            candidate_id = str(row.get("source_candidate_id") or "").strip()
            if candidate_id in excluded_source_candidate_ids:
                counters["excluded_source_candidate"] += 1
                continue
            if require_release_candidate and candidate_id not in allowed_candidates:
                counters["excluded_not_release_registered"] += 1
                continue
            if review_queue_inventory.missing_paths(root, row):
                counters["excluded_missing_evidence"] += 1
                continue
            score = preference_score(path, row)
            previous = best_by_identity.get(identity)
            if previous is None or score > previous[0]:
                best_by_identity[identity] = (score, path, row)

    grouped: dict[str, list[tuple[tuple[int, int, int, float, str], Path, dict[str, Any]]]] = defaultdict(list)
    for score, path, row in best_by_identity.values():
        family = audit_v2_0_gate.visualdiff_family(str(row.get("pair_id") or row.get("id") or ""))
        grouped[family].append((score, path, row))

    family_order = sorted(grouped, key=lambda family: (-len(grouped[family]), family))
    selected_families = family_order[:target_new_families]
    selected_rows: list[dict[str, Any]] = []
    family_summaries: list[dict[str, Any]] = []
    for family in selected_families:
        candidates = sorted(grouped[family], key=lambda item: item[0], reverse=True)
        chosen = candidates[:rows_per_family]
        for _score, _path, row in chosen:
            selected = dict(row)
            selected["selection_original_review_bucket"] = str(row.get("review_bucket") or "")
            selected["review_bucket"] = "v2_0_family_expansion"
            selected_rows.append(selected)
        family_summaries.append(
            {
                "family": family,
                "available_rows": len(candidates),
                "selected_rows": len(chosen),
                "source_candidate_ids": sorted(
                    {str(item[2].get("source_candidate_id") or "") for item in candidates}
                ),
                "queue_files": sorted({item[1].relative_to(root).as_posix() for item in candidates}),
            }
        )

    selected_rows.sort(key=lambda row: str(row.get("pair_id") or row.get("id") or ""))
    report = {
        "packet_date_label": packet_date_label,
        "target_new_families": target_new_families,
        "rows_per_family": rows_per_family,
        "require_release_candidate": require_release_candidate,
        "excluded_source_candidate_ids": sorted(excluded_source_candidate_ids),
        "excluded_families": sorted(excluded_families),
        "excluded_row_identities": len(excluded_row_identities),
        "active_gold_families": len(gold_families),
        "available_new_families": len(grouped),
        "selected_new_families": len(selected_families),
        "selected_rows": len(selected_rows),
        "counters": dict(sorted(counters.items())),
        "families": family_summaries,
    }
    return selected_rows, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Fresh-Family Review Queue",
        "",
        f"- Active gold families: `{report['active_gold_families']}`",
        f"- Available new families: `{report['available_new_families']}`",
        f"- Selected new families: `{report['selected_new_families']}`",
        f"- Selected rows: `{report['selected_rows']}`",
        f"- Rows per family cap: `{report['rows_per_family']}`",
        f"- Active packet index: `{report['packet_date_label']}`",
        "",
        "| Family | Available | Selected | Source Candidates |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in report["families"]:
        lines.append(
            f"| `{row['family']}` | {row['available_rows']} | {row['selected_rows']} | "
            f"`{', '.join(row['source_candidate_ids'])}` |"
        )
    lines.extend(
        [
            "",
            "This is a review-only queue. No selected row is gold until human acceptance and strict promotion gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--packet-date-label", required=True)
    parser.add_argument("--target-new-families", type=int, default=23)
    parser.add_argument("--rows-per-family", type=int, default=3)
    parser.add_argument("--exclude-source-candidate", action="append", default=[])
    parser.add_argument("--exclude-family", action="append", default=[])
    parser.add_argument(
        "--exclude-packet-manifest",
        action="append",
        default=[],
        help="Exclude every family represented in this packet manifest (repeatable).",
    )
    parser.add_argument(
        "--exclude-row-file",
        action="append",
        default=[],
        help="Exclude only pair IDs present in this JSONL while retaining other rows from the family.",
    )
    parser.add_argument("--allow-unregistered-source-candidates", action="store_true")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    excluded_families = set(args.exclude_family)
    excluded_families.update(families_from_packet_manifests(root, args.exclude_packet_manifest))
    excluded_row_identities = identities_from_row_files(root, args.exclude_row_file)
    rows, report = select_family_rows(
        root,
        packet_date_label=args.packet_date_label,
        target_new_families=max(0, args.target_new_families),
        rows_per_family=max(1, args.rows_per_family),
        excluded_source_candidate_ids=set(args.exclude_source_candidate),
        excluded_families=excluded_families,
        excluded_row_identities=excluded_row_identities,
        require_release_candidate=not args.allow_unregistered_source_candidates,
    )
    output_jsonl = Path(args.output_jsonl)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    write_jsonl(output_jsonl if output_jsonl.is_absolute() else root / output_jsonl, rows)
    write_json(output_json if output_json.is_absolute() else root / output_json, report)
    markdown_path = output_md if output_md.is_absolute() else root / output_md
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"selected_new_families": report["selected_new_families"], "selected_rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
