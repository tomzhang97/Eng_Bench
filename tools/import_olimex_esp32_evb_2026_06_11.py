"""Record the 2026-06-11 Olimex ESP32-EVB revision-family import.

Appends source-inventory rows, manifest doc rows, and manifest pair rows
(including the local Aquila textlayer pair), and writes the import-evidence
JSON. Snapshots were taken to derived/snapshots/2026-06-11/ before this run.
"""
import csv
import json
from pathlib import Path

ROOT = Path(".")
COMMIT = "a3ec2f448109cb8f35ce7cb21a027bb7c837ab61"
REPO_URL = "https://github.com/OLIMEX/ESP32-EVB"
RAW_BASE = f"https://raw.githubusercontent.com/OLIMEX/ESP32-EVB/{COMMIT}/HARDWARE"
IMPORT_DATE = "2026-06-11"

DOCS = [
    {
        "rev": "i",
        "sha256": "13b428d371fb786fd6d53677c4235ff24a44763d6866b25cda228d72a370a6d6",
        "upstream_path": "REV-I/ESP32-EVB_Rev_I.pdf",
        "text_spans": 1301,
    },
    {
        "rev": "j",
        "sha256": "6415e7d56b2e5a121d5cdd2c59a15a283e2096ed27336fcb82693ed0d750bc09",
        "upstream_path": "REV-J/ESP32-EVB_Rev_J.pdf",
        "text_spans": 1394,
    },
    {
        "rev": "k",
        "sha256": "e6f5be7acbcc1ee820eb083f022c95e161b2ed7cae3e6f42f356ee0a0437816c",
        "upstream_path": "REV-K/ESP32-EVB_Rev_K.pdf",
        "text_spans": 1391,
    },
    {
        "rev": "l",
        "sha256": "5825dbc902e54ee3767d8eb23ddd83d8e47ee01522e0925dfcdbc11af1f0b1bd",
        "upstream_path": "REV-L/ESP32-EVB_Rev_L.pdf",
        "text_spans": 1963,
    },
]

PAIRS = [("i", "j", 25), ("j", "k", 1), ("k", "l", 30)]

# --- 1. SOURCE_INVENTORY.csv rows ---
inventory_path = ROOT / "SOURCE_INVENTORY.csv"
with inventory_path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    existing = list(reader)
existing_ids = {row["doc_id"] for row in existing}

new_rows = []
for doc in DOCS:
    doc_id = f"olimex_esp32_evb_rev_{doc['rev']}"
    if doc_id in existing_ids:
        print(f"[SKIP] inventory row exists: {doc_id}")
        continue
    new_rows.append(
        {
            "path": f"visualdiff/docs/olimex_esp32_evb_rev_{doc['rev']}.pdf",
            "task": "visualdiff",
            "doc_id": doc_id,
            "domain": "pcb_schematic",
            "source_url": REPO_URL,
            "public_status": "apache_2_0_open_hardware_candidate",
            "textlayer_status": "present",
            "render_status": "present",
            "notes": (
                f"Candidate pcb_017; Olimex ESP32-EVB Rev {doc['rev'].upper()} schematic PDF "
                f"pinned to commit {COMMIT[:12]}; sha256={doc['sha256'].upper()}; "
                f"{doc['text_spans']} exact text spans; local LICENSE and UPSTREAM_README.md "
                "preserve Apache-2.0 terms and Olimex attribution; revision-family member "
                "for textlayer visualdiff review rows staged 2026-06-11."
            ),
        }
    )

if new_rows:
    with inventory_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerows(new_rows)
print(f"[OK] appended {len(new_rows)} inventory rows")

# --- 2. manifest.jsonl doc + pair rows ---
manifest_path = ROOT / "manifest.jsonl"
manifest_rows = [
    json.loads(line)
    for line in manifest_path.open(encoding="utf-8")
    if line.strip()
]
manifest_doc_ids = {row.get("doc_id") for row in manifest_rows if row.get("type") == "doc"}
manifest_pair_ids = {row.get("pair_id") for row in manifest_rows if row.get("type") == "pair"}

appended = []
for doc in DOCS:
    doc_id = f"olimex_esp32_evb_rev_{doc['rev']}"
    if doc_id in manifest_doc_ids:
        print(f"[SKIP] manifest doc exists: {doc_id}")
        continue
    appended.append(
        {
            "type": "doc",
            "doc_id": doc_id,
            "task": "visualdiff",
            "source_candidate_id": "pcb_017",
            "same_model_id": "olimex_esp32_evb",
            "domain": "pcb_schematic",
            "doc_type": "pdf_schematic",
            "version": {"revision": f"Rev {doc['rev'].upper()}", "imported": IMPORT_DATE},
            "path": f"visualdiff/docs/olimex_esp32_evb_rev_{doc['rev']}.pdf",
            "sha256": doc["sha256"],
            "pages": 1,
            "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
            "derived": {
                "pages_dir": f"derived/pages_300dpi/{doc_id}",
                "textlayer_dir": f"derived/textlayer/{doc_id}",
                "textlayer_jsonl": f"derived/textlayer/{doc_id}.jsonl",
            },
            "source_url": REPO_URL,
            "direct_source_url": f"{RAW_BASE}/{doc['upstream_path']}",
            "pinned_commit": COMMIT,
            "public_status": "apache_2_0_open_hardware_candidate",
            "license_note": (
                "Local LICENSE and UPSTREAM_README.md preserve Apache-2.0 terms and Olimex "
                "attribution; retain exact source hash, pinned commit, and provenance when redistributing."
            ),
            "attribution_path": "visualdiff/docs/olimex_esp32_evb/ATTRIBUTION.md",
            "notes": (
                f"Candidate pcb_017. Olimex ESP32-EVB Rev {doc['rev'].upper()} single-page schematic with "
                f"{doc['text_spans']} embedded text spans; revision-family member; no unreviewed row is active gold."
            ),
        }
    )

for old, new, row_count in PAIRS:
    pair_id = f"vdiff__olimex_esp32_evb__rev_{old}__to__rev_{new}"
    if pair_id in manifest_pair_ids:
        print(f"[SKIP] manifest pair exists: {pair_id}")
        continue
    appended.append(
        {
            "type": "pair",
            "pair_id": pair_id,
            "task": "visualdiff",
            "pair_type": "same_model_revision",
            "from_doc_id": f"olimex_esp32_evb_rev_{old}",
            "to_doc_id": f"olimex_esp32_evb_rev_{new}",
            "page_mapping": {"type": "by_text_similarity", "pages_A": "0..0", "pages_B": "0..0"},
            "derived": {
                "review_jsonl": (
                    f"visualdiff/annotations/visualdiff_review_olimex_esp32_evb_rev_{old}_to_rev_{new}_2026-06-11.jsonl"
                )
            },
            "notes": (
                f"Single-sheet revision pair; {row_count} review-only textlayer diff rows staged "
                "2026-06-11; rows are not gold until human adjudication."
            ),
        }
    )

aquila_pair_id = "vdiff__aquila_dev__v1_2__to__v1_3"
if aquila_pair_id not in manifest_pair_ids:
    appended.append(
        {
            "type": "pair",
            "pair_id": aquila_pair_id,
            "task": "visualdiff",
            "pair_type": "same_model_revision",
            "from_doc_id": "aquila_dev_v1.2",
            "to_doc_id": "aquila_dev_v1.3",
            "page_mapping": {"type": "by_text_similarity", "pages_A": "0..33", "pages_B": "0..33"},
            "derived": {
                "align_dir": "derived/align/vdiff__aquila_dev__v1_2__to__v1_3",
                "review_jsonl": (
                    "visualdiff/annotations/visualdiff_review_aquila_dev_v1_2_to_v1_3_2026-06-11.jsonl"
                ),
            },
            "notes": (
                "Toradex Aquila dev-board v1.2->v1.3 revision pair; 34 pages mapped by text "
                "similarity (mixed v1.3 page orientations defeat pixel diffing); 60 review-only "
                "textlayer diff rows staged 2026-06-11; rows are not gold until human adjudication."
            ),
        }
    )

if appended:
    with manifest_path.open("a", encoding="utf-8") as f:
        for row in appended:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
print(f"[OK] appended {len(appended)} manifest rows")

# --- 3. import evidence JSON ---
evidence = {
    "candidate_id": "pcb_017",
    "doc_ids": [f"olimex_esp32_evb_rev_{doc['rev']}" for doc in DOCS],
    "domain": "pcb_schematic",
    "import_date": IMPORT_DATE,
    "source_url": REPO_URL,
    "pinned_commit": COMMIT,
    "license": {
        "name": "Apache-2.0",
        "attribution": "OLIMEX Ltd",
        "local_license_path": "visualdiff/docs/olimex_esp32_evb/LICENSE",
        "local_upstream_readme_path": "visualdiff/docs/olimex_esp32_evb/UPSTREAM_README.md",
        "local_attribution_path": "visualdiff/docs/olimex_esp32_evb/ATTRIBUTION.md",
        "release_requirements": [
            "Preserve OLIMEX Ltd attribution and the Apache-2.0 license text.",
            "Preserve exact source hashes and the pinned upstream commit.",
        ],
    },
    "files": [
        {
            "doc_id": f"olimex_esp32_evb_rev_{doc['rev']}",
            "local_path": f"visualdiff/docs/olimex_esp32_evb_rev_{doc['rev']}.pdf",
            "direct_source_url": f"{RAW_BASE}/{doc['upstream_path']}",
            "sha256": doc["sha256"],
            "pages": 1,
            "textlayer_spans": doc["text_spans"],
        }
        for doc in DOCS
    ],
    "outputs": {
        "review_jsonl": [
            f"visualdiff/annotations/visualdiff_review_olimex_esp32_evb_rev_{old}_to_rev_{new}_2026-06-11.jsonl"
            for old, new, _ in PAIRS
        ],
        "review_rows": {f"rev_{old}_to_rev_{new}": count for old, new, count in PAIRS},
        "reports": [
            f"derived/quality/visualdiff_textlayer_review_olimex_esp32_evb_rev_{old}_to_rev_{new}_report_2026-06-11.json"
            for old, new, _ in PAIRS
        ],
    },
    "release_posture": "review_only_until_human_adjudication",
    "task": "visualdiff",
}
evidence_path = ROOT / "derived" / "source_imports" / "olimex_esp32_evb_revisions_import_2026-06-11.json"
evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"[OK] wrote {evidence_path}")
