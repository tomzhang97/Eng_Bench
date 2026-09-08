#!/usr/bin/env python3
"""Prepare evidence-pinned corrections for uniquely matched text relocation."""
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
        read_jsonl,
        textlayer,
        write_jsonl,
    )
    from visualdiff_description_finality import tentative_description_details
    from visualdiff_relocation_finality import (
        MAX_BOX_BIND_DISTANCE_PX,
        MAX_MOVE_DISTANCE_PX,
        MIN_MOVE_DISTANCE_PX,
        MIN_NORMALIZED_TARGET_LENGTH,
        POLICY_VERSION,
        movement_from_probes,
        normalized_target,
        relocation_description,
        span_shape_matches,
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
        read_jsonl,
        textlayer,
        write_jsonl,
    )
    from tools.visualdiff_description_finality import tentative_description_details
    from tools.visualdiff_relocation_finality import (
        MAX_BOX_BIND_DISTANCE_PX,
        MAX_MOVE_DISTANCE_PX,
        MIN_MOVE_DISTANCE_PX,
        MIN_NORMALIZED_TARGET_LENGTH,
        POLICY_VERSION,
        movement_from_probes,
        normalized_target,
        relocation_description,
        span_shape_matches,
    )


PREVIEW_MODE = "read_only_active_visualdiff_relocation_correction_preview"


def eligibility_reasons(
    row: dict[str, Any],
    details: dict[str, str],
    old_probe: dict[str, Any],
    new_probe: dict[str, Any],
    *,
    revision_target_count: int,
    audit_ids: set[str],
    evidence_holds: set[str],
    paper_ready_docs: set[str],
    old_doc_id: str,
    new_doc_id: str,
) -> tuple[list[str], dict[str, Any] | None]:
    reasons: list[str] = []
    pair_id = str(row.get("pair_id") or "")
    target = str(details.get("target") or "")
    if details.get("kind") not in {"text_added", "text_removed"}:
        return ["unsupported_tentative_kind"], None
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
    review_evidence = row.get("review_evidence") or {}
    if not isinstance(review_evidence, dict):
        reasons.append("missing_review_evidence")
    elif str(review_evidence.get("original_human_description") or "") != str(
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
    if len(normalized_target(target)) < MIN_NORMALIZED_TARGET_LENGTH:
        reasons.append("target_too_short_for_unique_relocation")
    if revision_target_count != 1:
        reasons.append("repeated_target_in_revision_pair")
    if old_probe.get("status") != "observed" or new_probe.get("status") != "observed":
        reasons.append("missing_textlayer_evidence")
        return reasons, None
    if int(old_probe.get("page_matches") or 0) != 1:
        reasons.append("old_side_not_single_page_exact_match")
    if int(new_probe.get("page_matches") or 0) != 1:
        reasons.append("new_side_not_single_page_exact_match")
    old_best = old_probe.get("best") or {}
    new_best = new_probe.get("best") or {}
    if not old_best or not new_best:
        reasons.append("exact_match_not_within_relocation_radius")
        return reasons, None
    if min(
        float(old_best.get("distance_px") or float("inf")),
        float(new_best.get("distance_px") or float("inf")),
    ) > MAX_BOX_BIND_DISTANCE_PX:
        reasons.append("neither_side_is_bound_to_gap_box")
    if str(old_best.get("font") or "") != str(new_best.get("font") or ""):
        reasons.append("font_changed")
    if int(old_best.get("flags") or 0) != int(new_best.get("flags") or 0):
        reasons.append("font_flags_changed")
    if abs(float(old_best.get("size") or 0) - float(new_best.get("size") or 0)) > 0.001:
        reasons.append("font_size_changed")
    if not span_shape_matches(old_best.get("bbox_px"), new_best.get("bbox_px")):
        reasons.append("text_span_shape_changed")
    movement = movement_from_probes(old_probe, new_probe)
    distance = float(movement["distance_px"])
    if distance < MIN_MOVE_DISTANCE_PX:
        reasons.append("movement_below_material_threshold")
    if distance > MAX_MOVE_DISTANCE_PX:
        reasons.append("movement_exceeds_local_relocation_radius")
    return reasons, movement


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
        str(row["doc_id"]) for row in provenance["documents"] if row.get("paper_ready")
    }
    audit_ids = active_audit_ids(audit_path)
    machine_holds = evidence_hold_ids(root)
    tentative: list[tuple[dict[str, Any], dict[str, str]]] = []
    revision_targets: Counter[tuple[str, str]] = Counter()
    for row in pairs:
        details = tentative_description_details(str(row.get("change_desc_gt") or ""))
        if not details:
            continue
        tentative.append((row, details))
        revision_targets[(str(row.get("project_id") or ""), normalized_target(details.get("target", "")))] += 1

    cache: dict[str, list[dict[str, Any]]] = {}
    evidence_hashes = {
        "manifest.jsonl": file_sha256(root / "manifest.jsonl"),
        audit_path.relative_to(root).as_posix(): file_sha256(audit_path),
    }
    selected: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    for row, details in tentative:
        manifest_row = pair_manifest.get(str(row.get("project_id") or ""), {})
        old_doc_id = str(manifest_row.get("from_doc_id") or "")
        new_doc_id = str(manifest_row.get("to_doc_id") or "")
        target = str(details.get("target") or "")
        old_probe = probe_target(
            textlayer(root, old_doc_id, cache), target,
            int(row.get("page_index_old") or 0), row.get("bbox_old"),
        ) if target and old_doc_id else {"status": "missing_textlayer", "nearby_matches": 0}
        new_probe = probe_target(
            textlayer(root, new_doc_id, cache), target,
            int(row.get("page_index_new") or 0), row.get("bbox_new"),
        ) if target and new_doc_id else {"status": "missing_textlayer", "nearby_matches": 0}
        revision_key = (str(row.get("project_id") or ""), normalized_target(target))
        reasons, movement = eligibility_reasons(
            row, details, old_probe, new_probe,
            revision_target_count=revision_targets[revision_key],
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
            "kind": details.get("kind"),
            "target": target,
            "original_description": row.get("change_desc_gt"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "old_probe": old_probe,
            "new_probe": new_probe,
            "movement": movement,
            "revision_target_count": revision_targets[revision_key],
            "reasons": reasons,
        }
        if reasons:
            withheld.append(finding)
            continue
        assert movement is not None
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
            "kind": details["kind"],
            "target": target,
            "original_description": row["change_desc_gt"],
            "final_description": relocation_description(target, movement),
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
            "movement": movement,
            "revision_target_count": revision_targets[revision_key],
            "safe_to_apply": True,
        })

    output_dir.mkdir(parents=True)
    evidence_dir = output_dir / "evidence"
    if render_evidence:
        evidence_dir.mkdir()
        pair_by_id = {row["pair_id"]: row for row in pairs}
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
    reason_counts = Counter(reason for row in withheld for reason in row["reasons"])
    report = {
        "goal": "Gold v2.0 Global",
        "mode": PREVIEW_MODE,
        "status": "PASS" if selected and before == after else "FAIL",
        "policy_version": POLICY_VERSION,
        "ready_for_apply": bool(selected) and before == after,
        "counts": {
            "tentative_rows": len(tentative),
            "prepared_corrections": len(selected),
            "withheld_rows": len(withheld),
        },
        "withheld_reason_counts": dict(sorted(reason_counts.items())),
        "criteria": {
            "human_edit_and_high_confidence_required": True,
            "paper_ready_source_docs_required": True,
            "active_audit_and_evidence_holds_excluded": True,
            "one_exact_page_match_per_revision": True,
            "matching_font_size_flags_and_span_shape_required": True,
            "at_least_one_gap_box_binding_px": MAX_BOX_BIND_DISTANCE_PX,
            "movement_distance_px": [MIN_MOVE_DISTANCE_PX, MAX_MOVE_DISTANCE_PX],
            "unique_target_per_revision_pair_required": True,
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
            "Unique exact text relocation proves a visible label movement, not an engineering "
            "topology change. Repeated labels, audit flags, replacements, and graphics remain held."
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
        root,
        root / args.output_dir,
        root / args.active_audit_rechecks,
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
