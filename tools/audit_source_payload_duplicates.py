#!/usr/bin/env python3
"""Audit source records for byte-identical payloads assigned to different doc IDs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
RECEIPT_GLOBS = (
    "derived/quality/source_asset_download_receipts_*.json",
    "derived/source_imports/source_download_receipts_*.json",
)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def nested_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_dicts(child)


def normalize_record(row: dict[str, Any], evidence_path: str) -> dict[str, str] | None:
    doc_id = str(row.get("doc_id") or "").strip()
    sha256 = str(row.get("sha256") or "").strip().lower()
    if not doc_id or not SHA256_RE.fullmatch(sha256):
        return None
    local_path = str(row.get("local_path") or row.get("path") or "").strip().replace("\\", "/")
    return {
        "doc_id": doc_id,
        "sha256": sha256,
        "local_path": local_path,
        "source_url": str(row.get("source_url") or row.get("page_url") or "").strip(),
        "evidence_path": evidence_path,
    }


def manifest_records(root: Path) -> list[dict[str, str]]:
    path = root / "manifest.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "doc":
                continue
            record = normalize_record(row, "manifest.jsonl")
            if record is not None:
                records.append(record)
    return records


def receipt_records(root: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_paths: set[Path] = set()
    for pattern in RECEIPT_GLOBS:
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            payload = read_json(path)
            if payload is None:
                continue
            rel_path = path.relative_to(root).as_posix()
            for row in nested_dicts(payload):
                record = normalize_record(row, rel_path)
                if record is not None:
                    records.append(record)
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(root: Path) -> dict[str, Any]:
    raw_records = manifest_records(root) + receipt_records(root)
    unique_records: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in raw_records:
        key = (row["doc_id"], row["sha256"], row["local_path"])
        existing = unique_records.get(key)
        if existing is None or row["evidence_path"] == "manifest.jsonl":
            unique_records[key] = row
    records = sorted(unique_records.values(), key=lambda row: (row["sha256"], row["doc_id"], row["local_path"]))

    by_sha: dict[str, list[dict[str, str]]] = defaultdict(list)
    local_mismatches: list[dict[str, str]] = []
    for row in records:
        by_sha[row["sha256"]].append(row)
        if not row["local_path"]:
            continue
        local = root / row["local_path"]
        if local.is_file():
            actual = file_sha256(local)
            if actual != row["sha256"]:
                local_mismatches.append({**row, "actual_sha256": actual})

    groups: list[dict[str, Any]] = []
    alias_doc_ids: set[str] = set()
    for sha256, group_records in sorted(by_sha.items()):
        doc_ids = sorted({row["doc_id"] for row in group_records})
        if len(doc_ids) < 2:
            continue
        canonical = doc_ids[0]
        aliases = doc_ids[1:]
        alias_doc_ids.update(aliases)
        groups.append(
            {
                "sha256": sha256,
                "canonical_doc_id": canonical,
                "alias_doc_ids": aliases,
                "doc_ids": doc_ids,
                "evidence_paths": sorted({row["evidence_path"] for row in group_records}),
                "local_paths": sorted({row["local_path"] for row in group_records if row["local_path"]}),
            }
        )

    return {
        "records": records,
        "duplicate_groups": groups,
        "alias_doc_ids": sorted(alias_doc_ids),
        "local_hash_mismatches": local_mismatches,
        "totals": {
            "records": len(records),
            "doc_ids": len({row["doc_id"] for row in records}),
            "payload_hashes": len(by_sha),
            "duplicate_groups": len(groups),
            "duplicate_alias_doc_ids": len(alias_doc_ids),
            "local_hash_mismatches": len(local_mismatches),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Payload Duplicate Audit",
        "",
        f"- source records: `{totals['records']}`",
        f"- distinct document IDs: `{totals['doc_ids']}`",
        f"- distinct payload hashes: `{totals['payload_hashes']}`",
        f"- duplicate payload groups: `{totals['duplicate_groups']}`",
        f"- duplicate alias document IDs: `{totals['duplicate_alias_doc_ids']}`",
        f"- local hash mismatches: `{totals['local_hash_mismatches']}`",
        "",
        "## Duplicate Groups",
        "",
        "| SHA-256 | Canonical document | Alias documents |",
        "| --- | --- | --- |",
    ]
    for group in report["duplicate_groups"]:
        lines.append(
            f"| `{group['sha256'][:16]}...` | `{group['canonical_doc_id']}` | "
            f"{', '.join(f'`{doc_id}`' for doc_id in group['alias_doc_ids'])} |"
        )
    if not report["duplicate_groups"]:
        lines.append("| none | none | none |")
    lines.extend(
        [
            "",
            "Aliases are excluded from unique source-inventory coverage but remain in the audit ledger.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sha256", "canonical_doc_id", "alias_doc_id"])
        writer.writeheader()
        for group in report["duplicate_groups"]:
            for alias in group["alias_doc_ids"]:
                writer.writerow(
                    {
                        "sha256": group["sha256"],
                        "canonical_doc_id": group["canonical_doc_id"],
                        "alias_doc_id": alias,
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    report = build_report(root)
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 1 if report["local_hash_mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
