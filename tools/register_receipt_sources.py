#!/usr/bin/env python3
"""Register source documents backed by verified download and render receipts."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from audit_active_gold_provenance import file_sha256, read_csv, read_jsonl


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def receipt_map(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in payload.get(key, [])
        if str(row.get("doc_id") or "").strip()
    }


def parse_version(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("version_json must decode to an object")
    return parsed


def commons_public_status(license_short_name: Any) -> str:
    normalized = " ".join(str(license_short_name or "").strip().lower().replace("_", " ").split())
    if not normalized:
        return ""
    if normalized in {"cc0", "cc zero"}:
        return "cc0_commons_candidate"
    if normalized in {"public domain", "pd", "pd-self"}:
        return "public_domain_commons_candidate"
    if normalized.startswith("cc by") and not any(
        marker in normalized for marker in ("-nc", " nc", "-nd", " nd")
    ):
        slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        return f"{slug}_commons_candidate"
    return ""


def nasa_ntrs_public_status(license_short_name: Any, source_url: Any) -> str:
    normalized = " ".join(str(license_short_name or "").strip().lower().replace("_", " ").split())
    url = str(source_url or "").strip().lower()
    if "ntrs.nasa.gov" not in url:
        return ""
    if normalized == "gov public use permitted":
        return "government_public_use_permitted_nasa_candidate"
    return ""


def build_registration(
    root: Path,
    download_receipts: Path,
    render_receipts: Path,
    doc_ids: set[str] | None = None,
    date_label: str = "manual",
) -> dict[str, Any]:
    root = root.resolve()
    download_path = download_receipts if download_receipts.is_absolute() else root / download_receipts
    render_path = render_receipts if render_receipts.is_absolute() else root / render_receipts
    downloads = receipt_map(read_json(download_path), "receipts")
    renders = receipt_map(read_json(render_path), "render_receipts")
    selected = sorted(doc_ids or (set(downloads) & set(renders)))
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    existing_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    records: list[dict[str, Any]] = []
    issues: list[str] = []
    for doc_id in selected:
        download = downloads.get(doc_id)
        render = renders.get(doc_id)
        if not download:
            issues.append(f"missing_download_receipt:{doc_id}")
            continue
        if not render:
            issues.append(f"missing_render_receipt:{doc_id}")
            continue
        if str(download.get("status") or "").strip().lower() != "downloaded":
            issues.append(f"download_not_complete:{doc_id}")
            continue
        if not str(render.get("status") or "").strip().lower().startswith("rendered"):
            issues.append(f"render_not_complete:{doc_id}")
            continue
        relative_source = str(download.get("local_path") or render.get("local_path") or "").strip()
        source_path = root / relative_source if relative_source else None
        if not source_path or not source_path.is_file():
            issues.append(f"missing_source_payload:{doc_id}")
            continue
        computed_sha = file_sha256(source_path)
        receipt_sha = str(download.get("sha256") or "").strip().lower()
        if not receipt_sha or computed_sha != receipt_sha:
            issues.append(f"source_sha256_mismatch:{doc_id}")
            continue
        pages_dir_value = str(render.get("pages_dir") or "").strip()
        pages_dir = root / pages_dir_value if pages_dir_value else None
        rendered_pages = int(render.get("rendered_pages") or 0)
        page_files = sorted(pages_dir.glob("*.png")) if pages_dir and pages_dir.is_dir() else []
        if rendered_pages <= 0 or len(page_files) < rendered_pages:
            issues.append(f"rendered_pages_missing:{doc_id}")
            continue
        source_url = str(
            download.get("source_url")
            or download.get("page_url")
            or download.get("final_url")
            or download.get("download_url")
            or ""
        ).strip()
        retrieved_from_url = str(
            download.get("retrieved_from_url")
            or download.get("final_url")
            or download.get("download_url")
            or ""
        ).strip()
        direct_source_url = str(
            download.get("official_direct_url")
            or download.get("final_url")
            or download.get("download_url")
            or ""
        ).strip()
        license_short_name = str(download.get("license_short_name") or "").strip()
        mapped_commons_status = commons_public_status(license_short_name)
        mapped_ntrs_status = nasa_ntrs_public_status(license_short_name, source_url)
        public_status = str(
            download.get("public_status")
            or mapped_commons_status
            or mapped_ntrs_status
            or download.get("rights_capture")
            or ""
        ).strip()
        if not source_url:
            issues.append(f"missing_source_url:{doc_id}")
            continue
        if not public_status:
            issues.append(f"missing_rights_capture:{doc_id}")
            continue
        domain = str(download.get("domain") or render.get("domain") or "").strip()
        candidate_id = str(download.get("candidate_id") or render.get("candidate_id") or "").strip()
        try:
            version = parse_version(download.get("version") or download.get("version_json"))
        except (json.JSONDecodeError, ValueError) as exc:
            issues.append(f"invalid_version_json:{doc_id}:{exc}")
            continue
        version.setdefault("receipt_date", date_label)
        selected_pages = str(
            render.get("page_selection") or download.get("page_selection") or ""
        ).strip()
        full_page_count = int(download.get("full_page_count") or rendered_pages)
        derived = {
            "pages_dir": pages_dir_value.replace("\\", "/"),
            "textlayer_jsonl": str(render.get("textlayer_jsonl") or "").replace("\\", "/"),
        }
        textlayer_dir = str(render.get("textlayer_dir") or "").strip()
        if textlayer_dir:
            derived["textlayer_dir"] = textlayer_dir.replace("\\", "/")
        if selected_pages:
            derived["rendered_page_selection_1based"] = selected_pages
        if retrieved_from_url and retrieved_from_url != direct_source_url:
            derived["retrieved_from_url"] = retrieved_from_url

        download_evidence_path = (
            download_path.relative_to(root).as_posix()
            if download_path.is_relative_to(root)
            else str(download_path)
        )
        artist = str(download.get("artist") or "").strip()
        license_note = str(download.get("license_note") or "").strip()
        if not license_note and mapped_commons_status:
            attribution = f"; author {artist}" if artist else ""
            license_note = (
                f"Wikimedia Commons API records {license_short_name}{attribution}; "
                "the exact source page, payload hash, and download receipt are preserved."
            )
        if not license_note and mapped_ntrs_status:
            license_note = (
                f"NASA NTRS metadata records {license_short_name}; the exact citation metadata, "
                "source URL, payload hash, and download receipt are preserved."
            )
        if not license_note:
            license_note = (
                "Public-source acquisition URL, local payload SHA-256, and render receipt are recorded; "
                "release remains subject to the source agency terms captured by the intake record."
            )
        rights_evidence_url = str(download.get("rights_evidence_url") or "").strip()
        if not rights_evidence_url and (mapped_commons_status or mapped_ntrs_status):
            rights_evidence_url = source_url
        rights_evidence_path = str(download.get("rights_evidence_path") or "").strip()
        if not rights_evidence_path and (mapped_commons_status or mapped_ntrs_status):
            rights_evidence_path = download_evidence_path

        manifest_row = {
            "type": "doc",
            "doc_id": doc_id,
            "task": "microtext",
            "source_candidate_id": candidate_id,
            "same_model_id": str(download.get("same_model_id") or candidate_id or doc_id),
            "domain": "civil_architectural" if domain == "civil" else domain,
            "doc_type": str(
                download.get("doc_type") or download.get("asset_kind") or "source_document"
            ),
            "version": version,
            "path": relative_source.replace("\\", "/"),
            "sha256": computed_sha,
            "pages": full_page_count,
            "render": {
                "dpi": int(render.get("dpi") or 300),
                "colorspace": str(render.get("colorspace") or "rgb"),
                "rotate_cw90": False,
            },
            "derived": derived,
            "source_url": source_url,
            "direct_source_url": direct_source_url,
            "public_status": public_status,
            "license_note": license_note,
            "notes": str(
                download.get("notes")
                or "Receipt-backed source registration only; no annotation row promoted."
            ),
        }
        if rights_evidence_url:
            manifest_row["rights_evidence_url"] = rights_evidence_url
        if rights_evidence_path:
            manifest_row["rights_evidence_path"] = rights_evidence_path.replace("\\", "/")
        records.append(
            {
                "doc_id": doc_id,
                "already_registered": doc_id in existing_docs,
                "manifest_row": manifest_row,
                "source_path": relative_source.replace("\\", "/"),
                "sha256": computed_sha,
                "rendered_pages": rendered_pages,
            }
        )
    return {
        "date_label": date_label,
        "download_receipts": download_path.relative_to(root).as_posix()
        if download_path.is_relative_to(root)
        else str(download_path),
        "render_receipts": render_path.relative_to(root).as_posix()
        if render_path.is_relative_to(root)
        else str(render_path),
        "target_docs": len(selected),
        "ready_records": len(records),
        "new_manifest_docs": sum(not row["already_registered"] for row in records),
        "already_registered_docs": sum(row["already_registered"] for row in records),
        "issues": issues,
        "records": records,
    }


def inventory_fields(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), [])


def apply_registration(
    root: Path,
    report: dict[str, Any],
    *,
    refresh_existing: bool = False,
) -> None:
    if report["issues"]:
        raise ValueError("registration has unresolved issues: " + ", ".join(report["issues"]))
    root = root.resolve()
    manifest_path = root / "manifest.jsonl"
    new_rows = [row["manifest_row"] for row in report["records"] if not row["already_registered"]]
    if refresh_existing:
        manifest_rows = read_jsonl(manifest_path)
        replacements = {
            row["doc_id"]: row["manifest_row"]
            for row in report["records"]
            if row["already_registered"]
        }
        refreshed = 0
        for index, row in enumerate(manifest_rows):
            replacement = replacements.get(str(row.get("doc_id") or ""))
            if not replacement or row.get("type") != "doc":
                continue
            if str(row.get("sha256") or "").lower() != str(replacement.get("sha256") or "").lower():
                raise ValueError(f"existing manifest SHA-256 mismatch: {row.get('doc_id')}")
            manifest_rows[index] = {**row, **replacement}
            refreshed += 1
        if refreshed != len(replacements):
            raise ValueError("not every requested existing manifest document was refreshed")
        temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in manifest_rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        temp_path.replace(manifest_path)
    if new_rows:
        with manifest_path.open("a", encoding="utf-8") as handle:
            for row in new_rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    inventory_path = root / "SOURCE_INVENTORY.csv"
    fields = inventory_fields(inventory_path)
    inventory = read_csv(inventory_path)
    by_doc = {str(row.get("doc_id") or ""): row for row in inventory}
    for record in report["records"]:
        manifest = record["manifest_row"]
        row = by_doc.get(record["doc_id"])
        if row is None:
            row = {field: "" for field in fields}
            row["doc_id"] = record["doc_id"]
            inventory.append(row)
            by_doc[record["doc_id"]] = row
        row.update(
            {
                "domain": manifest["domain"],
                "task": "microtext",
                "public_status": manifest["public_status"],
                "source_path": manifest["path"],
                "rendered_pages": str(record["rendered_pages"]),
                "source_url": manifest["source_url"],
            }
        )
    inventory.sort(key=lambda row: str(row.get("doc_id") or ""))
    with inventory_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(inventory)


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in report.items() if key != "records"},
        "records": [
            {key: value for key, value in row.items() if key != "manifest_row"}
            for row in report["records"]
        ],
    }


def write_manifest_preview(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in report["records"]:
            handle.write(json.dumps(record["manifest_row"], ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--download-receipts", type=Path, required=True)
    parser.add_argument("--render-receipts", type=Path, required=True)
    parser.add_argument("--doc-id", action="append", default=[])
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument(
        "--output-manifest-jsonl",
        type=Path,
        help="Write the exact proposed manifest document rows without changing active manifest.jsonl.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Refresh an existing hash-matched manifest row from the verified receipts.",
    )
    args = parser.parse_args()
    report = build_registration(
        args.root,
        args.download_receipts,
        args.render_receipts,
        set(args.doc_id) if args.doc_id else None,
        args.date_label,
    )
    if report["issues"]:
        print(json.dumps(public_report(report), indent=2))
        return 1
    if args.output_manifest_jsonl:
        manifest_preview = (
            args.output_manifest_jsonl
            if args.output_manifest_jsonl.is_absolute()
            else args.root / args.output_manifest_jsonl
        )
        write_manifest_preview(manifest_preview, report)
    if not args.dry_run:
        apply_registration(args.root, report, refresh_existing=args.refresh_existing)
    output = args.output_json if args.output_json.is_absolute() else args.root / args.output_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(public_report(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(public_report(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
