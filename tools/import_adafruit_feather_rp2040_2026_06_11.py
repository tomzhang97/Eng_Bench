"""Record the 2026-06-11 Adafruit Feather RP2040 revision-family import."""
import csv
import json
from pathlib import Path

ROOT = Path(".")
COMMIT = "ea88166891ee0a1697a3899a5d55ab3722a2f125"
REPO_URL = "https://github.com/adafruit/Adafruit-Feather-RP2040-PCB"
RAW_BASE = f"https://raw.githubusercontent.com/adafruit/Adafruit-Feather-RP2040-PCB/{COMMIT}"
IMPORT_DATE = "2026-06-11"
LICENSE_NOTE = (
    "Upstream README states Creative Commons Attribution, Share-Alike; preserve Adafruit "
    "Industries/Limor Fried attribution, the full README text, exact source hashes, and the "
    "pinned commit when redistributing."
)

DOCS = [
    {
        "doc_id": "adafruit_feather_rp2040_original_sch",
        "local_path": "visualdiff/docs/adafruit_feather_rp2040/adafruit_feather_rp2040_original.sch",
        "upstream_name": "Adafruit Feather RP2040 Original.sch",
        "sha256": "01aae2acd843c9f626d7119e77142e2897c8e98c5529a7c0faea2ce45402e422",
        "doc_type": "eagle_sch_xml",
        "task": "visualdiff",
        "text_spans": 485,
        "revision": "Original",
        "note": "EAGLE XML schematic rendered to one deterministic 300-DPI page with exact SVG text layer.",
    },
    {
        "doc_id": "adafruit_feather_rp2040_rev_b_sch",
        "local_path": "visualdiff/docs/adafruit_feather_rp2040/adafruit_feather_rp2040_rev_b.sch",
        "upstream_name": "Adafruit Feather RP2040 rev B.sch",
        "sha256": "267d036f97b97adf4b8889ab960081856535e5f557d6cf815f5b18453c603bdf",
        "doc_type": "eagle_sch_xml",
        "task": "visualdiff",
        "text_spans": 482,
        "revision": "Rev B",
        "note": "EAGLE XML schematic rendered to one deterministic 300-DPI page with exact SVG text layer.",
    },
    {
        "doc_id": "adafruit_feather_rp2040_pinout",
        "local_path": "visualdiff/docs/adafruit_feather_rp2040/adafruit_feather_rp2040_pinout.pdf",
        "upstream_name": "Adafruit Feather RP2040 pinout.pdf",
        "sha256": "e10a2a8c12d489d76631fa72abbc53914a1e2560ac2c23835b462261baa059ea",
        "doc_type": "pdf_pinout_card",
        "task": "microtext",
        "text_spans": 155,
        "revision": "current",
        "note": "Official pinout card with embedded text; 36 exact-text pin-label review rows staged 2026-06-11.",
    },
]

inventory_path = ROOT / "SOURCE_INVENTORY.csv"
with inventory_path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    existing_ids = {row["doc_id"] for row in reader}

new_rows = []
for doc in DOCS:
    if doc["doc_id"] in existing_ids:
        print(f"[SKIP] inventory row exists: {doc['doc_id']}")
        continue
    new_rows.append(
        {
            "path": doc["local_path"],
            "task": doc["task"],
            "doc_id": doc["doc_id"],
            "domain": "pcb_schematic",
            "source_url": REPO_URL,
            "public_status": "cc_by_sa_open_hardware_candidate",
            "textlayer_status": "present",
            "render_status": "present",
            "notes": (
                f"Candidate pcb_020; {doc['upstream_name']} pinned to commit {COMMIT[:12]}; "
                f"sha256={doc['sha256'].upper()}; {doc['text_spans']} exact text spans; "
                "local UPSTREAM_README.md and ATTRIBUTION.md preserve CC BY-SA terms and Adafruit "
                f"attribution. {doc['note']}"
            ),
        }
    )
if new_rows:
    with inventory_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerows(new_rows)
print(f"[OK] appended {len(new_rows)} inventory rows")

manifest_path = ROOT / "manifest.jsonl"
manifest_rows = [json.loads(line) for line in manifest_path.open(encoding="utf-8") if line.strip()]
manifest_doc_ids = {row.get("doc_id") for row in manifest_rows if row.get("type") == "doc"}
manifest_pair_ids = {row.get("pair_id") for row in manifest_rows if row.get("type") == "pair"}

appended = []
for doc in DOCS:
    if doc["doc_id"] in manifest_doc_ids:
        print(f"[SKIP] manifest doc exists: {doc['doc_id']}")
        continue
    derived = {
        "pages_dir": f"derived/pages_300dpi/{doc['doc_id']}",
        "textlayer_jsonl": f"derived/textlayer/{doc['doc_id']}.jsonl",
    }
    if doc["doc_type"] == "eagle_sch_xml":
        derived["eagle_svg_dir"] = f"derived/eagle_svg/{doc['doc_id']}"
    else:
        derived["textlayer_dir"] = f"derived/textlayer/{doc['doc_id']}"
    appended.append(
        {
            "type": "doc",
            "doc_id": doc["doc_id"],
            "task": doc["task"],
            "source_candidate_id": "pcb_020",
            "same_model_id": "adafruit_feather_rp2040",
            "domain": "pcb_schematic",
            "doc_type": doc["doc_type"],
            "version": {"revision": doc["revision"], "imported": IMPORT_DATE},
            "path": doc["local_path"],
            "sha256": doc["sha256"],
            "pages": 1,
            "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
            "derived": derived,
            "source_url": REPO_URL,
            "direct_source_url": f"{RAW_BASE}/{doc['upstream_name'].replace(' ', '%20')}",
            "pinned_commit": COMMIT,
            "public_status": "cc_by_sa_open_hardware_candidate",
            "license_note": LICENSE_NOTE,
            "attribution_path": "visualdiff/docs/adafruit_feather_rp2040/ATTRIBUTION.md",
            "notes": (
                f"Candidate pcb_020. {doc['note']} No unreviewed row is active gold."
            ),
        }
    )

pair_id = "vdiff__adafruit_feather_rp2040__original__to__rev_b"
if pair_id not in manifest_pair_ids:
    appended.append(
        {
            "type": "pair",
            "pair_id": pair_id,
            "task": "visualdiff",
            "pair_type": "same_model_revision",
            "from_doc_id": "adafruit_feather_rp2040_original_sch",
            "to_doc_id": "adafruit_feather_rp2040_rev_b_sch",
            "page_mapping": {"type": "by_text_similarity", "pages_A": "0..0", "pages_B": "0..0"},
            "derived": {
                "review_jsonl": (
                    "visualdiff/annotations/visualdiff_review_adafruit_feather_rp2040_original_to_rev_b_2026-06-11.jsonl"
                )
            },
            "notes": (
                "Single-sheet EAGLE revision pair; 5 review-only textlayer diff rows staged "
                "2026-06-11 covering the rev B NeoPixel power-gating change; rows are not gold "
                "until human adjudication."
            ),
        }
    )

if appended:
    with manifest_path.open("a", encoding="utf-8") as f:
        for row in appended:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
print(f"[OK] appended {len(appended)} manifest rows")

evidence = {
    "candidate_id": "pcb_020",
    "doc_ids": [doc["doc_id"] for doc in DOCS],
    "domain": "pcb_schematic",
    "import_date": IMPORT_DATE,
    "source_url": REPO_URL,
    "pinned_commit": COMMIT,
    "license": {
        "name": "CC BY-SA (per upstream README)",
        "attribution": "Adafruit Industries (Limor Fried/Ladyada)",
        "local_upstream_readme_path": "visualdiff/docs/adafruit_feather_rp2040/UPSTREAM_README.md",
        "local_attribution_path": "visualdiff/docs/adafruit_feather_rp2040/ATTRIBUTION.md",
        "release_requirements": [
            "Preserve Adafruit Industries attribution and the full upstream README text.",
            "Preserve CC BY-SA share-alike terms, exact source hashes, and the pinned commit.",
        ],
    },
    "files": [
        {
            "doc_id": doc["doc_id"],
            "local_path": doc["local_path"],
            "direct_source_url": f"{RAW_BASE}/{doc['upstream_name'].replace(' ', '%20')}",
            "sha256": doc["sha256"],
            "pages": 1,
            "textlayer_spans": doc["text_spans"],
        }
        for doc in DOCS
    ],
    "outputs": {
        "visualdiff_review_jsonl": (
            "visualdiff/annotations/visualdiff_review_adafruit_feather_rp2040_original_to_rev_b_2026-06-11.jsonl"
        ),
        "visualdiff_review_rows": 5,
        "microtext_candidates_jsonl": (
            "microtext/annotations/microtext_candidates_adafruit_feather_rp2040_pinout_2026-06-11.jsonl"
        ),
        "microtext_candidate_rows": 60,
        "microtext_review_jsonl": (
            "microtext/annotations/microtext_review_adafruit_feather_rp2040_pinout_2026-06-11.jsonl"
        ),
        "microtext_review_rows": 36,
    },
    "release_posture": "review_only_until_human_adjudication",
    "task": "visualdiff,microtext",
}
evidence_path = ROOT / "derived" / "source_imports" / "adafruit_feather_rp2040_import_2026-06-11.json"
evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"[OK] wrote {evidence_path}")
