#!/usr/bin/env python3
"""Create and optionally activate a hash-pinned candidate evidence hold report."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .candidate_evidence_holds import CURRENT_HOLDS, digest, evidence_hold_ids
    from .ingest_auditor_return_batch import parser as audit_parser
except ImportError:
    from candidate_evidence_holds import CURRENT_HOLDS, digest, evidence_hold_ids
    from ingest_auditor_return_batch import parser as audit_parser


HOLD_ROWS = "candidate_evidence_holds.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def identity_for(row: dict) -> str:
    identities = [
        str(row.get(key) or "")
        for key in ("candidate_id", "source_candidate_id", "item_id", "pair_id")
        if str(row.get(key) or "")
    ]
    if not identities:
        raise ValueError("candidate evidence hold missing identity")
    return identities[0]


def relative_evidence(root: Path, paths: list[Path]) -> list[dict[str, str]]:
    result = []
    for supplied in paths:
        path = supplied.resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"evidence artifact must be a file inside root: {path}")
        result.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path)})
    return result


def build_report(
    root: Path,
    input_path: Path,
    output: Path,
    evidence_paths: list[Path],
) -> dict:
    root, input_path, output = root.resolve(), input_path.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    if not input_path.is_file() or not input_path.is_relative_to(root):
        raise ValueError("hold input must be a file inside root")
    rows = read_jsonl(input_path)
    if not rows:
        raise ValueError("candidate evidence hold input is empty")
    seen = set()
    for row in rows:
        identity = identity_for(row)
        if identity in seen:
            raise ValueError(f"duplicate candidate evidence hold identity: {identity}")
        seen.add(identity)
        if row.get("safe_to_merge_gold") is not False or not str(row.get("hold_reason") or ""):
            raise ValueError(f"invalid candidate evidence hold: {identity}")

    before = audit_parser.active_gold_hashes(root)
    evidence = relative_evidence(root, [input_path, *evidence_paths])
    output.mkdir(parents=True)
    rows_path = output / HOLD_ROWS
    rows_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    after = audit_parser.active_gold_hashes(root)
    if before != after:
        raise ValueError("active Gold changed while building evidence holds")
    report = {
        "goal": "Gold v2.0 Global",
        "status": "PASS",
        "hold_rows_artifact": HOLD_ROWS,
        "hold_count": len(rows),
        "holds_by_task": dict(Counter(str(row.get("task_type") or "unknown") for row in rows)),
        "evidence_artifacts": evidence,
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "output_hashes": {HOLD_ROWS: digest(rows_path)},
        "interpretation": (
            "Machine evidence holds block promotion but do not alter human decisions or active Gold. "
            "Each hold requires an explicit evidence-based resolution before release."
        ),
    }
    report_path = output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def activate(root: Path, report_path: Path) -> dict:
    pointer = root / CURRENT_HOLDS
    if pointer.is_file():
        evidence_hold_ids(root)
        config = json.loads(pointer.read_text(encoding="utf-8"))
    else:
        config = {"reports": []}
    reference = {
        "report_path": report_path.relative_to(root).as_posix(),
        "report_sha256": digest(report_path),
    }
    reports = [entry for entry in config.get("reports", []) if entry["report_path"] != reference["report_path"]]
    reports.append(reference)
    updated = {
        "schema_version": 2,
        "reports": reports,
        "resolutions": config.get("resolutions", []),
    }
    pointer.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_hold_ids(root)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    output = resolve(args.output_dir)
    report = build_report(
        root,
        resolve(args.input),
        output,
        [resolve(path) for path in args.evidence],
    )
    if args.activate:
        activate(root, output / "report.json")
    print(json.dumps({
        "status": report["status"],
        "holds": report["hold_count"],
        "active_gold_modified": report["active_gold_modified"],
        "activated": args.activate,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
