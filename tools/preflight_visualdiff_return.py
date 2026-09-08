#!/usr/bin/env python3
"""Preflight a returned VisualDiff human handoff before any gold merge."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from tools.visualdiff_review_checklist import apply_checklist
except ModuleNotFoundError:
    from visualdiff_review_checklist import apply_checklist


MERGEABLE_STATUSES = {"valid", "edit"}
REJECT_STATUSES = {"reject_unclear", "reject_no_change", "reject_bbox_mismatch"}
HOLD_STATUSES = {"needs_full_page"}
EVIDENCE_FIELDS = (
    "old_crop_path",
    "new_crop_path",
    "panel_path",
    "old_page_path",
    "new_page_path",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def load_handoff_manifest_rows(handoff_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for manifest_path in sorted((handoff_dir / "review_packs").glob("*/manifest.jsonl")):
        pack_name = manifest_path.parent.name
        for row in read_jsonl(manifest_path):
            out = dict(row)
            out["_review_pack"] = pack_name
            rows.append(out)
    if not rows:
        errors.append(f"no manifest rows found under {handoff_dir / 'review_packs'}")
    ids = [pair_id(row) for row in rows if pair_id(row)]
    repeated = sorted(identifier for identifier, count in Counter(ids).items() if count > 1)
    errors.extend(f"duplicate pair_id in handoff manifests: {identifier}" for identifier in repeated)
    return rows, errors


def load_checklist_rows(handoff_dir: Path, checklist_path: Path | None = None) -> tuple[list[dict[str, str]], list[str]]:
    path = checklist_path or handoff_dir / "visualdiff_selected_rows_validation_checklist.csv"
    if not path.exists():
        return [], [f"missing checklist CSV: {path}"]
    rows = read_csv(path)
    ids = [str(row.get("pair_id") or "").strip() for row in rows if str(row.get("pair_id") or "").strip()]
    repeated = sorted(identifier for identifier, count in Counter(ids).items() if count > 1)
    errors = [f"duplicate pair_id in checklist: {identifier}" for identifier in repeated]
    return rows, errors


def evidence_path_exists(handoff_dir: Path, row: dict[str, Any], field: str) -> bool:
    value = str(row.get(field) or "").replace("\\", "/").strip()
    if not value:
        return False
    path = Path(value)
    if path.is_absolute():
        return path.exists()
    pack_name = str(row.get("_review_pack") or row.get("review_pack") or "")
    candidates = []
    if pack_name:
        candidates.append(handoff_dir / "review_packs" / pack_name / path)
    candidates.append(handoff_dir / path)
    return any(candidate.exists() and candidate.is_file() for candidate in candidates)


def missing_evidence_rows(handoff_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    missing = []
    for row in rows:
        for field in EVIDENCE_FIELDS:
            if not evidence_path_exists(handoff_dir, row, field):
                missing.append(
                    {
                        "pair_id": pair_id(row),
                        "review_pack": str(row.get("_review_pack") or ""),
                        "field": field,
                        "path": str(row.get(field) or ""),
                    }
                )
    return missing


def active_pair_ids(root: Path) -> set[str]:
    return {
        pair_id(row)
        for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
        if pair_id(row)
    }


def row_status(row: dict[str, Any]) -> str:
    return str(row.get("human_review_status") or "").strip().lower()


def summarize_statuses(checklist_rows: list[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in checklist_rows:
        status = str(row.get("human_status") or "").strip().lower()
        counts[status or "blank"] += 1
    return counts


def build_report(
    *,
    root: Path,
    handoff_dir: Path,
    checklist_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    handoff_dir = handoff_dir if handoff_dir.is_absolute() else root / handoff_dir
    handoff_dir = handoff_dir.resolve()
    if checklist_path is not None and not checklist_path.is_absolute():
        checklist_path = root / checklist_path

    manifest_rows, manifest_errors = load_handoff_manifest_rows(handoff_dir)
    checklist_rows, checklist_errors = load_checklist_rows(handoff_dir, checklist_path)
    updated_rows, apply_stats, apply_errors = apply_checklist(manifest_rows, checklist_rows)
    missing_evidence = missing_evidence_rows(handoff_dir, manifest_rows)
    active_ids = active_pair_ids(root)

    mergeable_rows: list[dict[str, Any]] = []
    hold_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    blank_rows: list[dict[str, Any]] = []
    duplicate_active_rows: list[dict[str, Any]] = []

    checklist_id_to_status = {
        str(row.get("pair_id") or "").strip(): str(row.get("human_status") or "").strip()
        for row in checklist_rows
    }

    for row in updated_rows:
        identifier = pair_id(row)
        status = row_status(row)
        if identifier in active_ids and status in MERGEABLE_STATUSES:
            duplicate = dict(row)
            duplicate["hold_reason"] = "duplicate_active_pair_id"
            duplicate_active_rows.append(duplicate)
            hold_rows.append(duplicate)
            continue
        if status in MERGEABLE_STATUSES:
            mergeable_rows.append(row)
        elif status in REJECT_STATUSES:
            rejected_rows.append(row)
        elif status in HOLD_STATUSES:
            held = dict(row)
            held["hold_reason"] = status
            hold_rows.append(held)
        elif not checklist_id_to_status.get(identifier, "").strip():
            blank_rows.append(row)
        else:
            held = dict(row)
            held["hold_reason"] = status or "unapplied_status"
            hold_rows.append(held)

    all_errors = manifest_errors + checklist_errors + apply_errors
    if missing_evidence:
        all_errors.append(f"missing local evidence refs: {len(missing_evidence)}")
    ready_to_merge = (
        not all_errors
        and len(mergeable_rows) > 0
        and not blank_rows
        and not duplicate_active_rows
    )
    totals = {
        "manifest_rows": len(manifest_rows),
        "checklist_rows": len(checklist_rows),
        "mergeable_rows": len(mergeable_rows),
        "hold_rows": len(hold_rows),
        "rejected_rows": len(rejected_rows),
        "blank_rows": len(blank_rows),
        "duplicate_active_rows": len(duplicate_active_rows),
        "missing_evidence_refs": len(missing_evidence),
        "errors": len(all_errors),
    }
    return {
        "handoff_dir": str(handoff_dir),
        "checklist_path": str(checklist_path or handoff_dir / "visualdiff_selected_rows_validation_checklist.csv"),
        "ready_to_merge": ready_to_merge,
        "totals": totals,
        "status_counts": dict(sorted(summarize_statuses(checklist_rows).items())),
        "apply_stats": dict(sorted(apply_stats.items())),
        "errors": all_errors,
        "missing_evidence_examples": missing_evidence[:25],
        "mergeable_rows": mergeable_rows,
        "hold_rows": hold_rows,
        "rejected_rows": rejected_rows,
        "blank_pair_ids": [pair_id(row) for row in blank_rows],
        "duplicate_active_pair_ids": [pair_id(row) for row in duplicate_active_rows],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# VisualDiff Return Preflight",
        "",
        f"- Handoff dir: `{report['handoff_dir']}`",
        f"- Checklist: `{report['checklist_path']}`",
        f"- Ready to merge: `{str(report['ready_to_merge']).lower()}`",
        f"- Manifest rows: `{totals['manifest_rows']}`",
        f"- Checklist rows: `{totals['checklist_rows']}`",
        f"- Mergeable rows: `{totals['mergeable_rows']}`",
        f"- Hold rows: `{totals['hold_rows']}`",
        f"- Rejected rows: `{totals['rejected_rows']}`",
        f"- Blank rows: `{totals['blank_rows']}`",
        f"- Duplicate active rows held: `{totals['duplicate_active_rows']}`",
        f"- Missing evidence refs: `{totals['missing_evidence_refs']}`",
        f"- Errors: `{totals['errors']}`",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in report["status_counts"].items():
        lines.append(f"- {status}: `{count}`")
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in report["errors"][:100]:
            lines.append(f"- {error}")
        if len(report["errors"]) > 100:
            lines.append(f"- ... {len(report['errors']) - 100} additional errors omitted")
    lines.extend(
        [
            "",
            "## Merge Policy",
            "",
            "This preflight does not modify active gold files. Mergeable rows still require the normal maintainer promotion path, duplicate/leakage checks, dataset rebuild, and strict validators.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_md: Path,
    mergeable_jsonl: Path,
    hold_jsonl: Path,
) -> None:
    payload = dict(report)
    payload["mergeable_rows"] = [pair_id(row) for row in report["mergeable_rows"]]
    payload["hold_rows"] = [pair_id(row) for row in report["hold_rows"]]
    payload["rejected_rows"] = [pair_id(row) for row in report["rejected_rows"]]
    write_json(output_json, payload)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_jsonl(mergeable_jsonl, report["mergeable_rows"])
    write_jsonl(hold_jsonl, report["hold_rows"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--handoff-dir", type=Path, required=True)
    parser.add_argument("--checklist", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--mergeable-jsonl", type=Path, required=True)
    parser.add_argument("--hold-jsonl", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(root=args.root, handoff_dir=args.handoff_dir, checklist_path=args.checklist)
    write_outputs(
        report,
        output_json=args.output_json,
        output_md=args.output_md,
        mergeable_jsonl=args.mergeable_jsonl,
        hold_jsonl=args.hold_jsonl,
    )
    print(
        json.dumps(
            {
                "ready_to_merge": report["ready_to_merge"],
                "totals": report["totals"],
                "output_json": str(args.output_json),
                "output_md": str(args.output_md),
                "mergeable_jsonl": str(args.mergeable_jsonl),
                "hold_jsonl": str(args.hold_jsonl),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["ready_to_merge"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
