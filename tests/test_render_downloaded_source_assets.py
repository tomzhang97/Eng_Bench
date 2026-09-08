from __future__ import annotations

import csv
import json
from pathlib import Path

import fitz
from PIL import Image

from tools import render_downloaded_source_assets


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
        "intake_rank": "1",
        "queue_rank": "1",
        "candidate_id": "civil_001",
        "domain": "civil",
        "asset_kind": "pdf",
        "import_action": "download_hash_render_textlayer",
        "doc_id": "civil_001_standard",
        "local_path": "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf",
        "download_url": "https://example.test/standard.pdf",
        "source_url": "https://example.test/standards",
        "page_url": "https://example.test/standards",
        "license_short_name": "",
        "license_url": "",
        "artist": "",
        "commons_sha1": "",
        "rights_capture": "public_agency_source_url_terms_sha256",
        "review_gate": "review_packet_required_before_gold",
        "manifest_content_type": "application/pdf",
        "manifest_content_length": "1000",
        "status": "downloaded",
        "bytes": "1000",
        "sha256": "0" * 64,
        "content_type": "application/pdf",
        "final_url": "https://example.test/standard.pdf",
        "error": "",
        "next_step": "",
    }
    row.update(overrides)
    return row


def make_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((20, 40), "PUMP TAG P-101", fontsize=12)
    doc.save(path)
    doc.close()


def make_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 32), color=(255, 255, 255)).save(path)


def test_pdf_receipt_renders_first_page_and_textlayer(tmp_path: Path) -> None:
    pdf_rel = "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf"
    make_pdf(tmp_path / pdf_rel)
    receipts = tmp_path / "receipts.csv"
    write_receipts(receipts, [receipt_row(local_path=pdf_rel)])

    report = render_downloaded_source_assets.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-06-16",
        pages="1",
        dpi=72,
    )

    row = report["render_receipts"][0]
    assert row["status"] == "rendered_pdf_textlayer"
    assert row["rendered_pages"] == 1
    assert row["text_spans"] >= 1
    assert (tmp_path / "derived/pages_72dpi/civil_001_standard/page_000.png").exists()
    jsonl = tmp_path / "derived/textlayer/civil_001_standard.jsonl"
    assert jsonl.exists()
    assert "PUMP TAG P-101" in jsonl.read_text(encoding="utf-8")


def test_image_receipt_normalizes_to_page_png(tmp_path: Path) -> None:
    image_rel = "microtext/docs/source_intake_2026_06_16/pcb_001_stackup.webp"
    make_image(tmp_path / image_rel)
    receipts = tmp_path / "receipts.csv"
    write_receipts(
        receipts,
        [
            receipt_row(
                candidate_id="pcb_001",
                domain="pcb_schematic",
                asset_kind="image",
                import_action="download_hash_render_image",
                doc_id="pcb_001_stackup",
                local_path=image_rel,
                manifest_content_type="image/webp",
                content_type="image/webp",
            )
        ],
    )

    report = render_downloaded_source_assets.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-06-16",
        dpi=72,
    )

    row = report["render_receipts"][0]
    assert row["status"] == "rendered_image"
    assert row["rendered_pages"] == 1
    assert row["text_spans"] == 0
    assert (tmp_path / "derived/pages_72dpi/pcb_001_stackup/page_000.png").exists()


def test_opt_in_raster_pixel_ceiling_is_reported_and_restored(tmp_path: Path) -> None:
    image_rel = "microtext/docs/source_intake_2026_06_16/large_audited_sheet.tif"
    make_image(tmp_path / image_rel)
    receipts = tmp_path / "receipts.csv"
    write_receipts(
        receipts,
        [
            receipt_row(
                asset_kind="image",
                import_action="download_hash_render_image",
                doc_id="large_audited_sheet",
                local_path=image_rel,
                manifest_content_type="image/tiff",
                content_type="image/tiff",
            )
        ],
    )
    default_ceiling = Image.MAX_IMAGE_PIXELS

    report = render_downloaded_source_assets.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-07-31",
        dpi=72,
        max_image_pixels=300_000_000,
    )

    assert report["totals"]["max_image_pixels"] == 300_000_000
    assert report["render_receipts"][0]["status"] == "rendered_image"
    assert Image.MAX_IMAGE_PIXELS == default_ceiling


def test_archive_and_skipped_receipts_are_not_rendered(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts.csv"
    write_receipts(
        receipts,
        [
            receipt_row(
                doc_id="archive_asset",
                asset_kind="archive",
                import_action="download_hash_unpack_select_assets",
                local_path="microtext/docs/source_intake_2026_06_16/archive_asset.zip",
            ),
            receipt_row(
                download_rank="2",
                doc_id="large_pdf",
                status="skipped_max_bytes",
                local_path="microtext/docs/source_intake_2026_06_16/large_pdf.pdf",
            ),
        ],
    )

    report = render_downloaded_source_assets.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-06-16",
    )

    statuses = [row["status"] for row in report["render_receipts"]]
    assert statuses == ["skipped_archive_manual_unpack", "skipped_receipt_status"]


def test_cli_dry_run_writes_report_without_render_outputs(tmp_path: Path) -> None:
    pdf_rel = "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf"
    make_pdf(tmp_path / pdf_rel)
    receipts = tmp_path / "receipts.csv"
    out_json = tmp_path / "render.json"
    out_md = tmp_path / "render.md"
    out_csv = tmp_path / "render.csv"
    write_receipts(receipts, [receipt_row(local_path=pdf_rel)])

    exit_code = render_downloaded_source_assets.main(
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert json.loads(out_json.read_text(encoding="utf-8"))["totals"]["dry_run"] == 1
    assert not (tmp_path / "derived/pages_300dpi/civil_001_standard/page_000.png").exists()


def test_doc_id_filter_limits_rendered_rows(tmp_path: Path) -> None:
    first_rel = "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf"
    second_rel = "microtext/docs/source_intake_2026_06_16/civil_002_standard.pdf"
    make_pdf(tmp_path / first_rel)
    make_pdf(tmp_path / second_rel)
    receipts = tmp_path / "receipts.csv"
    write_receipts(
        receipts,
        [
            receipt_row(doc_id="civil_001_standard", local_path=first_rel),
            receipt_row(
                download_rank="2",
                candidate_id="civil_002",
                doc_id="civil_002_standard",
                local_path=second_rel,
            ),
        ],
    )

    report = render_downloaded_source_assets.build_report(
        tmp_path,
        receipts_csv=receipts,
        date_label="2026-06-16",
        dpi=72,
        pages="1",
        doc_ids={"civil_002_standard"},
    )

    assert report["totals"]["download_receipt_rows"] == 2
    assert report["totals"]["processed_rows"] == 1
    assert report["render_receipts"][0]["doc_id"] == "civil_002_standard"
    assert not (tmp_path / "derived/pages_72dpi/civil_001_standard/page_000.png").exists()
    assert (tmp_path / "derived/pages_72dpi/civil_002_standard/page_000.png").exists()
