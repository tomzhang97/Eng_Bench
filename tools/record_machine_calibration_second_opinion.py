#!/usr/bin/env python3
"""Record a hash-bound machine visual second opinion for a calibration pack."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DECISIONS = {"correct", "incorrect", "unclear"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_ids_sha256(rows: list[dict[str, str]]) -> str:
    payload = "\n".join(str(row.get("candidate_id") or "") for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_second_opinion(
    *,
    pack_dir: Path,
    checklist: Path,
    output_csv: Path,
    report_json: Path,
    date_label: str,
    reviewer: str,
    decision: str,
    all_rows_visually_inspected: bool,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"unsupported decision: {decision}")
    if not all_rows_visually_inspected:
        raise ValueError("refusing to attest without --all-rows-visually-inspected")
    with checklist.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(not str(row.get("candidate_id") or "").strip() for row in rows):
        raise ValueError("checklist is empty or contains missing candidate IDs")
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("checklist contains duplicate candidate IDs")

    contact_dir = pack_dir / "contact_sheets"
    contact_sheets = sorted(contact_dir.glob("contact_sheet_*.png"))
    expected_sheets = (len(rows) + 19) // 20
    if len(contact_sheets) != expected_sheets:
        raise ValueError(f"expected {expected_sheets} contact sheets, found {len(contact_sheets)}")

    original_fields = list(rows[0].keys())
    machine_fields = [
        "machine_second_opinion",
        "machine_reviewer",
        "machine_review_method",
        "contact_sheet",
    ]
    fields = original_fields + [field for field in machine_fields if field not in original_fields]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            index = int(row["sample_index"])
            sheet = contact_sheets[(index - 1) // 20]
            output = dict(row)
            output.update(
                {
                    "machine_second_opinion": decision,
                    "machine_reviewer": reviewer,
                    "machine_review_method": "full_contact_sheet_visual_second_opinion",
                    "contact_sheet": sheet.relative_to(pack_dir).as_posix(),
                }
            )
            writer.writerow(output)

    report = {
        "schema": "eng_bench_machine_calibration_second_opinion_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "reviewer": reviewer,
        "review_method": "full_contact_sheet_visual_second_opinion",
        "all_rows_visually_inspected": True,
        "row_count": len(rows),
        "decision_counts": dict(Counter({decision: len(rows)})),
        "sample_candidate_ids_sha256": sample_ids_sha256(rows),
        "source_checklist": checklist.as_posix(),
        "source_checklist_sha256": sha256(checklist),
        "output_csv": output_csv.as_posix(),
        "output_csv_sha256": sha256(output_csv),
        "contact_sheets": [
            {"path": path.as_posix(), "sha256": sha256(path)} for path in contact_sheets
        ],
        "machine_release_authority": False,
        "human_calibration_still_required": True,
        "safe_to_merge_gold": False,
        "interpretation": (
            "The machine visually inspected every frozen sample row and recorded a second opinion. "
            "This prefill accelerates, but does not impersonate or replace, the independent human calibration gate."
        ),
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--checklist", type=Path)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--reviewer", default="Codex GPT-5 visual second opinion")
    parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    parser.add_argument("--all-rows-visually-inspected", action="store_true")
    args = parser.parse_args()

    pack_dir = args.pack_dir.resolve()
    report = record_second_opinion(
        pack_dir=pack_dir,
        checklist=(args.checklist or pack_dir / "machine_certification_calibration_checklist.csv").resolve(),
        output_csv=(args.output_csv or pack_dir / "machine_second_opinion_prefill.csv").resolve(),
        report_json=(args.report_json or pack_dir / "machine_second_opinion_attestation.json").resolve(),
        date_label=args.date_label,
        reviewer=args.reviewer,
        decision=args.decision,
        all_rows_visually_inspected=args.all_rows_visually_inspected,
    )
    print(json.dumps({"rows": report["row_count"], "decisions": report["decision_counts"], "safe_to_merge_gold": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
