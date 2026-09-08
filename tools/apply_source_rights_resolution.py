#!/usr/bin/env python3
"""Apply evidence-backed source-rights decisions to existing source records."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    from source_rights import rights_blocker
except ModuleNotFoundError:  # pragma: no cover - package import path
    from tools.source_rights import rights_blocker


RELEASE_DECISION = "release_safe"
HOLD_DECISION = "hold"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp_path, path)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_path, path)


def unique_index(rows: list[dict[str, Any]], key: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in rows:
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        if value in indexed:
            duplicates.add(value)
        else:
            indexed[value] = row
    return indexed, duplicates


def relative_evidence_path(root: Path, value: str) -> tuple[Path, str]:
    path = Path(value)
    full_path = path if path.is_absolute() else root / path
    try:
        relative = full_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = str(full_path.resolve())
    return full_path, relative


def build_resolution(
    root: Path,
    evidence_csv: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]], list[str]]:
    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    manifest_rows = read_jsonl(manifest_path)
    inventory_fields, inventory_rows = read_csv(inventory_path)
    _evidence_fields, evidence_rows = read_csv(evidence_csv)

    manifest_docs = [row for row in manifest_rows if row.get("type") == "doc"]
    manifest_by_doc, duplicate_manifest = unique_index(manifest_docs, "doc_id")
    inventory_by_doc, duplicate_inventory = unique_index(inventory_rows, "doc_id")
    evidence_by_doc, duplicate_evidence = unique_index(evidence_rows, "doc_id")

    issues: list[str] = []
    decisions: list[dict[str, Any]] = []
    release_updates: dict[str, dict[str, str]] = {}

    for doc_id in sorted(duplicate_evidence):
        issues.append(f"{doc_id}: duplicate evidence decision")

    for doc_id, evidence in evidence_by_doc.items():
        decision = str(evidence.get("decision") or "").strip().lower()
        reason = str(evidence.get("reason") or "").strip()
        record: dict[str, Any] = {
            "doc_id": doc_id,
            "decision": decision,
            "reason": reason,
            "applied": False,
            "issues": [],
        }
        row_issues: list[str] = record["issues"]
        if decision == HOLD_DECISION:
            if not reason:
                row_issues.append("hold decision requires a reason")
            terms_url = str(evidence.get("terms_url") or "").strip()
            evidence_value = str(evidence.get("evidence_path") or "").strip()
            evidence_relative = ""
            if terms_url and not terms_url.startswith("https://"):
                row_issues.append("terms_url must be an HTTPS URL when supplied")
            if evidence_value:
                evidence_path, evidence_relative = relative_evidence_path(root, evidence_value)
                if not evidence_path.is_file():
                    row_issues.append(f"evidence_path not found: {evidence_value}")
            expected_sha256 = str(evidence.get("expected_sha256") or "").strip().lower()
            computed_sha256 = ""
            manifest = manifest_by_doc.get(doc_id)
            if expected_sha256:
                if len(expected_sha256) != 64:
                    row_issues.append("expected_sha256 must contain 64 hex characters")
                if manifest is None:
                    row_issues.append("manifest document missing")
                else:
                    local_value = str(manifest.get("path") or "").strip()
                    local_path = root / local_value if local_value else Path()
                    recorded_sha256 = str(manifest.get("sha256") or "").strip().lower()
                    if not local_value or not local_path.is_file():
                        row_issues.append(f"local source missing: {local_value or 'blank'}")
                    else:
                        computed_sha256 = sha256_file(local_path)
                        if computed_sha256 != expected_sha256:
                            row_issues.append("local source SHA-256 does not match evidence")
                    if recorded_sha256 != expected_sha256:
                        row_issues.append("manifest SHA-256 does not match evidence")
            record.update(
                {
                    "terms_url": terms_url,
                    "evidence_path": evidence_relative,
                    "expected_sha256": expected_sha256,
                    "computed_sha256": computed_sha256,
                }
            )
            decisions.append(record)
            issues.extend(f"{doc_id}: {issue}" for issue in row_issues)
            continue
        if decision != RELEASE_DECISION:
            row_issues.append(f"unsupported decision: {decision or 'blank'}")
            decisions.append(record)
            issues.extend(f"{doc_id}: {issue}" for issue in row_issues)
            continue

        manifest = manifest_by_doc.get(doc_id)
        inventory = inventory_by_doc.get(doc_id)
        if doc_id in duplicate_manifest:
            row_issues.append("duplicate manifest document")
        if doc_id in duplicate_inventory:
            row_issues.append("duplicate inventory document")
        if manifest is None:
            row_issues.append("manifest document missing")
        if inventory is None:
            row_issues.append("inventory document missing")

        target_status = str(evidence.get("target_public_status") or "").strip()
        license_note = str(evidence.get("license_note") or "").strip()
        terms_url = str(evidence.get("terms_url") or "").strip()
        evidence_value = str(evidence.get("evidence_path") or "").strip()
        expected_sha256 = str(evidence.get("expected_sha256") or "").strip().lower()
        if rights_blocker(target_status):
            row_issues.append(f"target status is not release-safe: {target_status or 'blank'}")
        if not license_note:
            row_issues.append("license_note missing")
        if not terms_url.startswith("https://"):
            row_issues.append("terms_url must be an HTTPS URL")
        if not evidence_value:
            row_issues.append("evidence_path missing")
            evidence_relative = ""
        else:
            evidence_path, evidence_relative = relative_evidence_path(root, evidence_value)
            if not evidence_path.is_file():
                row_issues.append(f"evidence_path not found: {evidence_value}")
        if len(expected_sha256) != 64:
            row_issues.append("expected_sha256 must contain 64 hex characters")

        computed_sha256 = ""
        if manifest is not None:
            local_value = str(manifest.get("path") or "").strip()
            local_path = root / local_value if local_value else Path()
            recorded_sha256 = str(manifest.get("sha256") or "").strip().lower()
            if not local_value or not local_path.is_file():
                row_issues.append(f"local source missing: {local_value or 'blank'}")
            else:
                computed_sha256 = sha256_file(local_path)
                if computed_sha256 != expected_sha256:
                    row_issues.append("local source SHA-256 does not match evidence")
            if recorded_sha256 != expected_sha256:
                row_issues.append("manifest SHA-256 does not match evidence")

        record.update(
            {
                "target_public_status": target_status,
                "terms_url": terms_url,
                "evidence_path": evidence_relative,
                "expected_sha256": expected_sha256,
                "computed_sha256": computed_sha256,
            }
        )
        if not row_issues:
            release_updates[doc_id] = {
                "public_status": target_status,
                "license_note": license_note,
                "rights_evidence_url": terms_url,
                "rights_evidence_path": evidence_relative,
                "rights_reviewed_date": str(evidence.get("reviewed_date") or "").strip(),
            }
        decisions.append(record)
        issues.extend(f"{doc_id}: {issue}" for issue in row_issues)

    report: dict[str, Any] = {
        "evidence_csv": evidence_csv.resolve().as_posix(),
        "decisions": decisions,
        "release_safe_decisions": sum(row["decision"] == RELEASE_DECISION for row in decisions),
        "hold_decisions": sum(row["decision"] == HOLD_DECISION for row in decisions),
        "validated_release_updates": len(release_updates),
        "issues": issues,
        "applied": False,
    }
    return report, manifest_rows, inventory_rows, inventory_fields


def apply_resolution(
    root: Path,
    evidence_csv: Path,
    *,
    snapshot_dir: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    report, manifest_rows, inventory_rows, inventory_fields = build_resolution(root, evidence_csv)
    if report["issues"] or not apply:
        return report
    if snapshot_dir is None:
        raise ValueError("snapshot_dir is required when --apply is used")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if any(snapshot_dir.iterdir()):
        raise ValueError(f"snapshot directory is not empty: {snapshot_dir}")
    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    shutil.copy2(manifest_path, snapshot_dir / manifest_path.name)
    shutil.copy2(inventory_path, snapshot_dir / inventory_path.name)

    evidence_rows = read_csv(evidence_csv)[1]
    updates = {
        str(row.get("doc_id") or "").strip(): row
        for row in evidence_rows
        if str(row.get("decision") or "").strip().lower() == RELEASE_DECISION
    }
    for row in manifest_rows:
        evidence = updates.get(str(row.get("doc_id") or "").strip())
        if not evidence or row.get("type") != "doc":
            continue
        row["public_status"] = str(evidence["target_public_status"]).strip()
        row["license_note"] = str(evidence["license_note"]).strip()
        row["rights_evidence_url"] = str(evidence["terms_url"]).strip()
        _full, relative = relative_evidence_path(root, str(evidence["evidence_path"]).strip())
        row["rights_evidence_path"] = relative
        row["rights_reviewed_date"] = str(evidence.get("reviewed_date") or "").strip()
    for row in inventory_rows:
        evidence = updates.get(str(row.get("doc_id") or "").strip())
        if evidence:
            row["public_status"] = str(evidence["target_public_status"]).strip()

    before = {
        "manifest_sha256": sha256_file(manifest_path),
        "inventory_sha256": sha256_file(inventory_path),
    }
    write_jsonl(manifest_path, manifest_rows)
    write_csv(inventory_path, inventory_fields, inventory_rows)
    after = {
        "manifest_sha256": sha256_file(manifest_path),
        "inventory_sha256": sha256_file(inventory_path),
    }
    for decision in report["decisions"]:
        if decision["decision"] == RELEASE_DECISION:
            decision["applied"] = True
    report.update({"applied": True, "snapshot_dir": snapshot_dir.resolve().as_posix(), "before": before, "after": after})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply evidence-backed source-rights decisions")
    parser.add_argument("--root", default=".")
    parser.add_argument("--evidence-csv", required=True)
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--report-json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    evidence_csv = Path(args.evidence_csv)
    if not evidence_csv.is_absolute():
        evidence_csv = root / evidence_csv
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
    if snapshot_dir is not None and not snapshot_dir.is_absolute():
        snapshot_dir = root / snapshot_dir
    try:
        report = apply_resolution(
            root,
            evidence_csv,
            snapshot_dir=snapshot_dir,
            apply=args.apply,
        )
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1

    if args.report_json:
        report_path = Path(args.report_json)
        if not report_path.is_absolute():
            report_path = root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[OK] Wrote {report_path}")
    print(json.dumps({key: report[key] for key in ("release_safe_decisions", "hold_decisions", "validated_release_updates", "issues", "applied")}, indent=2))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
