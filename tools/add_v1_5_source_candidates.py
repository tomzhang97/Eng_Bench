#!/usr/bin/env python3
"""Append the 2026-05-22 v1.5 source-candidate expansion tranche.

The script is intentionally idempotent: existing candidate IDs are skipped.
It updates the candidate ledger, validation ledger, and dated expansion packet.
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


ROWS = r"""candidate_id|path_hint|domain|task_fit|rights_tier|source_url|source_family|likely_asset_type|revision_family_potential|annotation_yield|priority_bucket|next_action|release_posture|notes
ds_001|candidate/datasheet_spec/beagleboard_black_srm|datasheet_spec|microtext|cc_by_sa_docs_candidate|https://docs.beagleboard.org/latest/boards/beaglebone/black/index.html|beagleboard_reference_manuals|html_docs_pdf_assets|medium|high|A|intake_first|release_candidate|BeagleBone Black docs/source manual surface; dense connectors, expansion headers, block diagrams, and revision notes.
ds_002|candidate/datasheet_spec/pocketbeagle_srm|datasheet_spec|microtext|cc_by_sa_docs_candidate|https://docs.beagleboard.org/latest/boards/pocketbeagle/index.html|beagleboard_reference_manuals|html_docs_pdf_assets|medium|medium|B|intake_second|release_candidate|PocketBeagle docs; compact expansion-header and pinout label source.
ds_003|candidate/datasheet_spec/beagley_ai_srm|datasheet_spec|microtext|cc_by_sa_docs_candidate|https://docs.beagleboard.org/latest/boards/beagley/ai/index.html|beagleboard_reference_manuals|html_docs_pdf_assets|medium|medium|B|intake_second|release_candidate|BeagleY-AI docs; modern SBC pinout, connector, and block-diagram source.
ds_004|candidate/datasheet_spec/raspberry_pi_pico_datasheet.pdf|datasheet_spec|microtext|public_vendor_datasheet_candidate|https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf|raspberry_pi_datasheets|datasheet_pdf|low|high|A|intake_first|release_candidate|Raspberry Pi Pico datasheet includes board-level schematic appendix and pinout tables.
ds_005|candidate/datasheet_spec/rp2040_datasheet.pdf|datasheet_spec|microtext|public_vendor_datasheet_candidate|https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf|raspberry_pi_datasheets|datasheet_pdf|low|high|A|intake_second|release_candidate|RP2040 silicon datasheet; dense package pins, signal names, and electrical tables.
ds_006|candidate/datasheet_spec/rp2040_hardware_design.pdf|datasheet_spec|microtext|public_vendor_datasheet_candidate|https://datasheets.raspberrypi.com/rp2040/hardware-design-with-rp2040.pdf|raspberry_pi_datasheets|hardware_design_pdf|low|high|A|intake_first|release_candidate|RP2040 hardware design guide; reference schematics, layout notes, and design-rule labels.
ds_007|candidate/datasheet_spec/raspberry_pi_cm4_datasheet.pdf|datasheet_spec|microtext|public_vendor_datasheet_candidate|https://datasheets.raspberrypi.com/cm4/cm4-datasheet.pdf|raspberry_pi_datasheets|datasheet_pdf|low|high|A|intake_second|release_candidate|Compute Module 4 datasheet; connector pin tables and mechanical/electrical specs.
ds_008|candidate/datasheet_spec/raspberry_pi_pico_w_datasheet.pdf|datasheet_spec|microtext|public_vendor_datasheet_candidate|https://datasheets.raspberrypi.com/picow/pico-w-datasheet.pdf|raspberry_pi_datasheets|datasheet_pdf|low|medium|B|intake_second|release_candidate|Pico W datasheet; wireless board pinout and connector/table labels.
ds_009|candidate/datasheet_spec/arduino_opta|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/opta|arduino_hardware_docs|docs_page_schematic_datasheet_step|medium|high|A|intake_first|release_candidate|Arduino Opta industrial micro-PLC page exposes pinout, datasheet, schematics, and STEP files.
ds_010|candidate/datasheet_spec/arduino_mkr_wan_1300|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/mkr-wan-1300|arduino_hardware_docs|docs_page_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|Arduino MKR WAN 1300 page exposes pinout, schematics, Fritzing, and CAD files.
ds_011|candidate/datasheet_spec/arduino_nano|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano/|arduino_hardware_docs|docs_page_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|Arduino Nano page exposes pinout, datasheet, schematics, and CAD files.
ds_012|candidate/datasheet_spec/arduino_nano_esp32|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-esp32|arduino_hardware_docs|docs_page_schematic_pinout_cad_step|medium|medium|B|intake_second|release_candidate|Arduino Nano ESP32 page exposes pinout, datasheet, schematics, CAD, and STEP files.
ds_013|candidate/datasheet_spec/arduino_nano_rp2040_connect|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/nano-rp2040-connect|arduino_hardware_docs|docs_page_schematic_pinout_cad|medium|medium|B|intake_second|release_candidate|Arduino Nano RP2040 Connect page exposes pinout, datasheet, schematics, Fritzing, and CAD files.
ds_014|candidate/datasheet_spec/arduino_portenta_x8|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/portenta-x8/|arduino_hardware_docs|docs_page_schematic_datasheet_step|medium|medium|B|intake_second|release_candidate|Arduino Portenta X8 page exposes pinout, datasheet, schematics, and STEP resources.
ds_015|candidate/datasheet_spec/arduino_uno_breakout_carrier|datasheet_spec|microtext|cc_by_sa_hardware_docs_candidate|https://docs.arduino.cc/hardware/uno-breakout-carrier/|arduino_hardware_docs|docs_page_schematic_datasheet_cad_step|medium|medium|B|intake_second|release_candidate|Arduino UNO Breakout Carrier page exposes connector pinout, datasheet, schematics, CAD, and STEP files.
ds_016|candidate/datasheet_spec/espressif_esp_dev_kits|datasheet_spec|microtext|apache_2_cc_by_sa_4_candidate|https://github.com/espressif/esp-dev-kits|espressif_dev_kits|repo_docs_hardware_resources|medium|high|A|intake_first|release_candidate|Espressif dev-kits repo describes user guides, hardware resources, schematics, and open-source licensing.
ds_017|candidate/datasheet_spec/esp32_devkitc_docs|datasheet_spec|microtext|apache_2_cc_by_sa_4_candidate|https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/index.html|espressif_dev_kits|docs_page_hardware_resources|medium|medium|B|intake_second|release_candidate|ESP32-DevKitC hardware docs; board layout, connectors, and signal labels.
ds_018|candidate/datasheet_spec/esp32_s3_devkitc_1_docs|datasheet_spec|microtext|apache_2_cc_by_sa_4_candidate|https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/index.html|espressif_dev_kits|docs_page_hardware_resources|medium|medium|B|intake_second|release_candidate|ESP32-S3-DevKitC-1 hardware docs; modern MCU board pinout and layout source.
ds_019|candidate/datasheet_spec/st_nucleo_64_boards_um1724.pdf|datasheet_spec|microtext|public_vendor_user_manual_candidate|https://www.st.com/resource/en/user_manual/um1724-stm32-nucleo64-boards-mb1136-stmicroelectronics.pdf|stmicro_nucleo_docs|user_manual_pdf|low|high|A|intake_first|release_candidate|STM32 Nucleo-64 user manual; board schematics, connector headers, and solder bridge labels.
ds_020|candidate/datasheet_spec/st_x_nucleo_wifi_um1975.pdf|datasheet_spec|microtext|public_vendor_user_manual_candidate|https://www.st.com/resource/en/user_manual/um1975-getting-started-with-xnucleoidw01m1-wifi-expansion-board-based-on-spwf01sa-module-for-stm32-nucleo-stmicroelectronics.pdf|stmicro_x_nucleo_docs|user_manual_pdf|low|medium|B|intake_second|release_candidate|ST X-NUCLEO WiFi expansion user manual; schematic figure and connector labels.
ds_021|candidate/datasheet_spec/ti_msp_exp432p401r_launchpad.pdf|datasheet_spec|microtext|public_vendor_user_guide_candidate|https://www.ti.com/lit/ug/slau597f/slau597f.pdf|ti_launchpad_docs|user_guide_pdf|low|medium|B|intake_second|release_candidate|TI LaunchPad user guide candidate; board labels, jumpers, headers, and schematics.
ds_022|candidate/datasheet_spec/ti_msp_exp430g2et_launchpad.pdf|datasheet_spec|microtext|public_vendor_user_guide_candidate|https://www.ti.com/lit/ug/slau772/slau772.pdf|ti_launchpad_docs|user_guide_pdf|low|medium|B|intake_second|release_candidate|TI MSP430 LaunchPad user guide candidate; connector and schematic labels.
ds_023|candidate/datasheet_spec/nxp_frdm_k64f_docs|datasheet_spec|microtext|public_vendor_user_guide_candidate|https://www.nxp.com/design/design-center/development-boards-and-designs/freedom-development-boards/mcu-boards/freedom-development-platform-for-kinetis-k64-k63-and-k24-mcus:FRDM-K64F|nxp_freedom_docs|vendor_docs_page|low|medium|B|intake_second|release_candidate|NXP FRDM-K64F board docs page; pinout and board schematic resource candidate.
ds_024|candidate/datasheet_spec/microchip_curiosity_nano_docs|datasheet_spec|microtext|public_vendor_user_guide_candidate|https://www.microchip.com/en-us/development-tool/DM320115|microchip_curiosity_nano_docs|vendor_docs_page|low|medium|B|intake_second|release_candidate|Microchip Curiosity Nano board docs page; pinout, user-guide, and schematic resource candidate.
ds_025|candidate/datasheet_spec/toradex_verdin_development_board|datasheet_spec|microtext|public_vendor_design_resource_candidate|https://developer.toradex.com/hardware/verdin-som-family/carrier-boards/verdin-development-board/|toradex_carrier_board_docs|vendor_design_docs|medium|high|A|intake_first|release_candidate|Toradex Verdin Development Board docs; carrier-board connectors, schematics, and revision potential.
ds_026|candidate/datasheet_spec/toradex_dahlia_carrier_board|datasheet_spec|microtext|public_vendor_design_resource_candidate|https://developer.toradex.com/hardware/verdin-som-family/carrier-boards/dahlia-carrier-board/|toradex_carrier_board_docs|vendor_design_docs|medium|medium|B|intake_second|release_candidate|Toradex Dahlia carrier board docs; board connector labels and design resources.
ds_027|candidate/datasheet_spec/toradex_yavia_carrier_board|datasheet_spec|microtext|public_vendor_design_resource_candidate|https://developer.toradex.com/hardware/verdin-som-family/carrier-boards/yavia-carrier-board/|toradex_carrier_board_docs|vendor_design_docs|medium|medium|B|intake_second|release_candidate|Toradex Yavia carrier board docs; modern carrier-board labels and design resources.
ds_028|candidate/datasheet_spec/antmicro_jetson_orin_baseboard|datasheet_spec|microtext|open_hardware_candidate|https://openhardware.antmicro.com/boards/jetson-orin-baseboard/|antmicro_open_hardware_portal|portal_schematic_step_kicad|medium|high|A|intake_first|release_candidate|Antmicro Jetson Orin Baseboard page exposes KiCad project, schematic PDF, STEP model, measurements, and feature labels.
ds_029|candidate/datasheet_spec/sparkfun_micromod_general_pinout.pdf|datasheet_spec|microtext|open_hardware_docs_candidate|https://cdn.sparkfun.com/assets/1/6/f/a/2/MicroMod_General_Pinout_v10_Graphical_Datasheet.pdf|sparkfun_micromod_docs|pinout_pdf|low|medium|B|intake_second|release_candidate|SparkFun MicroMod graphical datasheet/pinout PDF; dense connector label source.
ds_030|candidate/datasheet_spec/adafruit_feather_rp2040_learn_pdf|datasheet_spec|microtext|cc_by_sa_docs_candidate|https://cdn-learn.adafruit.com/downloads/pdf/adafruit-feather-rp2040-pico.pdf|adafruit_learning_system_docs|learn_pdf_schematic_fab_print|low|medium|B|intake_second|release_candidate|Adafruit Feather RP2040 guide PDF includes schematic/fab print and product pinout details.
pcb_020|candidate/pcb_schematic/adafruit_feather_rp2040_pcb|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-RP2040-PCB|adafruit_feather_pcbs|repo_eagle_sch_brd_pdf|high|high|A|intake_first|release_candidate|Adafruit Feather RP2040 PCB repo includes Eagle schematic/board files, revisions, pinout PDF, and license.
pcb_021|candidate/pcb_schematic/sparkfun_micromod_rp2040_processor|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://github.com/sparkfun/MicroMod_RP2040_Processor|sparkfun_micromod_boards|repo_eagle_schematic_board|medium|medium|A|intake_first|release_candidate|SparkFun MicroMod RP2040 Processor board candidate; related MicroMod family and schematic/pinout source.
pcb_022|candidate/pcb_schematic/sparkfun_micromod_samd51_processor|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://github.com/sparkfun/MicroMod_SAMD51_Processor|sparkfun_micromod_boards|repo_eagle_schematic_board|medium|medium|B|intake_second|release_candidate|SparkFun MicroMod SAMD51 Processor candidate; useful family variation against ESP32/RP2040.
pcb_023|candidate/pcb_schematic/sparkfun_micromod_artemis_processor|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://github.com/sparkfun/MicroMod_Artemis_Processor|sparkfun_micromod_boards|repo_eagle_schematic_board|medium|medium|B|intake_second|release_candidate|SparkFun MicroMod Artemis Processor candidate; adds radio/MCU component labels.
pcb_024|candidate/pcb_schematic/espressif_esp_dev_kits|pcb_schematic|visualdiff,microtext|apache_2_cc_by_sa_4_candidate|https://github.com/espressif/esp-dev-kits|espressif_dev_kits|repo_hardware_resources|high|high|A|intake_first|release_candidate|Espressif dev-kits repo contains hardware resources and schematics for multiple ESP development boards.
pcb_025|candidate/pcb_schematic/antmicro_jetson_orin_baseboard|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://openhardware.antmicro.com/boards/jetson-orin-baseboard/|antmicro_open_hardware_boards|portal_kicad_schematic_step|medium|high|A|intake_first|release_candidate|Antmicro Jetson Orin Baseboard exposes KiCad project, schematic PDF, and STEP model.
pcb_026|candidate/pcb_schematic/antmicro_gmsl_deserializer|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://opensource.antmicro.com/projects/gmsl-deserializer/|antmicro_open_hardware_boards|project_page_kicad_schematic|medium|medium|B|intake_second|release_candidate|Antmicro GMSL deserializer board; open hardware design files and schematic-bearing project page.
pcb_027|candidate/pcb_schematic/raspberry_pi_pico_hardware|pcb_schematic|visualdiff,microtext|public_vendor_hardware_candidate|https://github.com/raspberrypi/pico-hardware|raspberry_pi_pico_hardware|repo_hardware_design_files|medium|medium|B|intake_second|release_candidate|Raspberry Pi Pico hardware repository candidate; use with official Pico datasheet/schematic resources.
pcb_028|candidate/pcb_schematic/olimexino_2560|pcb_schematic|visualdiff,microtext|open_hardware_candidate|https://www.olimex.com/Products/Duino/AVR/OLIMEXINO-2560/open-source-hardware|olimex_open_hardware|schematic_pdf_kicad_github|medium|medium|B|intake_second|release_candidate|Olimex OLIMEXINO-2560 page exposes schematic PDF and KiCad/GitHub design files.
pcb_029|candidate/pcb_schematic/olimex_stm32_e407|pcb_schematic|visualdiff,microtext|cc_by_sa_open_hardware_candidate|https://www.olimex.com/Products/ARM/ST/STM32-E407/open-source-hardware|olimex_open_hardware|schematic_pdf_eagle_github|medium|medium|B|intake_second|release_candidate|Olimex STM32-E407 page exposes schematic PDF and Eagle CAD files under open hardware license language.
pcb_030|candidate/pcb_schematic/adafruit_feather_nrf52840_express|pcb_schematic|visualdiff,microtext|cc_by_sa_candidate|https://github.com/adafruit/Adafruit-Feather-nRF52840-Express-PCB|adafruit_feather_pcbs|repo_pcb_design_files|medium|medium|B|intake_second|release_candidate|Adafruit Feather nRF52840 Express PCB repo candidate; useful modern BLE MCU labels.
mech_013|candidate/mechanical_cad/voron_0|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-0|open_hardware_3d_printer|repo_cad_dxf_stl|high|high|A|intake_first|release_candidate|Voron 0 repository; related 3D-printer CAD/DXF/STL source family.
mech_014|candidate/mechanical_cad/voron_switchwire|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Switchwire|open_hardware_3d_printer|repo_cad_dxf_stl|high|medium|B|intake_second|release_candidate|Voron Switchwire repository; mechanical printer CAD/DXF/STL source family.
mech_015|candidate/mechanical_cad/voron_legacy|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/VoronDesign/Voron-Legacy|open_hardware_3d_printer|repo_cad_dxf_stl|high|medium|B|intake_second|release_candidate|Voron Legacy repository; related older mechanical CAD family.
mech_016|candidate/mechanical_cad/farmbot_genesis_cad|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://genesis.farm.bot/v1.4/Extras/cad.html|farmbot_genesis|onshape_cad_export_docs|medium|high|A|intake_first|release_candidate|FarmBot Genesis CAD docs state open-source CAD model access and exportable parts.
mech_017|candidate/mechanical_cad/openflexure_microscope|mechanical_cad|visualdiff,microtext|cern_ohl_s_2_candidate|https://gitlab.com/openflexure/openflexure-microscope|openflexure_microscope|repo_openscad_stl_docs|medium|high|A|intake_first|release_candidate|OpenFlexure microscope repo includes OpenSCAD/STL generation and CERN-OHL-S licensing.
mech_018|candidate/mechanical_cad/openflexure_delta_stage|mechanical_cad|visualdiff,microtext|cern_ohl_candidate|https://gitlab.com/openflexure/openflexure-delta-stage/-/tree/master|openflexure_delta_stage|repo_openscad_stl_docs|medium|medium|B|intake_second|release_candidate|OpenFlexure Delta Stage source; OpenSCAD/STL mechanical translation-stage family.
mech_019|candidate/mechanical_cad/lulzbot_mini_3|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://gitlab.com/lulzbot3d/printers/lulzbot-mini-3|lulzbot_printers|repo_source_code_cad_assets|medium|medium|B|intake_second|release_candidate|LulzBot Mini 3 GitLab source candidate; open printer hardware/CAD family.
mech_020|candidate/mechanical_cad/lulzbot_taz6_source|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://download.lulzbot.com/TAZ/6.0/|lulzbot_printers|source_file_portal_manuals|medium|medium|B|intake_second|release_candidate|LulzBot TAZ 6 source portal/manual surface; candidate for machined/printed-part drawings.
mech_021|candidate/mechanical_cad/jubilee_machine|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/machineagency/jubilee|science_jubilee|repo_cad_machine_design|medium|medium|B|intake_second|release_candidate|Jubilee machine repository candidate; modular lab automation/mechanical CAD source.
mech_022|candidate/mechanical_cad/creality_ender_3|mechanical_cad|visualdiff,microtext|misc_public_candidate|https://github.com/CrealityOfficial/Ender-3|creality_ender_3|repo_mechanical_drawings_f360|medium|medium|B|intake_second|release_candidate|Creality Ender-3 source repository candidate; mechanical files and drawings can diversify 3D-printer sources.
mech_023|candidate/mechanical_cad/bigfdm|mechanical_cad|visualdiff,microtext|open_hardware_candidate|https://github.com/fab-machines/BigFDM|fab_machines_bigfdm|repo_cad_stl_bom|medium|medium|B|intake_second|release_candidate|BigFDM open-source large-format 3D printer repository; CAD/STL/BOM source candidate.
mech_024|candidate/mechanical_cad/open_lab_automata_pipettin_bot|mechanical_cad|visualdiff,microtext|cern_ohl_s_candidate|https://gitlab.com/open-la/pipettin-bot|open_lab_automata|repo_models_docs_submodules|medium|medium|B|intake_second|release_candidate|Open Lab Automata pipetting robot tracks CAD/models and hardware licensing; useful lab-automation mechanical source.
mech_025|candidate/mechanical_cad/syringe_pump_gitlab|mechanical_cad|visualdiff,microtext|open_source_candidate|https://gitlab.com/sarisko/syringe-pump|open_source_syringe_pump|repo_models_stl|low|medium|C|intake_second|release_candidate|Open-source 3D-printed syringe pump with model files; smaller but useful equipment source.
mech_026|candidate/mechanical_cad/uconduit_reprap|mechanical_cad|visualdiff,microtext|cc_sharealike_candidate|https://reprap.org/wiki/Uconduit|reprap_uconduit|wiki_openscad_github|medium|medium|C|intake_second|release_candidate|RepRap UConduit page describes OpenSCAD designed parts and open hardware posture; needs asset link follow-up.
mech_027|candidate/mechanical_cad/clip_and_block|mechanical_cad|visualdiff,microtext|cc_by_sa_4_candidate|https://gitlab.com/tedour/clip-and-block|clip_and_block|repo_openscad_stl_parts|low|medium|C|intake_second|release_candidate|Clip and block open construction-kit source with OpenSCAD/STL parts; useful as small mechanical source.
mech_028|candidate/mechanical_cad/windsensor_wifi_1000|mechanical_cad|visualdiff,microtext|open_source_candidate|https://gitlab.com/Norbert_Walter/Windsensor_WiFi_1000|windsensor_wifi_1000|repo_3d_cad_electronics_docs|medium|medium|C|recheck_later|research_candidate|Open-source wind transducer candidate with 3D CAD, electronics, and documentation; recheck direct GitLab path before import.
mech_029|candidate/mechanical_cad/cadquery_examples|mechanical_cad|microtext|apache_or_project_license_candidate|https://github.com/CadQuery/cadquery/tree/master/examples|cadquery_examples|repo_parametric_cad_examples|low|medium|C|intake_second|research_candidate|Parametric CAD examples for renderer development; not a release source until examples are selected and licensed.
mech_030|candidate/mechanical_cad/build123d_examples|mechanical_cad|microtext|project_license_candidate|https://github.com/gumyr/build123d/tree/dev/examples|build123d_examples|repo_parametric_cad_examples|low|medium|C|intake_second|research_candidate|Build123d examples for parametric CAD renderer development; use as research source unless specific examples are selected.
arch_013|candidate/architectural/loc_habs_elevation_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=elevation&co=hh|loc_habs_elevation_slice|collection_search|medium|high|A|intake_first|release_candidate|Targeted LOC HABS/HAER/HALS elevation search; complements floor-plan source with elevation and section labels.
arch_014|candidate/architectural/loc_habs_section_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=section&co=hh|loc_habs_section_slice|collection_search|medium|high|A|intake_first|release_candidate|Targeted LOC HABS/HAER/HALS section search; useful for section/elevation/dimension labels.
arch_015|candidate/architectural/loc_habs_detail_search|architectural|microtext|public_candidate|https://www.loc.gov/pictures/search/?q=detail&co=hh|loc_habs_detail_slice|collection_search|medium|medium|B|intake_second|release_candidate|Targeted LOC HABS/HAER/HALS detail-search slice; use for architectural detail labels after selecting specific sheets.
civil_014|candidate/civil/modot_bridge_standard_drawings|civil|visualdiff,microtext|misc_public_candidate|https://www.modot.org/bridge-standard-drawings|modot_bridge_standards|standard_drawing_pdf_dgn_index|high|high|A|intake_first|release_candidate|MoDOT bridge standard drawings page exposes individual PDF/DGN sheets and all-current-drawings PDF.
civil_015|candidate/civil/penndot_bridge_standards|civil|visualdiff,microtext|misc_public_candidate|https://www.pa.gov/agencies/penndot/programs-and-doing-business/bridges/bridge-plans-standards-and-specifications.html|penndot_bridge_standards|standard_drawing_pdf_index|high|high|A|intake_first|release_candidate|PennDOT bridge standard drawing page exposes index sheets, all-standard PDFs, and archived standards.
pid_023|candidate/pid/wikimedia_schema_pid.jpg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID.jpg|commons_pid_examples|raster_pid_diagram|low|medium|A|intake_first|release_candidate|Wikimedia Commons P&ID file page candidate; per-file license capture required before promotion.
pid_024|candidate/pid/wikimedia_pump_with_tank_pid_de.svg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Pump_with_tank_pid_de.svg|commons_pid_examples|svg_pid_diagram|low|medium|A|intake_second|release_candidate|German-language pump-with-tank P&ID SVG; useful for multilingual/variant symbol labels after license capture.
pid_025|candidate/pid/wikimedia_instrument_procede.png|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Instrument_proc%C3%A9d%C3%A9.png|commons_pid_examples|raster_pid_symbol_diagram|low|medium|B|intake_second|release_candidate|Commons instrument-process image candidate; likely small symbol/text source after per-file license review.
pid_026|candidate/pid/wikimedia_int_operateur_aux.svg|pid|microtext|commons_license_candidate|https://commons.wikimedia.org/wiki/File:Int-op%C3%A9rateur_aux.svg|commons_pid_examples|svg_pid_symbol_diagram|low|low|B|intake_second|release_candidate|Commons auxiliary-operator instrument symbol candidate; small but clean SVG text source if license passes.
pid_027|candidate/pid/wikimedia_chemical_engineering_symbols_category|pid|microtext|commons_license_collection_candidate|https://commons.wikimedia.org/wiki/Category:Chemical_engineering_symbols|commons_chemical_engineering_symbols|commons_category|low|medium|B|intake_second|release_candidate|Commons chemical-engineering symbol category; source pool for valves/instruments/equipment symbols after per-file selection.
pid_028|candidate/pid/libretexts_pid_notation|pid|microtext|cc_by_nc_sa_reference_candidate|https://eng.libretexts.org/Bookshelves/Industrial_and_Systems_Engineering/Chemical_Process_Dynamics_and_Controls_%28Woolf%29/04%253A_Piping_and_Instrumentation_Diagrams/4.02%253A_Piping_and_Instrumentation_Diagram_Standard_Notation|libretexts_pid_notation|web_article_sample_diagram|low|medium|C|rights_review_before_import|reference_only|LibreTexts P&ID notation page includes sample diagram/table; keep as reference unless noncommercial terms are cleared.
pid_029|candidate/pid/ou_piping_instrument_diagrams.pdf|pid|microtext|educational_pdf_rights_uncertain|https://www.ou.edu/class/che-design/design%201-2013/Piping%20%26%20Instrument%20Diagrams.pdf|ou_chemical_engineering_pid|lecture_pdf_sample_diagram|low|medium|C|rights_review_before_import|research_candidate|University-hosted P&ID lecture PDF with sample process labels; rights uncertain, use as research/reference until cleared.
pid_030|candidate/pid/creative_engineers_pid_sample_rev4.pdf|pid|visualdiff,microtext|commercial_sample_rights_uncertain|https://creativeengineers.com/wp-content/uploads/2019/07/CEI-PID-Sample-Rev4.pdf|creative_engineers_pid_sample|sample_pid_pdf|medium|high|C|prototype_only|internal_only_candidate|Commercial sample P&ID with revision marker; valuable for visualdiff prototype but not public gold without permission.
"""


def load_pipe_rows() -> list[dict[str, str]]:
    with StringIO(ROWS) as f:
        return list(csv.DictReader(f, delimiter="|"))


def load_existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8") as f:
        return {row["candidate_id"] for row in csv.DictReader(f) if row.get("candidate_id")}


def append_candidates(root: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    path = root / "SOURCE_CANDIDATES.csv"
    existing = load_existing_ids(path)
    new_rows = [row for row in rows if row["candidate_id"] not in existing]
    if not new_rows:
        return []
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_FIELDS)
        for row in new_rows:
            writer.writerow({key: row[key] for key in CANDIDATE_FIELDS})
    return new_rows


def append_validations(root: Path, rows: list[dict[str, str]], date: str) -> None:
    path = root / "SOURCE_CANDIDATE_VALIDATION.csv"
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=VALIDATION_FIELDS)
        for row in rows:
            writer.writerow(
                {
                    "candidate_id": row["candidate_id"],
                    "validation_date": date,
                    "browser_result": "web_triaged_pending_import_validation",
                    "source_validity": "candidate_queued_for_v1_5_source_breadth",
                    "release_posture": row["release_posture"],
                    "next_action": row["next_action"],
                    "notes": "v1.5 quota expansion candidate. " + row["notes"],
                }
            )


def quota_group(domain: str) -> str:
    if domain in {"civil", "architectural"}:
        return "civil_architectural"
    return domain


def write_packet(root: Path, rows: list[dict[str, str]], date: str) -> None:
    out_dir = root / "derived" / "source_imports"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"v1_5_source_quota_expansion_{date}.csv"
    md_path = out_dir / f"v1_5_source_quota_expansion_{date}.md"
    packet_fields = [
        "candidate_id",
        "domain",
        "task_fit",
        "rights_tier",
        "source_url",
        "priority_bucket",
        "first_action",
        "release_posture",
        "why_usable",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=packet_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "candidate_id": row["candidate_id"],
                    "domain": row["domain"],
                    "task_fit": row["task_fit"],
                    "rights_tier": row["rights_tier"],
                    "source_url": row["source_url"],
                    "priority_bucket": row["priority_bucket"],
                    "first_action": row["next_action"],
                    "release_posture": row["release_posture"],
                    "why_usable": row["notes"],
                }
            )

    counts = Counter(quota_group(row["domain"]) for row in rows)
    lines = [
        "# v1.5 Source Quota Expansion - 2026-05-22",
        "",
        "This packet adds source candidates to meet the v1.5 acquisition-plan requirement of roughly 2x candidate coverage per required active source domain. It changes source ledgers only; it does not promote gold JSONL rows.",
        "",
        "## Added Counts",
        "",
    ]
    for domain in [
        "pcb_schematic",
        "datasheet_spec",
        "mechanical_cad",
        "civil_architectural",
        "pid",
    ]:
        lines.append(f"- {domain}: {counts[domain]} new candidates")
    lines.extend(
        [
            "",
            "## Import Discipline",
            "",
            "- Treat `intake_first` as the first machine import queue, not as gold approval.",
            "- Download/render/extract each selected source before candidate annotation.",
            "- Keep `rights_review_before_import`, `prototype_only`, and `reference_only` rows out of public gold until rights are cleared.",
            "- Prefer `datasheet_spec`, `mechanical_cad`, and `pid` next because those lanes are still the weakest active-source domains.",
            "",
            "## Rows",
            "",
            "| Candidate | Domain | Priority | First action | Why usable |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        note = row["notes"].replace("|", "/")
        lines.append(
            f"| `{row['candidate_id']}` | {row['domain']} | "
            f"{row['priority_bucket']} | {row['next_action']} | {note} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date", default="2026-05-22")
    args = parser.parse_args()

    root = Path(args.root)
    rows = load_pipe_rows()
    new_rows = append_candidates(root, rows)
    append_validations(root, new_rows, args.date)
    write_packet(root, new_rows or rows, args.date)
    print(f"[OK] appended {len(new_rows)} new source candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
