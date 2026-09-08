#!/usr/bin/env python3
"""Verify active release files against a frozen SHA-256 reference manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def jsonl_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_report(root: Path, reference_path: Path) -> dict[str, Any]:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    files: list[dict[str, Any]] = []
    issues: list[str] = []
    for expected in reference.get("files", []):
        relative = Path(str(expected["path"]))
        path = root / relative
        exists = path.is_file()
        current = sha256(path) if exists else ""
        expected_hash = str(expected.get("sha256") or "").upper()
        matches = exists and bool(expected_hash) and current == expected_hash
        files.append(
            {
                "path": relative.as_posix(),
                "exists": exists,
                "size_bytes": path.stat().st_size if exists else 0,
                "expected_sha256": expected_hash,
                "current_sha256": current,
                "matches": matches,
            }
        )
        if not exists:
            issues.append(f"missing:{relative.as_posix()}")
        elif not matches:
            issues.append(f"sha256_mismatch:{relative.as_posix()}")

    expected_rows = int(reference.get("active_gold_rows") or 0)
    current_rows = jsonl_rows(root / "eng_bench.jsonl")
    if current_rows != expected_rows:
        issues.append(f"active_gold_rows:{current_rows}!={expected_rows}")
    return {
        "reference": reference_path.as_posix(),
        "reference_date_label": str(reference.get("date_label") or ""),
        "expected_active_gold_rows": expected_rows,
        "current_active_gold_rows": current_rows,
        "files": files,
        "issues": issues,
        "active_release_unchanged": not issues,
        "valid": not issues,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    matching = sum(1 for row in report["files"] if row["matches"])
    lines = [
        "# Active Release Hash Verification",
        "",
        f"- Frozen files matching: `{matching}/{len(report['files'])}`",
        f"- Active Gold rows: `{report['current_active_gold_rows']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "",
    ]
    if report["issues"]:
        lines.extend(["## Issues", ""])
        lines.extend(f"- `{issue}`" for issue in report["issues"])
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    reference = args.reference if args.reference.is_absolute() else root / args.reference
    report = build_report(root, reference)
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(output_md, report)
    print(
        json.dumps(
            {
                "valid": report["valid"],
                "matching_files": sum(1 for row in report["files"] if row["matches"]),
                "files": len(report["files"]),
                "active_gold_rows": report["current_active_gold_rows"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
