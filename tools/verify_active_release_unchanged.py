#!/usr/bin/env python3
"""Verify current active release files against a recorded SHA-256 reference."""
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
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def verify(root: Path, reference_path: Path) -> dict[str, Any]:
    root = root.resolve()
    reference_path = reference_path if reference_path.is_absolute() else root / reference_path
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    files: list[dict[str, Any]] = []
    issues: list[str] = []
    for expected in reference.get("files", []):
        relative = str(expected.get("path") or "")
        target = root / relative
        expected_hash = str(expected.get("sha256") or "").upper()
        if not target.is_file():
            files.append(
                {
                    "path": relative,
                    "exists": False,
                    "expected_sha256": expected_hash,
                    "current_sha256": "",
                    "matches": False,
                }
            )
            issues.append(f"missing active release file: {relative}")
            continue
        current_hash = sha256(target)
        matches = bool(expected_hash) and current_hash == expected_hash
        files.append(
            {
                "path": relative,
                "exists": True,
                "size_bytes": target.stat().st_size,
                "expected_sha256": expected_hash,
                "current_sha256": current_hash,
                "matches": matches,
            }
        )
        if not matches:
            issues.append(f"active release hash mismatch: {relative}")
    unified = root / "eng_bench.jsonl"
    active_rows = jsonl_rows(unified) if unified.is_file() else 0
    expected_rows = int(reference.get("active_gold_rows") or 0)
    if expected_rows and active_rows != expected_rows:
        issues.append(f"active row count changed: expected {expected_rows}, found {active_rows}")
    return {
        "reference": reference_path.relative_to(root).as_posix(),
        "reference_date_label": reference.get("date_label", ""),
        "expected_active_gold_rows": expected_rows,
        "current_active_gold_rows": active_rows,
        "files": files,
        "issues": issues,
        "active_release_unchanged": not issues,
        "valid": not issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Active Release Hash Verification",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Reference: `{report['reference']}`",
        f"- Active rows: `{report['current_active_gold_rows']}`",
        f"- Matching files: `{sum(bool(row['matches']) for row in report['files'])}/{len(report['files'])}`",
        f"- Issues: `{len(report['issues'])}`",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)
    report = verify(Path(args.root), Path(args.reference))
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
