#!/usr/bin/env python3
"""Import the 2026-06-11 Adafruit Feather wave (12 validated repos, 26 docs).

For each browser-validated candidate repo this script downloads the exact
schematic/pinout payloads pinned to a recorded commit, preserves upstream
README and license.txt plus a local ATTRIBUTION.md, renders EAGLE XML
schematics and pinout PDFs at 300 DPI, extracts exact text layers, and
records inventory/manifest/import-evidence rows. Review staging and packet
builds run separately; nothing here touches active gold.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(".")
IMPORT_DATE = "2026-06-11"
LICENSE_NOTE = (
    "Upstream README and license.txt state Creative Commons Attribution, Share-Alike; preserve "
    "Adafruit Industries/Limor Fried attribution, the full README text, exact source hashes, and "
    "the pinned commit when redistributing."
)

WAVE = [
    {
        "candidate": "pcb_033",
        "repo": "Adafruit-Feather-ESP32-S2-PCB",
        "commit": "4a6263861580",
        "slug": "adafruit_feather_esp32_s2",
        "files": [
            ("Adafruit Feather ESP32-S2.sch", "original_sch", "Original"),
            ("Adafruit Feather ESP32-S2 Rev C.sch", "rev_c_sch", "Rev C"),
            ("Adafruit Feather ESP32-S2 Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_034",
        "repo": "Adafruit-Feather-ESP32-S3-PCB",
        "commit": "2c95ed51a500",
        "slug": "adafruit_feather_esp32_s3",
        "files": [
            ("Adafruit ESP32-S3 8MB No PSRAM.sch", "sch", "current"),
            ("Adafruit Feather ESP32-S3 Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_035",
        "repo": "Adafruit-Feather-M4-Express-PCB",
        "commit": "d98407a60718",
        "slug": "adafruit_feather_m4_express",
        "files": [
            ("Adafruit Feather M4 Express.sch", "sch", "current"),
            ("Adafruit Feather M4 Express Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_046",
        "repo": "Adafruit-Feather-M0-Express-PCB",
        "commit": "5017ef418853",
        "slug": "adafruit_feather_m0_express",
        "files": [
            ("Adafruit Feather M0 Express.sch", "sch", "current"),
            ("Adafruit Feather M0 Express pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_047",
        "candidate_aliases": ["pcb_016"],
        "repo": "Adafruit-Feather-M0-Adalogger-PCB",
        "commit": "0147a7204c11",
        "slug": "adafruit_feather_m0_adalogger",
        "files": [
            ("Adafruit Feather M0 Adalogger.sch", "sch", "current"),
            ("Adafruit Feather M0 Adalogger Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_052",
        "repo": "Adafruit-Feather-32u4-Adalogger-PCB",
        "commit": "c7e87d6e0feb",
        "slug": "adafruit_feather_32u4_adalogger",
        "files": [
            ("Adafruit Feather 32u4 Adalogger.sch", "sch", "current"),
            ("Adafruit Feather 32u4 Adalogger Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_053",
        "repo": "Adafruit-Feather-32u4-Basic-Proto-PCB",
        "commit": "42d06b39d8af",
        "slug": "adafruit_feather_32u4_basic_proto",
        "files": [
            ("Adafruit Feather 32u4 Basic rev B.sch", "sch", "Rev B"),
            ("Adafruit Feather 32u4 Basic Proto Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_054",
        "repo": "Adafruit-Feather-M0-Basic-Proto-PCB",
        "commit": "1de541ff4616",
        "slug": "adafruit_feather_m0_basic_proto",
        "files": [
            ("Adafruit Feather M0 Basic rev C.sch", "sch", "Rev C"),
            ("Adafruit Feather M0 Basic Proto Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_066",
        "repo": "Adafruit-HUZZAH32-ESP32-Feather-PCB",
        "commit": "e23514c6f9e1",
        "slug": "adafruit_huzzah32_esp32_feather",
        "files": [
            ("Adafruit HUZZAH32 ESP32 Feather.sch", "sch", "current"),
            ("Adafruit HUZZAH32 ESP32 Feather Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_068",
        "repo": "Adafruit-Feather-nRF52840-Sense-PCB",
        "commit": "afeb0acb67e9",
        "slug": "adafruit_feather_nrf52840_sense",
        "files": [
            ("Adafruit Feather nRF52840 Sense.sch", "original_sch", "Original"),
            ("Adafruit Feather nRF52840 Sense Rev C.sch", "rev_c_sch", "Rev C"),
            ("Adafruit Feather nRF52840 Sense pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_074",
        "repo": "Adafruit-ESP32-S2-TFT-Feather-PCB",
        "commit": "9e9f52790239",
        "slug": "adafruit_esp32_s2_tft_feather",
        "files": [
            ("Adafruit ESP32-S2 TFT Feather.sch", "sch", "current"),
            ("Adafruit ESP32-S2 TFT Feather Pinout.pdf", "pinout", "current"),
        ],
    },
    {
        "candidate": "pcb_075",
        "repo": "Adafruit-ESP32-S3-TFT-Feather-PCB",
        "commit": "e2cfb6b5e5ed",
        "slug": "adafruit_esp32_s3_tft_feather",
        "files": [
            ("Adafruit ESP32-S3 TFT Feather.sch", "sch", "current"),
            ("Adafruit ESP32-S3 TFT Feather Pinout.pdf", "pinout", "current"),
        ],
    },
]

PAIRS = [
    {
        "slug": "adafruit_feather_esp32_s2",
        "pair_id": "vdiff__adafruit_feather_esp32_s2__original__to__rev_c",
        "from_suffix": "original_sch",
        "to_suffix": "rev_c_sch",
    },
    {
        "slug": "adafruit_feather_nrf52840_sense",
        "pair_id": "vdiff__adafruit_feather_nrf52840_sense__original__to__rev_c",
        "from_suffix": "original_sch",
        "to_suffix": "rev_c_sch",
    },
]


def download(url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    dest.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def run_tool(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"tool failed: {' '.join(args)}\n{result.stdout[-400:]}\n{result.stderr[-400:]}")


def count_spans(doc_id: str) -> int:
    path = ROOT / "derived" / "textlayer" / f"{doc_id}.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.open(encoding="utf-8") if line.strip())


def main() -> int:
    inventory_path = ROOT / "SOURCE_INVENTORY.csv"
    with inventory_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        inventory_fields = reader.fieldnames
        inventory_ids = {row["doc_id"] for row in reader}

    manifest_path = ROOT / "manifest.jsonl"
    manifest_rows = [json.loads(line) for line in manifest_path.open(encoding="utf-8") if line.strip()]
    manifest_doc_ids = {row.get("doc_id") for row in manifest_rows if row.get("type") == "doc"}
    manifest_pair_ids = {row.get("pair_id") for row in manifest_rows if row.get("type") == "pair"}

    evidence_repos = []
    inventory_appends: list[dict] = []
    manifest_appends: list[dict] = []

    for repo_spec in WAVE:
        repo = repo_spec["repo"]
        commit = repo_spec["commit"]
        slug = repo_spec["slug"]
        candidate = repo_spec["candidate"]
        raw_base = f"https://raw.githubusercontent.com/adafruit/{repo}/{commit}"
        docs_dir = ROOT / "visualdiff" / "docs" / slug
        print(f"[{candidate}] {repo}")

        readme_hash = download(f"{raw_base}/README.md", docs_dir / "UPSTREAM_README.md")
        license_hash = download(f"{raw_base}/license.txt", docs_dir / "license.txt")

        file_records = []
        for upstream_name, suffix, revision in repo_spec["files"]:
            doc_id = f"{slug}_{suffix}"
            extension = Path(upstream_name).suffix.lower()
            local_rel = f"visualdiff/docs/{slug}/{doc_id}{extension}"
            local_path = ROOT / local_rel
            url = f"{raw_base}/{urllib.parse.quote(upstream_name)}"
            sha256 = download(url, local_path)

            if extension == ".sch":
                run_tool(
                    [
                        "tools/render_eagle_xml.py",
                        "--root", ".",
                        "--input", local_rel,
                        "--doc-id", doc_id,
                        "--dpi", "300",
                        "--manifest", f"derived/source_imports/{doc_id}_render.json",
                    ]
                )
                run_tool(
                    [
                        "tools/extract_svg_textlayer.py",
                        "--root", ".",
                        "--doc-id", doc_id,
                        "--svg-dir", f"derived/eagle_svg/{doc_id}",
                        "--image-dir", f"derived/pages_300dpi/{doc_id}",
                    ]
                )
                doc_type = "eagle_sch_xml"
                task = "visualdiff"
            else:
                run_tool(["tools/01_render_pdf.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel, "--dpi", "300"])
                run_tool(["tools/02_extract_textlayer.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel])
                run_tool(["tools/02b_convert_textlayer_to_jsonl.py", "--root", ".", "--doc_id", doc_id, "--dpi", "300", "--pdf_relpath", local_rel])
                doc_type = "pdf_pinout_card"
                task = "microtext"

            spans = count_spans(doc_id)
            pages = len(list((ROOT / "derived" / "pages_300dpi" / doc_id).glob("page_*.png")))
            file_records.append(
                {
                    "doc_id": doc_id,
                    "upstream_name": upstream_name,
                    "local_rel": local_rel,
                    "sha256": sha256,
                    "doc_type": doc_type,
                    "task": task,
                    "revision": revision,
                    "spans": spans,
                    "pages": pages,
                    "url": url,
                }
            )
            print(f"   {doc_id}: pages={pages} spans={spans}")

        attribution_lines = [
            f"# {repo} Attribution",
            "",
            "- Designer and copyright holder: Adafruit Industries (Limor Fried/Ladyada)",
            f"- Upstream repository: https://github.com/adafruit/{repo}",
            f"- Pinned upstream commit: `{commit}`",
            "- License: Creative Commons Attribution, Share-Alike, per the upstream",
            "  README and `license.txt` (both copied verbatim alongside this file).",
            "  The README requires that all of its text be included in any",
            "  redistribution.",
            f"- Import date: {IMPORT_DATE}",
            "",
            "## Imported files",
            "",
            "| Local file | Upstream path | SHA-256 |",
            "| --- | --- | --- |",
        ]
        for record in file_records:
            attribution_lines.append(
                f"| `{record['local_rel']}` | `{record['upstream_name']}` | `{record['sha256']}` |"
            )
        attribution_lines.extend(
            [
                "",
                "## Release requirements",
                "",
                "- Preserve Adafruit Industries / Limor Fried (Ladyada) attribution.",
                "- Preserve the CC BY-SA share-alike terms, the full upstream README",
                "  text, and the upstream license.txt.",
                "- Preserve the exact source hashes and the pinned upstream commit.",
            ]
        )
        (docs_dir / "ATTRIBUTION.md").write_text("\n".join(attribution_lines) + "\n", encoding="utf-8")

        alias_note = ""
        if repo_spec.get("candidate_aliases"):
            alias_note = (
                " Candidate backlog rows "
                + ", ".join(repo_spec["candidate_aliases"])
                + " reference the same repository and should be reconciled to this import."
            )

        for record in file_records:
            if record["doc_id"] not in inventory_ids:
                inventory_appends.append(
                    {
                        "path": record["local_rel"],
                        "task": record["task"],
                        "doc_id": record["doc_id"],
                        "domain": "pcb_schematic",
                        "source_url": f"https://github.com/adafruit/{repo}",
                        "public_status": "cc_by_sa_open_hardware_candidate",
                        "textlayer_status": "present" if record["spans"] else "absent",
                        "render_status": "present" if record["pages"] else "absent",
                        "notes": (
                            f"Candidate {candidate}; {record['upstream_name']} pinned to commit {commit}; "
                            f"sha256={record['sha256'].upper()}; {record['spans']} exact text spans; local "
                            "UPSTREAM_README.md, license.txt, and ATTRIBUTION.md preserve CC BY-SA terms and "
                            f"Adafruit attribution.{alias_note}"
                        ),
                    }
                )
            if record["doc_id"] not in manifest_doc_ids:
                derived = {
                    "pages_dir": f"derived/pages_300dpi/{record['doc_id']}",
                    "textlayer_jsonl": f"derived/textlayer/{record['doc_id']}.jsonl",
                }
                if record["doc_type"] == "eagle_sch_xml":
                    derived["eagle_svg_dir"] = f"derived/eagle_svg/{record['doc_id']}"
                else:
                    derived["textlayer_dir"] = f"derived/textlayer/{record['doc_id']}"
                manifest_appends.append(
                    {
                        "type": "doc",
                        "doc_id": record["doc_id"],
                        "task": record["task"],
                        "source_candidate_id": candidate,
                        "same_model_id": slug,
                        "domain": "pcb_schematic",
                        "doc_type": record["doc_type"],
                        "version": {"revision": record["revision"], "imported": IMPORT_DATE},
                        "path": record["local_rel"],
                        "sha256": record["sha256"],
                        "pages": record["pages"],
                        "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                        "derived": derived,
                        "source_url": f"https://github.com/adafruit/{repo}",
                        "direct_source_url": record["url"],
                        "pinned_commit": commit,
                        "public_status": "cc_by_sa_open_hardware_candidate",
                        "license_note": LICENSE_NOTE,
                        "attribution_path": f"visualdiff/docs/{slug}/ATTRIBUTION.md",
                        "notes": (
                            f"Candidate {candidate}. {IMPORT_DATE} Feather-wave import with exact text layer; "
                            f"no unreviewed row is active gold.{alias_note}"
                        ),
                    }
                )

        evidence_repos.append(
            {
                "candidate_id": candidate,
                "candidate_aliases": repo_spec.get("candidate_aliases", []),
                "repo": repo,
                "source_url": f"https://github.com/adafruit/{repo}",
                "pinned_commit": commit,
                "upstream_readme_sha256": readme_hash,
                "upstream_license_sha256": license_hash,
                "attribution_path": f"visualdiff/docs/{slug}/ATTRIBUTION.md",
                "files": [
                    {
                        "doc_id": record["doc_id"],
                        "local_path": record["local_rel"],
                        "direct_source_url": record["url"],
                        "sha256": record["sha256"],
                        "pages": record["pages"],
                        "textlayer_spans": record["spans"],
                    }
                    for record in file_records
                ],
            }
        )

    for pair in PAIRS:
        if pair["pair_id"] in manifest_pair_ids:
            continue
        manifest_appends.append(
            {
                "type": "pair",
                "pair_id": pair["pair_id"],
                "task": "visualdiff",
                "pair_type": "same_model_revision",
                "from_doc_id": f"{pair['slug']}_{pair['from_suffix']}",
                "to_doc_id": f"{pair['slug']}_{pair['to_suffix']}",
                "page_mapping": {"type": "by_text_similarity", "pages_A": "0..0", "pages_B": "0..0"},
                "derived": {
                    "review_jsonl": (
                        f"visualdiff/annotations/visualdiff_review_{pair['slug']}_original_to_rev_c_2026-06-11.jsonl"
                    )
                },
                "notes": (
                    "Single-sheet EAGLE revision pair from the 2026-06-11 Feather wave; review-only "
                    "textlayer diff rows; rows are not gold until human adjudication."
                ),
            }
        )

    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=inventory_fields)
            writer.writerows(inventory_appends)
    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as f:
            for row in manifest_appends:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    evidence = {
        "wave": "adafruit_feather_wave",
        "import_date": IMPORT_DATE,
        "license": {
            "name": "CC BY-SA (per upstream README and license.txt)",
            "attribution": "Adafruit Industries (Limor Fried/Ladyada)",
        },
        "release_posture": "review_only_until_human_adjudication",
        "repos": evidence_repos,
    }
    evidence_path = ROOT / "derived" / "source_imports" / "adafruit_feather_wave_import_2026-06-11.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"[OK] inventory rows appended: {len(inventory_appends)}")
    print(f"[OK] manifest rows appended: {len(manifest_appends)}")
    print(f"[OK] wrote {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
