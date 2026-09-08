#!/usr/bin/env python3
"""Append the 2026-05-23 confident-source expansion tranche.

This adds 25 more Chrome-validated direct import candidates and writes a
combined 75-source confidence packet that reclassifies the previous 50 as
direct import candidates or source pools.
"""
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
ds_051|candidate/datasheet_spec/arduino_due|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/due/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Due hardware page loaded with datasheet, schematic, pinout, and CAD/resource signals.
ds_052|candidate/datasheet_spec/arduino_leonardo|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/leonardo/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Leonardo hardware page loaded with board documentation and schematic/pinout resource signals.
ds_053|candidate/datasheet_spec/arduino_micro|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/micro/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Micro hardware page loaded with board documentation and schematic/pinout resource signals.
ds_054|candidate/datasheet_spec/arduino_nano_every|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-every/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Nano Every hardware page loaded with dense Nano-family board resources.
ds_055|candidate/datasheet_spec/arduino_mkr_wan_1310|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-wan-1310/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_and_iab_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR WAN 1310 page loaded in Chrome and spot-checked in the in-app Browser; no placeholder/error page.
ds_056|candidate/datasheet_spec/arduino_mkr_nb_1500|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-nb-1500/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR NB 1500 hardware page loaded with board documentation and schematic/pinout resource signals.
ds_057|candidate/datasheet_spec/arduino_mkr_gsm_1400|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-gsm-1400/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino MKR GSM 1400 hardware page loaded with board documentation and schematic/pinout resource signals.
ds_058|candidate/datasheet_spec/arduino_zero|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/zero/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Zero hardware page loaded with board documentation and schematic/pinout resource signals.
ds_060|candidate/datasheet_spec/arduino_nano_r4|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-r4/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Arduino Nano R4 hardware page loaded with Nano-family documentation and board-resource signals.
pcb_052|candidate/pcb_schematic/adafruit_feather_32u4_adalogger|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-32u4-Adalogger-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Feather 32u4 Adalogger PCB repository loaded; title states PCB files and GitHub repo exposes source tree.
pcb_053|candidate/pcb_schematic/adafruit_feather_32u4_basic_proto|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-32u4-Basic-Proto-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Feather 32u4 Basic Proto PCB repository loaded; strong Feather-family revision source.
pcb_054|candidate/pcb_schematic/adafruit_feather_m0_basic_proto|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-M0-Basic-Proto-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Feather M0 Basic Proto PCB repository loaded; pairs with other Feather M0/32u4 variants.
pcb_055|candidate/pcb_schematic/adafruit_itsybitsy_m4_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-ItsyBitsy-M4-Express-PCB|adafruit_itsybitsy_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_and_iab_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit ItsyBitsy M4 Express PCB repository loaded in Chrome and spot-checked in the in-app Browser.
pcb_056|candidate/pcb_schematic/adafruit_itsybitsy_32u4|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-ItsyBitsy-32u4-PCB|adafruit_itsybitsy_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit ItsyBitsy 32u4 PCB repository loaded; useful close-family comparison with ItsyBitsy M4/RP2040.
pcb_057|candidate/pcb_schematic/adafruit_qt_py_esp32_s3|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-QT-Py-ESP32-S3-PCB|adafruit_qt_py_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit QT Py ESP32-S3 PCB repository loaded; useful with QT Py RP2040/SAMD family.
pcb_060|candidate/pcb_schematic/adafruit_trinket_m0|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Trinket-M0-PCB|adafruit_trinket_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Adafruit Trinket M0 PCB repository loaded; compact board schematic/layout source.
mech_053|candidate/mechanical_cad/rat_rig_v_minion|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/Rat-Rig/V-Minion|rat_rig_printer_hardware|repo_cad_stl_bom|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|Rat Rig V-Minion repository loaded; mechanical printer CAD/STL/BOM source.
mech_055|candidate/mechanical_cad/v1engineering_mpcnc_primo|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/V1EngineeringInc/MPCNC_Primo|v1engineering_cnc_hardware|repo_stl_cad_parts|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_and_iab_strict_loaded_direct_asset_signal|direct_import_candidate|direct_or_repo_import_candidate|V1Engineering MPCNC Primo repository loaded in Chrome and spot-checked in the in-app Browser; exposes STL/part folders.
pid_051|candidate/pid/wikimedia_amine_treating_svg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:AmineTreating.svg|commons_process_flow_diagrams|commons_file_page_svg|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for AmineTreating SVG loaded; per-file license capture required before promotion.
pid_052|candidate/pid/wikimedia_absorption_chiller_scheme_svg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Absorption_chiller_scheme.svg|commons_process_flow_diagrams|commons_file_page_svg|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_and_iab_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for Absorption chiller scheme SVG loaded in Chrome and spot-checked in the in-app Browser.
pid_054|candidate/pid/wikimedia_aminwaesche_jpg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Aminw%C3%A4sche.jpg|commons_process_flow_diagrams|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for Aminwaesche process image loaded; per-file license capture required before promotion.
pid_057|candidate/pid/wikimedia_schema_pid1_jpg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID1.jpg|commons_pid_examples|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for Schema P&ID1 loaded; per-file license capture required.
pid_058|candidate/pid/wikimedia_back_mixing_digester_png|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Back_Mixing_and_Digester.png|commons_process_flow_diagrams|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for Back Mixing and Digester process diagram loaded.
pid_059|candidate/pid/wikimedia_insulin_process_png|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Aliran_Proses_Untuk_Pembuatan_Insulin.png|commons_process_flow_diagrams|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for insulin-production process diagram loaded.
pid_060|candidate/pid/wikimedia_absorptionskuehlung_abb_2_jpg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Absorptionskuehlung_Abb_2.jpg|commons_process_flow_diagrams|commons_file_page_image|low|medium|B|license_capture_then_intake|release_candidate|validated_commons_file_candidate|chrome_strict_loaded_direct_file_page|direct_import_candidate|direct_file_page_candidate|Commons direct file page for absorption-cooling process image loaded.
"""

SOURCE_POOL_IDS = {
    "arch_016",
    "arch_017",
    "arch_018",
    "arch_019",
    "arch_020",
    "pid_031",
    "pid_032",
    "pid_033",
    "pid_034",
    "pid_035",
    "pid_036",
    "pid_037",
    "pid_038",
    "pid_039",
    "pid_040",
    "pid_041",
}

REJECTED_ROWS = [
    ("ds_059", "Arduino UNO R3 URL reached a placeholder/error page"),
    ("pcb_058", "GitHub page not found"),
    ("pcb_059", "GitHub page not found"),
    ("mech_051", "GitHub page not found"),
    ("mech_052", "GitHub page not found"),
    ("mech_054", "GitHub page not found"),
    ("mech_056", "GitHub page not found"),
    ("pid_053", "Accepted by Chrome but omitted from 75-source tranche because it duplicates the AmineTreating SVG diagram family"),
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


def prior_confidence_row(row: dict[str, str]) -> dict[str, str]:
    candidate_id = row["candidate_id"]
    source_pool = candidate_id in SOURCE_POOL_IDS
    return {
        **row,
        "confidence_status": "usable_source_pool" if source_pool else "direct_import_candidate",
        "source_mode": "source_pool_needs_per_file_selection" if source_pool else "direct_or_repo_import_candidate",
        "validation_round": "strict_recheck_2026-05-23",
        "confidence_notes": (
            "Strict Chrome recheck passed as a source pool; select per-file assets before import."
            if source_pool
            else "Strict Chrome recheck passed as a direct import candidate."
        ),
    }


def new_confidence_row(row: dict[str, str]) -> dict[str, str]:
    return {
        **{field: row.get(field, "") for field in CANDIDATE_FIELDS},
        "confidence_status": row["confidence_status"],
        "source_mode": row["source_mode"],
        "validation_round": "chrome_strict_validation_2026-05-23",
        "confidence_notes": row["notes"],
    }


def write_packets(root: Path, new_rows: list[dict[str, str]]) -> None:
    packet_dir = root / "derived" / "source_imports"
    packet_dir.mkdir(parents=True, exist_ok=True)
    prior_path = packet_dir / "browser_validated_50_more_sources_2026-05-22.csv"
    prior_rows = read_csv(prior_path)
    if len(prior_rows) != 50:
        raise SystemExit(f"Expected prior 50-source packet at {prior_path}, found {len(prior_rows)} rows")

    combined_rows = [prior_confidence_row(row) for row in prior_rows] + [new_confidence_row(row) for row in new_rows]
    if len(combined_rows) != 75:
        raise SystemExit(f"Expected combined 75-source packet, found {len(combined_rows)} rows")

    combined_csv = packet_dir / "browser_validated_75_sources_2026-05-23.csv"
    combined_md = packet_dir / "browser_validated_75_sources_2026-05-23.md"
    rejected_csv = packet_dir / "browser_rejected_additional_source_candidates_2026-05-23.csv"
    fieldnames = CANDIDATE_FIELDS + ["confidence_status", "source_mode", "validation_round", "confidence_notes"]
    with combined_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fieldnames} for row in combined_rows])

    with rejected_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "reject_reason"])
        writer.writeheader()
        for candidate_id, reason in REJECTED_ROWS:
            writer.writerow({"candidate_id": candidate_id, "reject_reason": reason})

    by_domain = Counter(row["domain"] for row in combined_rows)
    by_status = Counter(row["confidence_status"] for row in combined_rows)
    new_by_domain = Counter(row["domain"] for row in new_rows)
    lines = [
        "# Browser-Validated 75 Sources - 2026-05-23",
        "",
        "## Bottom Line",
        "- The previous 50-source packet was rechecked with a stricter Chrome gate: `34` direct import candidates, `16` usable source pools, `0` rejects.",
        "- Added `25` more direct import candidates after checking `33` fresh URLs in Chrome.",
        "- Four representative new sources were also spot-checked in the in-app Browser: `ds_055`, `pcb_055`, `mech_055`, and `pid_052`.",
        "- This packet is still source intake, not gold annotation. Direct candidates still need download/import, license capture, rendering, candidate annotation, and review.",
        "",
        "## 75-Source Confidence Mix",
    ]
    lines.extend(f"- {status}: {count}" for status, count in sorted(by_status.items()))
    lines.extend(["", "## 75-Source Domain Mix"])
    lines.extend(f"- {domain}: {count}" for domain, count in sorted(by_domain.items()))
    lines.extend(["", "## Newly Added 25 Domain Mix"])
    lines.extend(f"- {domain}: {count}" for domain, count in sorted(new_by_domain.items()))
    lines.extend(
        [
            "",
            "## Important Clarification",
            "- `direct_import_candidate` means the page/repository/file itself can plausibly enter the import/render pipeline.",
            "- `usable_source_pool` means the page is useful for finding public engineering files, but a specific file still needs to be selected and licensed before import.",
            "",
            "## Rejections / Omissions",
            f"- Rejection ledger: `{rejected_csv.as_posix()}`",
            "- `pid_053` loaded, but was omitted because it is a duplicate-family PNG for the accepted `pid_051` SVG.",
            "",
            "## New Candidate IDs",
            ", ".join(f"`{row['candidate_id']}`" for row in new_rows),
            "",
        ]
    )
    combined_md.write_text("\n".join(lines), encoding="utf-8")


def append_worklog(root: Path, added_candidates: int, added_validations: int) -> None:
    worklog = root / "TRACK_B_WORKLOG.md"
    entry = (
        "\n## 2026-05-23 75-Source Confidence Check\n"
        "- Strict Chrome recheck of prior 50: `34` direct import candidates, `16` usable source pools, `0` rejects.\n"
        "- Fresh Chrome validation checked `33` additional URLs and selected `25` direct import candidates.\n"
        f"- Added candidate rows: `{added_candidates}`.\n"
        f"- Added validation rows: `{added_validations}`.\n"
        "- Dated packet: `derived/source_imports/browser_validated_75_sources_2026-05-23.md`.\n"
        "- Status: source intake only; gold status still requires import/render/text-layer/candidate annotation/human review.\n"
    )
    existing = worklog.read_text(encoding="utf-8") if worklog.exists() else "# Track B Worklog\n"
    if "2026-05-23 75-Source Confidence Check" not in existing:
        worklog.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Add 25 direct sources and write a 75-source confidence packet")
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
            "validation_date": "2026-05-23",
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

    print(f"[OK] New direct rows in tranche: {len(rows)}")
    print(f"[OK] Added candidate rows: {added_candidates}")
    print(f"[OK] Added validation rows: {added_validations}")
    print(f"[OK] New domains: {dict(sorted(Counter(row['domain'] for row in rows).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
