from __future__ import annotations

import csv
import zipfile
from pathlib import Path

from tools import inspect_source_archives


RECEIPT_FIELDS = [
    "download_rank",
    "intake_rank",
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "doc_id",
    "local_path",
    "download_url",
    "source_url",
    "page_url",
    "license_short_name",
    "license_url",
    "artist",
    "commons_sha1",
    "rights_capture",
    "review_gate",
    "manifest_content_type",
    "manifest_content_length",
    "status",
    "bytes",
    "sha256",
    "content_type",
    "final_url",
    "error",
    "next_step",
]


def write_receipts(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RECEIPT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def receipt_row(**overrides: str) -> dict[str, str]:
    row = {
        "download_rank": "1",
        "intake_rank": "20",
        "queue_rank": "20",
        "candidate_id": "pcb_014",
        "domain": "pcb_schematic",
        "asset_kind": "archive",
        "import_action": "download_hash_unpack_select_assets",
        "doc_id": "pcb_014_cad_files",
        "local_path": "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip",
        "download_url": "https://example.test/cad.zip",
        "source_url": "https://example.test/source",
        "page_url": "https://example.test/source",
        "license_short_name": "",
        "license_url": "",
        "artist": "",
        "commons_sha1": "",
        "rights_capture": "source_page_terms_attribution_sha256",
        "review_gate": "review_packet_required_before_gold",
        "manifest_content_type": "application/zip",
        "manifest_content_length": "1000",
        "status": "downloaded",
        "bytes": "1000",
        "sha256": "0" * 64,
        "content_type": "application/zip",
        "final_url": "https://example.test/cad.zip",
        "error": "",
        "next_step": "",
    }
    row.update(overrides)
    return row


def make_zip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("cad/demo.sch", "schematic")
        z.writestr("cad/demo.brd", "board")
        z.writestr("cad/License.txt", "license")
        z.writestr("__MACOSX/cad/._License.txt", "junk")
        z.writestr("../escape.sch", "unsafe")


def test_archive_inventory_classifies_members_and_rejects_unsafe_paths(tmp_path: Path) -> None:
    archive_rel = "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip"
    make_zip(tmp_path / archive_rel)
    receipts = tmp_path / "receipts.csv"
    write_receipts(receipts, [receipt_row(local_path=archive_rel)])

    report = inspect_source_archives.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-06-16",
    )

    assert report["totals"]["archives"] == 1
    assert report["totals"]["members"] == 5
    assert report["totals"]["selected_members"] == 2
    assert report["totals"]["unsafe_members"] == 1
    selected = sorted(row["member_path"] for row in report["member_rows"] if row["selected_for_next_step"])
    assert selected == ["cad/demo.brd", "cad/demo.sch"]
    mac = [row for row in report["member_rows"] if row["member_path"].startswith("__MACOSX/")][0]
    assert mac["member_status"] == "ignored_junk"


def test_cli_writes_inventory_outputs(tmp_path: Path) -> None:
    archive_rel = "microtext/docs/source_intake_2026_06_16/pcb_014_cad_files.zip"
    make_zip(tmp_path / archive_rel)
    receipts = tmp_path / "receipts.csv"
    out_json = tmp_path / "archive.json"
    out_md = tmp_path / "archive.md"
    out_csv = tmp_path / "archive.csv"
    write_receipts(receipts, [receipt_row(local_path=archive_rel)])

    exit_code = inspect_source_archives.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--receipts-csv",
            str(receipts),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert "Source Archive Inventory" in out_md.read_text(encoding="utf-8")
