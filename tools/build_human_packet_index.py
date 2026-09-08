#!/usr/bin/env python3
"""Build a current human-packet index for Eng_Bench review handoffs."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, NamedTuple


class PacketSpec(NamedTuple):
    packet_id: str
    priority: int
    kind: str
    intended_use: str
    folder_path: str
    zip_path: str
    build_report: str
    verification_report: str
    processing_report: str
    return_command: str


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "priority",
        "packet_id",
        "kind",
        "intended_use",
        "zip_path",
        "folder_path",
        "zip_exists",
        "ready_to_send",
        "verified",
        "human_status",
        "review_rows",
        "primary_rows",
        "extra_review_rows",
        "source_rows",
        "excluded_reviewed_rows",
        "excluded_active_gold_rows",
        "excluded_packet_rows",
        "overlap_count",
        "blank_rows",
        "mergeable_rows",
        "missing_evidence_refs",
        "zip_entries",
        "crop_files",
        "page_files",
        "issues",
        "return_command",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def packet_specs(date_label: str) -> list[PacketSpec]:
    parallel_dir = f"derived/human_adjudication/{date_label}_v2_0_parallel_handoff"
    next_dir = f"derived/human_adjudication/{date_label}_v2_0_next_review_batch"
    diversity_dir = f"derived/human_adjudication/{date_label}_v2_0_diversity_review_batch"
    return [
        PacketSpec(
            packet_id=f"{date_label}_parallel_handoff",
            priority=1,
            kind="parallel_handoff",
            intended_use="Finish the 5/25 packet first, then review included machine-proposal packs.",
            folder_path=parallel_dir,
            zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_parallel_human_handoff_{date_label}.zip",
            build_report=f"{parallel_dir}/handoff_build_report.json",
            verification_report=f"derived/quality/handoff_verification_{date_label}.json",
            processing_report=f"{parallel_dir}/02_status_and_queues/processing_summary_{date_label}.json",
            return_command=(
                "python tools\\process_v1_5_human_return.py --root . "
                "--packet-root <returned_folder>\\derived\\human_adjudication\\2026-05-25_v1_5_review_handoff "
                "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
            ),
        ),
        PacketSpec(
            packet_id=f"{date_label}_fresh_follow_on",
            priority=2,
            kind="standalone_review_batch",
            intended_use="Fresh mixed microtext rows after the parallel handoff is underway.",
            folder_path=next_dir,
            zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_next_review_batch_{date_label}.zip",
            build_report=f"derived/quality/next_review_batch_{date_label}.json",
            verification_report=f"derived/quality/next_review_batch_zip_verification_{date_label}.json",
            processing_report=f"derived/human_adjudication/processed_returns/{date_label}_next_review_batch_status/processing_summary.json",
            return_command=(
                "python tools\\process_next_review_batch_return.py --root . "
                "--batch-root <returned_next_batch_folder> "
                "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
            ),
        ),
        PacketSpec(
            packet_id=f"{date_label}_diversity_review",
            priority=3,
            kind="standalone_review_batch",
            intended_use="Civil/architectural breadth packet; LOC raster rows require edited corrected_text.",
            folder_path=diversity_dir,
            zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_diversity_review_batch_{date_label}.zip",
            build_report=f"derived/quality/diversity_review_batch_{date_label}.json",
            verification_report=f"derived/quality/diversity_review_batch_zip_verification_{date_label}.json",
            processing_report=f"derived/human_adjudication/processed_returns/{date_label}_diversity_review_batch_status/processing_summary.json",
            return_command=(
                "python tools\\process_next_review_batch_return.py --root . "
                "--batch-root <returned_diversity_batch_folder> "
                "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
            ),
        ),
    ]


def optional_unpacketed_packet_spec(date_label: str) -> PacketSpec:
    folder = f"derived/human_adjudication/{date_label}_v2_0_unpacketed_review_batch"
    return PacketSpec(
        packet_id=f"{date_label}_unpacketed_review",
        priority=4,
        kind="standalone_review_batch",
        intended_use=(
            "Fresh unpacketed review rows after excluding active packet manifests; "
            "inspect NEXT_REVIEW_BATCH_MANIFEST.csv for the dated packet contents."
        ),
        folder_path=folder,
        zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_unpacketed_review_batch_{date_label}.zip",
        build_report=f"derived/quality/unpacketed_review_batch_{date_label}.json",
        verification_report=f"derived/quality/unpacketed_review_batch_zip_verification_{date_label}.json",
        processing_report=f"derived/human_adjudication/processed_returns/{date_label}_unpacketed_review_batch_status/processing_summary.json",
        return_command=(
            "python tools\\process_next_review_batch_return.py --root . "
            "--batch-root <returned_unpacketed_batch_folder> "
            "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
        ),
    )


def optional_next_review_packet_spec(date_label: str) -> PacketSpec:
    folder = f"derived/human_adjudication/{date_label}_v2_0_next_review_batch"
    return PacketSpec(
        packet_id=f"{date_label}_fresh_follow_on",
        priority=4,
        kind="standalone_review_batch",
        intended_use="Fresh mixed microtext rows after the parallel handoff is underway.",
        folder_path=folder,
        zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_next_review_batch_{date_label}.zip",
        build_report=f"{folder}/next_review_batch_build_report.json",
        verification_report=f"derived/quality/next_review_batch_zip_verification_{date_label}.json",
        processing_report=f"derived/human_adjudication/processed_returns/{date_label}_next_review_batch_status/processing_summary.json",
        return_command=(
            "python tools\\process_next_review_batch_return.py --root . "
            "--batch-root <returned_next_batch_folder> "
            "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
        ),
    )


def optional_source_family_packet_spec(date_label: str) -> PacketSpec:
    folder = f"derived/human_adjudication/{date_label}_v2_0_source_family_review_batch"
    return PacketSpec(
        packet_id=f"{date_label}_source_family_review",
        priority=5,
        kind="standalone_review_batch",
        intended_use=(
            "Fresh non-overlapping source-family review rows focused on VisualDiff revision "
            "families and diverse microtext source coverage."
        ),
        folder_path=folder,
        zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_source_family_review_batch_{date_label}.zip",
        build_report=f"derived/quality/source_family_review_batch_{date_label}.json",
        verification_report=f"derived/quality/source_family_review_batch_zip_verification_{date_label}.json",
        processing_report=f"derived/human_adjudication/processed_returns/{date_label}_source_family_review_batch_status/processing_summary.json",
        return_command=(
            "python tools\\process_next_review_batch_return.py --root . "
            "--batch-root <returned_source_family_batch_folder> "
            "--output-dir derived\\human_adjudication\\processed_returns\\<date> --strict"
        ),
    )


def totals_from_processing(processing: dict[str, Any]) -> dict[str, int]:
    totals = processing.get("totals") if isinstance(processing.get("totals"), dict) else {}
    blank_rows = safe_int(totals.get("blank_rows"))
    if not blank_rows:
        for key in ("tasks", "packs"):
            for item in processing.get(key, []) if isinstance(processing.get(key), list) else []:
                apply_stats = item.get("apply_stats") if isinstance(item.get("apply_stats"), dict) else {}
                status_counts = item.get("status_counts") if isinstance(item.get("status_counts"), dict) else {}
                blank_rows += safe_int(apply_stats.get("blank") or status_counts.get(""))
    rows = safe_int(totals.get("rows") or totals.get("checklist_rows"))
    return {
        "processing_rows": rows,
        "blank_rows": blank_rows,
        "mergeable_rows": safe_int(totals.get("mergeable_rows")),
        "missing_evidence_refs": safe_int(totals.get("missing_evidence_refs")),
    }


def human_status(processing: dict[str, Any], review_rows: int, processing_totals: dict[str, int]) -> str:
    if not processing:
        return "not_smoke_processed"
    if processing.get("complete"):
        return "returned_complete"
    if processing_totals["blank_rows"] and processing_totals["blank_rows"] == review_rows:
        return "unfilled"
    if (
        processing_totals["processing_rows"]
        and processing_totals["blank_rows"] == processing_totals["processing_rows"]
        and review_rows > processing_totals["processing_rows"]
    ):
        return "unfilled_primary_plus_extra_pack"
    if processing_totals["mergeable_rows"]:
        return "returned_partial_or_pending_merge"
    return "unfilled_or_partial"


def summarize_packet(root: Path, spec: PacketSpec) -> dict[str, Any]:
    build = read_json(root / spec.build_report)
    verification = read_json(root / spec.verification_report)
    processing = read_json(root / spec.processing_report)
    zip_exists = (root / spec.zip_path).exists()
    verified = bool(verification.get("valid")) and not verification.get("issues")
    processing_totals = totals_from_processing(processing)

    verification_totals = verification.get("totals") if isinstance(verification.get("totals"), dict) else {}
    build_rows = safe_int(build.get("rows"))
    verified_rows = safe_int(verification_totals.get("checklist_rows"))
    extra_rows = verified_rows
    excluded_reviewed = safe_int(build.get("excluded_reviewed_rows"))
    excluded_active = safe_int(build.get("excluded_active_gold_rows"))
    excluded_packet = safe_int(build.get("excluded_packet_rows"))
    overlap_count = safe_int(build.get("overlap_count"))
    if spec.kind == "parallel_handoff":
        primary_rows = processing_totals["processing_rows"]
        review_rows = primary_rows + extra_rows
        source_rows = review_rows
        notes = (
            f"{primary_rows} rows from the 5/25 packet plus "
            f"{extra_rows} extra machine-proposal rows."
        )
    else:
        # The verifier counts the checklist rows actually present in the ZIP, so
        # it is authoritative when a central build-report copy becomes stale.
        review_rows = verified_rows if verified and verified_rows else build_rows or verified_rows
        primary_rows = review_rows
        source_rows = safe_int(build.get("source_rows")) or review_rows
        notes = (
            f"{excluded_reviewed} reviewed rows, {excluded_active} active-gold rows, "
            f"and {excluded_packet} active-packet rows excluded before packaging."
        )
        if verified_rows and build_rows and verified_rows != build_rows:
            notes += f" Verified packet contains {verified_rows} rows; build report records {build_rows}."

    issues = len(verification.get("issues") or []) + len(build.get("issues") or [])
    missing_evidence = processing_totals["missing_evidence_refs"]
    ready_to_send = bool(zip_exists and verified and review_rows > 0 and issues == 0 and missing_evidence == 0)
    return {
        "priority": spec.priority,
        "packet_id": spec.packet_id,
        "kind": spec.kind,
        "intended_use": spec.intended_use,
        "folder_path": spec.folder_path,
        "zip_path": spec.zip_path,
        "zip_exists": zip_exists,
        "ready_to_send": ready_to_send,
        "verified": verified,
        "human_status": human_status(processing, review_rows, processing_totals),
        "review_rows": review_rows,
        "primary_rows": primary_rows,
        "extra_review_rows": extra_rows if spec.kind == "parallel_handoff" else 0,
        "source_rows": source_rows,
        "excluded_reviewed_rows": excluded_reviewed,
        "excluded_active_gold_rows": excluded_active,
        "excluded_packet_rows": excluded_packet,
        "overlap_count": overlap_count,
        "blank_rows": processing_totals["blank_rows"],
        "mergeable_rows": processing_totals["mergeable_rows"],
        "missing_evidence_refs": missing_evidence,
        "zip_entries": safe_int(verification.get("entry_count") or build.get("zipped_entries") or build.get("zip_entries")),
        "crop_files": safe_int(verification_totals.get("crop_files")),
        "page_files": safe_int(verification_totals.get("page_files")),
        "issues": issues,
        "return_command": spec.return_command,
        "notes": notes,
    }


def optional_packet_exists(root: Path, spec: PacketSpec) -> bool:
    return (
        (root / spec.zip_path).exists()
        or (root / spec.build_report).exists()
        or (root / spec.verification_report).exists()
    )


def base_index_packets(root: Path, base_date_label: str | None) -> list[dict[str, Any]]:
    if not base_date_label:
        return []
    index = read_json(root / "derived" / "quality" / f"human_packet_index_{base_date_label}.json")
    packets = index.get("packets")
    if not isinstance(packets, list):
        return []
    return [dict(packet) for packet in packets if isinstance(packet, dict)]


def next_packet_priority(packets: list[dict[str, Any]]) -> int:
    priorities = [safe_int(packet.get("priority")) for packet in packets]
    return (max(priorities) if priorities else 3) + 1


def build_index(
    root: Path,
    date_label: str,
    base_date_label: str | None = None,
    carry_forward_date_labels: list[str] | None = None,
) -> dict[str, Any]:
    base_date = base_date_label or date_label
    packets = base_index_packets(root, base_date_label)
    specs = [] if packets else packet_specs(base_date)
    carry_labels = list(dict.fromkeys(carry_forward_date_labels or []))
    optional_labels = list(dict.fromkeys([*carry_labels, date_label]))
    existing_packet_ids = {str(packet.get("packet_id") or "") for packet in packets}
    for label in optional_labels:
        if label != base_date:
            next_review_spec = optional_next_review_packet_spec(label)._replace(
                priority=next_packet_priority(packets) if packets else 4 + len(specs) - 3
            )
            if (
                optional_packet_exists(root, next_review_spec)
                and next_review_spec.packet_id not in existing_packet_ids
            ):
                row = summarize_packet(root, next_review_spec) if packets else None
                if row:
                    packets.append(row)
                    existing_packet_ids.add(next_review_spec.packet_id)
                else:
                    specs.append(next_review_spec)
        unpacketed_spec = optional_unpacketed_packet_spec(label)._replace(
            priority=next_packet_priority(packets) if packets else 4 + len(specs) - 3
        )
        if optional_packet_exists(root, unpacketed_spec) and unpacketed_spec.packet_id not in existing_packet_ids:
            row = summarize_packet(root, unpacketed_spec) if packets else None
            if row:
                packets.append(row)
                existing_packet_ids.add(unpacketed_spec.packet_id)
            else:
                specs.append(unpacketed_spec)
        source_family_spec = optional_source_family_packet_spec(label)._replace(
            priority=next_packet_priority(packets) if packets else 4 + len(specs) - 3
        )
        if (
            optional_packet_exists(root, source_family_spec)
            and source_family_spec.packet_id not in existing_packet_ids
        ):
            row = summarize_packet(root, source_family_spec) if packets else None
            if row:
                packets.append(row)
                existing_packet_ids.add(source_family_spec.packet_id)
            else:
                specs.append(source_family_spec)
    if specs:
        packets.extend(summarize_packet(root, spec) for spec in specs)
    totals = {
        "packets": len(packets),
        "ready_to_send": sum(1 for row in packets if row["ready_to_send"]),
        "verified_packets": sum(1 for row in packets if row["verified"]),
        "review_rows": sum(int(row["review_rows"]) for row in packets),
        "primary_rows": sum(int(row["primary_rows"]) for row in packets),
        "extra_review_rows": sum(int(row["extra_review_rows"]) for row in packets),
        "source_rows": sum(int(row["source_rows"]) for row in packets),
        "excluded_reviewed_rows": sum(int(row["excluded_reviewed_rows"]) for row in packets),
        "excluded_active_gold_rows": sum(int(row["excluded_active_gold_rows"]) for row in packets),
        "excluded_packet_rows": sum(int(row["excluded_packet_rows"]) for row in packets),
        "overlap_count": sum(int(row["overlap_count"]) for row in packets),
        "blank_rows": sum(int(row["blank_rows"]) for row in packets),
        "mergeable_rows": sum(int(row["mergeable_rows"]) for row in packets),
        "missing_evidence_refs": sum(int(row["missing_evidence_refs"]) for row in packets),
        "zip_entries": sum(int(row["zip_entries"]) for row in packets),
        "crop_files": sum(int(row["crop_files"]) for row in packets),
        "page_files": sum(int(row["page_files"]) for row in packets),
    }
    return {
        "date_label": date_label,
        "base_date_label": base_date,
        "carry_forward_date_labels": carry_labels,
        "totals": totals,
        "packets": packets,
    }


def render_markdown(index: dict[str, Any]) -> str:
    totals = index["totals"]
    lines = [
        "# Eng_Bench Human Packet Index",
        "",
        f"- Date label: `{index['date_label']}`",
        f"- Base packet date label: `{index.get('base_date_label', index['date_label'])}`",
        f"- Carried follow-on packet dates: `{', '.join(index.get('carry_forward_date_labels', [])) or 'none'}`",
        f"- Packets: `{totals['packets']}`",
        f"- Ready to send: `{totals['ready_to_send']}`",
        f"- Verified packets: `{totals['verified_packets']}`",
        f"- Review rows across packets: `{totals['review_rows']}`",
        f"- Primary packet rows: `{totals['primary_rows']}`",
        f"- Extra machine-proposal rows: `{totals['extra_review_rows']}`",
        f"- Excluded already-reviewed rows: `{totals['excluded_reviewed_rows']}`",
        f"- Excluded active-gold rows: `{totals['excluded_active_gold_rows']}`",
        f"- Excluded active-packet rows: `{totals.get('excluded_packet_rows', 0)}`",
        f"- Blank/unfilled rows in smoke checks: `{totals['blank_rows']}`",
        f"- Missing evidence refs: `{totals['missing_evidence_refs']}`",
        f"- Duplicate overlap count: `{totals['overlap_count']}`",
        "",
        "## Important",
        "",
        "- Send the current ZIPs listed below, not the obsolete broad 948-row follow-on packet.",
        "- Do not merge unreviewed rows into gold; returned CSVs must go through the processing commands here.",
        "- Recommended order: follow the priority order in the packet table below.",
        "",
        "## Packet Order",
        "",
        "| Priority | Packet | Use | Rows | Send | Status | Verified | Missing Evidence | ZIP Entries | Evidence |",
        "| ---: | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in sorted(index["packets"], key=lambda item: int(item["priority"])):
        evidence = f"{row['crop_files']} crops / {row['page_files']} pages"
        lines.append(
            f"| {row['priority']} | `{row['packet_id']}` | {row['intended_use']} | "
            f"{row['review_rows']} | `{str(row['ready_to_send']).lower()}` | `{row['human_status']}` | "
            f"`{str(row['verified']).lower()}` | {row['missing_evidence_refs']} | {row['zip_entries']} | {evidence} |"
        )
    lines.extend(["", "## ZIPs To Send", ""])
    for row in sorted(index["packets"], key=lambda item: int(item["priority"])):
        if row["ready_to_send"]:
            lines.append(f"- `{row['zip_path']}`")
    lines.extend(["", "## Return Processing Commands", ""])
    for row in sorted(index["packets"], key=lambda item: int(item["priority"])):
        lines.append(f"### {row['packet_id']}")
        lines.append("")
        lines.append("```powershell")
        lines.append(row["return_command"])
        lines.append("```")
        lines.append("")
        lines.append(f"Notes: {row['notes']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Eng_Bench human packet index.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default="2026-06-03")
    parser.add_argument(
        "--base-date-label",
        help="Use this date's existing packet set while writing a new dated index.",
    )
    parser.add_argument(
        "--carry-forward-date-label",
        action="append",
        default=[],
        help="Retain an earlier optional unpacketed review packet in the new index; repeat as needed.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    index = build_index(
        root,
        args.date_label,
        base_date_label=args.base_date_label,
        carry_forward_date_labels=args.carry_forward_date_label,
    )
    output_json = args.output_json or f"derived/quality/human_packet_index_{args.date_label}.json"
    output_md = args.output_md or f"derived/human_adjudication/HUMAN_PACKET_INDEX_{args.date_label}.md"
    output_csv = args.output_csv or f"derived/human_adjudication/HUMAN_PACKET_INDEX_{args.date_label}.csv"
    write_json(root / output_json, index)
    (root / output_md).parent.mkdir(parents=True, exist_ok=True)
    (root / output_md).write_text(render_markdown(index), encoding="utf-8")
    write_csv(root / output_csv, index["packets"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(index["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
