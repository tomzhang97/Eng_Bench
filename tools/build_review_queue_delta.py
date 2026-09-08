#!/usr/bin/env python3
"""Subtract issued review rows by both stable identity and evidence fingerprint."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def stable_identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def evidence_fingerprint(row: dict[str, Any]) -> str:
    return str(row.get("replacement_evidence_fingerprint") or "").strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def duplicate_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if value and count > 1)


def build_delta(
    input_rows: list[dict[str, Any]],
    excluded_groups: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    identities = [stable_identity(row) for row in input_rows]
    fingerprints = [evidence_fingerprint(row) for row in input_rows]
    missing_identities = sum(not value for value in identities)
    missing_fingerprints = sum(not value for value in fingerprints)
    duplicate_identities = duplicate_values(identities)
    duplicate_fingerprints = duplicate_values(fingerprints)
    if missing_identities or missing_fingerprints or duplicate_identities or duplicate_fingerprints:
        raise ValueError(
            "input queue is not uniquely auditable: "
            f"missing identities={missing_identities}, "
            f"missing fingerprints={missing_fingerprints}, "
            f"duplicate identities={len(duplicate_identities)}, "
            f"duplicate fingerprints={len(duplicate_fingerprints)}"
        )

    excluded_identities = {
        stable_identity(row)
        for group in excluded_groups
        for row in group
        if stable_identity(row)
    }
    excluded_fingerprints = {
        evidence_fingerprint(row)
        for group in excluded_groups
        for row in group
        if evidence_fingerprint(row)
    }
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row, identity, fingerprint in zip(input_rows, identities, fingerprints):
        identity_overlap = identity in excluded_identities
        evidence_overlap = fingerprint in excluded_fingerprints
        if not identity_overlap and not evidence_overlap:
            selected.append(dict(row))
            continue
        if identity_overlap and evidence_overlap:
            reason = "excluded_identity_and_evidence"
        elif identity_overlap:
            reason = "excluded_identity"
        else:
            reason = "excluded_evidence"
        held_row = dict(row)
        held_row["review_delta_hold_reason"] = reason
        held.append(held_row)
        reasons[reason] += 1

    selected_ids = [stable_identity(row) for row in selected]
    selected_fingerprints = [evidence_fingerprint(row) for row in selected]
    issues: list[str] = []
    if set(selected_ids) & excluded_identities:
        issues.append("selected identity overlaps excluded rows")
    if set(selected_fingerprints) & excluded_fingerprints:
        issues.append("selected evidence overlaps excluded rows")
    if any(row.get("safe_to_merge_gold") is not False for row in selected):
        issues.append("selected rows must keep safe_to_merge_gold=false")
    if any(str(row.get("review_status") or "") != "needs_review" for row in selected):
        issues.append("selected rows must keep review_status=needs_review")
    report = {
        "input_rows": len(input_rows),
        "excluded_group_count": len(excluded_groups),
        "excluded_identity_count": len(excluded_identities),
        "excluded_evidence_count": len(excluded_fingerprints),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "hold_reasons": dict(sorted(reasons.items())),
        "selected_unique_identities": len(set(selected_ids)),
        "selected_unique_evidence_fingerprints": len(set(selected_fingerprints)),
        "selected_identity_overlap": len(set(selected_ids) & excluded_identities),
        "selected_evidence_overlap": len(set(selected_fingerprints) & excluded_fingerprints),
        "issues": issues,
        "valid": not issues,
    }
    return selected, held, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    input_path = resolve(args.input)
    exclude_paths = [resolve(path) for path in args.exclude]
    selected, held, report = build_delta(
        read_jsonl(input_path),
        [read_jsonl(path) for path in exclude_paths],
    )
    if args.expected_rows is not None and len(selected) != args.expected_rows:
        raise ValueError(f"expected {args.expected_rows} selected rows, found {len(selected)}")
    output_path = resolve(args.output)
    hold_path = resolve(args.hold_output)
    report_path = resolve(args.report_json)
    write_jsonl(output_path, selected)
    write_jsonl(hold_path, held)
    report.update(
        {
            "input": input_path.relative_to(root).as_posix(),
            "input_sha256": sha256(input_path),
            "exclude_files": [path.relative_to(root).as_posix() for path in exclude_paths],
            "output": output_path.relative_to(root).as_posix(),
            "output_sha256": sha256(output_path),
            "hold_output": hold_path.relative_to(root).as_posix(),
            "hold_output_sha256": sha256(hold_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
