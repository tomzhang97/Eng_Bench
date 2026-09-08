#!/usr/bin/env python3
"""Register a source candidate and link existing manifest documents atomically."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any


CANDIDATE_PATH = Path("SOURCE_CANDIDATES.csv")
RANKED_PATH = Path("SOURCE_CANDIDATES_RANKED.csv")
VALIDATION_PATH = Path("SOURCE_CANDIDATE_VALIDATION.csv")
MANIFEST_PATH = Path("manifest.jsonl")


def load_readiness_module() -> Any:
    tools_dir = Path(__file__).resolve().parent
    tools_value = str(tools_dir)
    if tools_value not in sys.path:
        sys.path.insert(0, tools_value)
    return importlib.import_module("audit_source_conversion_readiness")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"missing CSV header: {path}")
        return list(reader.fieldnames), [dict(row) for row in reader]


def write_csv_atomic(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    temporary.replace(path)


def upsert_unique(
    rows: list[dict[str, str]],
    key: str,
    value: str,
    replacement: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    matches = [index for index, row in enumerate(rows) if row.get(key) == value]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}={value!r}")
    output: list[dict[str, Any]] = [dict(row) for row in rows]
    if matches:
        output[matches[0]] = dict(replacement)
        return output, False
    output.append(dict(replacement))
    return output, True


def read_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def link_manifest_docs(
    rows: list[dict[str, Any]],
    doc_ids: list[str],
    candidate_id: str,
) -> tuple[list[dict[str, Any]], int]:
    requested = set(doc_ids)
    found: set[str] = set()
    changed = 0
    output: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        doc_id = str(row.get("doc_id") or "")
        if row.get("type") == "doc" and doc_id in requested:
            found.add(doc_id)
            existing = str(row.get("source_candidate_id") or "")
            if existing and existing != candidate_id:
                raise ValueError(
                    f"manifest doc {doc_id} already links to {existing}, not {candidate_id}"
                )
            if existing != candidate_id:
                row["source_candidate_id"] = candidate_id
                changed += 1
        output.append(row)
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"manifest documents not found: {missing}")
    return output, changed


def validate_spec(spec: dict[str, Any]) -> tuple[dict[str, str], list[str], int, dict[str, str]]:
    candidate = spec.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError("spec.candidate must be an object")
    candidate = {str(key): str(value) for key, value in candidate.items()}
    candidate_id = candidate.get("candidate_id", "").strip()
    if not candidate_id:
        raise ValueError("candidate_id is required")
    doc_ids = [str(value).strip() for value in spec.get("doc_ids", []) if str(value).strip()]
    if not doc_ids or len(doc_ids) != len(set(doc_ids)):
        raise ValueError("doc_ids must be a non-empty unique list")
    priority_score = int(spec.get("priority_score") or 0)
    validation = spec.get("validation") or {}
    if not isinstance(validation, dict):
        raise ValueError("spec.validation must be an object")
    validation = {str(key): str(value) for key, value in validation.items()}
    if not validation.get("release_posture"):
        raise ValueError("validation.release_posture is required")
    return candidate, doc_ids, priority_score, validation


def register(root: Path, spec_path: Path, date_label: str) -> dict[str, Any]:
    root = root.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    candidate, doc_ids, priority_score, validation_overrides = validate_spec(spec)
    candidate_id = candidate["candidate_id"]
    paths = {
        "candidates": root / CANDIDATE_PATH,
        "ranked": root / RANKED_PATH,
        "validation": root / VALIDATION_PATH,
        "manifest": root / MANIFEST_PATH,
    }
    before_hashes = {name: sha256(path) for name, path in paths.items()}

    candidate_fields, candidate_rows = read_csv(paths["candidates"])
    missing_candidate_fields = sorted(set(candidate_fields) - set(candidate))
    if missing_candidate_fields:
        raise ValueError(f"candidate spec missing fields: {missing_candidate_fields}")
    candidate_rows, candidate_added = upsert_unique(
        candidate_rows,
        "candidate_id",
        candidate_id,
        candidate,
    )

    ranked_fields, ranked_rows = read_csv(paths["ranked"])
    ranked = dict(candidate)
    ranked["priority_score"] = str(priority_score)
    ranked_rows, ranked_added = upsert_unique(
        ranked_rows,
        "candidate_id",
        candidate_id,
        ranked,
    )

    validation_fields, validation_rows = read_csv(paths["validation"])
    seed = {field: "" for field in validation_fields}
    seed.update(
        {
            "candidate_id": candidate_id,
            "domain": candidate.get("domain", ""),
            "task_fit": candidate.get("task_fit", ""),
            "rights_tier": candidate.get("rights_tier", ""),
            "imported_or_staged": "True",
            "linked_local_source_count": str(len(doc_ids)),
            "linked_inventory_source_count": str(len(doc_ids)),
            "linked_local_doc_ids": ";".join(doc_ids),
            "lineage_evidence": "manifest_source_candidate_id",
            "source_url": candidate.get("source_url", ""),
            "notes": candidate.get("notes", ""),
        }
    )
    seed.update(validation_overrides)
    validation_rows, validation_added = upsert_unique(
        validation_rows,
        "candidate_id",
        candidate_id,
        seed,
    )

    manifest_rows = read_manifest(paths["manifest"])
    manifest_rows, manifest_docs_changed = link_manifest_docs(
        manifest_rows,
        doc_ids,
        candidate_id,
    )

    write_csv_atomic(paths["candidates"], candidate_fields, candidate_rows)
    write_csv_atomic(paths["ranked"], ranked_fields, ranked_rows)
    write_csv_atomic(paths["validation"], validation_fields, validation_rows)
    write_jsonl_atomic(paths["manifest"], manifest_rows)

    readiness_report = load_readiness_module().build_report(
        root,
        date_label=date_label,
    )
    computed_rows = [
        row
        for row in readiness_report.get("candidate_sources", [])
        if row.get("candidate_id") == candidate_id
    ]
    if len(computed_rows) != 1:
        raise ValueError(
            f"readiness audit returned {len(computed_rows)} rows for {candidate_id}"
        )
    computed = dict(computed_rows[0])
    computed.update(validation_overrides)
    validation_fields, validation_rows = read_csv(paths["validation"])
    validation_rows, _ = upsert_unique(
        validation_rows,
        "candidate_id",
        candidate_id,
        computed,
    )
    write_csv_atomic(paths["validation"], validation_fields, validation_rows)

    after_hashes = {name: sha256(path) for name, path in paths.items()}
    linked_manifest_docs = sorted(
        str(row.get("doc_id"))
        for row in read_manifest(paths["manifest"])
        if row.get("type") == "doc"
        and row.get("source_candidate_id") == candidate_id
    )
    if linked_manifest_docs != sorted(doc_ids):
        raise ValueError(
            f"manifest linkage mismatch: expected {sorted(doc_ids)}, got {linked_manifest_docs}"
        )
    return {
        "goal": "Gold v2.0 Global",
        "mode": "source_provenance_registration",
        "candidate_id": candidate_id,
        "candidate_added": candidate_added,
        "ranked_candidate_added": ranked_added,
        "validation_added": validation_added,
        "manifest_docs_changed": manifest_docs_changed,
        "linked_manifest_docs": linked_manifest_docs,
        "computed_validation": computed,
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "active_gold_rows_modified": 0,
        "valid": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    spec_path = args.spec if args.spec.is_absolute() else root / args.spec
    report = register(root, spec_path, args.date_label)
    report_path = (
        args.report_json if args.report_json.is_absolute() else root / args.report_json
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
