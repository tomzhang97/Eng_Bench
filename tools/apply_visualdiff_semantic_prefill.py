#!/usr/bin/env python3
"""Apply evidence-bound machine semantic prefills to a VisualDiff review queue."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


PREFILL_PASSTHROUGH_FIELDS = (
    "machine_qa_status",
    "machine_qa_notes",
    "reserved_split",
    "source_attribution_path",
    "source_doc_ids",
    "source_public_status",
    "source_rights_check",
    "source_sha256_by_doc",
    "split_reservation_basis",
    "split_reservation_reference",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_unique(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        pair_id = str(row.get("pair_id") or "")
        if not pair_id:
            raise ValueError(f"{label}: missing pair_id")
        if pair_id in indexed:
            raise ValueError(f"{label}: duplicate pair_id {pair_id}")
        indexed[pair_id] = row
    return indexed


def select_queue_rows(
    rows: list[dict[str, Any]], selected_pair_ids: list[str] | None
) -> list[dict[str, Any]]:
    if not selected_pair_ids:
        return rows
    selected = {str(value).strip() for value in selected_pair_ids if str(value).strip()}
    if len(selected) != len(selected_pair_ids):
        raise ValueError("selected pair IDs must be nonblank and unique")
    indexed = index_unique(rows, "queue")
    missing = sorted(selected - indexed.keys())
    if missing:
        raise ValueError(f"selected pair IDs missing from queue: {', '.join(missing)}")
    return [row for row in rows if str(row.get("pair_id") or "") in selected]


def validate_decision(row: dict[str, Any]) -> None:
    required = {
        "pair_id",
        "description",
        "machine_semantic_class",
        "machine_recommendation",
        "confidence",
        "evidence_sheet",
    }
    missing = sorted(key for key in required if not str(row.get(key) or "").strip())
    if missing:
        raise ValueError(f"{row.get('pair_id', '<unknown>')}: missing {', '.join(missing)}")
    if row["confidence"] not in {"high", "medium"}:
        raise ValueError(f"{row['pair_id']}: unsupported confidence {row['confidence']}")
    proposed_change_type = str(row.get("proposed_change_type") or "").strip()
    if proposed_change_type and proposed_change_type not in {
        "addition+text",
        "deletion+text",
        "text",
        "symbol",
        "layout",
    }:
        raise ValueError(
            f"{row['pair_id']}: unsupported proposed_change_type {proposed_change_type}"
        )
    reserved_split = str(row.get("reserved_split") or "").strip()
    if reserved_split and reserved_split not in {"train", "dev", "test"}:
        raise ValueError(f"{row['pair_id']}: unsupported reserved_split {reserved_split}")
    rights_check = str(row.get("source_rights_check") or "").strip()
    if rights_check and rights_check != "release_safe_status":
        raise ValueError(f"{row['pair_id']}: unsupported source_rights_check {rights_check}")
    source_doc_ids = row.get("source_doc_ids")
    source_hashes = row.get("source_sha256_by_doc")
    if rights_check and source_doc_ids is None:
        raise ValueError(f"{row['pair_id']}: release-safe decision missing source_doc_ids")
    if source_doc_ids is not None:
        if (
            not isinstance(source_doc_ids, list)
            or len(source_doc_ids) != 2
            or any(not str(value).strip() for value in source_doc_ids)
        ):
            raise ValueError(f"{row['pair_id']}: source_doc_ids must contain two IDs")
        if not isinstance(source_hashes, dict) or set(source_hashes) != set(source_doc_ids):
            raise ValueError(f"{row['pair_id']}: source SHA map must match source_doc_ids")
        if any(
            len(str(value)) != 64
            or any(character not in "0123456789abcdef" for character in str(value).lower())
            for value in source_hashes.values()
        ):
            raise ValueError(f"{row['pair_id']}: invalid source SHA-256")


def verify_evidence_files(
    root: Path, decisions: list[dict[str, Any]]
) -> dict[str, str]:
    verified: dict[str, str] = {}
    for row in decisions:
        pair_id = str(row.get("pair_id") or "<unknown>")
        value = str(row.get("evidence_sheet") or "").strip()
        if not value:
            raise ValueError(f"{pair_id}: missing evidence_sheet")
        path = Path(value)
        full = path if path.is_absolute() else root / path
        if not full.is_file():
            raise ValueError(f"{pair_id}: evidence file missing: {value}")
        verified[value] = sha256(full)
    return dict(sorted(verified.items()))


def verify_attribution_files(
    root: Path, decisions: list[dict[str, Any]]
) -> dict[str, str]:
    verified: dict[str, str] = {}
    for row in decisions:
        if str(row.get("source_rights_check") or "").strip() != "release_safe_status":
            continue
        pair_id = str(row.get("pair_id") or "<unknown>")
        value = str(row.get("source_attribution_path") or "").strip()
        if not value:
            raise ValueError(f"{pair_id}: release-safe decision missing source_attribution_path")
        path = Path(value)
        full = path if path.is_absolute() else root / path
        if not full.is_file():
            raise ValueError(f"{pair_id}: attribution file missing: {value}")
        verified[value] = sha256(full)
    return dict(sorted(verified.items()))


def verify_source_files(
    root: Path, decisions: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    manifest_rows = load_jsonl(root / "manifest.jsonl")
    manifest_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in manifest_rows
        if str(row.get("type") or "doc").strip().lower() == "doc"
        and str(row.get("doc_id") or "").strip()
    }
    verified: dict[str, dict[str, str]] = {}
    for decision in decisions:
        pair_id = str(decision.get("pair_id") or "<unknown>")
        doc_ids = decision.get("source_doc_ids") or []
        declared_hashes = decision.get("source_sha256_by_doc") or {}
        for raw_doc_id in doc_ids:
            doc_id = str(raw_doc_id).strip()
            manifest_row = manifest_docs.get(doc_id)
            if manifest_row is None:
                raise ValueError(f"{pair_id}: source doc missing from manifest: {doc_id}")
            declared_sha = str(declared_hashes.get(doc_id) or "").lower()
            manifest_sha = str(manifest_row.get("sha256") or "").lower()
            if declared_sha != manifest_sha:
                raise ValueError(f"{pair_id}: source SHA disagrees with manifest: {doc_id}")
            source_path = str(manifest_row.get("path") or "").strip()
            full = root / source_path
            if not source_path or not full.is_file():
                raise ValueError(f"{pair_id}: source file missing: {source_path or doc_id}")
            actual_sha = sha256(full)
            if actual_sha != declared_sha:
                raise ValueError(f"{pair_id}: source file SHA mismatch: {doc_id}")
            declared_status = str(decision.get("source_public_status") or "").strip()
            manifest_status = str(manifest_row.get("public_status") or "").strip()
            if declared_status and declared_status != manifest_status:
                raise ValueError(f"{pair_id}: source public status mismatch: {doc_id}")
            verified[doc_id] = {
                "path": source_path,
                "public_status": manifest_status,
                "sha256": actual_sha,
            }
    return dict(sorted(verified.items()))


def apply_prefills(
    queue_rows: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    required_source: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue_by_id = index_unique(queue_rows, "queue")
    decision_by_id = index_unique(decisions, "decisions")
    target_ids = {
        pair_id
        for pair_id, row in queue_by_id.items()
        if str(row.get("description_source") or "") == required_source
    }
    missing = sorted(target_ids - decision_by_id.keys())
    extra = sorted(decision_by_id.keys() - target_ids)
    if missing or extra:
        raise ValueError(
            f"decision coverage mismatch: missing={len(missing)} extra={len(extra)}"
        )
    for decision in decisions:
        validate_decision(decision)

    output: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    recommendation_counts: Counter[str] = Counter()
    for row in queue_rows:
        pair_id = str(row.get("pair_id") or "")
        decision = decision_by_id.get(pair_id)
        if decision is None:
            output.append(dict(row))
            continue
        out = dict(row)
        out["description"] = str(decision["description"])
        out["description_source"] = "machine_visual_semantic_prefill_candidate"
        out["review_confidence"] = f"machine_{decision['confidence']}_candidate"
        out["machine_semantic_prefill"] = {
            "class": str(decision["machine_semantic_class"]),
            "recommendation": str(decision["machine_recommendation"]),
            "confidence": str(decision["confidence"]),
            "evidence_sheet": str(decision["evidence_sheet"]),
            "human_confirmation_required": True,
        }
        if str(decision.get("proposed_change_type") or "").strip():
            out["change_type"] = str(decision["proposed_change_type"]).strip()
            out["machine_semantic_prefill"]["proposed_change_type"] = out["change_type"]
        for text_key in ("old_text", "new_text"):
            if text_key in decision:
                out[text_key] = str(decision.get(text_key) or "")
                out["machine_semantic_prefill"][text_key] = out[text_key]
        for field in PREFILL_PASSTHROUGH_FIELDS:
            if field in decision:
                out[field] = decision[field]
        out["review_status"] = "needs_review"
        out["human_review_status"] = "unassigned"
        out["safe_to_merge_gold"] = False
        output.append(out)
        class_counts[str(decision["machine_semantic_class"])] += 1
        recommendation_counts[str(decision["machine_recommendation"])] += 1

    report = {
        "queue_rows": len(queue_rows),
        "target_rows": len(target_ids),
        "prefilled_rows": len(decisions),
        "classes": dict(sorted(class_counts.items())),
        "recommendations": dict(sorted(recommendation_counts.items())),
        "human_confirmation_required": len(decisions),
        "human_reviewed_rows": 0,
        "safe_to_merge_gold_rows": 0,
        "active_gold_rows_modified": 0,
        "valid": True,
        "interpretation": "Machine descriptions are reviewer prefills only. Human confirmation remains required before promotion.",
    }
    return output, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--required-description-source",
        default="machine_visual_candidate_missing_textlayer",
    )
    parser.add_argument(
        "--pair-id",
        action="append",
        dest="pair_ids",
        help="Prefill only this pair ID; repeat for a strict selected subset.",
    )
    return parser.parse_args()


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    input_path = resolve(root, args.input)
    decisions_path = resolve(root, args.decisions)
    output_path = resolve(root, args.output)
    report_path = resolve(root, args.report)
    input_rows = load_jsonl(input_path)
    selected_rows = select_queue_rows(input_rows, args.pair_ids)
    decision_rows = load_jsonl(decisions_path)
    evidence_hashes = verify_evidence_files(root, decision_rows)
    attribution_hashes = verify_attribution_files(root, decision_rows)
    source_verification = verify_source_files(root, decision_rows)
    output_rows, report = apply_prefills(
        selected_rows,
        decision_rows,
        args.required_description_source,
    )
    write_jsonl(output_path, output_rows)
    report.update(
        {
            "input": input_path.relative_to(root).as_posix(),
            "input_sha256": sha256(input_path),
            "input_rows": len(input_rows),
            "selected_pair_ids": sorted(args.pair_ids or []),
            "decisions": decisions_path.relative_to(root).as_posix(),
            "decisions_sha256": sha256(decisions_path),
            "evidence_files_verified": len(evidence_hashes),
            "evidence_sha256": evidence_hashes,
            "attribution_files_verified": len(attribution_hashes),
            "attribution_sha256": attribution_hashes,
            "source_files_verified": len(source_verification),
            "source_verification": source_verification,
            "output": output_path.relative_to(root).as_posix(),
            "output_sha256": sha256(output_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
