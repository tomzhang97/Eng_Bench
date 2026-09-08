#!/usr/bin/env python3
"""Recover authoritative page-image paths from hash-verified manifest lineage."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PAGE_NAME = re.compile(r"^page_(\d+)\.(?:png|jpe?g|webp|tiff?)$", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(root: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def root_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def held_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    held = dict(row)
    held.update(
        {
            "review_status": "machine_held",
            "machine_qa_status": "machine_held",
            "machine_hold_reason": reason,
            "machine_qa_notes": (
                "Authoritative manifest page evidence could not be resolved; do not assign or merge."
            ),
            "safe_to_merge_gold": False,
        }
    )
    return held


def page_index(row: dict[str, Any]) -> int | None:
    value = row.get("page_index")
    if isinstance(value, bool):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def page_images(pages_dir: Path) -> dict[int, list[Path]]:
    indexed: dict[int, list[Path]] = {}
    if not pages_dir.is_dir():
        return indexed
    for path in pages_dir.iterdir():
        if not path.is_file():
            continue
        match = PAGE_NAME.fullmatch(path.name)
        if match:
            indexed.setdefault(int(match.group(1)), []).append(path)
    return indexed


def enrich_rows(
    root: Path,
    rows: list[dict[str, Any]],
    manifest_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    manifest_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in manifest_rows
        if row.get("type") == "doc" and str(row.get("doc_id") or "").strip()
    }
    source_status: dict[str, str] = {}
    page_maps: dict[str, dict[int, list[Path]]] = {}
    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    docs: Counter[str] = Counter()

    def prepare_doc(doc_id: str, manifest: dict[str, Any]) -> str:
        if doc_id in source_status:
            return source_status[doc_id]
        source_path = resolve_path(root, manifest.get("path"))
        expected_hash = str(manifest.get("sha256") or "").strip().lower()
        derived = manifest.get("derived") if isinstance(manifest.get("derived"), dict) else {}
        pages_dir = resolve_path(root, derived.get("pages_dir"))
        reason = ""
        if source_path is None or not source_path.is_file():
            reason = "missing_manifest_source_path"
        elif not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            reason = "missing_or_invalid_manifest_source_sha256"
        elif file_sha256(source_path).lower() != expected_hash:
            reason = "manifest_source_sha256_mismatch"
        elif pages_dir is None or not pages_dir.is_dir():
            reason = "missing_manifest_pages_dir"
        else:
            page_maps[doc_id] = page_images(pages_dir)
        source_status[doc_id] = reason
        return reason

    for original in rows:
        row = dict(original)
        doc_id = str(row.get("doc_id") or "").strip()
        docs[doc_id or "<missing_doc_id>"] += 1
        manifest = manifest_docs.get(doc_id)
        reason = ""
        if not doc_id:
            reason = "missing_doc_id"
        elif manifest is None:
            reason = "missing_manifest_doc"
        else:
            reason = prepare_doc(doc_id, manifest)

        index = page_index(row)
        if not reason and index is None:
            reason = "invalid_page_index"
        manifest_pages = manifest.get("pages") if manifest else None
        if not reason and isinstance(manifest_pages, int) and index is not None and index >= manifest_pages:
            reason = "page_index_out_of_manifest_range"

        candidates = page_maps.get(doc_id, {}).get(index, []) if index is not None else []
        if not reason and len(candidates) != 1:
            reason = "missing_or_ambiguous_manifest_page_image"

        if reason:
            counts[reason] += 1
            held.append(held_row(row, reason))
            continue

        image_path = candidates[0]
        existing = resolve_path(root, row.get("image_path"))
        row["image_path"] = root_relative(root, image_path)
        row["machine_page_image_enriched"] = existing is None or existing.resolve() != image_path.resolve()
        row["machine_page_image_source"] = "manifest.derived.pages_dir+page_index"
        row["safe_to_merge_gold"] = False
        status = "enriched" if row["machine_page_image_enriched"] else "preserved"
        counts[status] += 1
        passing.append(row)

    report = {
        "totals": {
            "input_rows": len(rows),
            "passing_rows": len(passing),
            "held_rows": len(held),
            "manifest_documents": len(manifest_docs),
        },
        "status_counts": dict(sorted(counts.items())),
        "document_counts": dict(sorted(docs.items())),
        "valid": not held,
        "interpretation": (
            "Recovered paths identify authoritative rendered pages only. Rows remain unreviewed, "
            "safe_to_merge_gold=false, and still require pixel and visual evidence audits."
        ),
    }
    return passing, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Manifest Page-Image Enrichment",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Passing rows: `{totals['passing_rows']}`",
        f"- Held rows: `{totals['held_rows']}`",
        "",
        "## Status Counts",
        "",
    ]
    lines.extend(f"- `{key}`: `{value}`" for key, value in report["status_counts"].items())
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    absolute = lambda path: path if path.is_absolute() else root / path
    passing, held, report = enrich_rows(
        root,
        read_jsonl(absolute(args.input)),
        read_jsonl(absolute(args.manifest)),
    )
    write_jsonl(absolute(args.output), passing)
    write_jsonl(absolute(args.held_output), held)
    write_report(absolute(args.report_json), report)
    write_markdown(absolute(args.report_md), report)
    print(json.dumps(report["totals"], indent=2))
    return 1 if args.strict and held else 0


if __name__ == "__main__":
    raise SystemExit(main())
