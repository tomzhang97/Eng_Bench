#!/usr/bin/env python3
"""Rank paper-ready review rows that are absent from active and staged capacity."""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness
import audit_staged_v2_capacity as staged_capacity
import audit_visualdiff_staged_readiness as visualdiff_readiness
import build_microtext_source_review_queue as microtext_selector
from audit_active_gold_provenance import manifest_maps, read_csv, resolve_visualdiff_docs
from build_canonical_staged_capacity import add_geometry, near_overlap_origin


OPEN_STATUSES = source_readiness.OPEN_STATUSES
PREQUALIFICATION_RANK = {
    "machine_prequalified_needs_visual_qa": 4,
    "visualdiff_alignment_candidate": 3,
    "manual_transcription_or_taxonomy": 2,
    "machine_semantic_hold": 1,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def parse_tier_output(value: str) -> tuple[str, Path]:
    tier, separator, path = value.partition("=")
    tier = tier.strip()
    path = path.strip()
    if not separator or not tier or not path:
        raise argparse.ArgumentTypeError("tier output must use TIER=PATH")
    return tier, Path(path)


def valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(part) for part in value)
    except (TypeError, ValueError):
        return False
    return min(x0, y0) >= 0 and x1 > x0 and y1 > y0


def existing_file(root: Path, value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return resolve_path(root, text).is_file()


def evidence_complete(root: Path, row: dict[str, Any]) -> bool:
    if staged_capacity.task_for_row(row) == "visualdiff":
        return (
            existing_file(root, row.get("image_old") or row.get("old_image_path"))
            and existing_file(root, row.get("image_new") or row.get("new_image_path"))
            and valid_bbox(row.get("bbox_old"))
            and valid_bbox(row.get("bbox_new"))
        )
    return (
        existing_file(root, row.get("image_path") or row.get("page_image_path"))
        and valid_bbox(row.get("bbox") or row.get("bbox_px"))
    )


def source_doc_ids(
    row: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> tuple[list[str], str]:
    if staged_capacity.task_for_row(row) == "microtext":
        doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
        return ([doc_id], "") if doc_id else ([], "missing_doc_id")
    explicit = [
        str(row.get(field) or "").strip()
        for field in ("old_doc_id", "new_doc_id", "from_doc_id", "to_doc_id")
        if str(row.get(field) or "").strip()
    ]
    explicit = list(dict.fromkeys(explicit))
    if len(explicit) == 2:
        return explicit, ""
    return resolve_visualdiff_docs(row, docs, manifest_pairs)


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip().casefold()


def textlayer_visible_text(value: Any) -> str:
    text = str(value or "")
    return (
        " ".join(re.findall(r">([^<>]+)<", text))
        if "<tspan" in text.casefold()
        else ""
    )


def prequalification(row: dict[str, Any], domain: str) -> tuple[str, list[str]]:
    """Classify machine readiness without claiming that visual content is correct."""
    if staged_capacity.task_for_row(row) == "visualdiff":
        reasons = visualdiff_readiness.seed_exclusion_reasons(row)
        if reasons:
            return "machine_semantic_hold", reasons
        return "visualdiff_alignment_candidate", ["requires_alignment_and_visual_qa"]

    text = str(row.get("proposed_text") or row.get("target_text") or "").strip()
    category = str(row.get("category") or "").strip().lower()
    if not text:
        return "manual_transcription_or_taxonomy", ["missing_proposed_text"]
    if not microtext_selector.usable_text(text):
        return "machine_semantic_hold", ["unusable_proposed_text"]
    if category not in microtext_selector.DEFAULT_CATEGORIES:
        return "manual_transcription_or_taxonomy", ["noncanonical_or_missing_category"]
    if not microtext_selector.domain_category_compatible(domain, category):
        return "machine_semantic_hold", ["domain_category_mismatch"]
    raw = row.get("raw_text") or row.get("text_context") or ""
    visible = textlayer_visible_text(raw)
    if visible and normalized_text(text) not in normalized_text(visible):
        return "machine_semantic_hold", ["textlayer_target_not_in_visible_text"]
    if not microtext_selector.resolved_version_id(row):
        return "manual_transcription_or_taxonomy", ["unresolved_version"]
    return "machine_prequalified_needs_visual_qa", []


def candidate_score(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    machine_status = str(
        row.get("machine_visual_qa_status") or row.get("machine_qa_status") or ""
    ).strip().lower()
    machine_rank = {
        "selected_for_human_review": 3,
        "visualdiff_queue_audit_pass": 2,
        "review_signal_passing": 2,
        "v2_expansion_reservoir": 1,
    }.get(machine_status, 0)
    text = str(
        row.get("proposed_text")
        or row.get("target_text")
        or row.get("description")
        or row.get("new_text")
        or ""
    ).strip()
    return (
        PREQUALIFICATION_RANK.get(str(row.get("unstaged_capacity_tier") or ""), 0),
        machine_rank,
        int(bool(text)),
        min(len(text), 500),
        str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or ""),
    )


def primary_identity(row: dict[str, Any]) -> str:
    kind = staged_capacity.task_for_row(row)
    return source_readiness.row_identity(row, kind)


def default_review_paths(root: Path) -> list[Path]:
    paths = list((root / "microtext" / "annotations").glob("microtext_review*.jsonl"))
    paths.extend((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl"))
    return sorted(path for path in paths if path.is_file())


def default_terminal_review_paths(root: Path) -> list[Path]:
    """Return derived decision ledgers that contain terminal machine holds."""
    queue_root = root / "derived" / "review_queues"
    paths = list(queue_root.glob("*_visual_held.jsonl"))
    paths.extend(queue_root.glob("*_visual_holds.jsonl"))
    paths.extend(queue_root.glob("*_machine_held.jsonl"))
    return sorted({path.resolve() for path in paths if path.is_file()})


def build_report(
    root: Path,
    *,
    review_paths: list[Path],
    current_paths: list[Path],
    future_paths: list[Path],
    reserved_paths: list[Path] | None = None,
    date_label: str,
    sample_rows_per_doc: int = 5,
    terminal_review_paths: list[Path] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    root = root.resolve()
    review_paths = [resolve_path(root, path).resolve() for path in review_paths]
    current_paths = [resolve_path(root, path).resolve() for path in current_paths]
    future_paths = [resolve_path(root, path).resolve() for path in future_paths]
    reserved_paths = [
        resolve_path(root, path).resolve() for path in (reserved_paths or [])
    ]
    terminal_review_paths = [
        resolve_path(root, path).resolve()
        for path in (
            terminal_review_paths
            if terminal_review_paths is not None
            else default_terminal_review_paths(root)
        )
    ]

    active_rows = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    active_rows += read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    current_rows = [row for path in current_paths for row in read_jsonl(path)]
    future_rows = [row for path in future_paths for row in read_jsonl(path)]
    reserved_rows = [row for path in reserved_paths for row in read_jsonl(path)]
    reference_rows = [*active_rows, *current_rows, *future_rows, *reserved_rows]
    reference_identities = {
        identity
        for row in reference_rows
        if (identity := staged_capacity.capacity_identity(row))
    }
    geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    for row in active_rows:
        add_geometry(geometry_by_page, row, "active_gold")
    for row in current_rows:
        add_geometry(geometry_by_page, row, "current_assignment")
    for row in future_rows:
        add_geometry(geometry_by_page, row, "future_capacity")
    for row in reserved_rows:
        add_geometry(geometry_by_page, row, "reserved_machine_capacity")

    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    provenance_cache: dict[str, dict[str, Any]] = {}
    terminal_identities = source_readiness.terminal_reviewed_row_keys(root)
    terminal_microtext_regions: set[
        tuple[str, int, tuple[int, int, int, int]]
    ] = set()
    for path in review_paths:
        for history_row in read_jsonl(path):
            status = source_readiness.status_for(history_row)
            exclusion = source_readiness.review_exclusion_reason(history_row)
            if status in OPEN_STATUSES and not exclusion:
                continue
            history_identity = primary_identity(history_row)
            if history_identity:
                terminal_identities.add(history_identity)
            if staged_capacity.task_for_row(history_row) == "microtext":
                region = source_readiness.microtext_region_identity(history_row)
                if region:
                    terminal_microtext_regions.add(region)
    for path in terminal_review_paths:
        for history_row in read_jsonl(path):
            status = source_readiness.status_for(history_row)
            exclusion = source_readiness.review_exclusion_reason(history_row)
            if status in OPEN_STATUSES and not exclusion:
                continue
            history_identity = primary_identity(history_row)
            if history_identity:
                terminal_identities.add(history_identity)
            if staged_capacity.task_for_row(history_row) == "microtext":
                region = source_readiness.microtext_region_identity(history_row)
                if region:
                    terminal_microtext_regions.add(region)

    def provenance(doc_id: str) -> dict[str, Any]:
        if doc_id not in provenance_cache:
            provenance_cache[doc_id] = staged_capacity.audit_source_doc(
                root, doc_id, docs, inventory
            )
        return provenance_cache[doc_id]

    counts: Counter[str] = Counter()
    best_by_identity: dict[str, dict[str, Any]] = {}
    for path in review_paths:
        display_path = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        for row in read_jsonl(path):
            counts["input_rows"] += 1
            status = source_readiness.status_for(row)
            exclusion = source_readiness.review_exclusion_reason(row)
            if status not in OPEN_STATUSES or exclusion:
                counts["not_open_or_terminal_rows"] += 1
                continue
            counts["open_rows"] += 1
            if primary_identity(row) in terminal_identities:
                counts["terminal_review_overlap_rows"] += 1
                continue
            if (
                staged_capacity.task_for_row(row) == "microtext"
                and source_readiness.microtext_region_identity(row)
                in terminal_microtext_regions
            ):
                counts["terminal_review_region_overlap_rows"] += 1
                continue
            if not evidence_complete(root, row):
                counts["missing_or_invalid_evidence_rows"] += 1
                continue
            counts["evidence_complete_rows"] += 1
            doc_ids, resolution_error = source_doc_ids(row, docs, manifest_pairs)
            if resolution_error or not doc_ids:
                counts["unresolved_source_rows"] += 1
                continue
            source_audits = [provenance(doc_id) for doc_id in doc_ids]
            if not all(audit.get("paper_ready") for audit in source_audits):
                counts["non_paper_ready_rows"] += 1
                continue
            counts["paper_ready_rows"] += 1
            identity = staged_capacity.capacity_identity(row)
            if not identity:
                counts["missing_identity_rows"] += 1
                continue
            if identity in reference_identities:
                counts["exact_reference_overlap_rows"] += 1
                continue
            overlap_origin = near_overlap_origin(geometry_by_page, row)
            if overlap_origin:
                counts[f"near_{overlap_origin}_rows"] += 1
                continue
            domain = str(inventory.get(doc_ids[0], {}).get("domain") or "unknown")
            tier, tier_reasons = prequalification(row, domain)
            candidate = dict(row)
            candidate["unstaged_capacity_identity"] = identity
            candidate["unstaged_capacity_source_docs"] = doc_ids
            candidate["unstaged_capacity_origin_path"] = display_path
            candidate["unstaged_capacity_status"] = "ranked_machine_candidate_only"
            candidate["unstaged_capacity_tier"] = tier
            candidate["unstaged_capacity_tier_reasons"] = tier_reasons
            candidate["safe_to_merge_gold"] = False
            existing = best_by_identity.get(identity)
            if existing is None or candidate_score(candidate) > candidate_score(existing):
                best_by_identity[identity] = candidate
            counts["paper_ready_nonreference_representations"] += 1

    candidates = sorted(
        best_by_identity.values(),
        key=lambda row: (
            str(row.get("doc_id") or row.get("project_id") or ""),
            -candidate_score(row)[0],
            staged_capacity.capacity_identity(row),
        ),
    )
    selected: list[dict[str, Any]] = []
    near_candidate_rows = 0
    for row in sorted(candidates, key=lambda value: candidate_score(value), reverse=True):
        overlap_origin = near_overlap_origin(geometry_by_page, row)
        if overlap_origin:
            near_candidate_rows += 1
            continue
        selected.append(row)
        add_geometry(geometry_by_page, row, "unstaged_candidate")
    selected.sort(
        key=lambda row: (
            str(row.get("doc_id") or row.get("project_id") or ""),
            staged_capacity.capacity_identity(row),
        )
    )

    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        scope = str(row.get("doc_id") or row.get("project_id") or "unknown")
        by_doc[scope].append(row)
    inventory_domain = {
        str(row.get("doc_id") or ""): str(row.get("domain") or "unknown")
        for row in inventory.values()
    }
    documents: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    for scope, rows in by_doc.items():
        rows = sorted(rows, key=candidate_score, reverse=True)
        task = staged_capacity.task_for_row(rows[0])
        categories = Counter(str(row.get("category") or row.get("change_type") or "unknown") for row in rows)
        tiers = Counter(str(row.get("unstaged_capacity_tier") or "unknown") for row in rows)
        source_docs = sorted({doc for row in rows for doc in row["unstaged_capacity_source_docs"]})
        documents.append(
            {
                "scope_id": scope,
                "task": task,
                "domain": inventory_domain.get(source_docs[0], "unknown") if source_docs else "unknown",
                "net_new_rows": len(rows),
                "source_docs": source_docs,
                "categories": dict(sorted(categories.items())),
                "readiness_tiers": dict(sorted(tiers.items())),
                "top_origin_paths": dict(sorted(Counter(row["unstaged_capacity_origin_path"] for row in rows).items())),
            }
        )
        sample_rows.extend(rows[: max(0, sample_rows_per_doc)])
    documents.sort(key=lambda row: (-row["net_new_rows"], row["scope_id"]))
    sample_ids = {staged_capacity.capacity_identity(row) for row in sample_rows}
    sample_rows = [row for row in selected if staged_capacity.capacity_identity(row) in sample_ids]

    counts["duplicate_representations"] = (
        counts["paper_ready_nonreference_representations"] - len(best_by_identity)
    )
    counts["near_candidate_rows"] = near_candidate_rows
    counts["net_new_rows"] = len(selected)
    for row in selected:
        counts[str(row.get("unstaged_capacity_tier") or "unknown") + "_rows"] += 1
    counts["net_new_scopes"] = len(documents)
    counts["paper_ready_source_docs"] = len(
        {doc for row in selected for doc in row["unstaged_capacity_source_docs"]}
    )
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "review_files": [
            path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            for path in review_paths
        ],
        "terminal_review_files": [
            path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            for path in terminal_review_paths
        ],
        "current_paths": [path.relative_to(root).as_posix() for path in current_paths],
        "future_paths": [path.relative_to(root).as_posix() for path in future_paths],
        "reserved_paths": [
            path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            for path in reserved_paths
        ],
        "totals": dict(sorted(counts.items())),
        "documents": documents,
        "interpretation": (
            "Read-only ranking of physical regions absent from active and canonical staged "
            "capacity. Net-new rows are an upper bound. Only machine_prequalified rows have "
            "passed deterministic text, taxonomy, version, evidence, provenance, and overlap "
            "screens; they still require visual curation, split reservation, human acceptance, "
            "and strict promotion gates."
        ),
    }
    return report, sample_rows, selected


def write_outputs(
    report: dict[str, Any],
    sample_rows: list[dict[str, Any]],
    *,
    json_path: Path,
    md_path: Path,
    csv_path: Path,
    samples_path: Path,
    ranked_rows: list[dict[str, Any]] | None = None,
    ranked_path: Path | None = None,
    actionable_path: Path | None = None,
    actionable_microtext_path: Path | None = None,
    actionable_visualdiff_path: Path | None = None,
    tier_paths: dict[str, Path] | None = None,
) -> None:
    paths = [json_path, md_path, csv_path, samples_path]
    if ranked_path is not None:
        paths.append(ranked_path)
    if actionable_path is not None:
        paths.append(actionable_path)
    if actionable_microtext_path is not None:
        paths.append(actionable_microtext_path)
    if actionable_visualdiff_path is not None:
        paths.append(actionable_visualdiff_path)
    paths.extend((tier_paths or {}).values())
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fields = ["scope_id", "task", "domain", "net_new_rows", "source_docs", "categories", "readiness_tiers", "top_origin_paths"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["documents"]:
            output = dict(row)
            for field in ("source_docs", "categories", "readiness_tiers", "top_origin_paths"):
                output[field] = json.dumps(output[field], sort_keys=True)
            writer.writerow(output)
    with samples_path.open("w", encoding="utf-8") as handle:
        for row in sample_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    if ranked_path is not None:
        with ranked_path.open("w", encoding="utf-8") as handle:
            for row in ranked_rows or []:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    if actionable_path is not None:
        actionable_tiers = {
            "machine_prequalified_needs_visual_qa",
            "visualdiff_alignment_candidate",
        }
        with actionable_path.open("w", encoding="utf-8") as handle:
            for row in ranked_rows or []:
                if row.get("unstaged_capacity_tier") in actionable_tiers:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    if actionable_microtext_path is not None:
        with actionable_microtext_path.open("w", encoding="utf-8") as handle:
            for row in ranked_rows or []:
                if row.get("unstaged_capacity_tier") == "machine_prequalified_needs_visual_qa":
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    if actionable_visualdiff_path is not None:
        with actionable_visualdiff_path.open("w", encoding="utf-8") as handle:
            for row in ranked_rows or []:
                if row.get("unstaged_capacity_tier") == "visualdiff_alignment_candidate":
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    for tier, tier_path in (tier_paths or {}).items():
        with tier_path.open("w", encoding="utf-8") as handle:
            for row in ranked_rows or []:
                if row.get("unstaged_capacity_tier") == tier:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    totals = report["totals"]
    lines = [
        "# Unstaged Review Capacity Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Review rows scanned: `{totals.get('input_rows', 0)}`",
        f"- Open rows: `{totals.get('open_rows', 0)}`",
        f"- Evidence-complete rows: `{totals.get('evidence_complete_rows', 0)}`",
        f"- Paper-ready rows: `{totals.get('paper_ready_rows', 0)}`",
        f"- Net-new physical rows: `{totals.get('net_new_rows', 0)}`",
        f"- Machine-prequalified rows: `{totals.get('machine_prequalified_needs_visual_qa_rows', 0)}`",
        f"- VisualDiff alignment candidates: `{totals.get('visualdiff_alignment_candidate_rows', 0)}`",
        f"- Net-new source scopes: `{totals.get('net_new_scopes', 0)}`",
        "",
        "| Scope | Task | Domain | Net-New Rows |",
        "| --- | --- | --- | ---: |",
    ]
    for row in report["documents"][:100]:
        lines.append(
            f"| `{row['scope_id']}` | `{row['task']}` | `{row['domain']}` | {row['net_new_rows']} |"
        )
    lines.extend(["", report["interpretation"], ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--review-path", type=Path, action="append", default=[])
    parser.add_argument(
        "--terminal-review-path",
        type=Path,
        action="append",
        default=[],
        help=(
            "Optional terminal decision ledger; repeatable. By default, derived "
            "*_visual_held.jsonl, *_visual_holds.jsonl, and *_machine_held.jsonl "
            "files are used."
        ),
    )
    parser.add_argument("--current", type=Path, action="append", required=True)
    parser.add_argument("--future", type=Path, action="append", required=True)
    parser.add_argument(
        "--reserved",
        type=Path,
        action="append",
        default=[],
        help=(
            "Optional machine-reserved or otherwise frozen staged cohort to exclude; "
            "repeatable."
        ),
    )
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--sample-rows-per-doc", type=int, default=5)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--samples-jsonl", type=Path, required=True)
    parser.add_argument(
        "--ranked-jsonl",
        type=Path,
        help="Optional complete ranked-row JSONL; every row remains safe_to_merge_gold=false.",
    )
    parser.add_argument(
        "--actionable-jsonl",
        type=Path,
        help="Optional machine-prequalified MicroText plus VisualDiff alignment candidates.",
    )
    parser.add_argument("--actionable-microtext-jsonl", type=Path)
    parser.add_argument("--actionable-visualdiff-jsonl", type=Path)
    parser.add_argument(
        "--tier-jsonl",
        action="append",
        type=parse_tier_output,
        default=[],
        metavar="TIER=PATH",
        help="Write all ranked rows from a named readiness tier; repeatable.",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    review_paths = args.review_path or default_review_paths(root)
    terminal_review_paths = args.terminal_review_path or default_terminal_review_paths(root)
    report, samples, ranked_rows = build_report(
        root,
        review_paths=review_paths,
        current_paths=args.current,
        future_paths=args.future,
        reserved_paths=args.reserved,
        date_label=args.date_label,
        sample_rows_per_doc=args.sample_rows_per_doc,
        terminal_review_paths=terminal_review_paths,
    )
    outputs = [args.output_json, args.output_md, args.output_csv, args.samples_jsonl]
    outputs = [resolve_path(root, path) for path in outputs]
    ranked_path = resolve_path(root, args.ranked_jsonl) if args.ranked_jsonl else None
    actionable_path = (
        resolve_path(root, args.actionable_jsonl) if args.actionable_jsonl else None
    )
    actionable_microtext_path = (
        resolve_path(root, args.actionable_microtext_jsonl)
        if args.actionable_microtext_jsonl
        else None
    )
    actionable_visualdiff_path = (
        resolve_path(root, args.actionable_visualdiff_jsonl)
        if args.actionable_visualdiff_jsonl
        else None
    )
    tier_paths: dict[str, Path] = {}
    for tier, path in args.tier_jsonl:
        if tier in tier_paths:
            parser.error(f"duplicate --tier-jsonl tier: {tier}")
        tier_paths[tier] = resolve_path(root, path)
    write_outputs(
        report,
        samples,
        json_path=outputs[0],
        md_path=outputs[1],
        csv_path=outputs[2],
        samples_path=outputs[3],
        ranked_rows=ranked_rows,
        ranked_path=ranked_path,
        actionable_path=actionable_path,
        actionable_microtext_path=actionable_microtext_path,
        actionable_visualdiff_path=actionable_visualdiff_path,
        tier_paths=tier_paths,
    )
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
