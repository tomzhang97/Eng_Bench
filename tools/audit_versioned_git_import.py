#!/usr/bin/env python3
"""Fail closed on provenance, rights, render, and pair integrity for a Git import."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from source_rights import rights_blocker


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        rows.append(row)
    return rows


def image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        source.verify()
    with Image.open(path) as source:
        gray = source.convert("L")
        gray.thumbnail((1200, 1200))
        histogram = gray.histogram()
        pixels = max(1, gray.width * gray.height)
        ink_pixels = sum(histogram[:245])
        extrema = gray.getextrema()
        return {
            "width": source.width,
            "height": source.height,
            "sample_width": gray.width,
            "sample_height": gray.height,
            "min_gray": int(extrema[0]),
            "max_gray": int(extrema[1]),
            "ink_fraction_below_245": ink_pixels / pixels,
        }


def build_report(root: Path, additions_path: Path) -> dict[str, Any]:
    root = root.resolve()
    additions_path = additions_path if additions_path.is_absolute() else root / additions_path
    rows = read_jsonl(additions_path)
    docs = [row for row in rows if row.get("type") == "doc"]
    pairs = [row for row in rows if row.get("type") == "pair"]
    issues: list[dict[str, Any]] = []
    doc_reports: list[dict[str, Any]] = []
    doc_by_id: dict[str, dict[str, Any]] = {}

    for doc in docs:
        doc_id = str(doc.get("doc_id") or "")
        if not doc_id or doc_id in doc_by_id:
            issues.append({"scope": "doc", "id": doc_id, "type": "missing_or_duplicate_doc_id"})
            continue
        doc_by_id[doc_id] = doc
        source_path = root / str(doc.get("path") or "")
        license_evidence = doc.get("license_evidence") if isinstance(doc.get("license_evidence"), dict) else {}
        license_path = root / str(license_evidence.get("path") or "")
        pages_dir = root / "derived" / "pages_300dpi" / doc_id
        textlayer_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
        page_paths = sorted(pages_dir.glob("page_*.png"))
        expected_pages = int(doc.get("pages") or 0)
        blocker = rights_blocker(str(doc.get("public_status") or ""))
        record: dict[str, Any] = {
            "doc_id": doc_id,
            "source_path": str(doc.get("path") or ""),
            "source_exists": source_path.is_file(),
            "source_sha256_match": False,
            "license_exists": license_path.is_file(),
            "license_sha256_match": False,
            "rights_blocker": blocker,
            "rendered_pages": len(page_paths),
            "expected_pages": expected_pages,
            "text_spans": 0,
            "textlayer_required": str(doc.get("task") or "") == "microtext",
            "page_metrics": [],
        }
        if source_path.is_file():
            record["source_sha256_match"] = sha256_file(source_path) == str(doc.get("sha256") or "").upper()
        if license_path.is_file():
            record["license_sha256_match"] = sha256_file(license_path) == str(license_evidence.get("sha256") or "").upper()
        if textlayer_path.is_file():
            with textlayer_path.open(encoding="utf-8") as handle:
                record["text_spans"] = sum(1 for line in handle if line.strip())
        for page_path in page_paths:
            try:
                metrics = image_metrics(page_path)
                record["page_metrics"].append({"path": page_path.relative_to(root).as_posix(), **metrics})
                if (
                    metrics["width"] < 256
                    or metrics["height"] < 256
                    or metrics["min_gray"] > 235
                    or metrics["ink_fraction_below_245"] < 0.0005
                ):
                    issues.append({"scope": "doc", "id": doc_id, "type": "blank_or_too_small_page", "path": page_path.relative_to(root).as_posix(), "metrics": metrics})
            except Exception as exc:
                issues.append({"scope": "doc", "id": doc_id, "type": "invalid_page_image", "path": page_path.relative_to(root).as_posix(), "error": str(exc)})

        checks = {
            "missing_source": not record["source_exists"],
            "source_hash_mismatch": record["source_exists"] and not record["source_sha256_match"],
            "missing_license": not record["license_exists"],
            "license_hash_mismatch": record["license_exists"] and not record["license_sha256_match"],
            "rights_blocked": bool(blocker),
            "missing_rendered_pages": not page_paths,
            "page_count_mismatch": bool(expected_pages) and expected_pages != len(page_paths),
            "missing_textlayer_spans": (
                record["textlayer_required"] and record["text_spans"] <= 0
            ),
        }
        for issue_type, failed in checks.items():
            if failed:
                issues.append({"scope": "doc", "id": doc_id, "type": issue_type})
        doc_reports.append(record)

    pair_reports: list[dict[str, Any]] = []
    seen_pair_ids: set[str] = set()
    for pair in pairs:
        pair_id = str(pair.get("pair_id") or "")
        if not pair_id or pair_id in seen_pair_ids:
            issues.append({"scope": "pair", "id": pair_id, "type": "missing_or_duplicate_pair_id"})
            continue
        seen_pair_ids.add(pair_id)
        old = doc_by_id.get(str(pair.get("from_doc_id") or ""))
        new = doc_by_id.get(str(pair.get("to_doc_id") or ""))
        record = {
            "pair_id": pair_id,
            "from_doc_id": str(pair.get("from_doc_id") or ""),
            "to_doc_id": str(pair.get("to_doc_id") or ""),
            "content_distinct": bool(old and new and old.get("sha256") != new.get("sha256")),
        }
        if old is None or new is None:
            issues.append({"scope": "pair", "id": pair_id, "type": "missing_referenced_doc"})
        else:
            if old.get("same_model_id") != new.get("same_model_id"):
                issues.append({"scope": "pair", "id": pair_id, "type": "same_model_id_mismatch"})
            if not (
                pair.get("source_candidate_id")
                == old.get("source_candidate_id")
                == new.get("source_candidate_id")
            ):
                issues.append({"scope": "pair", "id": pair_id, "type": "source_candidate_id_mismatch"})
            if not record["content_distinct"]:
                issues.append({"scope": "pair", "id": pair_id, "type": "identical_source_payloads"})
        pair_reports.append(record)

    source_hashes = [str(doc.get("sha256") or "") for doc in docs]
    duplicate_source_hashes = sorted({value for value in source_hashes if value and source_hashes.count(value) > 1})
    for value in duplicate_source_hashes:
        issues.append({"scope": "import", "id": value, "type": "duplicate_source_payload"})

    return {
        "goal": "Gold v2.0 Global",
        "mode": "versioned_git_import_gate",
        "manifest_additions": additions_path.relative_to(root).as_posix(),
        "totals": {
            "docs": len(docs),
            "pairs": len(pairs),
            "rendered_pages": sum(record["rendered_pages"] for record in doc_reports),
            "text_spans": sum(record["text_spans"] for record in doc_reports),
            "issues": len(issues),
        },
        "duplicate_source_hashes": duplicate_source_hashes,
        "issues": issues,
        "docs": doc_reports,
        "pairs": pair_reports,
        "active_gold_rows_modified": 0,
        "valid": not issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest-additions", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    report = build_report(root, args.manifest_additions)
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], sort_keys=True))
    if not report["valid"]:
        print(json.dumps(report["issues"][:20], sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
