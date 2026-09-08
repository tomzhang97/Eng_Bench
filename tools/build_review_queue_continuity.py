#!/usr/bin/env python3
"""Bind a current review selection to still-relevant rows from prior queues."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def evidence_fingerprint(row: dict[str, Any]) -> str:
    return str(row.get("replacement_evidence_fingerprint") or "").strip()


def build_continuity(
    current_rows: list[dict[str, Any]],
    prior_groups: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    issues: list[str] = []
    current_by_id: dict[str, dict[str, Any]] = {}
    current_by_fingerprint: dict[str, str] = {}
    for row in current_rows:
        identity = stable_identity(row)
        fingerprint = evidence_fingerprint(row)
        if not identity:
            issues.append("current_identity_missing")
            continue
        if not fingerprint:
            issues.append(f"current_fingerprint_missing:{identity}")
            continue
        if identity in current_by_id:
            issues.append(f"current_identity_duplicate:{identity}")
            continue
        if fingerprint in current_by_fingerprint:
            issues.append(
                f"current_fingerprint_duplicate:{fingerprint}:"
                f"{current_by_fingerprint[fingerprint]}:{identity}"
            )
            continue
        current_by_id[identity] = row
        current_by_fingerprint[fingerprint] = identity

    prior_by_id: dict[str, dict[str, Any]] = {}
    duplicate_prior_rows = 0
    for group in prior_groups:
        for row in group:
            identity = stable_identity(row)
            fingerprint = evidence_fingerprint(row)
            if not identity:
                issues.append("prior_identity_missing")
                continue
            if not fingerprint:
                issues.append(f"prior_fingerprint_missing:{identity}")
                continue
            existing = prior_by_id.get(identity)
            if existing is not None:
                duplicate_prior_rows += 1
                if evidence_fingerprint(existing) != fingerprint:
                    issues.append(f"prior_identity_evidence_conflict:{identity}")
                continue
            prior_by_id[identity] = row

    retained: list[dict[str, Any]] = []
    retired: list[dict[str, Any]] = []
    retired_reasons: dict[str, int] = {}
    for identity in sorted(prior_by_id):
        prior = prior_by_id[identity]
        prior_fingerprint = evidence_fingerprint(prior)
        current = current_by_id.get(identity)
        if current is not None:
            if evidence_fingerprint(current) != prior_fingerprint:
                issues.append(f"current_identity_evidence_changed:{identity}")
                continue
            retained.append(dict(current))
            continue

        alias_identity = current_by_fingerprint.get(prior_fingerprint, "")
        reason = (
            "evidence_selected_under_different_identity"
            if alias_identity
            else "not_selected_by_current_plan"
        )
        output = dict(prior)
        output["review_continuity_hold_reason"] = reason
        if alias_identity:
            output["review_continuity_current_identity"] = alias_identity
        retired.append(output)
        retired_reasons[reason] = retired_reasons.get(reason, 0) + 1

    retained_ids = [stable_identity(row) for row in retained]
    retained_fingerprints = [evidence_fingerprint(row) for row in retained]
    if len(retained_ids) != len(set(retained_ids)):
        issues.append("retained_identity_duplicate")
    if len(retained_fingerprints) != len(set(retained_fingerprints)):
        issues.append("retained_fingerprint_duplicate")

    report = {
        "current_selected_rows": len(current_rows),
        "current_unique_identities": len(current_by_id),
        "prior_queue_files": len(prior_groups),
        "prior_rows": sum(len(group) for group in prior_groups),
        "prior_unique_identities": len(prior_by_id),
        "duplicate_prior_rows": duplicate_prior_rows,
        "retained_rows": len(retained),
        "retained_unique_identities": len(set(retained_ids)),
        "retained_unique_evidence_fingerprints": len(set(retained_fingerprints)),
        "retired_rows": len(retired),
        "retired_reasons": dict(sorted(retired_reasons.items())),
        "issues": issues,
        "valid": not issues,
        "interpretation": (
            "Retained rows are current-plan copies of prior issued work. Retired rows should not "
            "be assigned again unless a later replacement plan selects them. This tool never marks "
            "a row reviewed or safe to merge Gold."
        ),
    }
    return retained, retired, report


def resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--prior", type=Path, action="append", default=[], required=True)
    parser.add_argument("--retained-output", type=Path, required=True)
    parser.add_argument("--retired-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--expected-retained", type=int)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    current_path = resolve(root, args.current)
    prior_paths = [resolve(root, path) for path in args.prior]
    retained, retired, report = build_continuity(
        read_jsonl(current_path),
        [read_jsonl(path) for path in prior_paths],
    )
    if args.expected_retained is not None and len(retained) != args.expected_retained:
        report["issues"].append(
            f"expected_retained_mismatch:{args.expected_retained}:{len(retained)}"
        )
        report["valid"] = False

    retained_path = resolve(root, args.retained_output)
    retired_path = resolve(root, args.retired_output)
    report_path = resolve(root, args.report_json)
    write_jsonl(retained_path, retained)
    write_jsonl(retired_path, retired)
    report.update(
        {
            "current": current_path.relative_to(root).as_posix(),
            "current_sha256": file_sha256(current_path),
            "prior": [path.relative_to(root).as_posix() for path in prior_paths],
            "retained_output": retained_path.relative_to(root).as_posix(),
            "retained_output_sha256": file_sha256(retained_path),
            "retired_output": retired_path.relative_to(root).as_posix(),
            "retired_output_sha256": file_sha256(retired_path),
        }
    )
    write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
