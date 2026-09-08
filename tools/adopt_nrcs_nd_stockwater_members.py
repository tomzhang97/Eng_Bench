#!/usr/bin/env python3
"""Adopt extracted NRCS ND stock-water PDFs as individual source receipts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


OUTPUT_FIELDS = [
    "download_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "doc_id",
    "local_path",
    "download_url",
    "source_url",
    "page_url",
    "rights_capture",
    "review_gate",
    "public_status",
    "license_note",
    "rights_evidence_url",
    "rights_evidence_path",
    "same_model_id",
    "doc_type",
    "version_json",
    "page_selection",
    "full_page_count",
    "notes",
    "status",
    "bytes",
    "sha256",
    "content_type",
    "final_url",
    "error",
    "next_step",
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def build_receipts(
    root: Path,
    *,
    parent_report: dict[str, Any],
    extract_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    parents = parent_report.get("receipts", [])
    issues: list[str] = []
    if len(parents) != 1:
        return [], [f"expected_one_parent_receipt:{len(parents)}"]
    parent = parents[0]
    if parent.get("status") not in {"downloaded", "skipped_existing"}:
        return [], [f"parent_not_downloaded:{parent.get('status')}"]
    try:
        base_version = json.loads(str(parent.get("version_json") or "{}"))
    except json.JSONDecodeError as exc:
        return [], [f"invalid_parent_version_json:{exc}"]

    receipts: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for extract in extract_report.get("extract_receipts", []):
        if extract.get("status") not in {"extracted", "skipped_existing"}:
            continue
        member = str(extract.get("member_path") or "").strip()
        if not re.fullmatch(r"(?i)ND-\d{3}\b.*\.pdf", member):
            issues.append(f"unexpected_member:{member}")
            continue
        local_path = str(extract.get("output_path") or "").strip()
        source_path = root / local_path
        if not source_path.is_file():
            issues.append(f"missing_member_payload:{member}")
            continue
        computed_sha = file_sha256(source_path)
        receipt_sha = str(extract.get("sha256") or "").strip().lower()
        if computed_sha != receipt_sha:
            issues.append(f"member_sha256_mismatch:{member}")
            continue
        doc_id = f"nrcs_nd_stockwater_{slugify(Path(member).stem)}"
        if doc_id in seen_doc_ids:
            issues.append(f"duplicate_doc_id:{doc_id}")
            continue
        seen_doc_ids.add(doc_id)
        version = {
            **base_version,
            "archive_member": member,
            "member_sha256": computed_sha,
        }
        receipts.append(
            {
                "download_rank": len(receipts) + 1,
                "candidate_id": parent.get("candidate_id", "civil_027"),
                "domain": parent.get("domain", "civil"),
                "asset_kind": "pdf",
                "import_action": "render_textlayer_mine_review",
                "doc_id": doc_id,
                "local_path": local_path,
                "download_url": parent.get("download_url", ""),
                "source_url": parent.get("source_url", ""),
                "page_url": parent.get("page_url", ""),
                "rights_capture": parent.get("rights_capture", ""),
                "review_gate": parent.get("review_gate", ""),
                "public_status": parent.get("public_status", ""),
                "license_note": parent.get("license_note", ""),
                "rights_evidence_url": parent.get("rights_evidence_url", ""),
                "rights_evidence_path": parent.get("rights_evidence_path", ""),
                "same_model_id": parent.get("same_model_id", ""),
                "doc_type": "federal_civil_stockwater_engineering_drawing_pdf",
                "version_json": json.dumps(version, separators=(",", ":")),
                "page_selection": "all",
                "full_page_count": "",
                "notes": (
                    f"Official USDA NRCS North Dakota archive member {member}. "
                    "Receipt-backed staged source; no Gold row is modified."
                ),
                "status": "downloaded",
                "bytes": source_path.stat().st_size,
                "sha256": computed_sha,
                "content_type": "application/pdf",
                "final_url": parent.get("final_url") or parent.get("download_url", ""),
                "error": "",
                "next_step": "Render at 300 DPI, extract text layer, and mine staged candidates.",
            }
        )
    return receipts, issues


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--parent-download-json", type=Path, required=True)
    parser.add_argument("--extract-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--expect-docs", type=int, default=21)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    parent_path = args.parent_download_json if args.parent_download_json.is_absolute() else root / args.parent_download_json
    extract_path = args.extract_json if args.extract_json.is_absolute() else root / args.extract_json
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    receipts, issues = build_receipts(
        root,
        parent_report=json.loads(parent_path.read_text(encoding="utf-8")),
        extract_report=json.loads(extract_path.read_text(encoding="utf-8")),
    )
    if len(receipts) != args.expect_docs:
        issues.append(f"unexpected_receipt_count:{len(receipts)}!={args.expect_docs}")
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "parent_download_json": parent_path.relative_to(root).as_posix(),
        "extract_json": extract_path.relative_to(root).as_posix(),
        "receipts": receipts,
        "documents": len(receipts),
        "issues": issues,
        "valid": not issues,
        "safe_to_merge_gold": False,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(output_csv, receipts)
    print(json.dumps({key: value for key, value in report.items() if key != "receipts"}, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
