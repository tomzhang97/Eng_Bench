#!/usr/bin/env python3
"""Import the validated Raspberry Pi datasheet candidates ds_004 and ds_006."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(".")
IMPORT_DATE = "2026-06-11"
DOCS = [
    {
        "candidate": "ds_004",
        "doc_id": "rpi_pico_datasheet",
        "url": "https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf",
        "title": "Raspberry Pi Pico Datasheet",
    },
    {
        "candidate": "ds_006",
        "doc_id": "rpi_rp2040_hardware_design",
        "url": "https://datasheets.raspberrypi.com/rp2040/hardware-design-with-rp2040.pdf",
        "title": "Hardware design with RP2040",
    },
]
LICENSE_NOTE = (
    "Official Raspberry Pi documentation PDF published on datasheets.raspberrypi.com; preserve the "
    "publisher attribution, source URL, and exact hash; verify the in-document licence statement "
    "before any redistribution beyond benchmark evidence."
)


def run_tool(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"tool failed: {' '.join(args)}\n{result.stderr[-300:]}")


def main() -> int:
    inventory_path = ROOT / "SOURCE_INVENTORY.csv"
    with inventory_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        existing = {row["doc_id"] for row in reader}
    manifest_path = ROOT / "manifest.jsonl"
    manifest_ids = {
        row.get("doc_id")
        for row in (json.loads(line) for line in manifest_path.open(encoding="utf-8") if line.strip())
        if row.get("type") == "doc"
    }
    inventory_appends, manifest_appends, evidence_files = [], [], []
    for doc in DOCS:
        doc_id = doc["doc_id"]
        if doc_id in existing:
            print(f"[SKIP] {doc_id}")
            continue
        local_rel = f"microtext/docs/{doc_id}.pdf"
        request = urllib.request.Request(doc["url"], headers={"User-Agent": "Eng-Bench-source-intake/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        (ROOT / local_rel).write_bytes(payload)
        sha256 = hashlib.sha256(payload).hexdigest()
        run_tool(["tools/01_render_pdf.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel, "--dpi", "300", "--grayscale"])
        run_tool(["tools/02_extract_textlayer.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel])
        run_tool(["tools/02b_convert_textlayer_to_jsonl.py", "--root", ".", "--doc_id", doc_id, "--dpi", "300", "--pdf_relpath", local_rel])
        spans = sum(1 for line in (ROOT / "derived" / "textlayer" / f"{doc_id}.jsonl").open(encoding="utf-8") if line.strip())
        pages = len(list((ROOT / "derived" / "pages_300dpi" / doc_id).glob("page_*.png")))
        print(f"[OK] {doc_id}: pages={pages} spans={spans}")
        inventory_appends.append(
            {
                "path": local_rel,
                "task": "microtext",
                "doc_id": doc_id,
                "domain": "pcb_schematic",
                "source_url": doc["url"],
                "public_status": "public_vendor_datasheet_candidate",
                "textlayer_status": "present",
                "render_status": "present",
                "notes": (
                    f"Candidate {doc['candidate']}; {doc['title']}; sha256={sha256.upper()}; {spans} exact "
                    f"text spans across {pages} pages; official Raspberry Pi documentation PDF with embedded "
                    "schematics and pinout tables."
                ),
            }
        )
        manifest_appends.append(
            {
                "type": "doc",
                "doc_id": doc_id,
                "task": "microtext",
                "source_candidate_id": doc["candidate"],
                "same_model_id": doc_id,
                "domain": "pcb_schematic",
                "doc_type": "pdf_vendor_datasheet",
                "version": {"snapshot": f"published_{IMPORT_DATE}", "imported": IMPORT_DATE},
                "path": local_rel,
                "sha256": sha256,
                "pages": pages,
                "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                "derived": {
                    "pages_dir": f"derived/pages_300dpi/{doc_id}",
                    "textlayer_dir": f"derived/textlayer/{doc_id}",
                    "textlayer_jsonl": f"derived/textlayer/{doc_id}.jsonl",
                },
                "source_url": doc["url"],
                "direct_source_url": doc["url"],
                "public_status": "public_vendor_datasheet_candidate",
                "license_note": LICENSE_NOTE,
                "notes": f"Candidate {doc['candidate']}. {doc['title']}; no unreviewed row is active gold.",
            }
        )
        evidence_files.append({"doc_id": doc_id, "url": doc["url"], "sha256": sha256, "pages": pages, "textlayer_spans": spans})
    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writerows(inventory_appends)
    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as f:
            for row in manifest_appends:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (ROOT / "derived" / "source_imports" / "rpi_datasheets_import_2026-06-11.json").write_text(
        json.dumps({"wave": "rpi_datasheets", "import_date": IMPORT_DATE, "files": evidence_files}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] inventory+{len(inventory_appends)} manifest+{len(manifest_appends)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
