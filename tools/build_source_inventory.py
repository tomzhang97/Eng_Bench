#!/usr/bin/env python3
"""Build a lightweight source inventory for local Eng_Bench source files."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "path",
    "task",
    "doc_id",
    "domain",
    "source_url",
    "public_status",
    "textlayer_status",
    "render_status",
    "notes",
]

SOURCE_SUFFIXES = {
    ".brd",
    ".dwg",
    ".dxf",
    ".json",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".sch",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
    ".zip",
}
SOURCE_DOC_ROOTS = ("microtext/docs", "visualdiff/docs")


def slug(path: Path) -> str:
    name = path.stem.lower()
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")


def infer_task(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "visualdiff" in parts:
        return "visualdiff"
    if "microtext" in parts:
        return "microtext"
    return "reference"


def infer_domain(path: Path) -> str:
    lower = str(path).lower()
    if any(token in lower for token in ["pid", "p&id", "kimray"]):
        return "pid"
    if any(token in lower for token in ["bbb", "beaglebone", "viola", "aquila", "schematic", "pcb"]):
        return "pcb_schematic"
    if any(token in lower for token in ["gdandt", "gdt", "tolerance", "mechanical"]):
        return "mechanical_cad"
    if any(token in lower for token in ["fdot", "wsdot", "addendum", "itawamba", "civil"]):
        return "civil"
    if any(token in lower for token in ["architect", "room", "door"]):
        return "architectural"
    if any(token in lower for token in ["ul508", "control_panel", "eaton"]):
        return "electrical_control"
    return "unknown"


def load_manifest(root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = root / "manifest.jsonl"
    by_path: dict[str, dict[str, Any]] = {}
    if not manifest_path.exists():
        return by_path
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "doc" and row.get("path"):
                original_path = str(Path(row["path"]).as_posix())
                row = dict(row)
                row["_inventory_rel_path"] = original_path
                by_path[original_path.lower()] = row
    return by_path


def read_inventory(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def textlayer_present(root: Path, doc_id: str, manifest_row: dict[str, Any] | None) -> str:
    candidates = [
        root / "derived" / "textlayer" / f"{doc_id}.jsonl",
        root / "derived" / "textlayer" / doc_id,
    ]
    if manifest_row:
        derived = manifest_row.get("derived") or {}
        for key in ("textlayer_jsonl", "textlayer_dir"):
            if derived.get(key):
                candidates.append(root / derived[key])
    return "present" if any(path.exists() for path in candidates) else "missing"


def render_present(root: Path, doc_id: str, manifest_row: dict[str, Any] | None) -> str:
    candidates = [root / "derived" / "pages_300dpi" / doc_id, root / "images" / doc_id]
    if manifest_row:
        derived = manifest_row.get("derived") or {}
        if derived.get("pages_dir"):
            candidates.append(root / derived["pages_dir"])
    return "present" if any(path.exists() for path in candidates) else "missing"


def inventory_row(root: Path, rel_path: str, path: Path, manifest_row: dict[str, Any] | None) -> dict[str, str]:
    doc_id = str(manifest_row.get("doc_id")) if manifest_row else slug(path)
    notes = str(manifest_row.get("notes", "")) if manifest_row else "not in manifest"
    return {
        "path": rel_path,
        "task": str(manifest_row.get("task")) if manifest_row else infer_task(path),
        "doc_id": doc_id,
        "domain": str(manifest_row.get("domain") or infer_domain(path)) if manifest_row else infer_domain(path),
        "source_url": str(manifest_row.get("source_url", "")) if manifest_row else "",
        "public_status": str(manifest_row.get("public_status", "unknown")) if manifest_row else "unknown",
        "textlayer_status": textlayer_present(root, doc_id, manifest_row),
        "render_status": render_present(root, doc_id, manifest_row),
        "notes": notes,
    }


def is_ntrs_receipt_path(path_value: str) -> bool:
    path = Path(path_value)
    if path.suffix.lower() != ".json":
        return False
    name = path.name.lower()
    return name.startswith("ntrs_citation_") or name.endswith("_ntrs.json")


def is_source_file(rel_path: str, path: Path) -> bool:
    suffix = path.suffix.lower()
    normalized = rel_path.replace("\\", "/").lower()
    if is_ntrs_receipt_path(rel_path):
        return False
    if "/selected_dwg/" in normalized or normalized.endswith("/intake_summary.json"):
        return False
    if suffix in {".dwg", ".zip"}:
        return False
    return suffix in SOURCE_SUFFIXES and any(
        normalized.startswith(f"{prefix}/") for prefix in SOURCE_DOC_ROOTS
    )


def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in SOURCE_DOC_ROOTS:
        source_root = root / relative_root
        if not source_root.is_dir():
            continue
        files.extend(path for path in source_root.rglob("*") if path.is_file())
    return sorted(files)


def build_inventory(root: str | Path) -> list[dict[str, str]]:
    root = Path(root)
    manifest_by_path = load_manifest(root)
    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for manifest_row in sorted(
        manifest_by_path.values(),
        key=lambda row: str(row.get("_inventory_rel_path") or "").lower(),
    ):
        rel_path = str(manifest_row.get("_inventory_rel_path") or "")
        path = root / rel_path
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        normalized = rel_path.lower()
        seen_paths.add(normalized)
        rows.append(inventory_row(root, rel_path, path, manifest_row))

    for source_path in iter_source_files(root):
        rel_path = source_path.relative_to(root).as_posix()
        if not is_source_file(rel_path, source_path):
            continue
        if rel_path.lower() in seen_paths:
            continue
        manifest_row = manifest_by_path.get(rel_path.lower())
        rows.append(inventory_row(root, rel_path, source_path, manifest_row))
    return rows


def merge_inventory_rows(
    existing_rows: list[dict[str, str]],
    rebuilt_rows: list[dict[str, str]],
    refresh_existing: bool = False,
    refresh_doc_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    existing_rows = [
        row
        for row in existing_rows
        if not (
            is_ntrs_receipt_path(str(row.get("path") or row.get("source_path") or ""))
        )
    ]
    merged = [dict(row) for row in existing_rows]
    seen_paths = {
        str(row.get(field) or "").replace("\\", "/").lower()
        for row in existing_rows
        for field in ("path", "source_path")
        if str(row.get(field) or "").strip()
    }
    seen_doc_ids = {
        str(row.get("doc_id") or "").strip().lower()
        for row in existing_rows
        if str(row.get("doc_id") or "").strip()
    }
    operational_fields = {
        "path",
        "task",
        "doc_id",
        "domain",
        "source_url",
        "public_status",
        "textlayer_status",
        "render_status",
    }
    existing_by_path: dict[str, int] = {}
    existing_by_doc_id: dict[str, int] = {}
    for index, row in enumerate(merged):
        for field in ("path", "source_path"):
            value = str(row.get(field) or "").replace("\\", "/").lower()
            if value:
                existing_by_path[value] = index
        doc_id = str(row.get("doc_id") or "").strip().lower()
        if doc_id:
            existing_by_doc_id[doc_id] = index

    for row in rebuilt_rows:
        path_keys = {
            str(row.get(field) or "").replace("\\", "/").lower()
            for field in ("path", "source_path")
            if str(row.get(field) or "").strip()
        }
        doc_id = str(row.get("doc_id") or "").strip().lower()
        matching_indexes = {
            existing_by_path[path]
            for path in path_keys
            if path in existing_by_path
        }
        if doc_id and doc_id in existing_by_doc_id:
            matching_indexes.add(existing_by_doc_id[doc_id])
        if matching_indexes:
            refresh_targeted = not refresh_doc_ids or doc_id in refresh_doc_ids
            if refresh_existing and refresh_targeted:
                if len(matching_indexes) != 1:
                    raise ValueError(
                        f"rebuilt inventory row resolves to multiple existing rows: {row}"
                    )
                target = merged[next(iter(matching_indexes))]
                for field in operational_fields:
                    value = str(row.get(field) or "")
                    if value:
                        target[field] = value
            continue
        merged.append(row)
        seen_paths.update(path_keys)
        if doc_id:
            seen_doc_ids.add(doc_id)
    return merged


def inventory_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    """Preserve an existing schema while retaining every discovered field."""
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fieldnames.append(field)
                seen.add(field)
    for field in FIELDNAMES:
        if field not in seen:
            fieldnames.append(field)
            seen.add(field)
    return fieldnames


def write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=inventory_fieldnames(rows))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Eng_Bench source inventory")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="SOURCE_INVENTORY.csv")
    parser.add_argument(
        "--merge-existing",
        help="Preserve rows from this inventory CSV and append newly discovered source files.",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help=(
            "With --merge-existing, refresh manifest-derived identity, rights, render, and "
            "text-layer fields while preserving curated columns."
        ),
    )
    parser.add_argument(
        "--refresh-doc-id",
        action="append",
        default=[],
        help=(
            "Limit --refresh-existing to this doc_id. Repeat for multiple documents; "
            "omit to refresh every matching row."
        ),
    )
    args = parser.parse_args()

    rows = build_inventory(Path(args.root))
    if args.merge_existing:
        rows = merge_inventory_rows(
            read_inventory(Path(args.merge_existing)),
            rows,
            refresh_existing=args.refresh_existing,
            refresh_doc_ids={value.strip().lower() for value in args.refresh_doc_id if value.strip()},
        )
    write_inventory(Path(args.output), rows)
    print(f"[OK] Wrote {len(rows)} source rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
