#!/usr/bin/env python3
"""Verify ZIP files listed by a human handoff control sheet."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_to_or_abs(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def expected_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "ok"}


def verify_zip(row: dict[str, str], zip_dir: Path) -> tuple[dict[str, Any], list[str]]:
    name = str(row.get("zip") or "").strip()
    issues: list[str] = []
    if not name:
        return {"zip": "", "valid": False, "issues": ["missing zip name"]}, ["missing zip name"]
    path = zip_dir / name
    result: dict[str, Any] = {
        "zip": name,
        "path": path.as_posix(),
        "assigned_paths": row.get("assigned_paths", ""),
        "exists": path.exists(),
        "valid": False,
        "issues": [],
    }
    if not path.exists():
        issues.append(f"missing zip: {name}")
        result["issues"] = issues
        return result, issues

    actual_sha = sha256(path)
    expected_sha = str(row.get("sha256") or "").strip().upper()
    result["sha256"] = actual_sha
    result["expected_sha256"] = expected_sha
    result["hash_match"] = bool(expected_sha) and actual_sha == expected_sha
    if not result["hash_match"]:
        issues.append(f"hash mismatch: {name}")

    try:
        with zipfile.ZipFile(path) as zf:
            bad_entry = zf.testzip()
            entries = zf.namelist()
    except zipfile.BadZipFile:
        issues.append(f"bad zip file: {name}")
        result.update({"crc_ok": False, "entries": 0, "nested_zip_entries": 0})
        result["issues"] = issues
        return result, issues

    nested = [entry for entry in entries if entry.lower().endswith(".zip")]
    result["crc_ok"] = bad_entry is None
    result["bad_entry"] = bad_entry or ""
    result["entries"] = len(entries)
    result["nested_zip_entries"] = len(nested)
    result["expected_entries"] = int(row.get("entries") or 0)
    if bad_entry:
        issues.append(f"crc failed: {name}: {bad_entry}")
    if nested:
        issues.append(f"nested zip entries: {name}: {len(nested)}")
    if expected_bool(row.get("crc_ok", "")) and bad_entry:
        issues.append(f"control expected crc ok but zip failed: {name}")
    result["valid"] = not issues
    result["issues"] = issues
    return result, issues


def verify_control_sheet(root: Path, control_csv: Path, zip_dir: Path) -> dict[str, Any]:
    control_csv = resolve(root, control_csv)
    zip_dir = resolve(root, zip_dir)
    rows = read_csv(control_csv)
    reports: list[dict[str, Any]] = []
    issues: list[str] = []
    totals: Counter[str] = Counter()
    for row in rows:
        report, row_issues = verify_zip(row, zip_dir)
        reports.append(report)
        issues.extend(row_issues)
        totals["zip_rows"] += 1
        totals["existing_zips"] += int(bool(report.get("exists")))
        totals["missing_zips"] += int(not report.get("exists"))
        totals["hash_matches"] += int(bool(report.get("hash_match")))
        totals["crc_ok"] += int(bool(report.get("crc_ok")))
        totals["nested_zip_entries"] += int(report.get("nested_zip_entries") or 0)
        totals["entries"] += int(report.get("entries") or 0)
    return {
        "valid": not issues and bool(rows),
        "control_csv": relative_to_or_abs(control_csv, root),
        "zip_dir": relative_to_or_abs(zip_dir, root),
        "issues": issues,
        "totals": dict(sorted(totals.items())),
        "zips": reports,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals", {})
    lines = [
        "# Human Handoff Control Verification",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Control CSV: `{report['control_csv']}`",
        f"- ZIP folder: `{report['zip_dir']}`",
        f"- ZIP rows: `{totals.get('zip_rows', 0)}`",
        f"- Existing ZIPs: `{totals.get('existing_zips', 0)}`",
        f"- Hash matches: `{totals.get('hash_matches', 0)}`",
        f"- CRC OK: `{totals.get('crc_ok', 0)}`",
        f"- Nested ZIP entries: `{totals.get('nested_zip_entries', 0)}`",
        "",
        "## ZIPs",
        "",
        "| ZIP | Exists | Hash | CRC | Nested ZIPs | Entries | Valid |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in report["zips"]:
        lines.append(
            f"| `{row.get('zip', '')}` | `{row.get('exists', False)}` | "
            f"`{row.get('hash_match', False)}` | `{row.get('crc_ok', False)}` | "
            f"{row.get('nested_zip_entries', 0)} | {row.get('entries', 0)} | "
            f"`{row.get('valid', False)}` |"
        )
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--control-csv", required=True)
    parser.add_argument("--zip-dir", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = verify_control_sheet(root, Path(args.control_csv), Path(args.zip_dir))
    if args.output_json:
        write_json(Path(args.output_json), report)
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
