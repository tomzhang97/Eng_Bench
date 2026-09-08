#!/usr/bin/env python3
"""Filter a microtext review queue against prior packet IDs and physical regions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_source_conversion_readiness import microtext_region_identity
from audit_staged_v2_capacity import (
    NEAR_OVERLAP_AREA_RATIO_THRESHOLD,
    NEAR_OVERLAP_CONTAINMENT_THRESHOLD,
    NEAR_OVERLAP_IOU_THRESHOLD,
    bboxes_are_near_duplicates,
)
from payload_alias_regions import load_payload_alias_map, payload_alias_region_key


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def resolve_exclude_paths(
    root: Path,
    explicit_paths: list[Path],
    patterns: list[str],
) -> list[Path]:
    paths: set[Path] = set()
    for value in explicit_paths:
        path = value if value.is_absolute() else root / value
        if not path.is_file():
            raise FileNotFoundError(f"exclude evidence not found: {path}")
        paths.add(path.resolve())
    for pattern in patterns:
        matches = [path for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise FileNotFoundError(f"exclude glob matched no files: {pattern}")
        paths.update(path.resolve() for path in matches)
    return sorted(paths)


def candidate_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(
        row.get("candidate_id")
        or row.get("item_id")
        or metadata.get("candidate_id")
        or metadata.get("item_id")
        or ""
    ).strip()


def candidate_aliases(row: dict[str, Any]) -> set[str]:
    """Return every stable MicroText alias used by the release capacity gate."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    values = [
        row.get("candidate_id"),
        row.get("item_id"),
        row.get("pre_padding_candidate_id"),
        metadata.get("candidate_id"),
        metadata.get("item_id"),
        metadata.get("pre_padding_candidate_id"),
    ]
    source_candidate_id = str(
        row.get("source_candidate_id") or metadata.get("source_candidate_id") or ""
    ).strip()
    if source_candidate_id.startswith("mtcand__"):
        values.append(source_candidate_id)
    return {
        str(value).strip()
        for value in values
        if str(value or "").strip()
    }


def region_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int]] | None:
    return microtext_region_identity(row)


def display_region(region: tuple[str, int, tuple[int, int, int, int]] | None) -> str:
    if not region:
        return ""
    doc_id, page_index, bbox = region
    return f"{doc_id}:p{page_index}:{','.join(str(value) for value in bbox)}"


def reactivate_for_review(row: dict[str, Any]) -> None:
    """Remove terminal metadata when an auditable source row is selected again."""
    row["review_status"] = "needs_review"
    row.pop("superseded_by", None)
    row.pop("machine_hold_reason", None)
    row.pop("cross_packet_collision_path", None)
    if str(row.get("machine_qa_status") or "").strip().lower() == "superseded":
        row.pop("machine_qa_status", None)
        row.pop("machine_qa_notes", None)


def build_report(
    input_path: Path,
    exclude_paths: list[Path],
    *,
    exclude_near_regions: bool = False,
    root: Path | None = None,
    payload_alias_report: Path | None = None,
    exclude_payload_alias_regions: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if exclude_payload_alias_regions and (root is None or payload_alias_report is None):
        raise ValueError(
            "payload-alias region suppression requires root and payload_alias_report"
        )
    alias_map: dict[str, str] = {}
    alias_group_count = 0
    if exclude_payload_alias_regions and payload_alias_report is not None:
        alias_map, alias_group_count = load_payload_alias_map(payload_alias_report)

    input_rows = read_rows(input_path)
    excluded_ids: dict[str, str] = {}
    excluded_regions: dict[tuple[str, int, tuple[int, int, int, int]], str] = {}
    excluded_region_index: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    excluded_alias_regions: dict[
        tuple[str, int, tuple[int, int, int, int]], str
    ] = {}
    excluded_alias_region_index: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    exclude_details: list[dict[str, Any]] = []

    for path in exclude_paths:
        rows = read_rows(path)
        for row in rows:
            for alias in candidate_aliases(row):
                excluded_ids.setdefault(alias, path.as_posix())
            region = region_key(row)
            if region:
                excluded_regions.setdefault(region, path.as_posix())
                doc_id, page_index, bbox = region
                excluded_region_index[(doc_id, page_index)].append((bbox, path.as_posix()))
            if exclude_payload_alias_regions and root is not None:
                alias_region = payload_alias_region_key(
                    row,
                    region,
                    root=root,
                    alias_map=alias_map,
                )
                if alias_region:
                    excluded_alias_regions.setdefault(alias_region, path.as_posix())
                    canonical_doc_id, page_index, bbox = alias_region
                    excluded_alias_region_index[(canonical_doc_id, page_index)].append(
                        (bbox, path.as_posix())
                    )
        exclude_details.append(
            {
                "path": path.as_posix(),
                "sha256": file_sha256(path),
                "rows": len(rows),
            }
        )

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    seen_regions: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    seen_region_index: dict[
        tuple[str, int], list[tuple[int, int, int, int]]
    ] = defaultdict(list)
    seen_alias_regions: set[
        tuple[str, int, tuple[int, int, int, int]]
    ] = set()
    seen_alias_region_index: dict[
        tuple[str, int], list[tuple[int, int, int, int]]
    ] = defaultdict(list)
    reason_counts: Counter[str] = Counter()

    for original in input_rows:
        row = dict(original)
        identity = candidate_id(row)
        aliases = candidate_aliases(row)
        region = region_key(row)
        alias_region = None
        if exclude_payload_alias_regions and root is not None:
            alias_region = payload_alias_region_key(
                row,
                region,
                root=root,
                alias_map=alias_map,
            )
        reason = ""
        collision_path = ""

        if not identity:
            reason = "missing_candidate_id"
        elif not region:
            reason = "missing_physical_region"
        elif collision_alias := next(
            (alias for alias in sorted(aliases) if alias in excluded_ids),
            "",
        ):
            reason = "excluded_candidate_id"
            collision_path = excluded_ids[collision_alias]
        elif region in excluded_regions:
            reason = "excluded_physical_region"
            collision_path = excluded_regions[region]
        elif alias_region and alias_region in excluded_alias_regions:
            reason = "excluded_payload_alias_region"
            collision_path = excluded_alias_regions[alias_region]
        if not reason and exclude_near_regions and region:
            doc_id, page_index, bbox = region
            collision = next(
                (
                    path
                    for excluded_bbox, path in excluded_region_index[(doc_id, page_index)]
                    if bboxes_are_near_duplicates(bbox, excluded_bbox)
                ),
                "",
            )
            if collision:
                reason = "excluded_near_physical_region"
                collision_path = collision
        if not reason and exclude_payload_alias_regions and alias_region:
            canonical_doc_id, page_index, bbox = alias_region
            collision = next(
                (
                    path
                    for excluded_bbox, path in excluded_alias_region_index[
                        (canonical_doc_id, page_index)
                    ]
                    if bboxes_are_near_duplicates(bbox, excluded_bbox)
                ),
                "",
            )
            if collision:
                reason = "excluded_near_payload_alias_region"
                collision_path = collision
        if not reason:
            if aliases & seen_aliases:
                reason = "duplicate_input_candidate_id"
            elif region in seen_regions:
                reason = "duplicate_input_physical_region"
            elif alias_region and alias_region in seen_alias_regions:
                reason = "duplicate_input_payload_alias_region"
        if not reason and exclude_near_regions and region:
            doc_id, page_index, bbox = region
            if any(
                bboxes_are_near_duplicates(bbox, seen_bbox)
                for seen_bbox in seen_region_index[(doc_id, page_index)]
            ):
                reason = "duplicate_input_near_physical_region"
        if not reason and exclude_payload_alias_regions and alias_region:
            canonical_doc_id, page_index, bbox = alias_region
            if any(
                bboxes_are_near_duplicates(bbox, seen_bbox)
                for seen_bbox in seen_alias_region_index[(canonical_doc_id, page_index)]
            ):
                reason = "duplicate_input_near_payload_alias_region"

        if reason:
            row["review_status"] = "machine_held"
            row["cross_packet_filter_status"] = "machine_held"
            row["machine_hold_reason"] = reason
            row["cross_packet_collision_path"] = collision_path
            held.append(row)
            reason_counts[reason] += 1
            continue

        seen_ids.add(identity)
        seen_aliases.update(aliases)
        seen_regions.add(region)
        doc_id, page_index, bbox = region
        seen_region_index[(doc_id, page_index)].append(bbox)
        if alias_region:
            seen_alias_regions.add(alias_region)
            canonical_doc_id, alias_page_index, alias_bbox = alias_region
            seen_alias_region_index[(canonical_doc_id, alias_page_index)].append(
                alias_bbox
            )
        reactivate_for_review(row)
        row["cross_packet_filter_status"] = "passing"
        kept.append(row)

    report = {
        "input": input_path.as_posix(),
        "input_sha256": file_sha256(input_path),
        "exclude_files": exclude_details,
        "exclude_near_regions": exclude_near_regions,
        "exclude_payload_alias_regions": exclude_payload_alias_regions,
        "payload_alias_report": (
            payload_alias_report.as_posix() if payload_alias_report else ""
        ),
        "payload_alias_groups": alias_group_count,
        "payload_alias_doc_ids": len(alias_map),
        "near_region_policy": {
            "iou_threshold": NEAR_OVERLAP_IOU_THRESHOLD,
            "containment_threshold": NEAR_OVERLAP_CONTAINMENT_THRESHOLD,
            "area_ratio_threshold": NEAR_OVERLAP_AREA_RATIO_THRESHOLD,
        },
        "totals": {
            "input_rows": len(input_rows),
            "kept_rows": len(kept),
            "held_rows": len(held),
            "exclude_files": len(exclude_paths),
            "excluded_candidate_ids": len(excluded_ids),
            "excluded_physical_regions": len(excluded_regions),
            "excluded_payload_alias_regions": len(excluded_alias_regions),
            "unique_kept_candidate_ids": len(seen_ids),
            "unique_kept_physical_regions": len(seen_regions),
            "unique_kept_payload_alias_regions": len(seen_alias_regions),
        },
        "held_reasons": dict(sorted(reason_counts.items())),
        "kept_categories": dict(
            sorted(Counter(str(row.get("category") or "") for row in kept).items())
        ),
        "kept_documents": len({str(row.get("doc_id") or "") for row in kept}),
        "interpretation": (
            "Passing rows remain human-review candidates only. This filter prevents ID and "
            "physical-region reuse across supplied current/future cohorts."
        ),
    }
    return kept, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Microtext Cross-Packet Filter Audit",
        "",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Kept for strict assembly: `{totals['kept_rows']}`",
        f"- Machine-held: `{totals['held_rows']}`",
        f"- Exclusion files: `{totals['exclude_files']}`",
        f"- Excluded candidate IDs indexed: `{totals['excluded_candidate_ids']}`",
        f"- Excluded physical regions indexed: `{totals['excluded_physical_regions']}`",
        f"- Payload-alias suppression enabled: `{str(report.get('exclude_payload_alias_regions', False)).lower()}`",
        f"- Payload-alias groups indexed: `{report.get('payload_alias_groups', 0)}`",
        f"- Excluded normalized payload-alias regions indexed: `{totals.get('excluded_payload_alias_regions', 0)}`",
        f"- Near-region suppression enabled: `{str(report.get('exclude_near_regions', False)).lower()}`",
        f"- Unique kept candidate IDs: `{totals['unique_kept_candidate_ids']}`",
        f"- Unique kept physical regions: `{totals['unique_kept_physical_regions']}`",
        f"- Kept source documents: `{report['kept_documents']}`",
        "",
        "## Held Reasons",
        "",
    ]
    if report["held_reasons"]:
        for reason, count in report["held_reasons"].items():
            lines.append(f"- `{reason}`: `{count}`")
    else:
        lines.append("- None.")
    lines.extend(["", "## Kept Categories", ""])
    for category, count in report["kept_categories"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Root-relative glob of JSONL or CSV exclusion evidence; repeat as needed.",
    )
    parser.add_argument(
        "--exclude-near-regions",
        action="store_true",
        help=(
            "Also suppress shifted or resized boxes that satisfy the staged-capacity "
            "near-region duplicate policy."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--payload-alias-report",
        type=Path,
        help="Source-payload duplicate audit used to canonicalize equivalent doc IDs.",
    )
    parser.add_argument(
        "--exclude-payload-alias-regions",
        action="store_true",
        help=(
            "Suppress normalized exact/near regions across byte-identical source aliases. "
            "Requires --payload-alias-report."
        ),
    )
    parser.add_argument("--kept-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.exclude_payload_alias_regions and not args.payload_alias_report:
        parser.error("--exclude-payload-alias-regions requires --payload-alias-report")
    try:
        exclude_paths = resolve_exclude_paths(
            args.root,
            args.exclude,
            args.exclude_glob,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))
    kept, held, report = build_report(
        args.input,
        exclude_paths,
        exclude_near_regions=args.exclude_near_regions,
        root=args.root,
        payload_alias_report=args.payload_alias_report,
        exclude_payload_alias_regions=args.exclude_payload_alias_regions,
    )
    write_jsonl(args.kept_output, kept)
    write_jsonl(args.held_output, held)
    write_report(args.output_json, report)
    write_markdown(args.output_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
