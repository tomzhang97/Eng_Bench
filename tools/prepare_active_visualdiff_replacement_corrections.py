#!/usr/bin/env python3
"""Prepare evidence-pinned corrections for same-slot text replacements."""
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
    from visualdiff_description_finality import tentative_description_details
    from visualdiff_replacement_finality import (
        MAX_BOX_BIND_DISTANCE_PX,
        MIN_RELATED_TEXT_SIMILARITY,
        POLICY_VERSION,
        normalized_text,
        replacement_description,
        same_slot_matches,
        texts_are_related,
    )
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
    from tools.visualdiff_description_finality import tentative_description_details
    from tools.visualdiff_replacement_finality import (
        MAX_BOX_BIND_DISTANCE_PX,
        MIN_RELATED_TEXT_SIMILARITY,
        POLICY_VERSION,
        normalized_text,
        replacement_description,
        same_slot_matches,
        texts_are_related,
    )


PREVIEW_MODE = "read_only_active_visualdiff_replacement_correction_preview"


def eligibility_reasons(
    row: dict[str, Any],
    details: dict[str, str],
    expected_probe: dict[str, Any],
    alternative_probe: dict[str, Any],
    localized_alternatives: list[dict[str, Any]],
    *,
    old_text: str,
    new_text: str,
    audit_ids: set[str],
    evidence_holds: set[str],
    paper_ready_docs: set[str],
    old_doc_id: str,
    new_doc_id: str,
) -> list[str]:
    reasons: list[str] = []
    pair_id = str(row.get("pair_id") or "")
    kind = str(details.get("kind") or "")
    target = str(details.get("target") or "")
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
    elif str(evidence.get("original_human_description") or "") != str(row.get("change_desc_gt") or ""):
        reasons.append("human_description_evidence_mismatch")
    if row.get("bbox_old") != row.get("bbox_new"):
        reasons.append("unaligned_localization_boxes")
    if row.get("page_index_old") != row.get("page_index_new"):
        reasons.append("page_index_changed")
    if not old_doc_id or not new_doc_id:
        reasons.append("missing_manifest_pair_docs")
    elif old_doc_id not in paper_ready_docs or new_doc_id not in paper_ready_docs:
        reasons.append("source_docs_not_paper_ready")
    if target != (new_text if kind == "text_added" else old_text):
        reasons.append("tentative_target_side_mismatch")
    if len(localized_alternatives) != 1:
        reasons.append("replacement_alternative_not_unique_in_gap")
    if not texts_are_related(old_text, new_text):
        reasons.append("replacement_text_not_related")
    for label, probe in (("expected", expected_probe), ("alternative", alternative_probe)):
        if probe.get("status") != "observed":
            reasons.append(f"{label}_textlayer_not_observed")
            continue
        if int(probe.get("nearby_matches") or 0) != 1:
            reasons.append(f"{label}_side_not_single_local_exact_match")
        best = probe.get("best") or {}
        if not best or float(best.get("distance_px") or float("inf")) > MAX_BOX_BIND_DISTANCE_PX:
            reasons.append(f"{label}_match_not_bound_to_gap")
    expected_best = expected_probe.get("best") or {}
    alternative_best = alternative_probe.get("best") or {}
    old_best, new_best = (
        (alternative_best, expected_best)
        if kind == "text_added"
        else (expected_best, alternative_best)
    )
    if old_best and new_best and not same_slot_matches(old_best, new_best):
        reasons.append("replacement_spans_not_same_slot")
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
            probe_target(textlayer(root, expected_doc, cache), target, expected_page, expected_bbox)
            if target and expected_doc
            else {"status": "missing_textlayer", "nearby_matches": 0, "best": None}
        )
        alternatives = (
            nearby_alternative_text(
                textlayer(root, opposite_doc, cache), target, opposite_page, opposite_bbox
            ).get("localized", [])
            if target and opposite_doc
            else []
        )
        related = [
            item for item in alternatives
            if float(item.get("target_similarity") or 0) >= MIN_RELATED_TEXT_SIMILARITY
            and normalized_text(str(item.get("text") or "")) != normalized_text(target)
        ]
        alternative_text = str(related[0].get("text") or "") if len(related) == 1 and len(alternatives) == 1 else ""
        alternative_probe = (
            probe_target(
                textlayer(root, opposite_doc, cache), alternative_text,
                opposite_page, opposite_bbox, radius=MAX_BOX_BIND_DISTANCE_PX,
            )
            if alternative_text and opposite_doc
            else {"status": "observed", "nearby_matches": 0, "best": None}
        )
        old_text, new_text = (
            (alternative_text, target) if kind == "text_added" else (target, alternative_text)
        )
        reasons = eligibility_reasons(
            row, details, expected_probe, alternative_probe, alternatives,
            old_text=old_text, new_text=new_text,
            audit_ids=audit_ids, evidence_holds=machine_holds,
            paper_ready_docs=paper_ready_docs,
            old_doc_id=old_doc_id, new_doc_id=new_doc_id,
        )
        finding = {
            "pair_id": row["pair_id"],
            "project_id": row.get("project_id"),
            "split": row.get("split"),
            "original_tentative_kind": kind,
            "target": target,
            "old_text": old_text,
            "new_text": new_text,
            "original_description": row.get("change_desc_gt"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "expected_probe": expected_probe,
            "alternative_probe": alternative_probe,
            "localized_alternatives": alternatives,
            "reasons": reasons,
        }
        if reasons:
            withheld.append(finding)
            continue
        old_probe, new_probe = (
            (alternative_probe, expected_probe)
            if kind == "text_added"
            else (expected_probe, alternative_probe)
        )
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
            "kind": "text_replaced",
            "original_tentative_kind": kind,
            "target": target,
            "old_text": old_text,
            "new_text": new_text,
            "original_description": row["change_desc_gt"],
            "final_description": replacement_description(old_text, new_text),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "image_old": row.get("image_old"),
            "image_new": row.get("image_new"),
            "bbox_old": row.get("bbox_old"),
            "bbox_new": row.get("bbox_new"),
            "page_index_old": row.get("page_index_old"),
            "page_index_new": row.get("page_index_new"),
            "old_probe": old_probe,
            "new_probe": new_probe,
            "localized_alternatives": alternatives,
            "safe_to_apply": True,
        })

    output_dir.mkdir(parents=True)
    evidence_dir = output_dir / "evidence"
    if render_evidence:
        evidence_dir.mkdir()
        pair_by_id = {item["pair_id"]: item for item in pairs}
        for correction in selected:
            row = pair_by_id[correction["pair_id"]]
            rendered = render_pair(root, evidence_dir, {
                "pair_id": correction["pair_id"],
                "old": {
                    **correction["old_probe"],
                    "image_path": row["image_old"],
                    "original_bbox": row["bbox_old"],
                },
                "new": {
                    **correction["new_probe"],
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
            "one_related_alternative_in_gap_required": True,
            "minimum_text_similarity": MIN_RELATED_TEXT_SIMILARITY,
            "matching_font_size_flags_left_edge_and_baseline_required": True,
            "both_spans_bound_to_gap_px": MAX_BOX_BIND_DISTANCE_PX,
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
            "This certifies only a same-slot engineering-text substitution. Ambiguous, "
            "multi-span, audit-held, graphical, shifted, or unrelated changes remain held."
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
