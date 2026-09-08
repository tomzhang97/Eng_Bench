#!/usr/bin/env python3
"""Import the 2026-06-11 Wikimedia process-flow-diagram wave.

Curated SVG members of the Commons "Process flow diagrams" collection
(candidate pid_031 lineage). For every file the script captures the exact
Commons license metadata through the API, downloads the pinned payload,
renders it at 300 DPI, extracts the exact SVG text layer, quarantines
renders that collapse or carry too little text (the pid_052 failure mode),
and records inventory/manifest/attribution/import evidence. Review staging
runs separately; nothing here touches active gold.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(".")
IMPORT_DATE = "2026-06-11"
CANDIDATE = "pid_031"
API = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSE_PATTERN = re.compile(r"^(cc0|cc[ -]by(?:[ -]sa)?[ -]\d|public domain|pd)", re.IGNORECASE)
USER_AGENT = "Eng-Bench-source-intake/1.0 (engineering-document benchmark; contact: maintainer)"


def fetch(url: str, timeout: int = 60, attempts: int = 5) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 5.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    raise RuntimeError("unreachable")

TITLES = [
    "File:Continuous Binary Fractional Distillation EN.svg",
    "File:Continuous Fractional Distillation EN.svg",
    "File:Ethylene oxide production plant.svg",
    "File:Haber-Bosch-En.svg",
    "File:IGCC diagram.svg",
    "File:NaturalGasCondensate en.svg",
    "File:Oil field flow metering.svg",
    "File:Phosphor production (en).svg",
    "File:Pipeline cross connection.svg",
    "File:Polyvinyl chloride (PVC) production from suspension polymerization.svg",
    "File:Steam cracking.svg",
    "File:Syngas Products.svg",
    "File:Well test separator.svg",
    "File:Carbon black production.svg",
    "File:Gas black process.svg",
    "File:Girdler.svg",
    "File:ITmk3 Process diagram.svg",
    "File:Midrex Process diagram.svg",
    "File:Krupp-Renn Process diagram.svg",
    "File:ChemSepProcDiagram.svg",
]


def slugify(title: str) -> str:
    stem = title.removeprefix("File:").removesuffix(".svg")
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"wikimedia_{slug}"


def api_imageinfo(title: str) -> dict:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|sha1|extmetadata",
        }
    )
    payload = json.loads(fetch(f"{API}?{params}", timeout=45))
    pages = payload["query"]["pages"]
    info = next(iter(pages.values()))
    return (info.get("imageinfo") or [{}])[0]


def meta_value(extmetadata: dict, key: str) -> str:
    value = (extmetadata.get(key) or {}).get("value") or ""
    return re.sub(r"<[^>]+>", "", str(value)).strip()


def license_tier(short_name: str) -> str | None:
    if not ALLOWED_LICENSE_PATTERN.match(short_name):
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", short_name.lower()).strip("_")
    return f"{slug}_commons_candidate"


def run_tool(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"tool failed: {' '.join(args)}\n{result.stdout[-300:]}\n{result.stderr[-300:]}")


def main() -> int:
    inventory_path = ROOT / "SOURCE_INVENTORY.csv"
    with inventory_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        inventory_fields = reader.fieldnames
        inventory_ids = {row["doc_id"] for row in reader}
    manifest_path = ROOT / "manifest.jsonl"
    manifest_doc_ids = {
        row.get("doc_id")
        for row in (json.loads(line) for line in manifest_path.open(encoding="utf-8") if line.strip())
        if row.get("type") == "doc"
    }

    imported, quarantined, skipped = [], [], []
    inventory_appends, manifest_appends = [], []

    for title in TITLES:
        doc_id = slugify(title)
        if doc_id in inventory_ids:
            skipped.append({"title": title, "reason": "already_in_inventory"})
            continue
        time.sleep(4.0)
        try:
            info = api_imageinfo(title)
        except Exception as error:  # noqa: BLE001 - record and continue the wave
            skipped.append({"title": title, "reason": f"fetch_error:{error}"})
            print(f"[SKIP] {doc_id}: {error}")
            continue
        extmetadata = info.get("extmetadata") or {}
        short_name = meta_value(extmetadata, "LicenseShortName")
        artist = meta_value(extmetadata, "Artist") or "unknown Commons contributor"
        license_url = meta_value(extmetadata, "LicenseUrl")
        tier = license_tier(short_name)
        if tier is None or not info.get("url"):
            skipped.append({"title": title, "reason": f"license_not_allowlisted:{short_name or 'missing'}"})
            continue

        local_rel = f"microtext/docs/{doc_id}.svg"
        local_path = ROOT / local_rel
        try:
            payload = fetch(info["url"], timeout=60)
        except Exception as error:  # noqa: BLE001 - record and continue the wave
            skipped.append({"title": title, "reason": f"fetch_error:{error}"})
            print(f"[SKIP] {doc_id}: {error}")
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(payload)
        sha256 = hashlib.sha256(payload).hexdigest()

        run_tool(["tools/01_render_pdf.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel, "--dpi", "300", "--grayscale"])
        page_png = ROOT / "derived" / "pages_300dpi" / doc_id / "page_000.png"
        with Image.open(page_png) as image:
            grayscale = image.convert("L")
            mean = sum(grayscale.getdata()) / (grayscale.width * grayscale.height)
        run_tool(
            [
                "tools/extract_svg_textlayer.py",
                "--root", ".",
                "--doc-id", doc_id,
                "--svg", local_rel,
                "--image", f"derived/pages_300dpi/{doc_id}/page_000.png",
            ]
        )
        textlayer = ROOT / "derived" / "textlayer" / f"{doc_id}.jsonl"
        spans = sum(1 for line in textlayer.open(encoding="utf-8") if line.strip()) if textlayer.exists() else 0

        record = {
            "title": title,
            "doc_id": doc_id,
            "local_path": local_rel,
            "sha256": sha256,
            "commons_sha1": info.get("sha1") or "",
            "direct_source_url": info.get("url"),
            "file_page_url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "license_short_name": short_name,
            "license_url": license_url,
            "artist": artist,
            "render_mean_gray": round(mean, 1),
            "textlayer_spans": spans,
        }
        if mean < 60.0 or mean > 253.0 or spans < 12:
            record["hold_reason"] = "render_or_textlayer_quality_hold"
            quarantined.append(record)
            print(f"[HOLD] {doc_id}: mean={mean:.0f} spans={spans}")
            continue

        attribution_path = ROOT / "microtext" / "docs" / f"{doc_id}_ATTRIBUTION.md"
        attribution_path.write_text(
            "\n".join(
                [
                    f"# {title.removeprefix('File:')} Attribution",
                    "",
                    f"- Author: {artist}",
                    f"- License: {short_name}" + (f" ({license_url})" if license_url else ""),
                    f"- Commons file page: {record['file_page_url']}",
                    f"- Direct payload: {record['direct_source_url']}",
                    f"- Local SHA-256: `{sha256}`",
                    f"- Import date: {IMPORT_DATE}",
                    "",
                    "Preserve author attribution, the license terms above, the exact",
                    "source hash, and the Commons file-page URL in any redistribution.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        inventory_appends.append(
            {
                "path": local_rel,
                "task": "microtext",
                "doc_id": doc_id,
                "domain": "pid",
                "source_url": record["file_page_url"],
                "public_status": tier,
                "textlayer_status": "present",
                "render_status": "present",
                "notes": (
                    f"Candidate {CANDIDATE} Commons process-flow collection; author {artist}; license "
                    f"{short_name}; sha256={sha256.upper()}; {spans} exact SVG text spans; attribution in "
                    f"microtext/docs/{doc_id}_ATTRIBUTION.md."
                ),
            }
        )
        manifest_appends.append(
            {
                "type": "doc",
                "doc_id": doc_id,
                "task": "microtext",
                "source_candidate_id": CANDIDATE,
                "same_model_id": "commons_process_flow_diagrams",
                "domain": "pid",
                "doc_type": "svg_process_flow_diagram",
                "version": {"snapshot": f"commons_current_{IMPORT_DATE}", "imported": IMPORT_DATE},
                "path": local_rel,
                "sha256": sha256,
                "pages": 1,
                "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                "derived": {
                    "pages_dir": f"derived/pages_300dpi/{doc_id}",
                    "textlayer_jsonl": f"derived/textlayer/{doc_id}.jsonl",
                    "rendered_source_path": f"derived/pages_300dpi/{doc_id}/page_000.png",
                },
                "source_url": record["file_page_url"],
                "direct_source_url": record["direct_source_url"],
                "public_status": tier,
                "license_note": (
                    f"Commons file page identifies {artist} as author under {short_name}; preserve attribution "
                    "and license terms. See local attribution file."
                ),
                "attribution_path": f"microtext/docs/{doc_id}_ATTRIBUTION.md",
                "notes": (
                    f"Candidate {CANDIDATE}. Exact SVG text layer contains {spans} spans; "
                    "no unreviewed row is active gold."
                ),
            }
        )
        imported.append(record)
        print(f"[OK] {doc_id}: license={short_name} spans={spans} mean={mean:.0f}")

    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=inventory_fields)
            writer.writerows(inventory_appends)
    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as f:
            for row in manifest_appends:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    evidence = {
        "wave": "wikimedia_process_flow_wave",
        "import_date": IMPORT_DATE,
        "candidate_id": CANDIDATE,
        "collection_url": "https://commons.wikimedia.org/wiki/Category:Process_flow_diagrams",
        "release_posture": "review_only_until_human_adjudication",
        "imported": imported,
        "quarantined": quarantined,
        "skipped": skipped,
    }
    evidence_path = ROOT / "derived" / "source_imports" / "wikimedia_pfd_wave_import_2026-06-11.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] imported={len(imported)} quarantined={len(quarantined)} skipped={len(skipped)}")
    print(f"[OK] inventory rows appended: {len(inventory_appends)}; manifest rows appended: {len(manifest_appends)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
