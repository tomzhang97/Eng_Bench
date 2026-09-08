#!/usr/bin/env python3
"""Prepare a read-only, evidence-pinned correction set for tentative Gold answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import audit_active_gold_provenance
    from audit_visualdiff_description_finality import probe_target, render_pair
    from candidate_evidence_holds import evidence_hold_ids
    from visualdiff_description_finality import definitive_description, tentative_description_details
except ModuleNotFoundError:
    from tools import audit_active_gold_provenance
    from tools.audit_visualdiff_description_finality import probe_target, render_pair
    from tools.candidate_evidence_holds import evidence_hold_ids
    from tools.visualdiff_description_finality import definitive_description, tentative_description_details


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
POLICY_VERSION = "active_visualdiff_textlayer_finality_v1"
MAX_MATCH_DISTANCE_PX = 25.0
MAX_RELATED_TEXT_DISTANCE_PX = 120.0
MIN_RELATED_TEXT_SIMILARITY = 0.60


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


def active_audit_ids(path: Path) -> set[str]:
    identities: set[str] = set()
    for row in read_jsonl(path):
        for field in ("pair_id", "record_id", "active_gold_identity"):
            value = str(row.get(field) or "").strip()
            if value.startswith("vdiff__"):
                identities.add(value)
    return identities


def manifest_pair_map(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["pair_id"]): row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "pair" and row.get("pair_id")
    }


def textlayer(root: Path, doc_id: str, cache: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if doc_id not in cache:
        path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
        cache[doc_id] = read_jsonl(path) if path.is_file() else []
    return cache[doc_id]


def normalized_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def nearby_alternative_text(
    spans: list[dict[str, Any]], target: str, page: int, crop: Any
) -> dict[str, list[dict[str, Any]]]:
    """Find opposite-side text that makes an add/remove interpretation unsafe."""
    if not isinstance(crop, (list, tuple)) or len(crop) != 4:
        return {"localized": [], "related": []}
    try:
        x0, y0, x1, y1 = [float(value) for value in crop]
    except (TypeError, ValueError):
        return {"localized": [], "related": []}
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    normalized_target = normalized_label(target)
    localized: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    for span in spans:
        bbox = span.get("bbox_px")
        text = str(span.get("text") or "").strip()
        if span.get("page") != page or not text or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            sx0, sy0, sx1, sy1 = [float(value) for value in bbox]
        except (TypeError, ValueError):
            continue
        distance = math.hypot((sx0 + sx1) / 2 - center[0], (sy0 + sy1) / 2 - center[1])
        finding = {"text": text, "bbox_px": list(bbox), "distance_px": round(distance, 4)}
        if distance <= MAX_MATCH_DISTANCE_PX:
            localized.append(finding)
        normalized = normalized_label(text)
        similarity = (
            SequenceMatcher(None, normalized, normalized_target).ratio()
            if normalized and normalized_target
            else 0.0
        )
        finding["target_similarity"] = round(similarity, 4)
        if (
            normalized
            and normalized_target
            and distance <= MAX_RELATED_TEXT_DISTANCE_PX
            and (
                normalized in normalized_target
                or normalized_target in normalized
                or similarity >= MIN_RELATED_TEXT_SIMILARITY
            )
        ):
            related.append(finding)
    return {
        "localized": sorted(localized, key=lambda row: (row["distance_px"], row["text"])),
        "related": sorted(related, key=lambda row: (row["distance_px"], row["text"])),
    }


def eligibility_reasons(
    row: dict[str, Any],
    details: dict[str, str],
    old_probe: dict[str, Any],
    new_probe: dict[str, Any],
    *,
    opposite_alternatives: dict[str, list[dict[str, Any]]] | None = None,
    audit_ids: set[str],
    evidence_holds: set[str],
    paper_ready_docs: set[str],
    old_doc_id: str,
    new_doc_id: str,
) -> list[str]:
    reasons: list[str] = []
    pair_id = str(row.get("pair_id") or "")
    kind = details.get("kind")
    if kind not in {"text_added", "text_removed"}:
        reasons.append("unsupported_tentative_kind")
        return reasons
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
    if not old_doc_id or not new_doc_id:
        reasons.append("missing_manifest_pair_docs")
    elif old_doc_id not in paper_ready_docs or new_doc_id not in paper_ready_docs:
        reasons.append("source_docs_not_paper_ready")

    expected = new_probe if kind == "text_added" else old_probe
    opposite = old_probe if kind == "text_added" else new_probe
    if expected.get("status") != "observed" or opposite.get("status") != "observed":
        reasons.append("missing_textlayer_evidence")
    else:
        if int(expected.get("nearby_matches") or 0) != 1:
            reasons.append("expected_side_not_single_exact_match")
        if int(opposite.get("nearby_matches") or 0) != 0:
            reasons.append("opposite_side_has_nearby_exact_match")
        best = expected.get("best") or {}
        if float(best.get("distance_px") or float("inf")) > MAX_MATCH_DISTANCE_PX:
            reasons.append("expected_match_too_far_from_box")
    alternatives = opposite_alternatives or {"localized": [], "related": []}
    if alternatives.get("localized"):
        reasons.append("opposite_side_has_localized_alternative_text")
    if alternatives.get("related"):
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
        str(row["doc_id"]) for row in provenance["documents"] if row.get("paper_ready")
    }
    audit_ids = active_audit_ids(audit_path)
    machine_holds = evidence_hold_ids(root)
    cache: dict[str, list[dict[str, Any]]] = {}
    evidence_hashes: dict[str, str] = {
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
        old_path = root / "derived" / "textlayer" / f"{old_doc_id}.jsonl"
        new_path = root / "derived" / "textlayer" / f"{new_doc_id}.jsonl"
        target = str(details.get("target") or "")
        old_probe = probe_target(
            textlayer(root, old_doc_id, cache), target,
            int(row.get("page_index_old") or 0), row.get("bbox_old"),
        ) if target and old_doc_id else {"status": "missing_textlayer", "nearby_matches": 0}
        new_probe = probe_target(
            textlayer(root, new_doc_id, cache), target,
            int(row.get("page_index_new") or 0), row.get("bbox_new"),
        ) if target and new_doc_id else {"status": "missing_textlayer", "nearby_matches": 0}
        opposite_spans = (
            textlayer(root, old_doc_id, cache)
            if details.get("kind") == "text_added"
            else textlayer(root, new_doc_id, cache)
        ) if details.get("kind") in {"text_added", "text_removed"} else []
        opposite_page = (
            int(row.get("page_index_old") or 0)
            if details.get("kind") == "text_added"
            else int(row.get("page_index_new") or 0)
        )
        opposite_bbox = (
            row.get("bbox_old")
            if details.get("kind") == "text_added"
            else row.get("bbox_new")
        )
        opposite_alternatives = nearby_alternative_text(
            opposite_spans, target, opposite_page, opposite_bbox
        ) if target else {"localized": [], "related": []}
        reasons = eligibility_reasons(
            row, details, old_probe, new_probe,
            opposite_alternatives=opposite_alternatives,
            audit_ids=audit_ids, evidence_holds=machine_holds,
            paper_ready_docs=paper_ready_docs,
            old_doc_id=old_doc_id, new_doc_id=new_doc_id,
        )
        finding = {
            "pair_id": row["pair_id"],
            "split": row.get("split"),
            "kind": details.get("kind"),
            "target": target,
            "original_description": row.get("change_desc_gt"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "old_probe": old_probe,
            "new_probe": new_probe,
            "opposite_alternative_text": opposite_alternatives,
            "reasons": reasons,
        }
        if reasons:
            withheld.append(finding)
            continue
        for path in (old_path, new_path, root / str(row.get("image_old")), root / str(row.get("image_new"))):
            if path.is_file():
                evidence_hashes[path.relative_to(root).as_posix()] = file_sha256(path)
        expected_side = "new" if details["kind"] == "text_added" else "old"
        selected.append({
            "goal": "Gold v2.0 Global",
            "policy_version": POLICY_VERSION,
            "pair_id": row["pair_id"],
            "split": row["split"],
            "kind": details["kind"],
            "target": target,
            "expected_side": expected_side,
            "original_description": row["change_desc_gt"],
            "final_description": definitive_description(details),
            "project_id": row.get("project_id"),
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "image_old": row.get("image_old"),
            "image_new": row.get("image_new"),
            "bbox_old": row.get("bbox_old"),
            "bbox_new": row.get("bbox_new"),
            "page_index_old": row.get("page_index_old"),
            "page_index_new": row.get("page_index_new"),
            "expected_probe": new_probe if expected_side == "new" else old_probe,
            "opposite_probe": old_probe if expected_side == "new" else new_probe,
            "opposite_alternative_text": opposite_alternatives,
            "human_evidence": {
                "desc_source": row.get("desc_source"),
                "review_status": row.get("review_status"),
                "human_review_status": row.get("human_review_status"),
                "review_confidence": row.get("review_confidence"),
                "original_human_description": (row.get("review_evidence") or {}).get(
                    "original_human_description"
                ),
            },
            "safe_to_apply": True,
        })

    output_dir.mkdir(parents=True)
    evidence_dir = output_dir / "evidence"
    if render_evidence:
        evidence_dir.mkdir()
        pair_by_id = {row["pair_id"]: row for row in pairs}
        for correction in selected:
            row = pair_by_id[correction["pair_id"]]
            old_probe = (
                correction["opposite_probe"]
                if correction["expected_side"] == "new"
                else correction["expected_probe"]
            )
            new_probe = (
                correction["expected_probe"]
                if correction["expected_side"] == "new"
                else correction["opposite_probe"]
            )
            finding = {
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
        "mode": "read_only_active_visualdiff_finality_correction_preview",
        "status": "PASS" if selected and before == after else "FAIL",
        "policy_version": POLICY_VERSION,
        "ready_for_apply": bool(selected) and before == after,
        "counts": {
            "tentative_rows": len(selected) + len(withheld),
            "prepared_corrections": len(selected),
            "withheld_rows": len(withheld),
        },
        "prepared_by_kind": dict(Counter(row["kind"] for row in selected)),
        "withheld_reason_counts": dict(sorted(reason_counts.items())),
        "active_audit_flag_count": len(audit_ids),
        "active_audit_rechecks": audit_path.relative_to(root).as_posix(),
        "active_audit_rechecks_sha256": file_sha256(audit_path),
        "provenance": {
            "active_source_docs": provenance["totals"]["active_source_docs"],
            "paper_ready_docs": provenance["totals"]["paper_ready_docs"],
        },
        "criteria": {
            "human_edit_required": True,
            "high_review_confidence_required": True,
            "aligned_boxes_required": True,
            "expected_side_exact_nearby_matches": 1,
            "opposite_side_exact_nearby_matches": 0,
            "max_expected_match_distance_px": MAX_MATCH_DISTANCE_PX,
            "opposite_local_text_exclusion_distance_px": MAX_MATCH_DISTANCE_PX,
            "opposite_related_text_exclusion_distance_px": MAX_RELATED_TEXT_DISTANCE_PX,
            "opposite_related_text_min_similarity": MIN_RELATED_TEXT_SIMILARITY,
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
            "This preview only removes generator uncertainty from high-confidence human-reviewed "
            "text additions/removals with one-sided exact source-text evidence. It does not certify "
            "withheld, auditor-flagged, repeated, distant, or graphic changes."
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
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
        "prepared_by_kind": report["prepared_by_kind"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
