#!/usr/bin/env python3
"""Download ready source-intake payloads and write SHA-256 receipts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
USER_AGENT = "curl/8.14.1 Eng-Bench-source-intake/1.0"
DownloadFn = Callable[..., dict[str, str | int]]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RECEIPT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def as_int(value: str | int | None, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return default


def truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def preflight_blocks_intake(row: dict[str, str]) -> str:
    ready_value = str(row.get("ready_for_intake") or "").strip()
    status_value = str(row.get("preflight_status") or "").strip()
    if ready_value and not truthy(ready_value):
        return status_value or f"ready_for_intake={ready_value}"
    if status_value and status_value != "ready_for_intake":
        return status_value
    return ""


def resolve_path(root: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_url_to_path(
    url: str,
    output_path: Path,
    *,
    timeout: float = 60,
    max_bytes: int | None = None,
) -> dict[str, str | int]:
    """Stream a URL to a temporary file, then atomically place it at output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(f"{output_path.name}.part")
    if part_path.exists():
        part_path.unlink()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    total = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            expected_bytes = as_int(response.headers.get("Content-Length"), 0)
            with part_path.open("wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if max_bytes is not None and max_bytes > 0 and total > max_bytes:
                        raise ValueError(f"download_exceeded_max_bytes:{total}>{max_bytes}")
                    digest.update(chunk)
                    f.write(chunk)
        if expected_bytes > 0 and total != expected_bytes:
            raise ValueError(f"download_content_length_mismatch:{total}!={expected_bytes}")
        os.replace(part_path, output_path)
        return {
            "bytes": total,
            "sha256": digest.hexdigest(),
            "content_type": content_type,
            "final_url": final_url,
        }
    finally:
        if part_path.exists():
            part_path.unlink()


def base_receipt(row: dict[str, str], rank: int) -> dict[str, Any]:
    download_url = str(
        row.get("download_url")
        or row.get("direct_asset_url")
        or row.get("resolved_direct_asset_url")
        or ""
    ).strip()
    local_path = str(row.get("local_path") or row.get("proposed_local_path") or "").strip()
    doc_id = str(row.get("doc_id") or row.get("proposed_doc_id") or "").strip()
    return {
        "download_rank": rank,
        "intake_rank": row.get("intake_rank", ""),
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "doc_id": doc_id,
        "local_path": local_path,
        "download_url": download_url,
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
        "content_type": "",
        "final_url": "",
        "error": "",
        "next_step": "",
    }


def process_row(
    root: Path,
    row: dict[str, str],
    rank: int,
    *,
    dry_run: bool,
    force: bool,
    max_bytes: int | None,
    timeout: float,
    downloader: DownloadFn,
) -> dict[str, Any]:
    receipt = base_receipt(row, rank)
    download_url = str(receipt.get("download_url") or "").strip()
    local_path = str(receipt.get("local_path") or "").strip()
    manifest_size = as_int(row.get("content_length"), 0)
    blocked_reason = preflight_blocks_intake(row)

    if blocked_reason:
        receipt.update(
            status="skipped_not_ready_for_intake",
            error=blocked_reason,
            next_step="Keep this row in the repair/intake queue until preflight marks it ready.",
        )
        return receipt
    if not download_url:
        receipt.update(status="failed_missing_download_url", error="missing download_url")
        return receipt
    if not local_path:
        receipt.update(status="failed_missing_local_path", error="missing local_path")
        return receipt
    if max_bytes is not None and max_bytes > 0 and manifest_size > max_bytes:
        receipt.update(
            status="skipped_max_bytes",
            error=f"manifest_content_length_exceeds_max_bytes:{manifest_size}>{max_bytes}",
            next_step="Re-run with a higher max-bytes limit only after deciding this payload is worth the bandwidth/render cost.",
        )
        return receipt
    if dry_run:
        receipt.update(
            status="dry_run",
            next_step="Dry run only. Re-run without --dry-run to download and hash this exact payload.",
        )
        return receipt

    output_path = resolve_path(root, local_path)
    if output_path.exists() and not force:
        existing_bytes = output_path.stat().st_size
        if manifest_size > 0 and existing_bytes != manifest_size:
            receipt.update(
                status="failed_existing_size_mismatch",
                bytes=existing_bytes,
                sha256=sha256_file(output_path),
                final_url=download_url,
                error=f"existing_content_length_mismatch:{existing_bytes}!={manifest_size}",
                next_step="Re-download with --force or replace the source URL before rendering.",
            )
            return receipt
        receipt.update(
            status="skipped_existing",
            bytes=existing_bytes,
            sha256=sha256_file(output_path),
            final_url=download_url,
            next_step="Existing payload retained. Render/extract only after receipt review.",
        )
        return receipt

    try:
        result = downloader(download_url, output_path, timeout=timeout, max_bytes=max_bytes)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        return receipt

    if output_path.exists():
        actual_bytes = as_int(result.get("bytes"), output_path.stat().st_size)
        actual_sha256 = str(result.get("sha256") or sha256_file(output_path))
    else:
        actual_bytes = as_int(result.get("bytes"), 0)
        actual_sha256 = str(result.get("sha256") or "")
    if manifest_size > 0 and actual_bytes != manifest_size:
        if output_path.exists():
            output_path.unlink()
        receipt.update(
            status="failed_content_length_mismatch",
            bytes=actual_bytes,
            sha256=actual_sha256,
            content_type=result.get("content_type", ""),
            final_url=result.get("final_url", download_url),
            error=f"download_content_length_mismatch:{actual_bytes}!={manifest_size}",
            next_step="Do not render. Re-resolve the exact payload URL and download again.",
        )
        return receipt
    receipt.update(
        status="downloaded",
        bytes=actual_bytes,
        sha256=actual_sha256,
        content_type=result.get("content_type", ""),
        final_url=result.get("final_url", download_url),
        next_step="Render/convert, extract text layer where possible, mine candidate annotations, then export review packets before gold.",
    )
    return receipt


def build_report(
    root: str | Path,
    *,
    manifest_csv: str | Path,
    date_label: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    max_bytes: int | None = None,
    timeout: float = 60,
    downloader: DownloadFn = download_url_to_path,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(manifest_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    selected_rows = rows[:limit] if limit is not None and limit > 0 else rows
    receipts = [
        process_row(
            root,
            row,
            idx + 1,
            dry_run=dry_run,
            force=force,
            max_bytes=max_bytes,
            timeout=timeout,
            downloader=downloader,
        )
        for idx, row in enumerate(selected_rows)
    ]
    status_counts = Counter(str(row["status"]) for row in receipts)
    totals: dict[str, Any] = {
        "date_label": date_label or date.today().isoformat(),
        "manifest_rows": len(rows),
        "processed_rows": len(receipts),
        "limit": limit or "",
        "max_bytes": max_bytes or "",
        "dry_run_mode": dry_run,
        "force": force,
    }
    totals.update(dict(sorted(status_counts.items())))
    return {
        "date_label": totals["date_label"],
        "manifest_csv": input_path.as_posix(),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "by_domain": dict(sorted(Counter(row["domain"] for row in receipts).items())),
        "by_action": dict(sorted(Counter(row["import_action"] for row in receipts).items())),
        "receipts": receipts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Asset Download Receipts",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Manifest CSV: `{report['manifest_csv']}`",
        f"- Manifest rows: `{totals['manifest_rows']}`",
        f"- Processed rows: `{totals['processed_rows']}`",
        f"- Limit: `{totals['limit']}`",
        f"- Max bytes: `{totals['max_bytes']}`",
        f"- Dry run mode: `{totals['dry_run_mode']}`",
        "- Receipt only: do not merge unreviewed rows into gold.",
        "",
        "## Status Counts",
        "",
    ]
    if report["status_counts"]:
        for status, count in report["status_counts"].items():
            lines.append(f"- {status}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Receipts",
            "",
            "| Rank | Candidate | Domain | Action | Status | Bytes | SHA-256 | Local Path |",
            "| ---: | --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in report["receipts"]:
        sha = row["sha256"] or ""
        short_sha = f"`{sha[:12]}...`" if sha else ""
        lines.append(
            f"| {row['download_rank']} | `{row['candidate_id']}` | {row['domain']} | "
            f"{row['import_action']} | {row['status']} | {row['bytes']} | {short_sha} | {row['local_path']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Receipts prove exact payload capture only; they are not annotation rows and not gold promotion.",
            "- Keep failed or skipped rows in the repair/intake queue until a later guarded pass handles them.",
            "- After receipt review, render or convert payloads, extract text layers where applicable, mine candidates, and export human review packets.",
            "- Do not merge unreviewed candidates into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download ready source payloads and write SHA-256 receipts.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--manifest-csv", default="derived/quality/ready_source_intake_manifest_2026-06-16.csv")
    parser.add_argument("--output-json", default="derived/quality/source_asset_download_receipts_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_asset_download_receipts_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_asset_download_receipts_2026-06-16.csv")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N manifest rows")
    parser.add_argument("--max-bytes", type=int, default=None, help="Skip rows whose declared or streamed bytes exceed this limit")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--dry-run", action="store_true", help="Write receipts without downloading files")
    parser.add_argument("--force", action="store_true", help="Re-download existing payload files")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        manifest_csv=args.manifest_csv,
        date_label=args.date_label,
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
        max_bytes=args.max_bytes,
        timeout=args.timeout,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["receipts"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
