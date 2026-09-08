#!/usr/bin/env python3
"""Import the first 20 v1.5 direct sources into reviewable microtext batches.

This script assumes the source assets have already been downloaded under
microtext/docs/source_intake_2026_05_24/. It performs only local processing:
render pages, extract text layers where possible, mine candidate labels, export
review crops, update manifest/source intake logs, and write provenance packets.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageOps

import export_review_packs
import microtext_review
import mine_microtext_candidates
import propose_microtext_regions


@dataclass(frozen=True)
class ImportSource:
    candidate_id: str
    doc_id: str
    domain: str
    source_url: str
    direct_asset_url: str
    local_relpath: str
    asset_kind: str
    rights_tier: str
    license_note: str
    notes: str


SOURCES: list[ImportSource] = [
    ImportSource("ds_031", "arduino_giga_r1_wifi_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/giga-r1-wifi/", "https://docs.arduino.cc/resources/schematics/ABX00063-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_giga_r1_wifi_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "GIGA R1 WiFi schematic PDF."),
    ImportSource("ds_032", "arduino_portenta_h7_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/portenta-h7/", "https://docs.arduino.cc/resources/schematics/ABX00042-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_portenta_h7_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Portenta H7 schematic PDF."),
    ImportSource("ds_033", "arduino_nicla_vision_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/nicla-vision/", "https://docs.arduino.cc/resources/schematics/ABX00051-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_nicla_vision_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Nicla Vision schematic PDF."),
    ImportSource("ds_039", "arduino_uno_r4_wifi_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/uno-r4-wifi/", "https://docs.arduino.cc/resources/schematics/ABX00087-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_uno_r4_wifi_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "UNO R4 WiFi schematic PDF."),
    ImportSource("ds_040", "arduino_uno_r4_minima_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/uno-r4-minima/", "https://docs.arduino.cc/resources/schematics/ABX00080-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_uno_r4_minima_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "UNO R4 Minima schematic PDF."),
    ImportSource("ds_051", "arduino_due_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/due/", "https://docs.arduino.cc/resources/schematics/A000056-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_due_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Arduino Due schematic PDF."),
    ImportSource("ds_055", "arduino_mkr_wan_1310_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/mkr-wan-1310/", "https://docs.arduino.cc/resources/schematics/ABX00029-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_mkr_wan_1310_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "MKR WAN 1310 schematic PDF."),
    ImportSource("ds_069", "arduino_portenta_machine_control_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/portenta-machine-control/", "https://docs.arduino.cc/resources/schematics/AKX00032-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_portenta_machine_control_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Portenta Machine Control schematic PDF."),
    ImportSource("ds_068", "arduino_portenta_breakout_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/portenta-breakout/", "https://docs.arduino.cc/resources/schematics/ASX00031-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_portenta_breakout_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Portenta Breakout schematic PDF."),
    ImportSource("ds_072", "arduino_nano_33_ble_sense_rev2_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/nano-33-ble-sense-rev2/", "https://docs.arduino.cc/resources/schematics/ABX00069-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_nano_33_ble_sense_rev2_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "Nano 33 BLE Sense Rev2 schematic PDF."),
    ImportSource("ds_061", "arduino_mkr_1000_wifi_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/mkr-1000-wifi/", "https://docs.arduino.cc/resources/schematics/ABX00004-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_mkr_1000_wifi_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "MKR 1000 WiFi schematic PDF."),
    ImportSource("ds_062", "arduino_mkr_fox_1200_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/mkr-fox-1200/", "https://docs.arduino.cc/resources/schematics/ABX00014-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_mkr_fox_1200_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "MKR FOX 1200 schematic PDF."),
    ImportSource("ds_063", "arduino_mkr_485_shield_schematics", "datasheet_spec", "https://docs.arduino.cc/hardware/mkr-485-shield/", "https://docs.arduino.cc/resources/schematics/ASX00004-schematics.pdf", "microtext/docs/source_intake_2026_05_24/arduino_mkr_485_shield_schematics.pdf", "pdf", "cc_by_sa_hardware_docs_candidate", "Arduino docs content is treated as a public hardware-docs candidate; confirm redistribution terms before public release.", "MKR 485 Shield schematic PDF."),
    ImportSource("pid_054", "wikimedia_aminwaesche", "pid", "https://commons.wikimedia.org/wiki/File:Aminw%C3%A4sche.jpg", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Aminw%C3%A4sche.jpg", "microtext/docs/source_intake_2026_05_24/wikimedia_aminwaesche.jpg", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons process image."),
    ImportSource("pid_057", "wikimedia_schema_pid1", "pid", "https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID1.jpg", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Sch%C3%A9ma_P%26ID1.jpg", "microtext/docs/source_intake_2026_05_24/wikimedia_schema_pid1.jpg", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons P&ID image."),
    ImportSource("pid_058", "wikimedia_back_mixing_digester", "pid", "https://commons.wikimedia.org/wiki/File:Back_Mixing_and_Digester.png", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Back_Mixing_and_Digester.png", "microtext/docs/source_intake_2026_05_24/wikimedia_back_mixing_digester.png", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons process image."),
    ImportSource("pid_059", "wikimedia_insulin_process", "pid", "https://commons.wikimedia.org/wiki/File:Aliran_Proses_Untuk_Pembuatan_Insulin.png", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Aliran_Proses_Untuk_Pembuatan_Insulin.png", "microtext/docs/source_intake_2026_05_24/wikimedia_insulin_process.png", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons process image."),
    ImportSource("pid_060", "wikimedia_absorptionskuehlung_abb2", "pid", "https://commons.wikimedia.org/wiki/File:Absorptionskuehlung_Abb_2.jpg", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Absorptionskuehlung_Abb_2.jpg", "microtext/docs/source_intake_2026_05_24/wikimedia_absorptionskuehlung_abb2.jpg", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons process image."),
    ImportSource("pid_063", "wikimedia_akm", "pid", "https://commons.wikimedia.org/wiki/File:AKM.png", "https://commons.wikimedia.org/wiki/Special:Redirect/file/AKM.png", "microtext/docs/source_intake_2026_05_24/wikimedia_akm.png", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons process image."),
    ImportSource("pid_065", "wikimedia_blue_grass_piping_designer", "pid", "https://commons.wikimedia.org/wiki/File:Blue_Grass_Chemical_Agent-Destruction_Pilot_Plant_Piping_Designer_(28947777487).jpg", "https://commons.wikimedia.org/wiki/Special:Redirect/file/Blue_Grass_Chemical_Agent-Destruction_Pilot_Plant_Piping_Designer_(28947777487).jpg", "microtext/docs/source_intake_2026_05_24/wikimedia_blue_grass_piping_designer.jpg", "image", "commons_license_candidate", "Commons file page must be captured and checked before public release.", "Direct Commons plant/piping image."),
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fieldnames} for row in rows])


def pdf_page_count(path: Path) -> int:
    doc = fitz.open(path)
    count = doc.page_count
    doc.close()
    return count


def render_pdf_and_text(root: Path, source: ImportSource, dpi: int) -> tuple[int, int]:
    pdf_path = root / source.local_relpath
    pages_dir = root / "derived" / f"pages_{dpi}dpi" / source.doc_id
    textlayer_dir = root / "derived" / "textlayer" / source.doc_id
    pages_dir.mkdir(parents=True, exist_ok=True)
    textlayer_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    spans_jsonl: list[dict[str, Any]] = []
    for page_index, page in enumerate(doc):
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
        pix.save(pages_dir / f"page_{page_index:03d}.png")
        page_spans: list[dict[str, Any]] = []
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = span.get("bbox") or [0, 0, 0, 0]
                    bbox_px = [float(coord) * dpi / 72.0 for coord in bbox]
                    row = {
                        "page": page_index,
                        "text": text,
                        "bbox_px": bbox_px,
                        "font": span.get("font"),
                        "size": span.get("size"),
                        "flags": span.get("flags"),
                        "color": span.get("color"),
                    }
                    spans_jsonl.append(row)
                    page_spans.append(
                        {
                            "page_index": page_index,
                            "text": text,
                            "bbox": bbox,
                            "origin": span.get("origin"),
                            "size": span.get("size"),
                            "font": span.get("font"),
                            "flags": span.get("flags"),
                            "color": span.get("color"),
                        }
                    )
        (textlayer_dir / f"page_{page_index:03d}.json").write_text(
            json.dumps({"doc_page": page_index, "spans": page_spans}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    doc.close()
    write_jsonl(root / "derived" / "textlayer" / f"{source.doc_id}.jsonl", spans_jsonl)
    return len(list(pages_dir.glob("page_*.png"))), len(spans_jsonl)


def import_image(root: Path, source: ImportSource, dpi: int) -> tuple[int, int]:
    image_path = root / source.local_relpath
    pages_dir = root / "derived" / f"pages_{dpi}dpi" / source.doc_id
    pages_dir.mkdir(parents=True, exist_ok=True)
    out_path = pages_dir / "page_000.png"
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.save(out_path, format="PNG", optimize=True)
    return 1, 0


def manifest_row(root: Path, source: ImportSource, page_count: int) -> dict[str, Any]:
    local_path = root / source.local_relpath
    derived: dict[str, str] = {"pages_dir": f"derived/pages_300dpi/{source.doc_id}"}
    if source.asset_kind == "pdf":
        derived["textlayer_dir"] = f"derived/textlayer/{source.doc_id}"
        derived["textlayer_jsonl"] = f"derived/textlayer/{source.doc_id}.jsonl"
    return {
        "type": "doc",
        "doc_id": source.doc_id,
        "task": "microtext",
        "same_model_id": source.source_url.rstrip("/").rsplit("/", 1)[-1],
        "doc_type": source.asset_kind,
        "version": {"imported": "2026-05-24"},
        "path": source.local_relpath,
        "sha256": sha256_file(local_path),
        "pages": page_count,
        "render": {"dpi": 300, "colorspace": "gray" if source.asset_kind == "pdf" else "rgb", "rotate_cw90": False},
        "derived": derived,
        "source_url": source.source_url,
        "direct_source_url": source.direct_asset_url,
        "public_status": source.rights_tier,
        "license_note": source.license_note,
        "notes": source.notes,
    }


def append_manifest(root: Path, rows: list[dict[str, Any]]) -> int:
    path = root / "manifest.jsonl"
    existing = load_jsonl(path)
    existing_ids = {str(row.get("doc_id")) for row in existing if row.get("type") == "doc"}
    new_rows = [row for row in rows if str(row.get("doc_id")) not in existing_ids]
    if not new_rows:
        return 0
    with path.open("a", encoding="utf-8") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new_rows)


def build_candidates(root: Path, sources: list[ImportSource]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text_candidates: list[dict[str, Any]] = []
    image_review_rows: list[dict[str, Any]] = []
    for source in sources:
        if source.asset_kind == "pdf":
            rows = load_jsonl(root / "derived" / "textlayer" / f"{source.doc_id}.jsonl")
            mined = mine_microtext_candidates.mine_rows(rows, doc_id=source.doc_id, version_id="2026-05-24")
            for candidate in mined:
                candidate["source_candidate_id"] = source.candidate_id
            text_candidates.extend(mined)
        else:
            image_review_rows.extend(
                propose_microtext_regions.build_review_rows(
                    root=root,
                    doc_id=source.doc_id,
                    version_id="2026-05-24",
                    page_indexes=[0],
                    dpi=300,
                    limit_per_page=30,
                    total_limit=20,
                    category="unknown_pid_microtext",
                )
            )
            for row in image_review_rows:
                if row.get("doc_id") == source.doc_id:
                    row["source_candidate_id"] = source.candidate_id
                    row["review_notes"] = f"{row.get('review_notes', '')}; {source.notes}".strip("; ")
    return text_candidates, image_review_rows


def append_worklog(root: Path, summary: dict[str, Any]) -> None:
    worklog = root / "TRACK_B_WORKLOG.md"
    existing = worklog.read_text(encoding="utf-8") if worklog.exists() else "# Track B Worklog\n"
    marker = "2026-05-24 v1.5 First20 Source Import"
    if marker in existing:
        return
    entry = (
        f"\n## {marker}\n"
        f"- Imported sources: `{summary['sources']}`.\n"
        f"- Rendered page images: `{summary['pages']}`.\n"
        f"- Extracted text spans: `{summary['text_spans']}`.\n"
        f"- Textlayer candidates mined: `{summary['text_candidates']}`.\n"
        f"- Review rows staged: `{summary['review_rows']}`.\n"
        "- Review pack: `derived/review_packs/microtext_v1_5_first20_2026-05-24/`.\n"
        "- Status: review candidates only; no rows promoted to gold in this machine pass.\n"
    )
    worklog.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import first 20 v1.5 source assets")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--review-limit", type=int, default=320)
    parser.add_argument("--max-per-category", type=int, default=120)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    missing = [source.local_relpath for source in SOURCES if not (root / source.local_relpath).exists()]
    if missing:
        raise SystemExit("Missing downloaded source assets: " + ", ".join(missing))

    import_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    total_pages = 0
    total_spans = 0
    for source in SOURCES:
        if source.asset_kind == "pdf":
            page_count, span_count = render_pdf_and_text(root, source, dpi=300)
        else:
            page_count, span_count = import_image(root, source, dpi=300)
        total_pages += page_count
        total_spans += span_count
        sha256 = sha256_file(root / source.local_relpath)
        import_row = {
            "candidate_id": source.candidate_id,
            "doc_id": source.doc_id,
            "domain": source.domain,
            "asset_kind": source.asset_kind,
            "local_relpath": source.local_relpath,
            "source_url": source.source_url,
            "direct_asset_url": source.direct_asset_url,
            "rights_tier": source.rights_tier,
            "sha256": sha256,
            "rendered_pages": page_count,
            "text_spans": span_count,
            "status": "rendered_for_review",
            "notes": source.notes,
        }
        import_rows.append(import_row)
        manifest_rows.append(manifest_row(root, source, page_count))

    appended_manifest = append_manifest(root, manifest_rows)
    text_candidates, image_review_rows = build_candidates(root, SOURCES)
    candidate_path = root / "microtext" / "annotations" / "microtext_candidates_v1_5_first20_2026-05-24.jsonl"
    write_jsonl(candidate_path, text_candidates + image_review_rows)

    existing_items = microtext_review.load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    text_review_rows = microtext_review.build_review_batch(
        root=root,
        candidates=text_candidates,
        existing_items=existing_items,
        reviewed_rows=[],
        limit=args.review_limit,
        max_per_category=args.max_per_category,
        doc_ids={source.doc_id for source in SOURCES if source.asset_kind == "pdf"},
    )
    # Keep machine-proposed image regions, but cap each raster source to avoid a giant low-information batch.
    review_rows = text_review_rows + image_review_rows
    review_path = root / "microtext" / "annotations" / "microtext_review_v1_5_first20_2026-05-24.jsonl"
    write_jsonl(review_path, review_rows)

    pack_dir = Path("derived/review_packs/microtext_v1_5_first20_2026-05-24")
    stats = export_review_packs.export_microtext_pack(root, review_rows, pack_dir)
    checklist_path = root / "derived" / "human_adjudication" / "2026-05-24_v1_5_first20" / "microtext_v1_5_first20_validation_checklist.csv"
    checklist_rows = []
    pack_manifest = load_jsonl(root / pack_dir / "manifest.jsonl")
    for idx, row in enumerate(pack_manifest, start=1):
        checklist_rows.append(
            {
                "review_index": idx,
                "candidate_id": row.get("candidate_id", ""),
                "doc_id": row.get("doc_id", ""),
                "version_id": row.get("version_id", ""),
                "page_index": row.get("page_index", ""),
                "category": row.get("category", ""),
                "proposed_text": row.get("proposed_text") or row.get("target_text", ""),
                "crop_path": row.get("crop_path", ""),
                "image_path": row.get("image_path", ""),
                "text_context": row.get("text_context", ""),
                "question_text": row.get("question_text", ""),
                "review_status": "",
                "corrected_text": "",
                "corrected_category": "",
                "review_notes": "",
            }
        )
    write_csv(
        checklist_path,
        checklist_rows,
        [
            "review_index",
            "candidate_id",
            "doc_id",
            "version_id",
            "page_index",
            "category",
            "proposed_text",
            "crop_path",
            "image_path",
            "text_context",
            "question_text",
            "review_status",
            "corrected_text",
            "corrected_category",
            "review_notes",
        ],
    )

    packet_dir = root / "derived" / "source_imports"
    packet_dir.mkdir(parents=True, exist_ok=True)
    import_csv = packet_dir / "v1_5_first20_import_manifest_2026-05-24.csv"
    write_csv(
        import_csv,
        import_rows,
        [
            "candidate_id",
            "doc_id",
            "domain",
            "asset_kind",
            "local_relpath",
            "source_url",
            "direct_asset_url",
            "rights_tier",
            "sha256",
            "rendered_pages",
            "text_spans",
            "status",
            "notes",
        ],
    )
    summary = {
        "sources": len(SOURCES),
        "pdf_sources": sum(1 for source in SOURCES if source.asset_kind == "pdf"),
        "image_sources": sum(1 for source in SOURCES if source.asset_kind == "image"),
        "pages": total_pages,
        "text_spans": total_spans,
        "text_candidates": len(text_candidates),
        "image_region_candidates": len(image_review_rows),
        "review_rows": len(review_rows),
        "review_pack_rows": int(stats["rows"]),
        "manifest_rows_appended": appended_manifest,
        "by_review_category": dict(sorted(Counter(row.get("category", "unknown") for row in review_rows).items())),
    }
    summary_path = packet_dir / "v1_5_first20_import_summary_2026-05-24.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = packet_dir / "v1_5_first20_import_summary_2026-05-24.md"
    lines = [
        "# v1.5 First20 Source Import - 2026-05-24",
        "",
        "## Summary",
        f"- Imported sources: `{summary['sources']}` (`{summary['pdf_sources']}` PDFs, `{summary['image_sources']}` raster images).",
        f"- Rendered page images: `{summary['pages']}`.",
        f"- Extracted PDF text spans: `{summary['text_spans']}`.",
        f"- Textlayer candidates mined: `{summary['text_candidates']}`.",
        f"- Raster-region candidates proposed: `{summary['image_region_candidates']}`.",
        f"- Review rows staged: `{summary['review_rows']}`.",
        f"- Review pack rows exported: `{summary['review_pack_rows']}`.",
        f"- Manifest doc rows appended: `{summary['manifest_rows_appended']}`.",
        "",
        "## Artifacts",
        f"- Import manifest: `{import_csv.relative_to(root).as_posix()}`",
        f"- Candidate JSONL: `{candidate_path.relative_to(root).as_posix()}`",
        f"- Review JSONL: `{review_path.relative_to(root).as_posix()}`",
        f"- Review pack: `{pack_dir.as_posix()}`",
        f"- Human checklist: `{checklist_path.relative_to(root).as_posix()}`",
        "",
        "## Review Categories",
    ]
    lines.extend(f"- {category}: `{count}`" for category, count in summary["by_review_category"].items())
    lines.extend(
        [
            "",
            "## Status",
            "",
            "These rows are review candidates only. Do not merge them into gold until a human fills the checklist and the accepted rows are applied through the microtext review/merge path.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    append_worklog(root, summary)

    print(f"[OK] Imported {summary['sources']} first20 source assets")
    print(f"[OK] Rendered pages: {summary['pages']}")
    print(f"[OK] Review rows: {summary['review_rows']}")
    print(f"[OK] Review pack rows: {summary['review_pack_rows']}")
    print(f"[OK] Summary: {md_path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
