#!/usr/bin/env python3
"""Prepare evidence-pinned corrections for exact spans containing reviewed crops."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import audit_active_gold_provenance
    from audit_visualdiff_description_finality import probe_target, render_pair
    from candidate_evidence_holds import evidence_hold_ids
    from prepare_active_visualdiff_finality_corrections import (
        ACTIVE_PATHS,
        active_audit_ids,
        file_sha256,
        manifest_pair_map,
        nearby_alternative_text,
        read_jsonl,
        textlayer,
        write_jsonl,
    )
    from visualdiff_contained_span_finality import (
        BOX_TOLERANCE_PX,
        MAX_SPAN_CENTER_DISTANCE_PX,
        MIN_SPAN_COVERAGE_BY_CROP,
        POLICY_VERSION,
        qualifying_contained_span,
    )
    from visualdiff_description_finality import definitive_description, tentative_description_details
except ModuleNotFoundError:
    from tools import audit_active_gold_provenance
    from tools.audit_visualdiff_description_finality import probe_target, render_pair
    from tools.candidate_evidence_holds import evidence_hold_ids
    from tools.prepare_active_visualdiff_finality_corrections import (
        ACTIVE_PATHS,
        active_audit_ids,
        file_sha256,
        manifest_pair_map,
        nearby_alternative_text,
        read_jsonl,
        textlayer,
        write_jsonl,
    )
    from tools.visualdiff_contained_span_finality import (
        BOX_TOLERANCE_PX,
        MAX_SPAN_CENTER_DISTANCE_PX,
        MIN_SPAN_COVERAGE_BY_CROP,
        POLICY_VERSION,
        qualifying_contained_span,
    )
    from tools.visualdiff_description_finality import definitive_description, tentative_description_details


PREVIEW_MODE = "read_only_active_visualdiff_contained_span_correction_preview"


def eligibility_reasons(
    row: dict[str, Any],
    details: dict[str, str],
    expected_probe: dict[str, Any],
    opposite_probe: dict[str, Any],
    opposite_alternatives: dict[str, list[dict[str, Any]]],
    *,
    audit_ids: set[str],
    evidence_holds: set[str],
    paper_ready_docs: set[str],
    old_doc_id: str,
    new_doc_id: str,
) -> list[str]:
    reasons: list[str] = []
    pair_id = str(row.get("pair_id") or "")
    kind = str(details.get("kind") or "")
    if kind not in {"text_added", "text_removed"}:
        return ["unsupported_tentative_kind"]
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
    if not isinstance(evidence, dict):
        reasons.append("missing_review_evidence")
    elif str(evidence.get("original_human_description") or "") != str(
        row.get("change_desc_gt") or ""
    ):
        reasons.append("human_description_evidence_mismatch")
    if row.get("bbox_old") != row.get("bbox_new"):
        reasons.append("unaligned_localization_boxes")
    if row.get("page_index_old") != row.get("page_index_new"):
        reasons.append("page_index_changed")
    if not old_doc_id or not new_doc_id:
        reasons.append("missing_manifest_pair_docs")
    elif old_doc_id not in paper_ready_docs or new_doc_id not in paper_ready_docs:
        reasons.append("source_docs_not_paper_ready")

    expected_side = "new" if kind == "text_added" else "old"
    expected_bbox = row.get(f"bbox_{expected_side}")
    if not qualifying_contained_span(expected_probe, expected_bbox):
        reasons.append("expected_exact_span_does_not_contain_reviewed_crop")
    if opposite_probe.get("status") != "observed":
        reasons.append("opposite_textlayer_not_observed")
    elif int(opposite_probe.get("page_matches") or 0) != 0:
        reasons.append("opposite_side_has_exact_page_match")
    if opposite_alternatives.get("localized"):
        reasons.append("opposite_side_has_localized_alternative_text")
    if opposite_alternatives.get("related"):
        reasons.append("opposite_side_has_related_text")
    return reasons


def prepare(
    root: Path,
    output_dir: Path,
    active_audit_rechecks: Path,
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

    before = {relative: file_sha256(root / relative) for relative in ACTIVE_PATHS}
    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    pair_manifest = manifest_pair_map(root)
    provenance = audit_active_gold_provenance.build_report(root)
    paper_ready_docs = {
        str(item["doc_id"]) for item in provenance["documents"] if item.get("paper_ready")
    }
    audit_ids = active_audit_ids(audit_path)
    machine_holds = evidence_hold_ids(root)
    cache: dict[str, list[dict[str, Any]]] = {}
    evidence_hashes = {
        "manifest.jsonl": file_sha256(root / "manifest.jsonl"),
        audit_path.relative_to(root).as_posix(): file_sha256(audit_path),
    }
    selected: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []

    for row in pairs:
        details = tentative_description_details(str(row.get("change_desc_gt") or ""))
        if not details:
            continue
        manifest_row = pair_manifest.get(str(row.get("project_id") or ""), {})
        old_doc_id = str(manifest_row.get("from_doc_id") or "")
        new_doc_id = str(manifest_row.get("to_doc_id") or "")
        kind = str(details.get("kind") or "")
        target = str(details.get("target") or "")
        expected_side = "new" if kind == "text_added" else "old"
        opposite_side = "old" if expected_side == "new" else "new"
        expected_doc = new_doc_id if expected_side == "new" else old_doc_id
        opposite_doc = old_doc_id if opposite_side == "old" else new_doc_id
        expected_page = int(row.get(f"page_index_{expected_side}") or 0)
        opposite_page = int(row.get(f"page_index_{opposite_side}") or 0)
        expected_bbox = row.get(f"bbox_{expected_side}")
        opposite_bbox = row.get(f"bbox_{opposite_side}")
        expected_probe = (
            probe_target(
                textlayer(root, expected_doc, cache), target,
                expected_page, expected_bbox, radius=MAX_SPAN_CENTER_DISTANCE_PX,
            )
            if target and expected_doc
            else {"status": "missing_textlayer", "nearby_matches": 0, "best": None}
        )
        opposite_probe = (
            probe_target(
                textlayer(root, opposite_doc, cache), target,
                opposite_page, opposite_bbox, radius=MAX_SPAN_CENTER_DISTANCE_PX,
            )
            if target and opposite_doc
            else {"status": "missing_textlayer", "nearby_matches": 0, "best": None}
        )
        alternatives = (
            nearby_alternative_text(
                textlayer(root, opposite_doc, cache), target, opposite_page, opposite_bbox
            )
            if target and opposite_doc
            else {"localized": [], "related": []}
        )
        reasons = eligibility_reasons(
            row, details, expected_probe, opposite_probe, alternatives,
            audit_ids=audit_ids,
            evidence_holds=machine_holds,
            paper_ready_docs=paper_ready_docs,
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
        )
        finding = {
            "pair_id": row["pair_id"],
            "project_id": row.get("project_id"),
            "split": row.get("split"),
            "kind": kind,
            "target": target,
            "expected_side": expected_side,
            "original_description": row.get("change_desc_gt"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "expected_probe": expected_probe,
            "opposite_probe": opposite_probe,
            "opposite_alternative_text": alternatives,
            "reasons": reasons,
        }
        if reasons:
            withheld.append(finding)
            continue
        for path in (
            root / "derived" / "textlayer" / f"{old_doc_id}.jsonl",
            root / "derived" / "textlayer" / f"{new_doc_id}.jsonl",
            root / str(row.get("image_old")),
            root / str(row.get("image_new")),
        ):
            if path.is_file():
                evidence_hashes[path.relative_to(root).as_posix()] = file_sha256(path)
        selected.append({
            "goal": "Gold v2.0 Global",
            "policy_version": POLICY_VERSION,
            "pair_id": row["pair_id"],
            "project_id": row.get("project_id"),
            "split": row.get("split"),
            "kind": kind,
            "target": target,
            "expected_side": expected_side,
            "original_description": row["change_desc_gt"],
            "final_description": definitive_description(details),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "image_old": row.get("image_old"),
            "image_new": row.get("image_new"),
            "bbox_old": row.get("bbox_old"),
            "bbox_new": row.get("bbox_new"),
            "page_index_old": row.get("page_index_old"),
            "page_index_new": row.get("page_index_new"),
            "expected_probe": expected_probe,
            "opposite_probe": opposite_probe,
            "opposite_alternative_text": alternatives,
            "safe_to_apply": True,
        })

    output_dir.mkdir(parents=True)
    evidence_dir = output_dir / "evidence"
    if render_evidence:
        evidence_dir.mkdir()
        pair_by_id = {item["pair_id"]: item for item in pairs}
        for correction in selected:
            row = pair_by_id[correction["pair_id"]]
            old_probe, new_probe = (
                (correction["opposite_probe"], correction["expected_probe"])
                if correction["expected_side"] == "new"
                else (correction["expected_probe"], correction["opposite_probe"])
            )
            rendered = render_pair(root, evidence_dir, {
                "pair_id": correction["pair_id"],
                "old": {
                    **old_probe,
                    "image_path": row["image_old"],
                    "original_bbox": row["bbox_old"],
                },
                "new": {
                    **new_probe,
                    "image_path": row["image_new"],
                    "original_bbox": row["bbox_new"],
                },
            })
            correction["rendered_evidence"] = rendered
            evidence_hashes[rendered["path"]] = rendered["sha256"]

    corrections_path = output_dir / "corrections.jsonl"
    withheld_path = output_dir / "withheld.jsonl"
    write_jsonl(corrections_path, selected)
    write_jsonl(withheld_path, withheld)
    after = {relative: file_sha256(root / relative) for relative in ACTIVE_PATHS}
    reason_counts = Counter(reason for item in withheld for reason in item["reasons"])
    report = {
        "goal": "Gold v2.0 Global",
        "mode": PREVIEW_MODE,
        "status": "PASS" if selected and before == after else "FAIL",
        "policy_version": POLICY_VERSION,
        "ready_for_apply": bool(selected) and before == after,
        "counts": {
            "tentative_rows": len(selected) + len(withheld),
            "prepared_corrections": len(selected),
            "withheld_rows": len(withheld),
        },
        "prepared_pair_ids": [item["pair_id"] for item in selected],
        "withheld_reason_counts": dict(sorted(reason_counts.items())),
        "criteria": {
            "human_edit_and_high_confidence_required": True,
            "paper_ready_source_docs_required": True,
            "active_audit_and_evidence_holds_excluded": True,
            "unique_exact_page_span_required": True,
            "exact_span_must_contain_complete_reviewed_crop": True,
            "box_tolerance_px": BOX_TOLERANCE_PX,
            "maximum_span_center_distance_px": MAX_SPAN_CENTER_DISTANCE_PX,
            "minimum_span_coverage_by_crop": MIN_SPAN_COVERAGE_BY_CROP,
            "opposite_page_exact_localized_and_related_text_excluded": True,
        },
        "active_file_hashes_before": before,
        "active_file_hashes_after": after,
        "active_gold_modified": before != after,
        "active_audit_rechecks": audit_path.relative_to(root).as_posix(),
        "active_audit_rechecks_sha256": file_sha256(audit_path),
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
            "This certifies only one unique exact PDF text span that fully contains a "
            "substantial reviewed crop. Opposite-side exact, local, related, replacement, "
            "audit-held, graphical, or ambiguous cases remain held."
        ),
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
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = prepare(
        root, root / args.output_dir, root / args.active_audit_rechecks,
        render_evidence=not args.no_render,
    )
    print(json.dumps({
        "status": report["status"],
        "ready_for_apply": report["ready_for_apply"],
        **report["counts"],
        "prepared_pair_ids": report["prepared_pair_ids"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
