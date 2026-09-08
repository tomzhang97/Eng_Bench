#!/usr/bin/env python3
"""Adopt browser-downloaded source assets into a verified intake receipt set."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from tools.download_ready_source_assets import RECEIPT_FIELDS
except ModuleNotFoundError:  # Direct invocation: python tools/adopt_preflight_browser_downloads.py
    from download_ready_source_assets import RECEIPT_FIELDS


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_int(value: Any) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return 0


def source_filename(row: dict[str, str]) -> str:
    url = str(row.get("resolved_direct_asset_url") or row.get("direct_asset_url") or "").strip()
    return Path(unquote(urlparse(url).path)).name


def source_filename_candidates(row: dict[str, str]) -> list[str]:
    url = str(row.get("resolved_direct_asset_url") or row.get("direct_asset_url") or "").strip()
    encoded = Path(urlparse(url).path).name
    decoded = source_filename(row)
    return list(dict.fromkeys(name for name in (decoded, encoded) if name))


def signature_error(path: Path, asset_kind: str) -> str:
    with path.open("rb") as handle:
        prefix = handle.read(16)
    if asset_kind == "pdf" and not prefix.startswith(b"%PDF-"):
        return "invalid_pdf_signature"
    return ""


def within_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def receipt_base(row: dict[str, str], rank: int) -> dict[str, Any]:
    direct_url = str(
        row.get("resolved_direct_asset_url") or row.get("direct_asset_url") or ""
    ).strip()
    return {
        "download_rank": rank,
        "intake_rank": row.get("intake_rank", ""),
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "doc_id": row.get("proposed_doc_id", ""),
        "local_path": row.get("proposed_local_path", ""),
        "download_url": direct_url,
        "source_url": row.get("source_url", ""),
        "page_url": row.get("page_url", ""),
        "license_short_name": row.get("license_short_name", ""),
        "license_url": row.get("license_url", ""),
        "artist": row.get("artist", ""),
        "commons_sha1": row.get("commons_sha1", ""),
        "rights_capture": row.get("rights_capture", ""),
        "review_gate": row.get("review_gate", ""),
        "public_status": row.get("public_status", ""),
        "license_note": row.get("license_note", ""),
        "rights_evidence_url": row.get("rights_evidence_url", ""),
        "rights_evidence_path": row.get("rights_evidence_path", ""),
        "same_model_id": row.get("same_model_id", ""),
        "doc_type": row.get("doc_type", ""),
        "version_json": row.get("version_json", ""),
        "page_selection": row.get("page_selection", ""),
        "full_page_count": row.get("full_page_count", ""),
        "notes": row.get("notes", ""),
        "manifest_content_type": row.get("content_type", ""),
        "manifest_content_length": row.get("content_length", ""),
        "status": "",
        "bytes": "",
        "sha256": "",
        "content_type": row.get("content_type", ""),
        "final_url": direct_url,
        "error": "",
        "next_step": "",
    }


def adopt_row(root: Path, downloads_dir: Path, row: dict[str, str], rank: int) -> dict[str, Any]:
    receipt = receipt_base(row, rank)
    filenames = source_filename_candidates(row)
    if not filenames:
        receipt.update(status="failed", error="missing_source_filename")
        return receipt
    sources = [downloads_dir / filename for filename in filenames if (downloads_dir / filename).is_file()]
    if not sources:
        receipt.update(status="failed", error=f"missing_browser_download:{filenames[0]}")
        return receipt
    if len(sources) > 1 and len({sha256_file(source) for source in sources}) > 1:
        receipt.update(status="failed", error="ambiguous_browser_download_filename_variants")
        return receipt
    source = sources[0]
    expected_bytes = as_int(row.get("content_length"))
    actual_bytes = source.stat().st_size
    if expected_bytes and actual_bytes != expected_bytes:
        receipt.update(
            status="failed",
            bytes=actual_bytes,
            error=f"browser_download_size_mismatch:{actual_bytes}!={expected_bytes}",
        )
        return receipt
    error = signature_error(source, str(row.get("asset_kind") or ""))
    if error:
        receipt.update(status="failed", bytes=actual_bytes, error=error)
        return receipt
    relative_target = str(row.get("proposed_local_path") or "").strip()
    target = root / relative_target
    if not relative_target or not within_root(root, target):
        receipt.update(status="failed", error="unsafe_or_missing_target_path")
        return receipt
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(source)
    if target.exists():
        if sha256_file(target) != digest:
            receipt.update(status="failed", error="existing_target_hash_mismatch")
            return receipt
        status = "downloaded"
    else:
        temporary = target.with_suffix(target.suffix + ".part")
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        status = "downloaded"
    receipt.update(
        status=status,
        bytes=actual_bytes,
        sha256=digest,
        next_step=(
            "Browser-acquired payload verified and adopted. Render, extract text, mine "
            "candidates, and require human review before Gold."
        ),
    )
    return receipt


def build_report(root: Path, preflight_csv: Path, downloads_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    preflight_path = preflight_csv if preflight_csv.is_absolute() else root / preflight_csv
    downloads_path = downloads_dir.resolve()
    rows = read_csv(preflight_path)
    receipts = [
        adopt_row(root, downloads_path, row, rank)
        for rank, row in enumerate(rows, start=1)
    ]
    statuses = Counter(str(row.get("status") or "") for row in receipts)
    return {
        "workflow": "browser_download_adoption",
        "preflight_csv": preflight_path.relative_to(root).as_posix(),
        "downloads_dir": downloads_path.as_posix(),
        "receipts": receipts,
        "totals": {
            "rows": len(receipts),
            "downloaded": statuses.get("downloaded", 0),
            "failed": statuses.get("failed", 0),
        },
        "safe_to_merge_gold": False,
        "valid": statuses.get("failed", 0) == 0 and bool(receipts),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECEIPT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    return "\n".join(
        [
            "# Browser Download Adoption",
            "",
            f"- Rows: `{totals['rows']}`",
            f"- Downloaded and verified: `{totals['downloaded']}`",
            f"- Failed: `{totals['failed']}`",
            f"- Valid: `{str(report['valid']).lower()}`",
            "- Gold modified: `false`",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preflight-csv", type=Path, required=True)
    parser.add_argument("--downloads-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = build_report(root, args.preflight_csv, args.downloads_dir)
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(output_csv, report["receipts"])
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({**report["totals"], "valid": report["valid"]}, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
