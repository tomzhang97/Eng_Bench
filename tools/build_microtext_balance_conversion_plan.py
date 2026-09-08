#!/usr/bin/env python3
"""Rank release-safe source conversions against the Gold v2.0 MicroText deficit."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256, manifest_maps, read_csv
from audit_staged_v2_capacity import audit_source_doc
from source_rights import is_release_safe_status


DOMAIN_CATEGORY_WEIGHTS: dict[str, dict[str, int]] = {
    "pid": {
        "equipment_tag": 6,
        "instrument_tag": 7,
        "pipe_line_tag": 8,
        "process_label": 8,
        "process_value": 7,
    },
    "pcb_schematic": {
        "component_value": 10,
    },
    "electrical_control": {
        "component_value": 8,
        "equipment_tag": 4,
        "instrument_tag": 4,
        "process_label": 3,
    },
    "mechanical": {
        "dimension_value": 5,
        "tolerance_value": 8,
    },
    "mechanical_cad": {
        "dimension_value": 5,
        "tolerance_value": 8,
    },
    "civil_architectural": {
        "dimension_value": 5,
        "room_label": 6,
    },
    "civil_structural": {
        "dimension_value": 5,
        "tolerance_value": 4,
    },
    "civil_hydraulic": {
        "dimension_value": 5,
        "equipment_tag": 3,
    },
    "datasheet_spec": {
        "component_value": 5,
        "dimension_value": 2,
        "tolerance_value": 7,
    },
}
HOLD_NEXT_STEPS = {
    "await_human_return",
    "duplicate_payload_alias",
    "machine_exhausted",
    "machine_exhausted_after_reviewed_pass",
    "machine_exhausted_no_candidate",
    "rights_review_or_hold",
    "reviewed_sibling_or_active_gold",
    "staged_future_review_capacity",
}

EXISTING_REVIEW_NEXT_STEPS = {
    "human_review",
    "human_review_partial_packeted",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def source_mode(row: dict[str, Any]) -> str:
    next_step = str(row.get("next_step") or "").strip()
    if (
        next_step in EXISTING_REVIEW_NEXT_STEPS
        and int(row.get("fresh_open_review_rows") or 0) > 0
    ):
        return "existing_review_reservoir"
    if int(row.get("textlayer_spans") or 0) > 0:
        return "textlayer_regex_remine"
    if int(row.get("rendered_pages") or 0) > 0:
        return "ocr_or_manual_region_recovery"
    return "import_render_extract"


def category_score(category: str, shortfall: int, weight: int) -> int:
    return weight * min(shortfall, 1000)


def select_diverse_sources(
    ranked: list[dict[str, Any]],
    limit: int,
    *,
    min_sources_per_domain: int,
) -> list[dict[str, Any]]:
    """Reserve a small domain floor, then fill the remaining slots by score."""
    if limit <= 0 or not ranked:
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in ranked:
        groups.setdefault(str(row["domain"]), []).append(row)
    domains = sorted(groups, key=lambda domain: int(groups[domain][0]["rank"]))
    selected: list[dict[str, Any]] = []
    selected_docs: set[str] = set()
    for offset in range(max(0, min_sources_per_domain)):
        for domain in domains:
            if len(selected) >= limit:
                break
            group = groups[domain]
            if offset >= len(group):
                continue
            row = group[offset]
            selected.append(row)
            selected_docs.add(str(row["doc_id"]))
    for row in ranked:
        if len(selected) >= limit:
            break
        if str(row["doc_id"]) in selected_docs:
            continue
        selected.append(row)
        selected_docs.add(str(row["doc_id"]))
    return sorted(selected, key=lambda row: int(row["rank"]))


def build_plan(
    root: Path,
    readiness_report: Path,
    capacity_report: Path,
    *,
    date_label: str,
    top_sources: int = 40,
    min_sources_per_domain: int = 3,
    exact_span_only: bool = True,
    exclude_review_files: list[Path] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    readiness_path = resolve_path(root, readiness_report).resolve()
    capacity_path = resolve_path(root, capacity_report).resolve()
    readiness = read_json(readiness_path)
    capacity = read_json(capacity_path)
    exclusion_paths = [
        resolve_path(root, path).resolve() for path in (exclude_review_files or [])
    ]
    missing_exclusions = [path for path in exclusion_paths if not path.is_file()]
    if missing_exclusions:
        raise ValueError(
            "missing review exclusion files: "
            + ", ".join(path.as_posix() for path in missing_exclusions)
        )
    docs, _ = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    balance = capacity.get("microtext_balance_capacity") or {}
    active = balance.get("active") or {}
    floor_shortfalls = active.get("category_shortfalls") or {}
    target_projection = balance.get("row_target_projection") or {}
    required_non_pin = int(target_projection.get("additional_canonical_non_pin_rows_needed") or 0)

    ranked: list[dict[str, Any]] = []
    exclusion_reasons: Counter[str] = Counter()
    for source in readiness.get("local_sources", []):
        doc_id = str(source.get("doc_id") or "").strip()
        domain = str(source.get("domain") or "unknown").strip() or "unknown"
        task = str(source.get("task") or "").strip()
        public_status = str(source.get("public_status") or "").strip()
        next_step = str(source.get("next_step") or "").strip()
        if not doc_id:
            exclusion_reasons["missing_doc_id"] += 1
            continue
        if task not in {"microtext", "reference", ""}:
            exclusion_reasons["not_microtext_source"] += 1
            continue
        if not is_release_safe_status(public_status):
            exclusion_reasons["not_release_safe"] += 1
            continue
        source_audit = audit_source_doc(root, doc_id, docs, inventory)
        if not source_audit.get("paper_ready"):
            exclusion_reasons["not_paper_ready"] += 1
            for issue in source_audit.get("issues") or []:
                exclusion_reasons[f"provenance:{issue}"] += 1
            continue
        if bool(source.get("duplicate_payload_alias")):
            exclusion_reasons["duplicate_payload_alias"] += 1
            continue
        if next_step in HOLD_NEXT_STEPS:
            exclusion_reasons[f"next_step:{next_step}"] += 1
            continue
        if (
            next_step in EXISTING_REVIEW_NEXT_STEPS
            and int(source.get("fresh_open_review_rows") or 0) <= 0
        ):
            exclusion_reasons[f"next_step:{next_step}_without_fresh_rows"] += 1
            continue
        category_weights = DOMAIN_CATEGORY_WEIGHTS.get(domain, {})
        relevant = {
            category: int(floor_shortfalls.get(category) or 0)
            for category in category_weights
            if int(floor_shortfalls.get(category) or 0) > 0
        }
        if not relevant:
            exclusion_reasons["domain_not_mapped_to_open_floor"] += 1
            continue
        pages = int(source.get("rendered_pages") or 0)
        spans = int(source.get("textlayer_spans") or 0)
        mineable = int(source.get("mineable_candidates") or 0)
        fresh = int(source.get("fresh_open_review_rows") or 0)
        staged = int(source.get("staged_future_rows") or 0)
        fresh_unstaged = max(0, fresh - staged)
        textlayer_headroom = max(0, spans - staged)
        mode = source_mode(source)
        yield_signal = max(
            mineable,
            min(textlayer_headroom, 5000),
            min(fresh_unstaged, 5000),
            pages * 5,
        )
        score = sum(
            category_score(category, shortfall, category_weights[category])
            for category, shortfall in relevant.items()
        )
        score += min(yield_signal, 5000)
        score += min(fresh_unstaged * 2, 10000)
        if domain == "pid":
            score += 2000
        elif domain == "pcb_schematic":
            score += 1800
        elif domain == "electrical_control":
            score += 1600
        elif domain in {"mechanical", "mechanical_cad"}:
            score += 1500
        elif domain.startswith("civil"):
            score += 1000
        ranked.append(
            {
                "rank": 0,
                "doc_id": doc_id,
                "domain": domain,
                "public_status": public_status,
                "conversion_mode": mode,
                "target_categories": ";".join(
                    sorted(relevant, key=lambda category: (-category_weights[category], category))
                ),
                "category_floor_shortfalls": ";".join(
                    f"{category}:{relevant[category]}" for category in sorted(relevant)
                ),
                "rendered_pages": pages,
                "textlayer_spans": spans,
                "mineable_candidates": mineable,
                "fresh_open_review_rows": fresh,
                "staged_future_rows": staged,
                "fresh_unstaged_rows": fresh_unstaged,
                "textlayer_headroom_after_staged": textlayer_headroom,
                "source_path": str(source.get("source_path") or ""),
                "source_url": str(source.get("source_url") or ""),
                "readiness_next_step": next_step,
                "balance_priority_score": score,
            }
        )
    ranked.sort(
        key=lambda row: (
            -int(row["balance_priority_score"]),
            str(row["domain"]),
            str(row["doc_id"]),
        )
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank

    selected = select_diverse_sources(
        ranked,
        max(0, top_sources),
        min_sources_per_domain=min_sources_per_domain,
    )
    textlayer_rows = [
        row for row in selected if row["conversion_mode"] == "textlayer_regex_remine"
    ]
    existing_review_rows = [
        row for row in selected if row["conversion_mode"] == "existing_review_reservoir"
    ]
    textlayer_docs = [row["doc_id"] for row in textlayer_rows]
    ocr_docs = [row["doc_id"] for row in selected if row["conversion_mode"] == "ocr_or_manual_region_recovery"]
    commands: list[str] = []
    textlayer_groups: dict[tuple[str, ...], list[str]] = {}
    for row in textlayer_rows:
        categories = tuple(
            category
            for category in str(row["target_categories"]).split(";")
            if category
        )
        if categories:
            textlayer_groups.setdefault(categories, []).append(str(row["doc_id"]))
    for categories, doc_ids in sorted(
        textlayer_groups.items(), key=lambda item: (item[0], item[1])
    ):
        category_slug = "_".join(categories)
        span_flags = "--exact-span-only " if exact_span_only else (
            "--max-per-doc-page-category 250 --max-per-answer 25 "
        )
        commands.append(
            "python tools\\mine_microtext_candidates.py --root . "
            f"--doc-ids {','.join(doc_ids)} "
            f"--categories {','.join(categories)} {span_flags}"
            "--candidate-id-mode fingerprint --all-profile-matches "
            "--output "
            f"microtext\\annotations\\microtext_candidates_v2_0_balance_{category_slug}_{date_label}.jsonl"
        )
    existing_review_groups: dict[tuple[str, ...], list[str]] = {}
    for row in existing_review_rows:
        categories = tuple(
            category
            for category in str(row["target_categories"]).split(";")
            if category
        )
        if categories:
            existing_review_groups.setdefault(categories, []).append(str(row["doc_id"]))
    capacity_argument = capacity_path.relative_to(root).as_posix()
    exclusion_arguments = " ".join(
        f"--exclude-review-file {path.relative_to(root).as_posix()}"
        for path in exclusion_paths
    )
    for categories, doc_ids in sorted(
        existing_review_groups.items(), key=lambda item: (item[0], item[1])
    ):
        category_slug = "_".join(categories)
        category_flags = " ".join(f"--category {category}" for category in categories)
        doc_flags = " ".join(f"--include-doc-id {doc_id}" for doc_id in doc_ids)
        commands.append(
            "python tools\\build_microtext_source_review_queue.py --root . "
            f"--packet-date-label {date_label} --target-source-docs {len(doc_ids)} "
            f"--rows-per-source 500 {category_flags} {doc_flags} "
            f"--exclude-capacity-report {capacity_argument} "
            f"{exclusion_arguments} "
            "--exclude-region-overlap-threshold 0.8 --allow-item-audited-source-docs "
            "--allow-active-packet-source-docs --allow-active-source-docs "
            f"--output-jsonl derived\\review_queues\\v2_0_balance_existing_{category_slug}_{date_label}.jsonl "
            f"--output-json derived\\quality\\v2_0_balance_existing_{category_slug}_{date_label}.json "
            f"--output-md derived\\quality\\v2_0_balance_existing_{category_slug}_{date_label}.md"
        )
    for row in selected:
        if row["conversion_mode"] != "ocr_or_manual_region_recovery":
            continue
        flags = []
        if row["domain"] == "pid":
            flags.append("--include-pid-labels")
        if row["domain"] == "civil_architectural":
            flags.append("--include-architectural-room-labels")
        commands.append(
            "python tools\\propose_microtext_ocr_regions.py --root . "
            f"--doc-id {row['doc_id']} --version-id balance_{date_label} "
            f"{' '.join(flags)} --output-jsonl microtext\\annotations\\microtext_candidates_v2_0_balance_{row['doc_id']}_{date_label}.jsonl "
            f"--report-json derived\\quality\\v2_0_balance_{row['doc_id']}_{date_label}.json "
            f"--report-md derived\\quality\\v2_0_balance_{row['doc_id']}_{date_label}.md"
        )
    return {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "inputs": {
            "source_readiness_report": readiness_path.relative_to(root).as_posix(),
            "source_readiness_sha256": file_sha256(readiness_path),
            "staged_capacity_report": capacity_path.relative_to(root).as_posix(),
            "staged_capacity_sha256": file_sha256(capacity_path),
            "excluded_review_files": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": file_sha256(path),
                }
                for path in exclusion_paths
            ],
        },
        "balance_deficit": {
            "additional_canonical_non_pin_rows_needed": required_non_pin,
            "active_category_shortfalls": floor_shortfalls,
            "category_floor_shortfalls_after_all_canonical_staged_rows": balance.get(
                "category_floor_shortfalls_after_all_canonical_staged_rows", {}
            ),
        },
        "policy": {
            "release_safe_sources_only": True,
            "paper_ready_provenance_required": True,
            "duplicate_payload_aliases_excluded": True,
            "pin_label_not_targeted": True,
            "textlayer_commands_are_grouped_by_domain_target_categories": True,
            "textlayer_match_scope": "exact_span" if exact_span_only else "capped_substring",
            "existing_open_review_rows_are_capacity_filtered_before_remining": True,
            "awaiting_human_return_sources_are_not_remined": True,
            "machine_reserved_review_rows_are_excluded": bool(exclusion_paths),
            "minimum_sources_per_eligible_domain": min_sources_per_domain,
            "active_gold_modified": False,
            "human_packet_created": False,
        },
        "source_counts": {
            "readiness_local_sources": len(readiness.get("local_sources", [])),
            "eligible_balance_sources": len(ranked),
            "selected_sources": len(selected),
            "selected_textlayer_sources": len(textlayer_docs),
            "selected_existing_review_sources": len(existing_review_rows),
            "selected_ocr_or_manual_sources": len(ocr_docs),
            "excluded": dict(sorted(exclusion_reasons.items())),
        },
        "selected_sources": selected,
        "ranked_sources": ranked,
        "commands": commands,
        "interpretation": (
            "This is a machine conversion plan, not a human packet and not Gold. It targets "
            "canonical non-pin MicroText deficits using release-safe local source payloads. Every "
            "mined row still requires physical-region deduplication, visual QA, human acceptance, "
            "split reservation, and strict promotion."
        ),
    }


def write_outputs(report: dict[str, Any], json_path: Path, md_path: Path, csv_path: Path) -> None:
    for path in (json_path, md_path, csv_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fields = list(report["ranked_sources"][0]) if report["ranked_sources"] else ["rank", "doc_id"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["ranked_sources"])
    lines = [
        "# Gold v2.0 MicroText Balance Conversion Plan",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Additional canonical non-pin rows needed: "
        f"`{report['balance_deficit']['additional_canonical_non_pin_rows_needed']}`",
        f"- Eligible release-safe local sources: `{report['source_counts']['eligible_balance_sources']}`",
        f"- Selected machine conversion sources: `{report['source_counts']['selected_sources']}`",
        "- Active Gold modified: `false`",
        "- Human packet created: `false`",
        "",
        "| Rank | Source | Domain | Mode | Target categories | Score |",
        "| ---: | --- | --- | --- | --- | ---: |",
    ]
    for row in report["selected_sources"]:
        lines.append(
            f"| {row['rank']} | `{row['doc_id']}` | `{row['domain']}` | "
            f"`{row['conversion_mode']}` | `{row['target_categories']}` | "
            f"{row['balance_priority_score']} |"
        )
    lines.extend(["", "## Commands", ""])
    for command in report["commands"]:
        lines.extend(["```powershell", command, "```", ""])
    lines.extend(["## Interpretation", "", report["interpretation"], ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--top-sources", type=int, default=40)
    parser.add_argument("--min-sources-per-domain", type=int, default=3)
    parser.add_argument(
        "--allow-substring-matches",
        action="store_true",
        help=(
            "Mine capped proportional substring boxes in addition to full-span labels. "
            "Default is the lower-risk exact-span-only mode."
        ),
    )
    parser.add_argument(
        "--exclude-review-file",
        type=Path,
        action="append",
        default=[],
        help=(
            "Frozen machine or human review cohort to exclude from generated existing-"
            "reservoir commands; repeatable."
        ),
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = build_plan(
        root,
        args.readiness_report,
        args.capacity_report,
        date_label=args.date_label,
        top_sources=args.top_sources,
        min_sources_per_domain=args.min_sources_per_domain,
        exact_span_only=not args.allow_substring_matches,
        exclude_review_files=args.exclude_review_file,
    )
    write_outputs(
        report,
        resolve_path(root, args.output_json),
        resolve_path(root, args.output_md),
        resolve_path(root, args.output_csv),
    )
    print(json.dumps({"balance_deficit": report["balance_deficit"], "source_counts": report["source_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
