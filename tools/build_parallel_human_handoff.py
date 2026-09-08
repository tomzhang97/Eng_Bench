#!/usr/bin/env python3
"""Build and verify a self-contained parallel human handoff package."""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import audit_unmerged_reviewed_rows
import audit_source_conversion_readiness
import build_gold_expansion_plan
import review_queue_inventory


DEFAULT_SOURCE_PACKET = "derived/human_adjudication/2026-05-25_v1_5_intern_delivery"
DEFAULT_HANDOFF_DIR = "derived/human_adjudication/2026-06-02_v2_0_parallel_handoff"
DEFAULT_ZIP = "derived/human_adjudication/Eng_Bench_v2_0_parallel_human_handoff_2026-06-02.zip"
DEFAULT_PROCESSED_STATUS = "derived/human_adjudication/processed_returns/2026-06-02_v1_5_packet_status"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_tree_contents(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    if not source.exists():
        return copied
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
            copied += sum(1 for path in child.rglob("*") if path.is_file())
        elif child.is_file():
            shutil.copy2(child, target)
            copied += 1
    return copied


def copy_extra_review_packs(root: Path, sources: list[Path], destination: Path) -> dict[str, Any]:
    copied_files = 0
    copied_packs: list[str] = []
    for source in sources:
        source_path = source if source.is_absolute() else root / source
        if not source_path.exists():
            continue
        target = destination / source_path.name
        copied = copy_tree_contents(source_path, target)
        copied_files += copied
        copied_packs.append(source_path.name)
    return {"copied_files": copied_files, "copied_packs": copied_packs}


def regenerate_quality_artifacts(root: Path, date_label: str, limit: int) -> dict[str, Path]:
    plan = build_gold_expansion_plan.build_plan(root, limit=limit)
    plan_json = root / "derived" / "quality" / f"gold_expansion_plan_{date_label}.json"
    plan_md = root / "derived" / "quality" / f"gold_expansion_plan_{date_label}.md"
    human_csv = root / "docs" / "GOLD_EXPANSION_HUMAN_QUEUE.csv"
    machine_csv = root / "docs" / "GOLD_EXPANSION_MACHINE_QUEUE.csv"
    write_json(plan_json, plan)
    plan_md.parent.mkdir(parents=True, exist_ok=True)
    plan_md.write_text(build_gold_expansion_plan.render_markdown(plan), encoding="utf-8")
    build_gold_expansion_plan.write_csv(
        human_csv,
        plan["review_queues"]["human_priority_files"],
        fieldnames=build_gold_expansion_plan.HUMAN_QUEUE_FIELDS,
    )
    build_gold_expansion_plan.write_csv(machine_csv, plan["machine_import_priority"])

    unmerged = audit_unmerged_reviewed_rows.audit(root)
    unmerged_json = root / "derived" / "quality" / f"unmerged_reviewed_rows_{date_label}.json"
    unmerged_md = root / "derived" / "quality" / f"unmerged_reviewed_rows_{date_label}.md"
    write_json(unmerged_json, unmerged)
    unmerged_md.write_text(audit_unmerged_reviewed_rows.render_markdown(unmerged), encoding="utf-8")

    conversion = audit_source_conversion_readiness.build_report(root)
    conversion_json = root / "derived" / "quality" / f"source_conversion_readiness_{date_label}.json"
    conversion_md = root / "derived" / "quality" / f"source_conversion_readiness_{date_label}.md"
    conversion_local_csv = root / "docs" / "SOURCE_CONVERSION_LOCAL_QUEUE.csv"
    conversion_candidate_csv = root / "docs" / "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv"
    write_json(conversion_json, conversion)
    conversion_md.write_text(audit_source_conversion_readiness.render_markdown(conversion), encoding="utf-8")
    audit_source_conversion_readiness.write_csv(conversion_local_csv, conversion["local_sources"])
    audit_source_conversion_readiness.write_csv(conversion_candidate_csv, conversion["candidate_sources"])

    review_inventory = review_queue_inventory.inventory(root)
    review_inventory_json = root / "derived" / "quality" / f"review_queue_inventory_{date_label}.json"
    review_inventory_md = root / "derived" / "quality" / f"review_queue_inventory_{date_label}.md"
    write_json(review_inventory_json, review_inventory)
    review_inventory_md.write_text(review_queue_inventory.render_markdown(review_inventory), encoding="utf-8")

    return {
        "plan_json": plan_json,
        "plan_md": plan_md,
        "human_csv": human_csv,
        "machine_csv": machine_csv,
        "unmerged_json": unmerged_json,
        "unmerged_md": unmerged_md,
        "conversion_json": conversion_json,
        "conversion_md": conversion_md,
        "conversion_local_csv": conversion_local_csv,
        "conversion_candidate_csv": conversion_candidate_csv,
        "review_inventory_json": review_inventory_json,
        "review_inventory_md": review_inventory_md,
    }


def default_category_guide() -> str:
    return "\n".join(
        [
            "# Human Review Category Guide",
            "",
            "Use `corrected_category` when a microtext checklist row is miscategorized.",
            "",
            "- `pin_label`: PCB pins, connectors, nets, component references.",
            "- `dimension_value`: dimensions, lengths, angles, radii, drawing quantities.",
            "- `tolerance_value`: tolerances, fit classes, plus/minus ranges.",
            "- `room_label`: architectural or facility room labels.",
            "- `instrument_tag`: P&ID/instrument/control tags.",
            "- `equipment_tag`: equipment names or identifiers.",
            "- `pipe_line_tag`: pipe, line, or stream identifiers.",
            "- `process_value`: pressures, flows, temperatures, or other process values.",
            "",
            "For OCR-less rows with blank proposed text, use `edited`, fill `corrected_text`, and fill `corrected_category`; reject if the text cannot be read.",
            "",
        ]
    )


def write_handoff_docs(handoff_dir: Path, date_label: str) -> None:
    readme = f"""# Eng_Bench v2.0 Parallel Human Handoff

This folder is the current intern packet for human verification while machine-side source expansion continues.

## Start Here

1. Open `HUMAN_REVIEW_ORDER.md`.
2. Complete the 5/25 packet first under `01_current_5_25_packet/`.
3. Return the whole completed folder, not only the CSVs.

## Included

- `01_current_5_25_packet/`: the unfinished 2026-05-25 review packet, including images, review pages, and checklist CSVs.
- `02_status_and_queues/gold_expansion_plan_{date_label}.md`: current benchmark gaps and priority queues.
- `02_status_and_queues/GOLD_EXPANSION_HUMAN_QUEUE.csv`: ranked human review queue after 5/25.
- `02_status_and_queues/GOLD_EXPANSION_MACHINE_QUEUE.csv`: machine-side source import priority, included for transparency.
- `02_status_and_queues/HUMAN_REVIEW_CATEGORY_GUIDE.md`: category and corrected-category guide for microtext review.
- `02_status_and_queues/unmerged_reviewed_rows_{date_label}.md`: audit confirming whether reviewed rows are already active or still unmerged.
- `02_status_and_queues/source_conversion_readiness_{date_label}.md`: source-by-source conversion state for human and machine queues.
- `02_status_and_queues/SOURCE_CONVERSION_LOCAL_QUEUE.csv`: local source conversion queue.
- `02_status_and_queues/SOURCE_CONVERSION_CANDIDATE_QUEUE.csv`: candidate import/render/extract queue.
- `02_status_and_queues/review_queue_inventory_{date_label}.md`: evidence-complete staged review queues.
- `02_status_and_queues/processing_summary_{date_label}.*`: status from the current 5/25 packet audit, if available.
- `03_new_machine_review_packs/`: newly generated machine-side review packs, if present.

## Important

- Do not edit JSONL files manually.
- Fill checklist CSV files only.
- Do not rename, move, or delete images/panels.
- Mark uncertain examples as rejected or needs-full-page rather than guessing.
"""
    order = f"""# Human Review Order

## Priority 1: Finish The 5/25 Packet

Work inside:

`01_current_5_25_packet/derived/human_adjudication/2026-05-25_v1_5_review_handoff/`

Fill every checklist CSV in that folder. Use the local `index.html`/panel images for each task.

Required checklist files:

- `microtext_caltrans_bridge_standard_details_validation_checklist.csv`
- `microtext_mechanical_drawing_faunce_validation_checklist.csv`
- `microtext_mechanical_drawing_reid_validation_checklist.csv`
- `microtext_v1_5_first20_validation_checklist.csv`
- `visualdiff_rusefi_hellen121vag_validation_checklist.csv`
- `visualdiff_train_description_rewrite_checklist.csv`

## Priority 2: Follow The Ranked Queue

After the 5/25 packet is complete, open:

`02_status_and_queues/GOLD_EXPANSION_HUMAN_QUEUE.csv`

Prefer rows with large `open_rows`, `missing_evidence_rows` equal to `0`, and categories that improve balance.

For category decisions, use:

`02_status_and_queues/HUMAN_REVIEW_CATEGORY_GUIDE.md`

## Priority 3: Review New Machine Proposal Packs

If `03_new_machine_review_packs/` is present, review those packs after the ranked queue. OCR-less rows usually have blank `proposed_text`; use `edited`, fill `corrected_text`, and fill `corrected_category` when the crop is readable.

## CSV Status Rules

For microtext:

- `accepted`: text is visible and correct.
- `edited`: text is visible but the proposed answer must be corrected.
- `rejected`: crop is not usable, text is not visible, or label is wrong.
- `needs_full_page`: crop alone is insufficient.

For visualdiff:

- `valid`: change is visible inside the red box and description is correct.
- `edit`: change is visible, but write a better human description.
- `reject_unclear`: no visible change, change outside box, identical old/new, or ambiguous evidence.
- `needs_full_page`: crop is insufficient and full sheet inspection is required.

## Return Format

Return the full completed `{handoff_dir.name}` folder. Keeping the images and CSVs together lets the merge tool audit paths and process only safe rows.
"""
    handoff_dir.mkdir(parents=True, exist_ok=True)
    (handoff_dir / "README.md").write_text(readme, encoding="utf-8")
    (handoff_dir / "HUMAN_REVIEW_ORDER.md").write_text(order, encoding="utf-8")


def stage_status_files(
    root: Path,
    status_dir: Path,
    artifacts: dict[str, Path],
    processed_status_dir: Path,
    date_label: str,
) -> list[str]:
    status_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for key in (
        "plan_md",
        "human_csv",
        "machine_csv",
        "unmerged_md",
        "conversion_md",
        "conversion_local_csv",
        "conversion_candidate_csv",
        "review_inventory_md",
    ):
        source = artifacts[key]
        if source.exists():
            target = status_dir / source.name
            shutil.copy2(source, target)
            copied.append(target.name)
    category_guide = root / "docs" / "HUMAN_REVIEW_CATEGORY_GUIDE.md"
    if category_guide.exists():
        shutil.copy2(category_guide, status_dir / "HUMAN_REVIEW_CATEGORY_GUIDE.md")
    else:
        (status_dir / "HUMAN_REVIEW_CATEGORY_GUIDE.md").write_text(default_category_guide(), encoding="utf-8")
    copied.append("HUMAN_REVIEW_CATEGORY_GUIDE.md")

    for suffix in ("json", "md"):
        source = processed_status_dir / f"processing_summary.{suffix}"
        if source.exists():
            target = status_dir / f"processing_summary_{date_label}.{suffix}"
            shutil.copy2(source, target)
            copied.append(target.name)
    return copied


def zip_directory(source_dir: Path, zip_output: Path) -> int:
    zip_output.parent.mkdir(parents=True, exist_ok=True)
    entry_count = 0
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            archive_name = path.relative_to(source_dir.parent).as_posix()
            if path.is_dir():
                continue
            zf.write(path, archive_name)
            entry_count += 1
    return entry_count


def verify_zip(zip_output: Path, handoff_name: str, date_label: str) -> dict[str, Any]:
    required = [
        f"{handoff_name}/README.md",
        f"{handoff_name}/HUMAN_REVIEW_ORDER.md",
        f"{handoff_name}/02_status_and_queues/gold_expansion_plan_{date_label}.md",
        f"{handoff_name}/02_status_and_queues/GOLD_EXPANSION_HUMAN_QUEUE.csv",
        f"{handoff_name}/02_status_and_queues/GOLD_EXPANSION_MACHINE_QUEUE.csv",
        f"{handoff_name}/02_status_and_queues/HUMAN_REVIEW_CATEGORY_GUIDE.md",
        f"{handoff_name}/02_status_and_queues/unmerged_reviewed_rows_{date_label}.md",
        f"{handoff_name}/02_status_and_queues/source_conversion_readiness_{date_label}.md",
        f"{handoff_name}/02_status_and_queues/SOURCE_CONVERSION_LOCAL_QUEUE.csv",
        f"{handoff_name}/02_status_and_queues/SOURCE_CONVERSION_CANDIDATE_QUEUE.csv",
        f"{handoff_name}/handoff_build_report.json",
    ]
    if not zip_output.exists():
        return {"exists": False, "entry_count": 0, "missing_required_entries": required, "valid": False}
    with zipfile.ZipFile(zip_output, "r") as zf:
        names = {name.replace("\\", "/") for name in zf.namelist()}
    missing = [name for name in required if name not in names]
    has_packet = any(name.startswith(f"{handoff_name}/01_current_5_25_packet/") for name in names)
    if not has_packet:
        missing.append(f"{handoff_name}/01_current_5_25_packet/*")
    return {
        "exists": True,
        "entry_count": len(names),
        "missing_required_entries": missing,
        "has_5_25_packet": has_packet,
        "valid": not missing,
    }


def build_handoff_package(
    root: Path,
    source_packet: Path,
    handoff_dir: Path,
    zip_output: Path,
    processed_status_dir: Path,
    date_label: str,
    limit: int,
    extra_review_packs: list[Path] | None = None,
) -> dict[str, Any]:
    if not source_packet.is_absolute():
        source_packet = root / source_packet
    if not handoff_dir.is_absolute():
        handoff_dir = root / handoff_dir
    if not zip_output.is_absolute():
        zip_output = root / zip_output
    if not processed_status_dir.is_absolute():
        processed_status_dir = root / processed_status_dir

    artifacts = regenerate_quality_artifacts(root, date_label, limit=limit)
    packet_destination = handoff_dir / "01_current_5_25_packet"
    copied_packet_files = copy_tree_contents(source_packet, packet_destination)
    extra_pack_status = copy_extra_review_packs(
        root,
        extra_review_packs or [],
        handoff_dir / "03_new_machine_review_packs",
    )
    status_files = stage_status_files(
        root,
        handoff_dir / "02_status_and_queues",
        artifacts,
        processed_status_dir,
        date_label,
    )
    write_handoff_docs(handoff_dir, date_label)
    first_zipped_entries = zip_directory(handoff_dir, zip_output)
    zip_status = verify_zip(zip_output, handoff_dir.name, date_label)
    report = {
        "handoff_dir": str(handoff_dir),
        "source_packet": str(source_packet),
        "zip_output": str(zip_output),
        "date_label": date_label,
        "copied_packet_files": copied_packet_files,
        "extra_review_packs": extra_pack_status,
        "status_files": status_files,
        "zipped_entries": first_zipped_entries,
        "zip_status": zip_status,
        "valid": bool(zip_status["valid"]) and copied_packet_files > 0,
    }
    write_json(handoff_dir / "handoff_build_report.json", report)
    final_zipped_entries = zip_directory(handoff_dir, zip_output)
    final_zip_status = verify_zip(zip_output, handoff_dir.name, date_label)
    report["zipped_entries"] = final_zipped_entries
    report["zip_status"] = final_zip_status
    report["valid"] = bool(final_zip_status["valid"]) and copied_packet_files > 0
    write_json(handoff_dir / "handoff_build_report.json", report)
    zip_directory(handoff_dir, zip_output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Eng_Bench parallel human handoff zip.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--source-packet", default=DEFAULT_SOURCE_PACKET)
    parser.add_argument("--handoff-dir", default=DEFAULT_HANDOFF_DIR)
    parser.add_argument("--zip-output", default=DEFAULT_ZIP)
    parser.add_argument("--processed-status-dir", default=DEFAULT_PROCESSED_STATUS)
    parser.add_argument("--date-label", default="2026-06-02")
    parser.add_argument("--limit", type=int, default=40, help="Machine import queue limit")
    parser.add_argument(
        "--extra-review-pack",
        action="append",
        default=[],
        help="Relative or absolute review-pack directory to include under 03_new_machine_review_packs",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_handoff_package(
        root=root,
        source_packet=Path(args.source_packet),
        handoff_dir=Path(args.handoff_dir),
        zip_output=Path(args.zip_output),
        processed_status_dir=Path(args.processed_status_dir),
        date_label=args.date_label,
        limit=args.limit,
        extra_review_packs=[Path(path) for path in args.extra_review_pack],
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
