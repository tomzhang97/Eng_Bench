#!/usr/bin/env python3
"""Sync packet-level instruction docs from unpacked folders into handoff ZIPs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_DOC_NAMES = ("README.md", "HUMAN_REVIEW_STEPS.md")


def resolve(root: Path, path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else root / path


def is_packet_root_doc(entry_name: str, doc_names: set[str]) -> bool:
    if entry_name.endswith("/"):
        return False
    parts = entry_name.replace("\\", "/").strip("/").split("/")
    return len(parts) <= 2 and parts[-1] in doc_names


def sync_zip_docs(
    *,
    packet_dir: Path,
    zip_path: Path,
    doc_names: tuple[str, ...] = DEFAULT_DOC_NAMES,
    dry_run: bool = False,
) -> dict[str, Any]:
    doc_name_set = set(doc_names)
    source_docs: dict[str, bytes] = {}
    issues: list[str] = []
    for name in doc_names:
        source_path = packet_dir / name
        if source_path.exists():
            source_docs[name] = source_path.read_bytes()

    if not source_docs:
        return {
            "zip_path": zip_path.as_posix(),
            "packet_dir": packet_dir.as_posix(),
            "updated": False,
            "docs_seen": 0,
            "docs_updated": 0,
            "issues": [f"no source docs found: {', '.join(doc_names)}"],
        }
    if not zip_path.exists():
        return {
            "zip_path": zip_path.as_posix(),
            "packet_dir": packet_dir.as_posix(),
            "updated": False,
            "docs_seen": 0,
            "docs_updated": 0,
            "issues": ["missing zip"],
        }

    try:
        with zipfile.ZipFile(zip_path, "r") as source_zip:
            infos = source_zip.infolist()
            contents = {info.filename: source_zip.read(info.filename) for info in infos}
    except zipfile.BadZipFile as exc:
        return {
            "zip_path": zip_path.as_posix(),
            "packet_dir": packet_dir.as_posix(),
            "updated": False,
            "docs_seen": 0,
            "docs_updated": 0,
            "issues": [f"bad zip: {exc}"],
        }

    replacements: dict[str, bytes] = {}
    seen_doc_names: set[str] = set()
    for info in infos:
        if not is_packet_root_doc(info.filename, doc_name_set):
            continue
        doc_name = Path(info.filename).name
        seen_doc_names.add(doc_name)
        if doc_name not in source_docs:
            issues.append(f"zip has {doc_name} but source folder is missing it")
            continue
        if contents[info.filename] != source_docs[doc_name]:
            replacements[info.filename] = source_docs[doc_name]

    for doc_name in source_docs:
        if doc_name not in seen_doc_names:
            issues.append(f"zip missing packet-level {doc_name}")

    if replacements and not dry_run:
        fd, temp_name = tempfile.mkstemp(
            prefix=f"{zip_path.stem}.", suffix=".tmp.zip", dir=str(zip_path.parent)
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with zipfile.ZipFile(
                temp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as output_zip:
                for info in infos:
                    data = replacements.get(info.filename, contents[info.filename])
                    output_zip.writestr(info, data)
            os.replace(temp_path, zip_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    return {
        "zip_path": zip_path.as_posix(),
        "packet_dir": packet_dir.as_posix(),
        "updated": bool(replacements),
        "docs_seen": len(seen_doc_names),
        "docs_updated": len(replacements),
        "updated_entries": sorted(replacements),
        "issues": issues,
    }


def sync_from_index(
    *,
    root: Path,
    index_path: Path,
    doc_names: tuple[str, ...] = DEFAULT_DOC_NAMES,
    ready_only: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    packets = index.get("packets", [])
    packet_reports: list[dict[str, Any]] = []
    skipped_unready = 0
    for packet in packets:
        if ready_only and packet.get("ready_to_send") is False:
            skipped_unready += 1
            continue
        folder_rel = str(packet.get("folder_path") or packet.get("packet_root") or "").strip()
        zip_rel = str(packet.get("zip_path") or "").strip()
        if not folder_rel or not zip_rel:
            packet_reports.append(
                {
                    "packet_id": packet.get("packet_id", ""),
                    "updated": False,
                    "docs_seen": 0,
                    "docs_updated": 0,
                    "issues": ["missing folder_path or zip_path"],
                }
            )
            continue
        report = sync_zip_docs(
            packet_dir=resolve(root, folder_rel),
            zip_path=resolve(root, zip_rel),
            doc_names=doc_names,
            dry_run=dry_run,
        )
        report["packet_id"] = packet.get("packet_id", "")
        packet_reports.append(report)

    issues = sum(len(report.get("issues", [])) for report in packet_reports)
    return {
        "index_path": index_path.as_posix(),
        "dry_run": dry_run,
        "doc_names": list(doc_names),
        "totals": {
            "packets_considered": len(packet_reports),
            "skipped_unready_packets": skipped_unready,
            "updated_zips": sum(1 for report in packet_reports if report.get("updated")),
            "docs_updated": sum(int(report.get("docs_updated", 0)) for report in packet_reports),
            "issues": issues,
        },
        "packets": packet_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--index-json", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-unready", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    report = sync_from_index(
        root=root,
        index_path=resolve(root, args.index_json),
        ready_only=not args.include_unready,
        dry_run=args.dry_run,
    )
    if args.output_json:
        output_path = resolve(root, args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[OK] Wrote {output_path}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 1 if report["totals"]["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
