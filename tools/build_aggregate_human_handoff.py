#!/usr/bin/env python3
"""Bundle every verified pending human packet into one aggregate handoff ZIP.

Reads the dated human packet index, refuses unverified or missing packet
ZIPs, copies each packet ZIP plus the independent agreement-audit ZIP into
one folder with a README and a tracking manifest, zips the folder, and
verifies the archive contents. Read-only with respect to annotations and
active gold.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--agreement-zip", default="derived/human_adjudication/Eng_Bench_agreement_audit_2026-06-05.zip")
    parser.add_argument("--output-dir")
    parser.add_argument("--zip-output")
    args = parser.parse_args()

    root = Path(args.root)
    index_path = root / "derived" / "quality" / f"human_packet_index_{args.date_label}.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    packets = index.get("packets") or []
    if not packets:
        raise SystemExit(f"no packets in {index_path}")

    bundle_dir = root / (args.output_dir or f"derived/human_adjudication/{args.date_label}_aggregate_handoff")
    zip_output = root / (args.zip_output or f"derived/human_adjudication/Eng_Bench_human_work_aggregate_{args.date_label}.zip")
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    (bundle_dir / "01_review_packets").mkdir(parents=True)
    (bundle_dir / "02_agreement_audit").mkdir(parents=True)

    manifest_rows = []
    total_rows = 0
    for packet in packets:
        zip_rel = str(packet.get("zip_path") or "")
        source = root / zip_rel
        if not packet.get("verified") or not source.exists():
            raise SystemExit(f"refusing to bundle unverified or missing packet: {zip_rel}")
        destination = bundle_dir / "01_review_packets" / source.name
        shutil.copy2(source, destination)
        rows = int(packet.get("review_rows") or 0)
        total_rows += rows
        manifest_rows.append(
            {
                "packet_zip": source.name,
                "review_rows": rows,
                "sha256": sha256_of(source),
                "return_command": str(packet.get("return_command") or ""),
                "status_when_done": "",
                "reviewer_initials": "",
            }
        )

    agreement_source = root / args.agreement_zip
    agreement_entry = None
    if agreement_source.exists():
        shutil.copy2(agreement_source, bundle_dir / "02_agreement_audit" / agreement_source.name)
        agreement_entry = {"zip": agreement_source.name, "sha256": sha256_of(agreement_source)}

    for suffix in ("md", "csv"):
        control = root / "derived" / "human_adjudication" / f"HUMAN_PACKET_INDEX_{args.date_label}.{suffix}"
        if control.exists():
            shutil.copy2(control, bundle_dir / control.name)

    manifest_path = bundle_dir / "AGGREGATE_MANIFEST.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    readme = [
        "# Eng_Bench Aggregate Human Work Bundle",
        "",
        f"- Date label: `{args.date_label}`",
        f"- Review packets: `{len(manifest_rows)}` ZIPs, `{total_rows}` total review rows",
        "- Independent agreement audit: "
        + (f"`02_agreement_audit/{agreement_entry['zip']}` (163 rows, TWO reviewers)" if agreement_entry else "not included"),
        "",
        "## How to work",
        "",
        "1. Start with `02_agreement_audit/` if two reviewers are available: give",
        "   Reviewer A and Reviewer B separate extracted copies. They must work",
        "   independently, fill only their own checklist CSV, and not discuss",
        "   rows until both CSVs are returned.",
        "2. Then work through `01_review_packets/` in any order. Every packet ZIP",
        "   is self-contained: extract it and follow its `HUMAN_REVIEW_STEPS.md`",
        "   (or per-pack `what_to_validate` checklist columns). Crops, panels,",
        "   and full-page evidence images are inside each ZIP - no other files",
        "   are needed.",
        "3. Microtext rows: use `accepted` only when the proposed text exactly",
        "   matches the visible label; `edited` plus `corrected_text` when it",
        "   needs fixing; `rejected` for non-labels; `needs_full_page` when the",
        "   crop is ambiguous. Visualdiff rows: use `edit` and write a concise",
        "   `human_description` for usable TODO rows; `valid` is only allowed",
        "   when the existing description is already complete.",
        "4. Track progress in `AGGREGATE_MANIFEST.csv` (status_when_done,",
        "   reviewer_initials). Keep filenames and folder layout unchanged.",
        "",
        "## How to return",
        "",
        "Send back the extracted packet folders with the filled checklist CSVs",
        "(or re-zip them) plus both agreement-audit reviewer CSVs. The",
        "maintainer-side return command for each packet is recorded in",
        "`AGGREGATE_MANIFEST.csv`; returns are processed with `--strict` and",
        "nothing is merged into gold without passing the merge audits.",
        "",
        "`HUMAN_PACKET_INDEX_*.md` in this folder is the authoritative control",
        "sheet listing every packet, row count, and verification status.",
    ]
    (bundle_dir / "README_FIRST.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    if zip_output.exists():
        zip_output.unlink()
    with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_dir.parent))

    with zipfile.ZipFile(zip_output) as archive:
        names = archive.namelist()
        bad = [info.filename for info in archive.infolist() if info.file_size == 0]
    expected_inner = len(manifest_rows) + (1 if agreement_entry else 0)
    inner_zips = [name for name in names if name.endswith(".zip")]
    verification = {
        "date_label": args.date_label,
        "zip_output": str(zip_output.relative_to(root)).replace("\\", "/"),
        "zip_entries": len(names),
        "inner_packet_zips": len(inner_zips),
        "expected_inner_zips": expected_inner,
        "zero_byte_entries": bad,
        "review_rows": total_rows,
        "valid": len(inner_zips) == expected_inner and not bad,
    }
    verification_path = root / "derived" / "quality" / f"aggregate_handoff_verification_{args.date_label}.json"
    verification_path.write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(verification, indent=2, sort_keys=True))
    return 0 if verification["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
