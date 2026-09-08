#!/usr/bin/env python3
"""Append the browser-validated 50-source expansion tranche.

The script is intentionally idempotent. It appends only missing candidate IDs
to the source and validation ledgers, then writes a dated import packet under
derived/source_imports/.
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

ROWS = r"""candidate_id|path_hint|domain|task_fit|rights_tier|source_url|source_family|likely_asset_type|revision_family_potential|annotation_yield|priority_bucket|next_action|release_posture|source_validity|browser_result|notes
ds_031|candidate/datasheet_spec/arduino_giga_r1_wifi|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/giga-r1-wifi/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|high|A|intake_first|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino GIGA R1 WiFi hardware page loaded with datasheet, schematic, pinout, CAD, and board-resource signals.
ds_032|candidate/datasheet_spec/arduino_portenta_h7|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/portenta-h7/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|high|A|intake_first|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino Portenta H7 hardware page loaded with board docs and dense connector/pinout resources.
ds_033|candidate/datasheet_spec/arduino_nicla_vision|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nicla-vision/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino Nicla Vision page loaded with schematic/pinout/datasheet resource signals.
ds_034|candidate/datasheet_spec/arduino_nano_33_ble_rev2|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-33-ble-rev2/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino Nano 33 BLE Rev2 page loaded with board-level docs and pinout resource signals.
ds_035|candidate/datasheet_spec/arduino_nano_33_iot|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-33-iot/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino Nano 33 IoT page loaded with datasheet, schematic, pinout, and CAD resource signals.
ds_036|candidate/datasheet_spec/arduino_mkr_wifi_1010|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-wifi-1010/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino MKR WiFi 1010 page loaded with MKR board documentation and schematic/pinout resources.
ds_037|candidate/datasheet_spec/arduino_mkr_zero|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-zero/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino MKR Zero page loaded with board docs and schematic/pinout resource signals.
ds_038|candidate/datasheet_spec/arduino_mkr_vidor_4000|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-vidor-4000/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino MKR Vidor 4000 page loaded with FPGA board documentation and schematic/pinout resources.
ds_039|candidate/datasheet_spec/arduino_uno_r4_wifi|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/uno-r4-wifi/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino UNO R4 WiFi page loaded with datasheet, schematic, pinout, and CAD signals.
ds_040|candidate/datasheet_spec/arduino_uno_r4_minima|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/uno-r4-minima/|arduino_hardware_docs|docs_page_datasheet_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|validated_public_docs_candidate|chrome_loaded_asset_signal|Arduino UNO R4 Minima page loaded with datasheet, schematic, pinout, and CAD signals.
pcb_031|candidate/pcb_schematic/adafruit_qt_py_rp2040|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-QT-Py-RP2040-PCB|adafruit_qt_py_pcbs|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit QT Py RP2040 PCB repository loaded; suitable for Eagle/PCB schematic import and family comparisons.
pcb_032|candidate/pcb_schematic/adafruit_kb2040|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-KB2040-PCB|adafruit_keyboard_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit KB2040 PCB repository loaded with EagleCAD/PCB source signals.
pcb_033|candidate/pcb_schematic/adafruit_feather_esp32_s2|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-ESP32-S2-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Feather ESP32-S2 PCB repository loaded; useful as part of the Feather revision/family source pool.
pcb_034|candidate/pcb_schematic/adafruit_feather_esp32_s3|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-ESP32-S3-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Feather ESP32-S3 PCB repository loaded; close-family visualdiff source with Feather ESP32-S2.
pcb_035|candidate/pcb_schematic/adafruit_feather_m4_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-M4-Express-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Feather M4 Express PCB repository loaded; adds MCU-family diversity within Feather boards.
pcb_036|candidate/pcb_schematic/adafruit_itsybitsy_rp2040|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-ItsyBitsy-RP2040-PCB|adafruit_itsybitsy_pcbs|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit ItsyBitsy RP2040 PCB repository loaded with PCB design-file signals.
pcb_037|candidate/pcb_schematic/adafruit_metro_rp2040|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Metro-RP2040-PCB|adafruit_metro_pcbs|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Metro RP2040 PCB repository loaded; useful board-scale schematic/layout source.
pcb_038|candidate/pcb_schematic/adafruit_metro_m4_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Metro-M4-Express-PCB|adafruit_metro_pcbs|repo_eagle_sch_brd_pdf|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Metro M4 Express PCB repository loaded with EagleCAD/PCB design-file signals.
pcb_043|candidate/pcb_schematic/olimex_esp32_poe|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://github.com/OLIMEX/ESP32-POE|olimex_esp32_boards|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Olimex ESP32-POE repository loaded; open hardware ESP32 Ethernet board with board/schematic source signals.
pcb_044|candidate/pcb_schematic/olimex_esp32_gateway|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://github.com/OLIMEX/ESP32-GATEWAY|olimex_esp32_boards|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Olimex ESP32-GATEWAY repository loaded; useful for ESP32 gateway board schematic/layout labels.
pcb_046|candidate/pcb_schematic/adafruit_feather_m0_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-M0-Express-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_and_iab_loaded_asset_signal|Adafruit Feather M0 Express PCB repository loaded in Chrome and spot-checked in the in-app Browser.
pcb_047|candidate/pcb_schematic/adafruit_feather_m0_adalogger|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-M0-Adalogger-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|medium|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Adafruit Feather M0 Adalogger PCB repository loaded; pairs naturally with other Feather M0/M4/ESP32 variants.
mech_031|candidate/mechanical_cad/original_prusa_mini|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/prusa3d/Original-Prusa-MINI|prusa_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Original Prusa MINI hardware repository loaded; useful mechanical CAD/STL source with printed-part labels.
mech_033|candidate/mechanical_cad/original_prusa_sl1|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/prusa3d/Original-Prusa-SL1|prusa_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Original Prusa SL1 parts repository loaded; adds resin-printer mechanical equipment source.
mech_034|candidate/mechanical_cad/original_prusa_cw1|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/prusa3d/Original-Prusa-CW1|prusa_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Original Prusa CW1 parts repository loaded; useful equipment/mechanical CAD source.
mech_035|candidate/mechanical_cad/voron_stealthburner|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Stealthburner|voron_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Voron Stealthburner repository loaded; subassembly CAD source for mechanical visualdiff/microtext.
mech_036|candidate/mechanical_cad/voron_tap|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Tap|voron_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Voron Tap repository loaded; toolhead/probe mechanical source with CAD and assembly labels.
mech_037|candidate/mechanical_cad/voron_hardware|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Hardware|voron_printer_hardware|repo_hardware_cad_assets|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Voron Hardware repository loaded; broad set of printer hardware modules and CAD artifacts.
mech_038|candidate/mechanical_cad/rat_rig_v_core_3|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/Rat-Rig/V-core-3|rat_rig_printer_hardware|repo_cad_bom_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Rat Rig V-Core 3 repository loaded; large-format printer CAD/BOM source.
mech_040|candidate/mechanical_cad/science_jubilee|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/machineagency/science-jubilee|science_jubilee|repo_cad_machine_design|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Science Jubilee repository loaded; lab automation/mechanical equipment source.
mech_042|candidate/mechanical_cad/openflexure_microscope_gitlab|mechanical_cad|visualdiff,microtext|cern_ohl_s_2_candidate|https://gitlab.com/openflexure/openflexure-microscope|openflexure_microscope|repo_openscad_stl_docs|medium|high|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|OpenFlexure microscope GitLab repository loaded; strong OpenSCAD/STL documentation source.
mech_043|candidate/mechanical_cad/voron_trident|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Trident|voron_printer_hardware|repo_cad_stl_drawings|high|high|A|intake_first|release_candidate|validated_open_hardware_candidate|chrome_and_iab_loaded_asset_signal|Voron Trident repository loaded in Chrome and spot-checked in the in-app Browser; strong printer-family CAD source.
mech_044|candidate/mechanical_cad/original_prusa_i3|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/prusa3d/Original-Prusa-i3|prusa_printer_hardware|repo_cad_stl_drawings|medium|medium|B|intake_second|release_candidate|validated_open_hardware_candidate|chrome_loaded_asset_signal|Original Prusa i3 repository loaded; mature printer-family source for mechanical visualdiff and labels.
civil_017|candidate/civil/scdot_standard_drawings|civil|visualdiff,microtext|misc_public_candidate|https://www.scdot.org/business/standard-drawings.aspx|scdot_standard_drawings|standard_drawing_pdf_index|high|high|A|intake_first|release_candidate|validated_public_candidate|chrome_loaded_asset_signal|SCDOT Standard Drawings page loaded with standard drawing/plan index signals.
arch_016|candidate/architectural/loc_habs_roof_plan_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=roof%20plan&co=hh|loc_habs_roof_plan_slice|collection_search|medium|high|A|intake_first|release_candidate|validated_public_candidate|chrome_loaded_asset_signal|LOC HABS/HAER/HALS roof-plan search loaded; candidate pool for selecting public-domain plan sheets.
arch_017|candidate/architectural/loc_habs_stair_detail_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=stair%20detail&co=hh|loc_habs_stair_detail_slice|collection_search|medium|high|A|intake_first|release_candidate|validated_public_candidate|chrome_loaded_asset_signal|LOC HABS/HAER/HALS stair-detail search loaded; useful architectural detail label pool.
arch_018|candidate/architectural/loc_habs_window_detail_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=window%20detail&co=hh|loc_habs_window_detail_slice|collection_search|medium|high|A|intake_first|release_candidate|validated_public_candidate|chrome_loaded_asset_signal|LOC HABS/HAER/HALS window-detail search loaded; useful detail and dimension-label pool.
arch_019|candidate/architectural/loc_habs_site_plan_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=site%20plan&co=hh|loc_habs_site_plan_slice|collection_search|medium|high|A|intake_first|release_candidate|validated_public_candidate|chrome_loaded_asset_signal|LOC HABS/HAER/HALS site-plan search loaded; public-domain source pool for civil/architectural plan labels.
arch_020|candidate/architectural/loc_habs_door_detail_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=door%20detail&co=hh|loc_habs_door_detail_slice|collection_search|medium|high|A|intake_first|release_candidate|validated_public_candidate|chrome_and_iab_loaded_asset_signal|LOC HABS/HAER/HALS door-detail search loaded in Chrome and spot-checked in the in-app Browser.
pid_031|candidate/pid/wikimedia_process_flow_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Process_flow_diagrams|commons_process_flow_diagrams|commons_category|low|high|A|intake_first|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons process-flow-diagrams category loaded; per-file license capture required before promotion.
pid_032|candidate/pid/wikimedia_block_flow_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Block_flow_diagrams|commons_block_flow_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons block-flow-diagrams category loaded; source pool for process block diagrams.
pid_033|candidate/pid/wikimedia_chemical_engineering_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Chemical_engineering_diagrams|commons_chemical_engineering_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons chemical-engineering-diagrams category loaded; source pool for equipment/process labels.
pid_034|candidate/pid/wikimedia_diagrams_of_chemical_processes_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Diagrams_of_chemical_processes|commons_chemical_process_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons diagrams-of-chemical-processes category loaded; per-file follow-up needed.
pid_035|candidate/pid/wikimedia_oil_refinery_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Oil_refinery_diagrams|commons_oil_refinery_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons oil-refinery-diagrams category loaded; good equipment/process source pool.
pid_036|candidate/pid/wikimedia_power_plant_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Diagrams_of_power_plants|commons_power_plant_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons power-plant-diagrams category loaded; useful equipment/process label pool.
pid_037|candidate/pid/wikimedia_water_treatment_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Water_treatment_diagrams|commons_water_treatment_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons water-treatment-diagrams category loaded; useful process/equipment source pool.
pid_038|candidate/pid/wikimedia_wastewater_treatment_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Wastewater_treatment_diagrams|commons_wastewater_treatment_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons wastewater-treatment-diagrams category loaded; useful process/equipment source pool.
pid_039|candidate/pid/wikimedia_industrial_processes_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Industrial_processes_diagrams|commons_industrial_process_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons industrial-processes-diagrams category loaded; per-file follow-up needed.
pid_040|candidate/pid/wikimedia_diagrams_of_industrial_processes_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Diagrams_of_industrial_processes|commons_industrial_process_diagrams|commons_category|low|medium|B|intake_second|release_candidate|validated_commons_collection_candidate|chrome_loaded_asset_signal|Wikimedia Commons diagrams-of-industrial-processes category loaded; useful industrial-process source pool.
pid_041|candidate/pid/wikimedia_piping_and_instrumentation_diagrams_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Piping_and_instrumentation_diagrams|commons_pid_diagrams|commons_category|low|high|A|intake_first|release_candidate|validated_commons_collection_candidate|chrome_and_iab_loaded_asset_signal|Wikimedia Commons P&ID category loaded in Chrome and spot-checked in the in-app Browser; per-file license capture required.
"""

REJECTED_ROWS = [
    ("ds_041", "net::ERR_BLOCKED_BY_CLIENT while opening direct Raspberry Pi PDF in Chrome"),
    ("ds_042", "net::ERR_BLOCKED_BY_CLIENT while opening direct Raspberry Pi PDF in Chrome"),
    ("ds_043", "net::ERR_BLOCKED_BY_CLIENT while opening direct Raspberry Pi PDF in Chrome"),
    ("ds_044", "Chrome navigation timeout on direct ST PDF"),
    ("ds_045", "Chrome navigation timeout on direct ST PDF"),
    ("ds_046", "in-app Browser spot-check showed Arduino 'Oops! There's nothing here' page"),
    ("pcb_039", "GitHub page not found"),
    ("pcb_040", "GitHub page not found"),
    ("pcb_041", "GitHub page not found"),
    ("pcb_042", "GitHub page not found"),
    ("pcb_045", "GitHub page not found"),
    ("pcb_048", "GitHub page not found"),
    ("pcb_050", "GitHub page not found"),
    ("mech_032", "GitHub page not found"),
    ("mech_039", "GitHub page not found"),
    ("mech_041", "GitHub page not found"),
    ("civil_016", "NCDOT page not found"),
    ("civil_018", "Chrome navigation timeout"),
    ("civil_019", "UDOT page not found"),
    ("civil_020", "MDOT 404 page"),
    ("civil_021", "Chrome navigation timeout"),
    ("civil_022", "Cloudflare interstitial"),
    ("civil_023", "Chrome net::ERR_ABORTED"),
    ("civil_024", "NDOT page not found"),
    ("civil_025", "Chrome navigation timeout"),
    ("civil_026", "404 page"),
]


def pipe_rows() -> list[dict[str, str]]:
    with StringIO(ROWS) as handle:
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


def write_packet(root: Path, rows: list[dict[str, str]]) -> None:
    packet_dir = root / "derived" / "source_imports"
    packet_dir.mkdir(parents=True, exist_ok=True)
    csv_path = packet_dir / "browser_validated_50_more_sources_2026-05-22.csv"
    md_path = packet_dir / "browser_validated_50_more_sources_2026-05-22.md"
    rejected_path = packet_dir / "browser_rejected_source_candidates_2026-05-22.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS + ["next_action", "release_posture", "source_validity", "browser_result"])
        writer.writeheader()
        writer.writerows(rows)

    with rejected_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "reject_reason"])
        writer.writeheader()
        for candidate_id, reason in REJECTED_ROWS:
            writer.writerow({"candidate_id": candidate_id, "reject_reason": reason})

    by_domain = Counter(row["domain"] for row in rows)
    by_action = Counter(row["next_action"] for row in rows)
    lines = [
        "# Browser-Validated 50 More Sources - 2026-05-22",
        "",
        "## Scope",
        "- Adds exactly 50 usable source candidates beyond the previous 150-candidate source ledger.",
        "- Validation used Chrome navigation for all accepted rows, plus in-app Browser spot-checks for representative datasheet/PCB/mechanical/architectural/P&ID surfaces.",
        "- This is an import queue, not gold annotation. Each candidate still needs source-asset download, license capture, rendering, candidate annotation, and human review before release promotion.",
        "",
        "## Accepted Counts",
    ]
    lines.extend(f"- {domain}: {count}" for domain, count in sorted(by_domain.items()))
    lines.extend(["", "## Next Actions"])
    lines.extend(f"- {action}: {count}" for action, count in sorted(by_action.items()))
    lines.extend(
        [
            "",
            "## Important Rejections",
            "- `ds_046` was excluded after the in-app Browser showed an Arduino 'Oops! There's nothing here' page.",
            "- Several guessed GitHub/SparkFun/agency URLs were excluded because Chrome reached page-not-found, interstitial, timeout, or navigation-error states.",
            f"- Full rejection ledger: `{rejected_path.as_posix()}`",
            "",
            "## Accepted Candidate IDs",
        ]
    )
    lines.append(", ".join(f"`{row['candidate_id']}`" for row in rows))
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def append_worklog(root: Path, added_candidates: int, added_validations: int) -> None:
    worklog = root / "TRACK_B_WORKLOG.md"
    entry = (
        "\n## 2026-05-22 Browser-Validated 50-Source Expansion\n"
        f"- Added candidate rows: `{added_candidates}`.\n"
        f"- Added validation rows: `{added_validations}`.\n"
        "- Browser validation excluded false positives including Arduino `ds_046` and page-not-found GitHub/agency URLs.\n"
        "- Dated packet: `derived/source_imports/browser_validated_50_more_sources_2026-05-22.md`.\n"
        "- Status: source-candidate expansion only; these are not gold rows until import/render/text-layer/annotation/review passes complete.\n"
    )
    existing = worklog.read_text(encoding="utf-8") if worklog.exists() else "# Track B Worklog\n"
    if "2026-05-22 Browser-Validated 50-Source Expansion" not in existing:
        worklog.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Append 50 browser-validated Eng_Bench source candidates")
    parser.add_argument("--root", default=".", help="Eng_Bench repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rows = pipe_rows()
    if len(rows) != 50:
        raise SystemExit(f"Expected exactly 50 accepted rows, found {len(rows)}")
    ids = [row["candidate_id"] for row in rows]
    duplicates = sorted({candidate_id for candidate_id in ids if ids.count(candidate_id) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate candidate IDs in tranche: {duplicates}")

    candidate_rows = [{field: row.get(field, "") for field in CANDIDATE_FIELDS} for row in rows]
    validation_rows = [
        {
            "candidate_id": row["candidate_id"],
            "validation_date": "2026-05-22",
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
    write_packet(root, rows)
    append_worklog(root, added_candidates, added_validations)

    print(f"[OK] Accepted rows in tranche: {len(rows)}")
    print(f"[OK] Added candidate rows: {added_candidates}")
    print(f"[OK] Added validation rows: {added_validations}")
    print(f"[OK] Domains: {dict(sorted(Counter(row['domain'] for row in rows).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
