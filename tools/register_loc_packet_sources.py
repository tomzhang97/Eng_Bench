#!/usr/bin/env python3
"""Register audited LOC packet sources without promoting review rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packet_doc_ids(packet_dirs: Iterable[Path]) -> set[str]:
    doc_ids: set[str] = set()
    for packet_dir in packet_dirs:
        manifests = sorted(packet_dir.rglob("manifest.jsonl"))
        if not manifests:
            raise FileNotFoundError(f"no manifest.jsonl found under packet: {packet_dir}")
        for manifest in manifests:
            for row in read_jsonl(manifest):
                doc_id = str(row.get("doc_id") or "").strip()
                if doc_id:
                    doc_ids.add(doc_id)
    return doc_ids


def audit_rows(paths: Iterable[Path]) -> dict[str, dict[str, str]]:
    by_doc: dict[str, dict[str, str]] = {}
    for path in paths:
        for row in read_csv(path):
            doc_id = str(row.get("proposed_doc_id") or "").strip()
            if not doc_id:
                continue
            if doc_id in by_doc and by_doc[doc_id] != row:
                raise ValueError(f"conflicting LOC audit rows for {doc_id}")
            by_doc[doc_id] = row
    return by_doc


def find_source(source_dirs: Iterable[Path], doc_id: str) -> Path | None:
    matches: list[Path] = []
    for source_dir in source_dirs:
        for suffix in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
            candidate = source_dir / f"{doc_id}{suffix}"
            if candidate.exists():
                matches.append(candidate)
    if not matches:
        return None
    preferred = sorted(matches, key=lambda path: (path.suffix.lower() not in {".tif", ".tiff"}, str(path)))
    return preferred[0]


def find_page(root: Path, doc_id: str) -> Path | None:
    page_dir = root / "derived" / "pages_300dpi" / doc_id
    for name in ("page_000.png", "page_0000.png"):
        path = page_dir / name
        if path.exists():
            return path
    return None


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def inventory_fields(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        fields = next(reader, [])
    if not fields:
        raise ValueError(f"inventory has no header: {path}")
    return fields


def write_inventory(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_attribution(records: list[dict[str, Any]], existing_text: str = "") -> str:
    if existing_text.strip():
        lines = [existing_text.rstrip(), ""]
    else:
        lines = [
            "# LOC HABS/HAER/HALS Source Attribution",
            "",
            "These source sheets were selected from the Library of Congress HABS/HAER/HALS collection.",
            "Each item-level metadata record states that there are no known restrictions on images made by the U.S. Government; copied third-party material may differ.",
            "Only rows whose item audit was marked unrestricted and ready for intake are listed here.",
            "",
        ]
    for record in records:
        heading = f"## {record['doc_id']}"
        if heading in existing_text:
            continue
        audit = record["audit"]
        lines.extend(
            [
                heading,
                "",
                f"- LOC item: `{audit.get('loc_item_id', '')}`",
                f"- Title: {audit.get('title', '')}",
                f"- Call number: {audit.get('call_number', '')}",
                f"- Item page: {audit.get('item_url', '')}",
                f"- Master asset: {audit.get('resolved_master_url', '')}",
                f"- Local source: `{record['source_path']}`",
                f"- SHA-256: `{record['sha256']}`",
                f"- Rights advisory: {audit.get('rights_information', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def build_registration(
    *,
    root: Path,
    packet_dirs: list[Path],
    doc_ids: Iterable[str] = (),
    audit_csvs: list[Path],
    source_dirs: list[Path],
    split: str,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    packets = [path if path.is_absolute() else root / path for path in packet_dirs]
    audits = [path if path.is_absolute() else root / path for path in audit_csvs]
    sources = [path if path.is_absolute() else root / path for path in source_dirs]
    target_docs = {str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()}
    if packets:
        target_docs.update(packet_doc_ids(packets))
    if not target_docs:
        raise ValueError("provide at least one --packet-dir or --doc-id")
    by_doc = audit_rows(audits)
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    existing_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }

    records: list[dict[str, Any]] = []
    issues: list[str] = []
    for doc_id in sorted(target_docs):
        audit = by_doc.get(doc_id)
        if audit is None:
            issues.append(f"missing_item_audit:{doc_id}")
            continue
        if str(audit.get("audit_status") or "") != "ready_for_intake":
            issues.append(f"audit_not_ready:{doc_id}")
            continue
        if str(audit.get("unrestricted") or "").strip().lower() not in {"true", "1", "yes"}:
            issues.append(f"audit_not_unrestricted:{doc_id}")
            continue
        source = find_source(sources, doc_id)
        if source is None:
            issues.append(f"missing_local_source:{doc_id}")
            continue
        page = find_page(root, doc_id)
        if page is None:
            issues.append(f"missing_rendered_page:{doc_id}")
            continue
        source_path = relative(root, source)
        records.append(
            {
                "doc_id": doc_id,
                "audit": audit,
                "source_path": source_path,
                "page_path": relative(root, page),
                "sha256": sha256_file(source),
                "already_registered": doc_id in existing_docs,
                "manifest_row": {
                    "type": "doc",
                    "doc_id": doc_id,
                    "task": "microtext",
                    "source_candidate_id": str(audit.get("candidate_id") or ""),
                    "same_model_id": "loc_" + str(audit.get("loc_item_id") or doc_id).replace(".", "_"),
                    "domain": str(audit.get("domain") or "civil_architectural"),
                    "doc_type": "loc_habs_haer_master_tiff",
                    "version": {
                        "loc_item_id": str(audit.get("loc_item_id") or ""),
                        "metadata_modified": str(audit.get("metadata_modified") or ""),
                        "registered": date_label,
                    },
                    "path": source_path,
                    "sha256": sha256_file(source),
                    "pages": 1,
                    "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                    "derived": {"pages_dir": f"derived/pages_300dpi/{doc_id}"},
                    "source_url": str(audit.get("item_url") or ""),
                    "direct_source_url": str(audit.get("resolved_master_url") or ""),
                    "public_status": "public_domain_us_federal_candidate",
                    "license_note": (
                        "LOC item metadata reports no known restrictions on images made by the U.S. Government; "
                        "item-level HABS/HAER/HALS audit is recorded in the project quality report."
                    ),
                    "attribution_path": "microtext/docs/LOC_HABS_HAER_ATTRIBUTION.md",
                    "notes": "Review-packet source registration only; no annotation row promoted.",
                },
            }
        )

    return {
        "date_label": date_label,
        "split": split,
        "target_docs": len(target_docs),
        "ready_records": len(records),
        "new_manifest_docs": sum(not record["already_registered"] for record in records),
        "already_registered_docs": sum(record["already_registered"] for record in records),
        "issues": issues,
        "records": records,
    }


def apply_registration(root: Path, report: dict[str, Any]) -> None:
    if report["issues"]:
        raise ValueError("registration has unresolved issues: " + ", ".join(report["issues"]))
    root = root.resolve()
    manifest_path = root / "manifest.jsonl"
    new_rows = [record["manifest_row"] for record in report["records"] if not record["already_registered"]]
    if new_rows:
        with manifest_path.open("a", encoding="utf-8") as handle:
            for row in new_rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    inventory_path = root / "SOURCE_INVENTORY.csv"
    fields = inventory_fields(inventory_path)
    inventory = read_csv(inventory_path)
    by_doc = {row.get("doc_id", ""): row for row in inventory}
    for record in report["records"]:
        audit = record["audit"]
        row = by_doc.get(record["doc_id"])
        if row is None:
            row = {field: "" for field in fields}
            row["doc_id"] = record["doc_id"]
            inventory.append(row)
            by_doc[record["doc_id"]] = row
        row.update(
            {
                "domain": str(audit.get("domain") or "civil_architectural"),
                "task": "microtext",
                "public_status": "public_domain_us_federal_candidate",
                "source_path": record["source_path"],
                "rendered_pages": "1",
                "next_step": "await_human_return",
                "source_url": str(audit.get("item_url") or ""),
            }
        )
    inventory.sort(key=lambda row: row.get("doc_id", ""))
    write_inventory(inventory_path, fields, inventory)

    split_path = root / "splits" / f"microtext_{report['split']}.txt"
    current = {line.strip() for line in split_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    current.update(record["doc_id"] for record in report["records"])
    split_path.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")

    attribution_path = root / "microtext" / "docs" / "LOC_HABS_HAER_ATTRIBUTION.md"
    attribution_path.parent.mkdir(parents=True, exist_ok=True)
    existing_attribution = (
        attribution_path.read_text(encoding="utf-8") if attribution_path.exists() else ""
    )
    attribution = render_attribution(report["records"], existing_attribution)
    attribution_path.write_text(attribution, encoding="utf-8")


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in report.items() if key != "records"},
        "records": [
            {
                "doc_id": record["doc_id"],
                "loc_item_id": record["audit"].get("loc_item_id", ""),
                "source_candidate_id": record["audit"].get("candidate_id", ""),
                "source_path": record["source_path"],
                "page_path": record["page_path"],
                "sha256": record["sha256"],
                "already_registered": record["already_registered"],
            }
            for record in report["records"]
        ],
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# LOC Packet Source Registration",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Target source documents: `{report['target_docs']}`",
        f"- Verified records: `{report['ready_records']}`",
        f"- New manifest documents: `{report['new_manifest_docs']}`",
        f"- Existing manifest documents: `{report['already_registered_docs']}`",
        f"- Assigned split: `{report['split']}`",
        f"- Issues: `{len(report['issues'])}`",
        "",
        "| Document | LOC item | Candidate | SHA-256 |",
        "| --- | --- | --- | --- |",
    ]
    for record in report["records"]:
        lines.append(
            f"| `{record['doc_id']}` | `{record['audit'].get('loc_item_id', '')}` | "
            f"`{record['audit'].get('candidate_id', '')}` | `{record['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "This registration changes provenance, inventory, and split readiness only. It does not promote any human-review row into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--packet-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--doc-id",
        action="append",
        default=[],
        help="Explicit audited LOC document to register; repeat for multiple documents.",
    )
    parser.add_argument("--audit-csv", type=Path, action="append", required=True)
    parser.add_argument("--source-dir", type=Path, action="append", required=True)
    parser.add_argument("--split", choices=("train", "dev", "test"), default="test")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = build_registration(
        root=args.root,
        packet_dirs=args.packet_dir,
        doc_ids=args.doc_id,
        audit_csvs=args.audit_csv,
        source_dirs=args.source_dir,
        split=args.split,
        date_label=args.date_label,
    )
    if report["issues"]:
        raise SystemExit("[ERROR] " + "; ".join(report["issues"]))
    if not args.dry_run:
        apply_registration(args.root, report)
    output_json = args.output_json if args.output_json.is_absolute() else args.root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else args.root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(public_report(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_report(report), encoding="utf-8")
    print(json.dumps(public_report(report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
