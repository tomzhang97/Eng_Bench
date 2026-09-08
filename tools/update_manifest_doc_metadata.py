#!/usr/bin/env python3
"""Update one manifest document's structured metadata after evidence review."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def update_doc(
    root: Path,
    manifest_path: Path,
    doc_id: str,
    patch: dict[str, Any],
    expected_payload_sha256: str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    rows = read_jsonl(manifest_path)
    matches = [row for row in rows if row.get("type") == "doc" and row.get("doc_id") == doc_id]
    issues: list[str] = []
    if len(matches) != 1:
        issues.append(f"expected exactly one manifest document, found {len(matches)}")
    document = matches[0] if len(matches) == 1 else {}
    payload_path = root / str(document.get("path") or "") if document else Path()
    recorded_sha256 = str(document.get("sha256") or "").strip().lower()
    expected = expected_payload_sha256.strip().lower()
    computed_sha256 = file_sha256(payload_path) if payload_path.is_file() else ""
    if len(expected) != 64:
        issues.append("expected payload SHA-256 must contain 64 hex characters")
    if not payload_path.is_file():
        issues.append("manifest payload is missing")
    if recorded_sha256 != expected:
        issues.append("manifest payload SHA-256 does not match expected value")
    if computed_sha256 != expected:
        issues.append("computed payload SHA-256 does not match expected value")
    if not isinstance(patch, dict) or not patch:
        issues.append("metadata patch must be a non-empty JSON object")
    forbidden = {"type", "doc_id", "path", "sha256"} & set(patch)
    if forbidden:
        issues.append(f"metadata patch cannot change protected keys: {sorted(forbidden)}")

    before = {key: document.get(key) for key in patch} if document else {}
    after = dict(before)
    if not issues:
        after = patch
        document.update(patch)
        if apply:
            write_jsonl_atomic(manifest_path, rows)
    return {
        "doc_id": doc_id,
        "valid": not issues,
        "applied": bool(apply and not issues),
        "manifest_path": manifest_path.as_posix(),
        "payload_path": payload_path.as_posix() if document else "",
        "recorded_sha256": recorded_sha256,
        "computed_sha256": computed_sha256,
        "expected_sha256": expected,
        "before": before,
        "after": after,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path("manifest.jsonl"))
    parser.add_argument("--doc-id", required=True)
    patch_group = parser.add_mutually_exclusive_group(required=True)
    patch_group.add_argument("--patch-json", help="JSON object merged into the document")
    patch_group.add_argument("--patch-file", type=Path, help="UTF-8 JSON file containing the patch object")
    parser.add_argument("--expected-payload-sha256", required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        patch = (
            json.loads(args.patch_json)
            if args.patch_json is not None
            else json.loads(
                (args.patch_file if args.patch_file.is_absolute() else root / args.patch_file).read_text(
                    encoding="utf-8"
                )
            )
        )
        report = update_doc(
            root,
            manifest,
            args.doc_id,
            patch,
            args.expected_payload_sha256,
            apply=args.apply,
        )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    if args.report_json:
        output = args.report_json if args.report_json.is_absolute() else root / args.report_json
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Wrote {output}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
