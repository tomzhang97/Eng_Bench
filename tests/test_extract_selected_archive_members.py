from __future__ import annotations

import csv
import hashlib
import zipfile
from pathlib import Path

from tools import extract_selected_archive_members


INVENTORY_FIELDS = [
    "archive_rank",
    "download_rank",
    "candidate_id",
    "domain",
    "doc_id",
    "archive_path",
    "member_path",
    "member_size",
    "member_suffix",
    "member_status",
    "selected_for_next_step",
    "next_step",
]


def write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def inventory_row(**overrides: str) -> dict[str, str]:
    row = {
        "archive_rank": "1",
        "download_rank": "20",
        "candidate_id": "pcb_014",
        "domain": "pcb_schematic",
        "doc_id": "pcb_014_cad_files",
        "archive_path": "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip",
        "member_path": "A000066-cad-files/UNO-TH_Rev3e.sch",
        "member_size": "9",
        "member_suffix": ".sch",
        "member_status": "selected_eagle_source",
        "selected_for_next_step": "True",
        "next_step": "Extract into a controlled work folder.",
    }
    row.update(overrides)
    return row


def make_zip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("A000066-cad-files/UNO-TH_Rev3e.sch", "schematic")
        z.writestr("A000066-cad-files/UNO-TH_Rev3e.brd", "board")
        z.writestr("../escape.sch", "unsafe")


def test_extracts_selected_members_to_controlled_paths(tmp_path: Path) -> None:
    archive_rel = "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip"
    make_zip(tmp_path / archive_rel)
    inventory = tmp_path / "inventory.csv"
    write_inventory(
        inventory,
        [
            inventory_row(member_path="A000066-cad-files/UNO-TH_Rev3e.sch", member_size="9"),
            inventory_row(member_path="A000066-cad-files/UNO-TH_Rev3e.brd", member_size="5"),
            inventory_row(
                member_path="../escape.sch",
                member_size="6",
                member_status="unsafe_path",
                selected_for_next_step="False",
            ),
        ],
    )

    report = extract_selected_archive_members.build_report(
        tmp_path,
        inventory_csv=inventory,
        date_label="2026-06-16",
    )

    assert report["totals"]["inventory_rows"] == 3
    assert report["totals"]["selected_rows"] == 2
    assert report["totals"]["extracted"] == 2
    receipts = report["extract_receipts"]
    extracted_paths = [row["output_path"] for row in receipts if row["status"] == "extracted"]
    assert extracted_paths == [
        "derived/source_imports/archive_extracts/2026-06-16/pcb_014_cad_files/A000066-cad-files/UNO-TH_Rev3e.sch",
        "derived/source_imports/archive_extracts/2026-06-16/pcb_014_cad_files/A000066-cad-files/UNO-TH_Rev3e.brd",
    ]
    output = tmp_path / extracted_paths[0]
    assert output.read_text(encoding="utf-8") == "schematic"
    assert receipts[0]["sha256"] == hashlib.sha256(b"schematic").hexdigest()


def test_cli_dry_run_writes_receipts_without_extracting(tmp_path: Path) -> None:
    archive_rel = "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip"
    make_zip(tmp_path / archive_rel)
    inventory = tmp_path / "inventory.csv"
    out_json = tmp_path / "extract.json"
    out_md = tmp_path / "extract.md"
    out_csv = tmp_path / "extract.csv"
    write_inventory(inventory, [inventory_row()])

    exit_code = extract_selected_archive_members.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--inventory-csv",
            str(inventory),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
            "--dry-run",
        ]
    )

    output = tmp_path / "derived/source_imports/archive_extracts/2026-06-16/pcb_014_cad_files/A000066-cad-files/UNO-TH_Rev3e.sch"
    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert not output.exists()
