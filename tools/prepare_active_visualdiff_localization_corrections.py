#!/usr/bin/env python3
"""Prepare exact, evidence-pinned English localizations for active VisualDiff rows."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import audit_active_gold_provenance
    from audit_visualdiff_description_finality import render_pair
    from candidate_evidence_holds import evidence_hold_ids
    from prepare_active_visualdiff_finality_corrections import active_audit_ids
    from visualdiff_human_localization import (
        DESC_SOURCE,
        EXACT_LOCALIZATIONS,
        METHOD,
        POLICY_VERSION,
        PREVIEW_MODE,
        localized_description,
    )
except ModuleNotFoundError:
    from tools import audit_active_gold_provenance
    from tools.audit_visualdiff_description_finality import render_pair
    from tools.candidate_evidence_holds import evidence_hold_ids
    from tools.prepare_active_visualdiff_finality_corrections import active_audit_ids
    from tools.visualdiff_human_localization import (
        DESC_SOURCE,
        EXACT_LOCALIZATIONS,
        METHOD,
        POLICY_VERSION,
        PREVIEW_MODE,
        localized_description,
    )


ACTIVE_PATHS = (
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "eng_bench.jsonl",
    "manifest.jsonl",
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
)


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def eligibility_reasons(
    row: dict[str, Any],
    question: dict[str, Any] | None,
    *,
    audit_ids: set[str],
    evidence_holds: set[str],
    paper_ready_docs: set[str],
    raster_confirmed_ids: set[str],
    old_doc_id: str,
    new_doc_id: str,
) -> list[str]:
    reasons: list[str] = []
    pair_id = str(row.get("pair_id") or "")
    original = str(row.get("change_desc_gt") or "")
    if original not in EXACT_LOCALIZATIONS:
        reasons.append("unsupported_human_localization_text")
    if pair_id in audit_ids:
        reasons.append("active_audit_flag")
    if pair_id in evidence_holds:
        reasons.append("machine_evidence_hold")
    if str(row.get("desc_source") or "").lower() != "human":
        reasons.append("description_not_human_sourced")
    if str(row.get("review_status") or "").lower() != "edit":
        reasons.append("review_status_not_edit")
    if str(row.get("human_review_status") or "").lower() != "edit":
        reasons.append("human_review_status_not_edit")
    if str(row.get("review_confidence") or "").lower() != "high":
        reasons.append("review_confidence_not_high")
    source = row.get("source") or {}
    if not isinstance(source, dict) or source.get("type") != "human_reviewed_source_expansion":
        reasons.append("unsupported_source_contract")
    evidence = row.get("review_evidence") or {}
    if not isinstance(evidence, dict) or str(evidence.get("original_human_description") or "") != original:
        reasons.append("human_description_evidence_mismatch")
    if pair_id not in raster_confirmed_ids:
        reasons.append("raster_semantic_confirmation_missing")
    if row.get("bbox_old") != row.get("bbox_new"):
        reasons.append("unaligned_localization_boxes")
    if "deletion" not in {str(value).lower() for value in row.get("change_type") or []}:
        reasons.append("change_type_not_deletion")
    if question is None:
        reasons.append("active_question_missing")
    elif str(question.get("answer_text") or "") != original:
        reasons.append("active_question_answer_mismatch")
    if not old_doc_id or not new_doc_id:
        reasons.append("missing_manifest_pair_docs")
    elif old_doc_id not in paper_ready_docs or new_doc_id not in paper_ready_docs:
        reasons.append("source_docs_not_paper_ready")
    return sorted(dict.fromkeys(reasons))


def prepare(
    root: Path,
    output_dir: Path,
    active_audit_rechecks: Path,
    raster_confirmations: Path,
    *,
    render_evidence: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    allowed = (root / "derived" / "quality").resolve()
    if allowed not in output_dir.parents or output_dir.exists():
        raise ValueError("output_dir must be a new directory under derived/quality")
    audit_path = active_audit_rechecks.resolve()
    if not audit_path.is_file():
        raise ValueError(f"active audit recheck file is missing: {audit_path}")
    confirmation_path = raster_confirmations.resolve()
    if not confirmation_path.is_file() or not confirmation_path.is_relative_to(
        root / "derived" / "quality"
    ):
        raise ValueError("raster confirmations must be a file under derived/quality")
    confirmation_spec = json.loads(confirmation_path.read_text(encoding="utf-8"))
    if confirmation_spec.get("schema_version") != 1:
        raise ValueError("unsupported raster confirmation schema")
    confirmations = confirmation_spec.get("confirmations")
    if not isinstance(confirmations, list) or not confirmations:
        raise ValueError("raster confirmations are empty")
    raster_confirmed_ids: set[str] = set()
    confirmation_evidence_hashes: dict[str, str] = {}
    for confirmation in confirmations:
        pair_id = str(confirmation.get("pair_id") or "")
        if not pair_id or pair_id in raster_confirmed_ids:
            raise ValueError(f"missing or duplicate raster confirmation identity: {pair_id}")
        if confirmation.get("status") != "confirmed_change":
            raise ValueError(f"unsupported raster confirmation status: {pair_id}")
        evidence_relative = str(confirmation.get("evidence_path") or "").replace("\\", "/")
        evidence_path = (root / evidence_relative).resolve()
        expected_hash = str(confirmation.get("evidence_sha256") or "").lower()
        if (
            not evidence_relative.startswith("derived/quality/")
            or not evidence_path.is_file()
            or not evidence_path.is_relative_to(root / "derived" / "quality")
            or file_sha256(evidence_path).lower() != expected_hash
        ):
            raise ValueError(f"raster confirmation evidence mismatch: {pair_id}")
        if not str(confirmation.get("observation") or "").strip():
            raise ValueError(f"raster confirmation observation missing: {pair_id}")
        raster_confirmed_ids.add(pair_id)
        confirmation_evidence_hashes[evidence_relative] = expected_hash

    before = {relative: file_sha256(root / relative) for relative in ACTIVE_PATHS}
    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    questions = {
        str(row.get("pair_id") or ""): row
        for row in read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    }
    manifest = {
        str(row["pair_id"]): row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "pair" and row.get("pair_id")
    }
    provenance = audit_active_gold_provenance.build_report(root)
    paper_ready_docs = {
        str(row["doc_id"]) for row in provenance["documents"] if row.get("paper_ready")
    }
    audit_ids = active_audit_ids(audit_path)
    machine_holds = evidence_hold_ids(root)
    evidence_hashes = {
        "manifest.jsonl": file_sha256(root / "manifest.jsonl"),
        audit_path.relative_to(root).as_posix(): file_sha256(audit_path),
        confirmation_path.relative_to(root).as_posix(): file_sha256(confirmation_path),
        **confirmation_evidence_hashes,
    }
    selected: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []

    for row in pairs:
        original = str(row.get("change_desc_gt") or "")
        if original not in EXACT_LOCALIZATIONS:
            continue
        pair_id = str(row.get("pair_id") or "")
        manifest_row = manifest.get(str(row.get("project_id") or ""), {})
        old_doc_id = str(manifest_row.get("from_doc_id") or "")
        new_doc_id = str(manifest_row.get("to_doc_id") or "")
        reasons = eligibility_reasons(
            row,
            questions.get(pair_id),
            audit_ids=audit_ids,
            evidence_holds=machine_holds,
            paper_ready_docs=paper_ready_docs,
            raster_confirmed_ids=raster_confirmed_ids,
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
        )
        finding = {
            "pair_id": pair_id,
            "split": row.get("split"),
            "original_description": original,
            "final_description": localized_description(original),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "image_old": row.get("image_old"),
            "image_new": row.get("image_new"),
            "bbox_old": row.get("bbox_old"),
            "bbox_new": row.get("bbox_new"),
            "page_index_old": row.get("page_index_old"),
            "page_index_new": row.get("page_index_new"),
            "reasons": reasons,
        }
        if reasons:
            withheld.append(finding)
            continue
        for relative in (row.get("image_old"), row.get("image_new")):
            path = root / str(relative or "")
            if path.is_file():
                evidence_hashes[path.relative_to(root).as_posix()] = file_sha256(path)
        selected.append({
            "goal": "Gold v2.0 Global",
            "policy_version": POLICY_VERSION,
            "pair_id": pair_id,
            "split": row.get("split"),
            "kind": "human_reviewed_deletion_localization",
            "target": "",
            "original_description": original,
            "final_description": localized_description(original),
            "project_id": row.get("project_id"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "image_old": row.get("image_old"),
            "image_new": row.get("image_new"),
            "bbox_old": row.get("bbox_old"),
            "bbox_new": row.get("bbox_new"),
            "page_index_old": row.get("page_index_old"),
            "page_index_new": row.get("page_index_new"),
            "human_evidence": {
                "desc_source": row.get("desc_source"),
                "review_status": row.get("review_status"),
                "human_review_status": row.get("human_review_status"),
                "review_confidence": row.get("review_confidence"),
                "original_human_description": (row.get("review_evidence") or {}).get(
                    "original_human_description"
                ),
            },
            "localization_method": METHOD,
            "safe_to_apply": True,
        })

    output_dir.mkdir(parents=True)
    evidence_dir = output_dir / "evidence"
    evidence_rows = [*selected, *withheld]
    if render_evidence and evidence_rows:
        evidence_dir.mkdir()
        for correction in evidence_rows:
            finding = {
                "pair_id": correction["pair_id"],
                "old": {
                    "best": None,
                    "image_path": correction["image_old"],
                    "original_bbox": correction["bbox_old"],
                },
                "new": {
                    "best": None,
                    "image_path": correction["image_new"],
                    "original_bbox": correction["bbox_new"],
                },
            }
            rendered = render_pair(root, evidence_dir, finding)
            correction["rendered_evidence"] = rendered
            evidence_hashes[rendered["path"]] = rendered["sha256"]

    corrections_path = output_dir / "corrections.jsonl"
    withheld_path = output_dir / "withheld.jsonl"
    write_jsonl(corrections_path, selected)
    write_jsonl(withheld_path, withheld)
    after = {relative: file_sha256(root / relative) for relative in ACTIVE_PATHS}
    reason_counts = Counter(reason for row in withheld for reason in row["reasons"])
    report = {
        "goal": "Gold v2.0 Global",
        "mode": PREVIEW_MODE,
        "status": "PASS" if selected and before == after else "FAIL",
        "policy_version": POLICY_VERSION,
        "ready_for_apply": bool(selected) and before == after,
        "counts": {
            "localizable_rows": len(selected) + len(withheld),
            "prepared_corrections": len(selected),
            "withheld_rows": len(withheld),
        },
        "withheld_reason_counts": dict(sorted(reason_counts.items())),
        "active_audit_flag_count": len(audit_ids),
        "machine_evidence_hold_count": len(machine_holds),
        "provenance": {
            "active_source_docs": provenance["totals"]["active_source_docs"],
            "paper_ready_docs": provenance["totals"]["paper_ready_docs"],
        },
        "criteria": {
            "exact_translation_dictionary_only": True,
            "raster_semantic_confirmation_required": True,
            "human_edit_required": True,
            "high_review_confidence_required": True,
            "aligned_boxes_required": True,
            "paper_ready_source_docs_required": True,
            "active_audit_flags_excluded": True,
            "machine_evidence_holds_excluded": True,
        },
        "active_file_hashes_before": before,
        "active_file_hashes_after": after,
        "active_gold_modified": before != after,
        "artifacts": {
            "corrections": corrections_path.relative_to(root).as_posix(),
            "withheld": withheld_path.relative_to(root).as_posix(),
        },
        "artifact_sha256": {
            "corrections": file_sha256(corrections_path),
            "withheld": file_sha256(withheld_path),
        },
        "evidence_hashes": dict(sorted(evidence_hashes.items())),
        "gold_rows_modified": 0,
        "limitation": (
            "This preview translates an exact human-reviewed sentence without adding "
            "object identity or design-intent claims. It does not resolve evidence holds."
        ),
        "description_source_after_apply": DESC_SOURCE,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--active-audit-rechecks", type=Path, required=True)
    parser.add_argument("--raster-confirmations", type=Path, required=True)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = prepare(
        root,
        root / args.output_dir,
        root / args.active_audit_rechecks,
        root / args.raster_confirmations,
        render_evidence=not args.no_render,
    )
    print(json.dumps({
        "status": report["status"],
        "ready_for_apply": report["ready_for_apply"],
        **report["counts"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
