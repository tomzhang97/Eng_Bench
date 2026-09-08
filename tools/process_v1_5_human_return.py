#!/usr/bin/env python3
"""Validate and stage a returned v1.5 human review packet.

This tool intentionally does not mutate active gold JSONL files. It maps the
six v1.5 handoff CSV checklists back to their source review JSONLs, validates
human decisions, and writes reviewed staging JSONL plus a packet summary.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import microtext_review_checklist


MICROTEXT_STATUSES = {"accepted", "edited", "rejected", "needs_full_page"}
VISUALDIFF_STATUSES = {"valid", "edit", "reject_unclear", "needs_full_page"}


class PacketTask(NamedTuple):
    checklist: str
    kind: str
    source_jsonl: str
    output_stem: str


TASKS = [
    PacketTask(
        "microtext_caltrans_bridge_standard_details_validation_checklist.csv",
        "microtext",
        "microtext/annotations/microtext_review_caltrans_bridge_standard_details_2026-05-20.jsonl",
        "microtext_caltrans_bridge_standard_details",
    ),
    PacketTask(
        "microtext_mechanical_drawing_faunce_validation_checklist.csv",
        "microtext",
        "microtext/annotations/microtext_review_mechanical_drawing_faunce_2026-05-22.jsonl",
        "microtext_mechanical_drawing_faunce",
    ),
    PacketTask(
        "microtext_mechanical_drawing_reid_validation_checklist.csv",
        "microtext",
        "microtext/annotations/microtext_review_mechanical_drawing_reid_2026-05-22.jsonl",
        "microtext_mechanical_drawing_reid",
    ),
    PacketTask(
        "microtext_v1_5_first20_validation_checklist.csv",
        "microtext",
        "microtext/annotations/microtext_review_v1_5_first20_2026-05-24.jsonl",
        "microtext_v1_5_first20",
    ),
    PacketTask(
        "visualdiff_rusefi_hellen121vag_validation_checklist.csv",
        "visualdiff",
        "visualdiff/annotations/visualdiff_review_rusefi_hellen121vag_2026-05-22.jsonl",
        "visualdiff_rusefi_hellen121vag",
    ),
    PacketTask(
        "visualdiff_train_description_rewrite_checklist.csv",
        "visualdiff",
        "visualdiff/annotations/visualdiff_review_queue.jsonl",
        "visualdiff_train_description_rewrite",
    ),
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def current_visualdiff_description(row: dict[str, Any]) -> str:
    return str(row.get("current_description") or row.get("change_desc_gt") or row.get("description") or "").strip()


def is_todo_description(description: str) -> bool:
    return not description or description == "CHANGE_DESC_GT_TODO"


def status_counts(rows: list[dict[str, str]], key: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row.get(key) or "").strip().lower()] += 1
    return counts


def evidence_fields(row: dict[str, str]) -> list[tuple[str, str]]:
    fields = []
    for key, value in row.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("_path") or key in {"image_path", "old_crop_path", "new_crop_path", "panel_path"}:
            fields.append((key, value.strip().replace("\\", "/")))
    return fields


def path_exists(root: Path, packet_root: Path, value: str) -> bool:
    path = Path(value)
    if path.is_absolute():
        return path.exists()
    return (root / path).exists() or (packet_root / path).exists()


def missing_evidence(root: Path, packet_root: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    missing = []
    for row in rows:
        identifier = row.get("candidate_id") or row.get("pair_id") or ""
        for field, value in evidence_fields(row):
            if not path_exists(root, packet_root, value):
                missing.append({"id": identifier, "field": field, "path": value})
    return missing


def process_microtext(
    root: Path,
    task: PacketTask,
    checklist_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    source_rows = load_jsonl(root / task.source_jsonl)
    checklist_rows = read_csv(checklist_path)
    updated_rows, stats, errors = microtext_review_checklist.apply_checklist(source_rows, checklist_rows)
    output_path = output_dir / f"{task.output_stem}_reviewed.jsonl"
    write_jsonl(output_path, updated_rows)
    summary = {
        "checklist": task.checklist,
        "kind": "microtext",
        "source_jsonl": task.source_jsonl,
        "output_jsonl": str(output_path),
        "source_rows": len(source_rows),
        "checklist_rows": len(checklist_rows),
        "status_counts": dict(sorted(status_counts(checklist_rows, "review_status").items())),
        "apply_stats": dict(sorted(stats.items())),
        "errors": errors,
        "complete": stats.get("blank", 0) == 0 and not errors,
        "mergeable_rows": int(stats.get("mergeable", 0)),
    }
    return summary, errors


def apply_visualdiff_checklist(
    source_rows: list[dict[str, Any]],
    checklist_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str], list[str]]:
    by_id = {pair_id(row): dict(row) for row in source_rows if pair_id(row)}
    reviewed_ids: set[str] = set()
    reviewed_rows: list[dict[str, Any]] = []
    quarantine_rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    errors: list[str] = []

    for checklist_row in checklist_rows:
        pid = str(checklist_row.get("pair_id") or "").strip()
        if not pid:
            stats["missing_pair_id"] += 1
            continue
        if pid not in by_id:
            stats["unknown_pair_id"] += 1
            errors.append(f"unknown pair_id: {pid}")
            continue
        status = str(checklist_row.get("human_status") or "").strip().lower()
        description = str(checklist_row.get("human_description") or "").strip()
        notes = str(checklist_row.get("human_notes") or "").strip()
        if not status:
            stats["blank"] += 1
            continue
        if status not in VISUALDIFF_STATUSES:
            stats["invalid_status"] += 1
            errors.append(f"{pid}: invalid human_status={status!r}")
            continue
        row = dict(by_id[pid])
        current_description = current_visualdiff_description(row) or current_visualdiff_description(checklist_row)
        if status == "edit" and not description:
            stats["edit_missing_human_description"] += 1
            errors.append(f"{pid}: edit rows require human_description")
            continue
        if status == "valid" and is_todo_description(current_description):
            stats["valid_on_todo_description"] += 1
            errors.append(f"{pid}: valid cannot be used while current_description is TODO")
            continue

        reviewed_ids.add(pid)
        row["human_review_status"] = status
        row["human_review_notes"] = notes
        row["human_description"] = description
        if status == "edit":
            row["change_desc_gt"] = description
            row["description"] = description
            row["desc_source"] = "human"
            row["review_confidence"] = "human_validated"
            row["review_status"] = "accepted_for_merge"
            reviewed_rows.append(row)
            stats["mergeable"] += 1
        elif status == "valid":
            row["change_desc_gt"] = current_description
            row["description"] = current_description
            row["desc_source"] = "human_validated_existing"
            row["review_confidence"] = "human_validated"
            row["review_status"] = "accepted_for_merge"
            reviewed_rows.append(row)
            stats["mergeable"] += 1
        elif status == "reject_unclear":
            row["review_status"] = "rejected"
            row["quarantine_reason"] = "reject_unclear"
            quarantine_rows.append(row)
        elif status == "needs_full_page":
            row["review_status"] = "needs_full_page"
            reviewed_rows.append(row)
        stats[status] += 1

    stats["unreviewed_source_rows"] = len([row for row in source_rows if pair_id(row) not in reviewed_ids])
    return reviewed_rows, quarantine_rows, stats, errors


def process_visualdiff(
    root: Path,
    task: PacketTask,
    checklist_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    source_rows = load_jsonl(root / task.source_jsonl)
    checklist_rows = read_csv(checklist_path)
    reviewed_rows, quarantine_rows, stats, errors = apply_visualdiff_checklist(source_rows, checklist_rows)
    reviewed_path = output_dir / f"{task.output_stem}_reviewed.jsonl"
    quarantine_path = output_dir / f"{task.output_stem}_quarantine.jsonl"
    write_jsonl(reviewed_path, reviewed_rows)
    write_jsonl(quarantine_path, quarantine_rows)
    summary = {
        "checklist": task.checklist,
        "kind": "visualdiff",
        "source_jsonl": task.source_jsonl,
        "reviewed_output_jsonl": str(reviewed_path),
        "quarantine_output_jsonl": str(quarantine_path),
        "source_rows": len(source_rows),
        "checklist_rows": len(checklist_rows),
        "status_counts": dict(sorted(status_counts(checklist_rows, "human_status").items())),
        "apply_stats": dict(sorted(stats.items())),
        "errors": errors,
        "complete": stats.get("blank", 0) == 0 and not errors,
        "mergeable_rows": int(stats.get("mergeable", 0)),
        "quarantine_rows": len(quarantine_rows),
    }
    return summary, errors


def process_packet(root: Path, packet_root: Path, output_dir: Path) -> tuple[dict[str, Any], list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    all_errors: list[str] = []
    totals: Counter[str] = Counter()
    for task in TASKS:
        checklist_path = packet_root / task.checklist
        if not checklist_path.exists():
            error = f"missing checklist: {task.checklist}"
            summaries.append({"checklist": task.checklist, "kind": task.kind, "errors": [error], "complete": False})
            all_errors.append(error)
            continue
        checklist_rows = read_csv(checklist_path)
        evidence_missing = missing_evidence(root, packet_root, checklist_rows)
        if task.kind == "microtext":
            summary, errors = process_microtext(root, task, checklist_path, output_dir)
        else:
            summary, errors = process_visualdiff(root, task, checklist_path, output_dir)
        if evidence_missing:
            summary["missing_evidence_refs"] = len(evidence_missing)
            summary["missing_evidence_examples"] = evidence_missing[:10]
            errors = list(errors) + [f"{task.checklist}: missing evidence refs={len(evidence_missing)}"]
        else:
            summary["missing_evidence_refs"] = 0
            summary["missing_evidence_examples"] = []
        summaries.append(summary)
        all_errors.extend(errors)
        totals["checklists"] += 1
        totals["rows"] += int(summary.get("checklist_rows", 0))
        totals["mergeable_rows"] += int(summary.get("mergeable_rows", 0))
        totals["missing_evidence_refs"] += int(summary.get("missing_evidence_refs", 0))
        totals["complete_checklists"] += int(bool(summary.get("complete", False)) and not evidence_missing)

    report = {
        "packet_root": str(packet_root),
        "output_dir": str(output_dir),
        "totals": dict(sorted(totals.items())),
        "complete": not all_errors and totals.get("complete_checklists", 0) == len(TASKS),
        "errors": all_errors,
        "tasks": summaries,
    }
    return report, all_errors


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# v1.5 Human Return Processing Report",
        "",
        f"- Packet: `{report['packet_root']}`",
        f"- Output dir: `{report['output_dir']}`",
        f"- Complete: `{report['complete']}`",
        f"- Checklists: `{totals.get('checklists', 0)}`",
        f"- Complete checklists: `{totals.get('complete_checklists', 0)}`",
        f"- Rows: `{totals.get('rows', 0)}`",
        f"- Mergeable rows: `{totals.get('mergeable_rows', 0)}`",
        f"- Missing evidence refs: `{totals.get('missing_evidence_refs', 0)}`",
        f"- Errors: `{len(report['errors'])}`",
        "",
        "| Checklist | Kind | Rows | Complete | Mergeable | Missing Evidence | Errors |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for task in report["tasks"]:
        lines.append(
            f"| `{task['checklist']}` | {task.get('kind', '')} | {task.get('checklist_rows', 0)} | "
            f"`{task.get('complete', False)}` | {task.get('mergeable_rows', 0)} | "
            f"{task.get('missing_evidence_refs', 0)} | {len(task.get('errors', []))} |"
        )
    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        for error in report["errors"][:100]:
            lines.append(f"- {error}")
        if len(report["errors"]) > 100:
            lines.append(f"- ... {len(report['errors']) - 100} additional errors omitted")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and stage a returned v1.5 human review packet.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--packet-root",
        required=True,
        help="Folder containing the six filled v1.5 checklist CSV files",
    )
    parser.add_argument(
        "--output-dir",
        default="derived/human_adjudication/processed_returns/v1_5_latest",
        help="Reviewed staging output directory",
    )
    parser.add_argument("--strict", action="store_true", help="Return nonzero if packet is incomplete or invalid")
    args = parser.parse_args(argv)

    root = Path(args.root)
    packet_root = Path(args.packet_root)
    if not packet_root.is_absolute():
        packet_root = root / packet_root
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    report, errors = process_packet(root, packet_root, output_dir)
    write_json(output_dir / "processing_summary.json", report)
    (output_dir / "processing_summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {output_dir / 'processing_summary.json'}")
    print(f"[OK] Wrote {output_dir / 'processing_summary.md'}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    if errors:
        print("[WARN] Packet errors:")
        for error in errors[:20]:
            print(f"  - {error}")
        if len(errors) > 20:
            print(f"  - ... {len(errors) - 20} additional errors omitted")
    return 1 if args.strict and (errors or not report["complete"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
