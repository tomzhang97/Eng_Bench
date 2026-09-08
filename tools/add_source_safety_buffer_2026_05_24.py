#!/usr/bin/env python3
"""Append a 25-source safety buffer and write a 100-source confidence packet."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from io import StringIO
from pathlib import Path


CANDIDATE_FIELDS = [
    "candidate_id",
    "path_hint",
    "domain",
    "task_fit",
    "rights_tier",
    "source_url",
    "source_family",
    "likely_asset_type",
    "revision_family_potential",
    "annotation_yield",
    "priority_bucket",
    "notes",
]

VALIDATION_FIELDS = [
    "candidate_id",
    "validation_date",
    "browser_result",
    "source_validity",
    "release_posture",
    "next_action",
    "notes",
]

NEW_ROWS = r"""candidate_id|path_hint|domain|task_fit|rights_tier|source_url|source_family|likely_asset_type|revision_family_potential|annotation_yield|priority_bucket|next_action|release_posture|source_validity|browser_result|confidence_status|source_mode|notes
ds_061|candidate/datasheet_spec/arduino_mkr_1000_wifi|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-1000-wifi/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR 1000 WiFi page loaded with dense board-documentation, schematic, pinout, and CAD/resource signals.
ds_062|candidate/datasheet_spec/arduino_mkr_fox_1200|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-fox-1200/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR FOX 1200 page loaded with board-resource and documentation signals.
ds_063|candidate/datasheet_spec/arduino_mkr_485_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-485-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR 485 Shield page loaded with hardware documentation and pinout/schematic resource signals.
ds_064|candidate/datasheet_spec/arduino_mkr_can_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-can-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR CAN Shield page loaded with hardware documentation and pinout/schematic resource signals.
ds_065|candidate/datasheet_spec/arduino_mkr_eth_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-eth-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR ETH Shield page loaded with hardware documentation and board-resource signals.
ds_066|candidate/datasheet_spec/arduino_mkr_iot_carrier_rev2|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-iot-carrier-rev2/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR IoT Carrier Rev2 page loaded with carrier-board documentation and resource signals.
ds_067|candidate/datasheet_spec/arduino_giga_display_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/giga-display-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino GIGA Display Shield page loaded with board-resource signals.
ds_068|candidate/datasheet_spec/arduino_portenta_breakout|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/portenta-breakout/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Portenta Breakout page loaded with connector/pinout and board-resource signals.
ds_069|candidate/datasheet_spec/arduino_portenta_machine_control|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/portenta-machine-control/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|high|A|intake_first|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Portenta Machine Control page loaded; strong industrial-control hardware source with dense terminal/IO labels.
ds_070|candidate/datasheet_spec/arduino_nicla_sense_me|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nicla-sense-me/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Nicla Sense ME page loaded with small-board datasheet and pinout resource signals.
ds_071|candidate/datasheet_spec/arduino_nicla_sense_env|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nicla-sense-env/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Nicla Sense Env page loaded with hardware documentation and resource signals.
ds_072|candidate/datasheet_spec/arduino_nano_33_ble_sense_rev2|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-33-ble-sense-rev2/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Nano 33 BLE Sense Rev2 page loaded with Nano-family board documentation and resource signals.
ds_073|candidate/datasheet_spec/arduino_mkr_env_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-env-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR ENV Shield page loaded with documentation, pinout, and board-resource signals.
ds_074|candidate/datasheet_spec/arduino_mkr_gps_shield|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-gps-shield/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR GPS Shield page loaded with documentation, pinout, and board-resource signals.
pcb_066|candidate/pcb_schematic/adafruit_huzzah32_esp32_feather|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-HUZZAH32-ESP32-Feather-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit HUZZAH32 ESP32 Feather PCB repository loaded; title states PCB files and repo exposes source tree.
pcb_068|candidate/pcb_schematic/adafruit_feather_nrf52840_sense|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-nRF52840-Sense-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Feather nRF52840 Sense PCB repository loaded; useful with existing Feather nRF52840 and Feather family sources.
pcb_069|candidate/pcb_schematic/adafruit_metro_m0_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Metro-M0-Express-PCB|adafruit_metro_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Metro M0 Express PCB repository loaded; Metro-family board design source.
pcb_070|candidate/pcb_schematic/adafruit_circuit_playground_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Circuit-Playground-Express-PCB|adafruit_circuit_playground_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Circuit Playground Express PCB repository loaded; board-level schematic/layout source.
pcb_072|candidate/pcb_schematic/adafruit_clue|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-CLUE-PCB|adafruit_clue_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit CLUE PCB repository loaded; compact sensor-board schematic/layout source.
pcb_074|candidate/pcb_schematic/adafruit_esp32_s2_tft_feather|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-ESP32-S2-TFT-Feather-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit ESP32-S2 TFT Feather PCB repository loaded; useful display-board Feather variant.
pcb_075|candidate/pcb_schematic/adafruit_esp32_s3_tft_feather|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-ESP32-S3-TFT-Feather-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit ESP32-S3 TFT Feather PCB repository loaded; close-family visualdiff candidate with ESP32-S2 TFT Feather.
mech_064|candidate/mechanical_cad/original_prusa_enclosure|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/prusa3d/Original-Prusa-Enclosure|prusa_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Original Prusa Enclosure repository loaded; equipment/mechanical CAD source.
pid_063|candidate/pid/wikimedia_akm_png|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:AKM.png|commons_process_flow_diagrams|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for AKM process-flow image loaded; per-file license capture required.
pid_064|candidate/pid/wikimedia_complex_procedure_svg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:A_complex_procedure_composed_of_simpler_procedures.svg|commons_process_flow_diagrams|commons_file_page_svg|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for procedure/process SVG loaded; per-file license capture required.
pid_065|candidate/pid/wikimedia_blue_grass_piping_designer_jpg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Blue_Grass_Chemical_Agent-Destruction_Pilot_Plant_Piping_Designer_(28947777487).jpg|commons_pid_examples|commons_file_page_image|low|low|C|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for chemical-plant piping designer image loaded; lower-yield but useful as P&ID/equipment context.
"""

REJECTED_ROWS = [
    ("pcb_061", "GitHub page not found"),
    ("pcb_062", "GitHub page not found"),
    ("pcb_063", "GitHub page not found"),
    ("pcb_064", "GitHub page not found"),
    ("pcb_065", "GitHub page not found"),
    ("pcb_067", "GitHub page not found"),
    ("pcb_071", "GitHub page not found"),
    ("pcb_073", "GitHub page not found"),
    ("mech_061", "GitHub page not found"),
    ("mech_062", "GitHub page not found"),
    ("mech_063", "GitHub page not found"),
    ("ds_075", "Chrome validated as reserve source but not needed for the 25-source buffer"),
    ("ds_076", "Chrome validated as reserve source but not needed for the 25-source buffer"),
    ("ds_077", "Chrome validated as reserve source but not needed for the 25-source buffer"),
    ("ds_078", "Chrome validated as reserve source but not needed for the 25-source buffer"),
]


def pipe_rows(text: str) -> list[dict[str, str]]:
    with StringIO(text) as handle:
        return list(csv.DictReader(handle, delimiter="|"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]], key: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_csv(path)
    existing_keys = {row.get(key, "") for row in existing}
    new_rows = [{field: row.get(field, "") for field in fieldnames} for row in rows if row.get(key) not in existing_keys]
    if not new_rows:
        return 0
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def new_confidence_row(row: dict[str, str]) -> dict[str, str]:
    return {
        **{field: row.get(field, "") for field in CANDIDATE_FIELDS},
        "confidence_status": row["confidence_status"],
        "source_mode": row["source_mode"],
        "validation_round": "chrome_strict_validation_2026-05-24",
        "confidence_notes": row["notes"],
    }


def write_packets(root: Path, rows: list[dict[str, str]]) -> None:
    packet_dir = root / "derived" / "source_imports"
    packet_dir.mkdir(parents=True, exist_ok=True)
    prior_path = packet_dir / "browser_validated_75_sources_2026-05-23.csv"
    prior_rows = read_csv(prior_path)
    if len(prior_rows) != 75:
        raise SystemExit(f"Expected prior 75-source packet at {prior_path}, found {len(prior_rows)} rows")

    fieldnames = CANDIDATE_FIELDS + ["confidence_status", "source_mode", "validation_round", "confidence_notes"]
    combined_rows = [{field: row.get(field, "") for field in fieldnames} for row in prior_rows] + [
        new_confidence_row(row) for row in rows
    ]
    if len(combined_rows) != 100:
        raise SystemExit(f"Expected combined 100-source packet, found {len(combined_rows)} rows")

    combined_csv = packet_dir / "browser_validated_100_sources_2026-05-24.csv"
    combined_md = packet_dir / "browser_validated_100_sources_2026-05-24.md"
    rejected_csv = packet_dir / "browser_rejected_safety_buffer_candidates_2026-05-24.csv"
    new_csv = packet_dir / "browser_validated_safety_buffer_25_sources_2026-05-24.csv"

    with combined_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fieldnames} for row in combined_rows])
    with new_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS + ["next_action", "release_posture", "source_validity", "browser_result", "confidence_status", "source_mode"])
        writer.writeheader()
        writer.writerows(rows)
    with rejected_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "reject_reason"])
        writer.writeheader()
        for candidate_id, reason in REJECTED_ROWS:
            writer.writerow({"candidate_id": candidate_id, "reject_reason": reason})

    by_domain = Counter(row["domain"] for row in combined_rows)
    by_status = Counter(row["confidence_status"] for row in combined_rows)
    new_by_domain = Counter(row["domain"] for row in rows)
    lines = [
        "# Browser-Validated 100 Sources - 2026-05-24",
        "",
        "## Bottom Line",
        "- Added a 25-source safety buffer on top of the 75-source confidence packet.",
        "- The new buffer contains `25` Chrome-strict direct import candidates and `0` source pools.",
        "- Chrome checked `40` fresh/replacement URLs for this buffer; `25` were selected, `11` were rejected as page-not-found, and `4` validated Arduino reserve pages were held back because the buffer was already full.",
        "- In-app Browser spot-check was attempted, but no active Codex browser pane was available in this session. Chrome validation is therefore the authoritative browser evidence for this packet.",
        "",
        "## 100-Source Confidence Mix",
    ]
    lines.extend(f"- {status}: {count}" for status, count in sorted(by_status.items()))
    lines.extend(["", "## 100-Source Domain Mix"])
    lines.extend(f"- {domain}: {count}" for domain, count in sorted(by_domain.items()))
    lines.extend(["", "## New Safety Buffer Domain Mix"])
    lines.extend(f"- {domain}: {count}" for domain, count in sorted(new_by_domain.items()))
    lines.extend(
        [
            "",
            "## Interpretation",
            "- The source-acquisition queue now has a safer cushion: if some candidates fail license capture, rendering, or annotation yield, the pipeline still has many direct replacements.",
            "- This is still not gold benchmark data. Each source must pass import/download, license capture, rendering, candidate annotation, and human review before release promotion.",
            "",
            "## New Candidate IDs",
            ", ".join(f"`{row['candidate_id']}`" for row in rows),
            "",
            "## Rejections / Reserve",
            f"- Rejection and reserve ledger: `{rejected_csv.as_posix()}`",
            "",
        ]
    )
    combined_md.write_text("\n".join(lines), encoding="utf-8")


def append_worklog(root: Path, added_candidates: int, added_validations: int) -> None:
    worklog = root / "TRACK_B_WORKLOG.md"
    entry = (
        "\n## 2026-05-24 Source Safety Buffer\n"
        "- Added a 25-source Chrome-strict direct-import safety buffer.\n"
        f"- Added candidate rows: `{added_candidates}`.\n"
        f"- Added validation rows: `{added_validations}`.\n"
        "- Wrote combined packet: `derived/source_imports/browser_validated_100_sources_2026-05-24.md`.\n"
        "- Browser note: in-app Browser spot-check was attempted but no active Codex browser pane was available; Chrome validation is recorded as authoritative for this tranche.\n"
    )
    existing = worklog.read_text(encoding="utf-8") if worklog.exists() else "# Track B Worklog\n"
    if "2026-05-24 Source Safety Buffer" not in existing:
        worklog.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Add 25 direct sources as a safety buffer")
    parser.add_argument("--root", default=".", help="Eng_Bench repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rows = pipe_rows(NEW_ROWS)
    if len(rows) != 25:
        raise SystemExit(f"Expected 25 new rows, found {len(rows)}")
    ids = [row["candidate_id"] for row in rows]
    duplicates = sorted({candidate_id for candidate_id in ids if ids.count(candidate_id) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate candidate IDs in new rows: {duplicates}")

    candidate_rows = [{field: row.get(field, "") for field in CANDIDATE_FIELDS} for row in rows]
    validation_rows = [
        {
            "candidate_id": row["candidate_id"],
            "validation_date": "2026-05-24",
            "browser_result": row["browser_result"],
            "source_validity": row["source_validity"],
            "release_posture": row["release_posture"],
            "next_action": row["next_action"],
            "notes": row["notes"],
        }
        for row in rows
    ]

    added_candidates = append_rows(root / "SOURCE_CANDIDATES.csv", CANDIDATE_FIELDS, candidate_rows, "candidate_id")
    added_validations = append_rows(root / "SOURCE_CANDIDATE_VALIDATION.csv", VALIDATION_FIELDS, validation_rows, "candidate_id")
    write_packets(root, rows)
    append_worklog(root, added_candidates, added_validations)

    print(f"[OK] New safety-buffer rows: {len(rows)}")
    print(f"[OK] Added candidate rows: {added_candidates}")
    print(f"[OK] Added validation rows: {added_validations}")
    print(f"[OK] New domains: {dict(sorted(Counter(row['domain'] for row in rows).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
