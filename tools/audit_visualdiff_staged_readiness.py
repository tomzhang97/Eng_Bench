#!/usr/bin/env python3
"""Rank staged VisualDiff families for a future, family-diverse human packet."""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_v2_0_gate import visualdiff_family


HUMAN_DECISIONS = {"accept", "accepted", "edit", "edited", "valid"}
FRESH_HUMAN_STATUSES = {"", "none", "unassigned", "needs_review", "needs_human_review"}
READY_MACHINE_STATUSES = {
    "review_signal_passing",
    "selected_for_human_review",
    "visualdiff_queue_audit_pass",
}
VALID_SPLITS = {"train", "dev", "test"}
GENERIC_DOCUMENT_LABELS = {"date", "patch", "rev", "revision", "title"}
REVISION_METADATA_RE = re.compile(r"^[a-z0-9_]+-[0-9a-f]{10,}$")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def numeric(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(part) for part in value)
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0 and min(x0, y0) >= 0


def path_exists(root: Path, value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    path = Path(text)
    return (path if path.is_absolute() else root / path).is_file()


def row_family(row: dict[str, Any]) -> str:
    pair_id = str(row.get("pair_id") or row.get("id") or "").strip()
    return visualdiff_family(pair_id) if pair_id else ""


def row_machine_status(row: dict[str, Any]) -> str:
    return normalized(row.get("machine_visual_qa_status") or row.get("machine_qa_status"))


def row_human_status(row: dict[str, Any]) -> str:
    return normalized(row.get("human_review_status"))


def row_signal_score(row: dict[str, Any]) -> float:
    status_score = {
        "selected_for_human_review": 4.0,
        "visualdiff_queue_audit_pass": 3.0,
        "review_signal_passing": 2.0,
    }.get(row_machine_status(row), 0.0)
    metrics = row.get("gap_mining_metrics") or {}
    changed_ratio = numeric(
        metrics.get("normalized_changed_pixel_ratio_gt16")
        or metrics.get("changed_pixel_ratio_gt12")
        or 0.0
    )
    mean_delta = numeric(
        metrics.get("normalized_mean_absolute_delta")
        or metrics.get("mean_absolute_delta")
        or 0.0
    )
    confidence = numeric(row.get("review_confidence") or row.get("confidence"))
    old_text = normalized(row.get("old_text"))
    new_text = normalized(row.get("new_text"))
    if old_text and new_text and old_text != new_text:
        text_score = 2.0
        relatedness = difflib.SequenceMatcher(None, old_text, new_text).ratio()
        relation_score = 2.0 * relatedness - (2.0 if relatedness < 0.15 else 0.0)
    elif bool(old_text) != bool(new_text):
        text_score = 1.0
        relation_score = 0.0
    elif old_text and old_text == new_text:
        text_score = -2.5
        relation_score = 0.0
    else:
        text_score = -1.0
        relation_score = 0.0
    area_ratio = numeric(metrics.get("bbox_area_ratio"))
    if area_ratio > 0.01:
        scale_score = -3.0
    elif area_ratio > 0.005:
        scale_score = -2.0
    elif area_ratio > 0.001:
        scale_score = -0.75
    else:
        scale_score = 0.0
    if max(len(old_text), len(new_text)) > 240:
        scale_score -= 1.0
    return round(
        status_score
        + text_score
        + relation_score
        + scale_score
        + min(changed_ratio, 1.0)
        + min(mean_delta / 255.0, 1.0)
        + confidence,
        8,
    )


def seed_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    """Return reasons a valid staged row should not seed a human packet."""
    old_text = normalized(row.get("old_text"))
    new_text = normalized(row.get("new_text"))
    description = normalized(row.get("description") or row.get("change_desc_gt"))
    change_type = normalized(row.get("change_type"))
    described_geometry_change = (
        change_type in {"layout", "symbol"}
        and description not in {"", "change_desc_gt_todo"}
    )
    reasons: list[str] = []
    if not old_text and not new_text and not described_geometry_change:
        reasons.append("both_text_blank")
    elif old_text and old_text == new_text:
        reasons.append("same_old_new_text")
    elif old_text and re.sub(r"\s+", "", old_text) == re.sub(r"\s+", "", new_text):
        reasons.append("whitespace_only_text_change")
    if (
        "kicad e.d.a." in old_text
        or "kicad e.d.a." in new_text
        or REVISION_METADATA_RE.fullmatch(old_text)
        or REVISION_METADATA_RE.fullmatch(new_text)
    ):
        reasons.append("eda_or_revision_metadata")
    if bool(old_text) != bool(new_text) and (old_text or new_text) in GENERIC_DOCUMENT_LABELS:
        reasons.append("generic_document_label")
    return reasons


def audit_row(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    family = row_family(row)
    human_status = row_human_status(row)
    machine_status = row_machine_status(row)
    image_old_exists = path_exists(root, row.get("image_old"))
    image_new_exists = path_exists(root, row.get("image_new"))
    boxes_valid = valid_bbox(row.get("bbox_old")) and valid_bbox(row.get("bbox_new"))
    split = normalized(row.get("reserved_split") or row.get("split"))
    rights_ready = normalized(row.get("source_rights_check")) == "release_safe_status"
    prior_human_decision = human_status in HUMAN_DECISIONS
    fresh_status = human_status in FRESH_HUMAN_STATUSES and normalized(
        row.get("review_status")
    ) in FRESH_HUMAN_STATUSES
    machine_signal_ready = machine_status in READY_MACHINE_STATUSES
    reasons: list[str] = []
    if not family:
        reasons.append("missing_family")
    if not image_old_exists:
        reasons.append("missing_old_image")
    if not image_new_exists:
        reasons.append("missing_new_image")
    if not boxes_valid:
        reasons.append("invalid_bbox")
    if split not in VALID_SPLITS:
        reasons.append("missing_reserved_split")
    if not rights_ready:
        reasons.append("rights_not_explicitly_release_safe")
    if not machine_signal_ready:
        reasons.append("machine_signal_not_ready")
    if not fresh_status and not prior_human_decision:
        reasons.append("nonfresh_review_status")
    fresh_review_ready = not reasons and not prior_human_decision
    seed_reasons = seed_exclusion_reasons(row) if fresh_review_ready else []
    seed_review_ready = fresh_review_ready and not seed_reasons
    return {
        "pair_id": str(row.get("pair_id") or row.get("id") or ""),
        "family_id": family,
        "project_id": str(row.get("project_id") or family),
        "reserved_split": split,
        "change_type": str(row.get("change_type") or ""),
        "machine_status": machine_status,
        "human_status": human_status,
        "prior_human_decision": prior_human_decision,
        "fresh_review_ready": fresh_review_ready,
        "seed_review_ready": seed_review_ready,
        "seed_exclusion_reasons": seed_reasons,
        "evidence_complete": image_old_exists and image_new_exists and boxes_valid,
        "rights_ready": rights_ready,
        "signal_score": row_signal_score(row),
        "readiness_reasons": reasons,
        "source_row": row,
    }


def build_report(
    root: Path,
    capacity_path: Path,
    paused_assignment_path: Path | None,
    *,
    target_families: int = 30,
    rows_per_family: int = 3,
    date_label: str = "manual",
    family_exclusions: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    capacity_path = capacity_path if capacity_path.is_absolute() else root / capacity_path
    active_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    active_rows = read_jsonl(active_path)
    active_families = {row_family(row) for row in active_rows if row_family(row)}
    paused_rows: list[dict[str, Any]] = []
    if paused_assignment_path:
        paused_assignment_path = (
            paused_assignment_path
            if paused_assignment_path.is_absolute()
            else root / paused_assignment_path
        )
        paused_rows = read_jsonl(paused_assignment_path)
    paused_families = {row_family(row) for row in paused_rows if row_family(row)}
    family_exclusions = dict(family_exclusions or {})

    audited = [audit_row(root, row) for row in read_jsonl(capacity_path) if row_family(row)]
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audited:
        by_family[row["family_id"]].append(row)

    family_rows: list[dict[str, Any]] = []
    for family_id, rows in by_family.items():
        fresh = [row for row in rows if row["fresh_review_ready"]]
        seed_ready = [row for row in rows if row["seed_review_ready"]]
        prior_human = [row for row in rows if row["prior_human_decision"]]
        excluded = (
            family_id in active_families
            or family_id in paused_families
            or family_id in family_exclusions
        )
        reason_counts = Counter(
            reason for row in rows for reason in row["readiness_reasons"]
        )
        seed_reason_counts = Counter(
            reason for row in rows for reason in row["seed_exclusion_reasons"]
        )
        family_rows.append(
            {
                "family_id": family_id,
                "rows": len(rows),
                "fresh_review_ready_rows": len(fresh),
                "seed_review_ready_rows": len(seed_ready),
                "prior_human_decision_rows": len(prior_human),
                "evidence_complete_rows": sum(row["evidence_complete"] for row in rows),
                "rights_ready_rows": sum(row["rights_ready"] for row in rows),
                "reserved_splits": dict(sorted(Counter(row["reserved_split"] for row in rows).items())),
                "excluded_active_family": family_id in active_families,
                "excluded_paused_family": family_id in paused_families,
                "excluded_machine_family": family_id in family_exclusions,
                "machine_family_exclusion_reason": family_exclusions.get(family_id, ""),
                "eligible_for_future_packet": bool(seed_ready) and not excluded,
                "top_signal_score": max(
                    (row["signal_score"] for row in seed_ready), default=0.0
                ),
                "readiness_reason_counts": dict(sorted(reason_counts.items())),
                "seed_exclusion_reason_counts": dict(sorted(seed_reason_counts.items())),
            }
        )

    family_rows.sort(
        key=lambda row: (
            not row["eligible_for_future_packet"],
            -row["top_signal_score"],
            -row["fresh_review_ready_rows"],
            row["family_id"],
        )
    )
    missing_families = max(0, target_families - len(active_families))
    selected_family_ids = [
        row["family_id"] for row in family_rows if row["eligible_for_future_packet"]
    ][:missing_families]
    selected_rows: list[dict[str, Any]] = []
    for family_id in selected_family_ids:
        candidates = sorted(
            (
                row
                for row in by_family[family_id]
                if row["seed_review_ready"]
            ),
            key=lambda row: (-row["signal_score"], row["pair_id"]),
        )[:rows_per_family]
        for row in candidates:
            selected = dict(row["source_row"])
            selected["readiness_family_id"] = family_id
            selected["readiness_signal_score"] = row["signal_score"]
            selected["readiness_selection_status"] = "future_packet_seed_only"
            selected["safe_to_merge_gold"] = False
            selected_rows.append(selected)

    totals = {
        "active_gold_families": len(active_families),
        "target_families": target_families,
        "missing_families_to_target": missing_families,
        "staged_families": len(by_family),
        "paused_assignment_families": len(paused_families),
        "machine_excluded_families": len(
            set(by_family).intersection(family_exclusions)
        ),
        "eligible_future_packet_families": sum(
            row["eligible_for_future_packet"] for row in family_rows
        ),
        "selected_family_seeds": len(selected_family_ids),
        "selected_row_seeds": len(selected_rows),
        "fresh_review_ready_rows": sum(row["fresh_review_ready"] for row in audited),
        "seed_review_ready_rows": sum(row["seed_review_ready"] for row in audited),
        "seed_excluded_rows": sum(
            row["fresh_review_ready"] and not row["seed_review_ready"] for row in audited
        ),
        "prior_human_decision_rows": sum(row["prior_human_decision"] for row in audited),
        "rows_with_complete_evidence": sum(row["evidence_complete"] for row in audited),
        "rows_with_explicit_release_safe_rights": sum(row["rights_ready"] for row in audited),
        "input_visualdiff_rows": len(audited),
    }
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "capacity_path": capacity_path.relative_to(root).as_posix(),
        "paused_assignment_path": (
            paused_assignment_path.relative_to(root).as_posix()
            if paused_assignment_path and paused_assignment_path.is_relative_to(root)
            else str(paused_assignment_path or "")
        ),
        "totals": totals,
        "selected_family_ids": selected_family_ids,
        "machine_family_exclusions": dict(sorted(family_exclusions.items())),
        "families": family_rows,
        "interpretation": (
            "Read-only machine planning. Selected rows are semantically screened seeds for later "
            "packet construction, not human decisions and never safe to merge into gold."
        ),
    }
    return report, selected_rows


def write_outputs(
    report: dict[str, Any],
    selected_rows: list[dict[str, Any]],
    json_path: Path,
    md_path: Path,
    csv_path: Path,
    selection_path: Path,
) -> None:
    for path in (json_path, md_path, csv_path, selection_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "family_id", "rows", "fresh_review_ready_rows", "prior_human_decision_rows",
            "seed_review_ready_rows", "evidence_complete_rows", "rights_ready_rows",
            "eligible_for_future_packet",
            "excluded_active_family", "excluded_paused_family", "excluded_machine_family",
            "machine_family_exclusion_reason", "top_signal_score",
            "reserved_splits", "readiness_reason_counts", "seed_exclusion_reason_counts",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["families"]:
            output = {field: row.get(field, "") for field in fields}
            output["reserved_splits"] = json.dumps(row["reserved_splits"], sort_keys=True)
            output["readiness_reason_counts"] = json.dumps(
                row["readiness_reason_counts"], sort_keys=True
            )
            output["seed_exclusion_reason_counts"] = json.dumps(
                row["seed_exclusion_reason_counts"], sort_keys=True
            )
            writer.writerow(output)
    with selection_path.open("w", encoding="utf-8") as handle:
        for row in selected_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    totals = report["totals"]
    lines = [
        "# VisualDiff Staged Readiness Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Active families: `{totals['active_gold_families']}/{totals['target_families']}`",
        f"- Missing families: `{totals['missing_families_to_target']}`",
        f"- Staged families: `{totals['staged_families']}`",
        f"- Paused-assignment families excluded: `{totals['paused_assignment_families']}`",
        f"- Machine alignment-failed families excluded: `{totals['machine_excluded_families']}`",
        f"- Eligible fresh families: `{totals['eligible_future_packet_families']}`",
        f"- Recommended seed: `{totals['selected_family_seeds']}` families / `{totals['selected_row_seeds']}` rows",
        f"- Fresh review-ready rows: `{totals['fresh_review_ready_rows']}`",
        f"- Semantically screened seed-ready rows: `{totals['seed_review_ready_rows']}`",
        f"- Valid rows excluded from seed ranking: `{totals['seed_excluded_rows']}`",
        f"- Prior human-decision rows kept separate: `{totals['prior_human_decision_rows']}`",
        "",
        "| Family | Rows | Fresh Ready | Seed Ready | Prior Human | Eligible | Top Score |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    selected = set(report["selected_family_ids"])
    for row in report["families"]:
        if row["family_id"] not in selected:
            continue
        lines.append(
            f"| `{row['family_id']}` | {row['rows']} | {row['fresh_review_ready_rows']} | "
            f"{row['seed_review_ready_rows']} | {row['prior_human_decision_rows']} | "
            f"`{row['eligible_for_future_packet']}` | "
            f"{row['top_signal_score']:.4f} |"
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--paused-assignment", type=Path)
    parser.add_argument(
        "--family-exclusions",
        type=Path,
        help="JSON object mapping family IDs to non-empty machine exclusion reasons.",
    )
    parser.add_argument("--target-families", type=int, default=30)
    parser.add_argument("--rows-per-family", type=int, default=3)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--selection-jsonl", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    family_exclusions: dict[str, str] = {}
    if args.family_exclusions:
        exclusion_path = (
            args.family_exclusions
            if args.family_exclusions.is_absolute()
            else root / args.family_exclusions
        )
        raw_exclusions = json.loads(exclusion_path.read_text(encoding="utf-8"))
        if not isinstance(raw_exclusions, dict):
            raise ValueError("family exclusions must be a JSON object")
        family_exclusions = {
            str(family_id).strip(): str(reason).strip()
            for family_id, reason in raw_exclusions.items()
        }
        if any(not family_id or not reason for family_id, reason in family_exclusions.items()):
            raise ValueError("each family exclusion requires a non-empty family ID and reason")
    report, selected = build_report(
        root,
        args.capacity,
        args.paused_assignment,
        target_families=args.target_families,
        rows_per_family=args.rows_per_family,
        date_label=args.date_label,
        family_exclusions=family_exclusions,
    )
    paths = [args.output_json, args.output_md, args.output_csv, args.selection_jsonl]
    paths = [path if path.is_absolute() else root / path for path in paths]
    write_outputs(report, selected, *paths)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
