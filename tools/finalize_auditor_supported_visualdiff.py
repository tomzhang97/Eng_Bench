#!/usr/bin/env python3
"""Finalize exact text templates backed by primary and independent review."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from . import candidate_evidence_holds
    from . import preview_reviewed_gold_promotion as preview
    from .reconcile_auditor_active_links import p
    from .visualdiff_description_finality import definitive_description
except ImportError:
    import candidate_evidence_holds
    import preview_reviewed_gold_promotion as preview
    from reconcile_auditor_active_links import p
    from visualdiff_description_finality import definitive_description


SHA256_RE = re.compile(r"[0-9a-f]{64}")


def correspondence_issues(row: dict[str, Any], details: dict[str, str]) -> list[str]:
    kind = details["kind"]
    old_text = str(row.get("old_text") or "").strip()
    new_text = str(row.get("new_text") or "").strip()
    change_types = set(preview.visualdiff_merge.normalized_change_type(row))
    issues: list[str] = []
    if kind == "text_added":
        if old_text or new_text != details["target"]:
            issues.append("template_text_does_not_match_old_new_text")
        if "addition" not in change_types:
            issues.append("change_type_does_not_support_addition")
    elif kind == "text_removed":
        if new_text or old_text != details["target"]:
            issues.append("template_text_does_not_match_old_new_text")
        if "deletion" not in change_types:
            issues.append("change_type_does_not_support_deletion")
    elif kind == "text_changed":
        if old_text != details["target_old"] or new_text != details["target_new"]:
            issues.append("template_text_does_not_match_old_new_text")
        if "text" not in change_types:
            issues.append("change_type_does_not_support_text_change")
    else:
        issues.append("graphic_or_unknown_template_requires_semantic_review")
    return issues


def finalize_row(
    row: dict[str, Any],
    held_ids: set[str],
    evidence_artifacts: list[dict[str, str]],
    input_sha256: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    identity = preview.identity_for(row, "visualdiff")
    reasons: list[str] = []
    if not identity:
        reasons.append("missing_pair_id")
    if preview.status_for(row, "visualdiff") not in preview.FINAL_VISUALDIFF:
        reasons.append("primary_review_not_final")
    if row.get("safe_to_merge_gold") is not False:
        reasons.append("staging_safety_flag_missing")
    support = row.get("independent_audit_support")
    if not isinstance(support, dict) or support.get("decision_code") != "1":
        reasons.append("positive_independent_audit_missing")
    else:
        for field in (
            "assignment_payload_sha256",
            "evidence_sha256",
            "source_workbook_sha256",
        ):
            if not SHA256_RE.fullmatch(str(support.get(field) or "").lower()):
                reasons.append(f"independent_audit_{field}_missing")
    if candidate_evidence_holds.is_evidence_held(row, held_ids):
        reasons.append("unresolved_machine_evidence_hold")

    original = preview.visualdiff_merge.description(row)
    details = preview.tentative_description_details(original)
    if details is None:
        reasons.append("description_not_tentative")
    else:
        reasons.extend(correspondence_issues(row, details))
    if reasons:
        return None, sorted(dict.fromkeys(reasons))

    assert details is not None
    finalized = definitive_description(details)
    if preview.tentative_description_details(finalized):
        raise AssertionError("finalized description remains tentative")
    if preview.visualdiff_description_requires_english_localization(finalized):
        raise AssertionError("finalized description is not English-localized")

    output = dict(row)
    output["machine_final_description"] = finalized
    output["description_finalization"] = {
        "method": "machine_epistemic_normalization",
        "basis": (
            "Exact template tokens match old/new text and change type; the same "
            "semantics have final primary review and hash-complete independent support."
        ),
        "original_description": original,
        "source_input_sha256": input_sha256,
        "evidence_artifacts": evidence_artifacts,
        "active_gold_modified": False,
    }
    output["safe_to_merge_gold"] = False
    return output, []


def run(
    root: Path,
    reconciliation: Path,
    output: Path,
    evidence_artifacts: list[Path],
) -> dict[str, Any]:
    root, reconciliation, output = root.resolve(), reconciliation.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    if not evidence_artifacts:
        raise ValueError("at least one evidence artifact is required")

    report_path = reconciliation / "report.json"
    reconciliation_report = json.loads(report_path.read_text(encoding="utf-8"))
    if reconciliation_report.get("status") != "PASS":
        raise ValueError("reconciliation is not passing")
    if reconciliation_report.get("active_gold_hashes_after") != p.active_gold_hashes(root):
        raise ValueError("reconciliation is stale against active Gold")

    input_path = reconciliation / "supported_not_active_visualdiff.jsonl"
    input_sha256 = p.sha256_file(input_path)
    if input_sha256 != reconciliation_report["output_hashes"][input_path.name]:
        raise ValueError("reconciled VisualDiff input changed")

    evidence: list[dict[str, str]] = []
    for supplied in evidence_artifacts:
        path = supplied if supplied.is_absolute() else root / supplied
        path = path.resolve()
        if not path.is_file() or not path.is_relative_to(root / "derived/quality"):
            raise ValueError(f"invalid evidence artifact: {supplied}")
        relative = path.relative_to(root).as_posix()
        evidence.append({"path": relative, "sha256": p.sha256_file(path)})

    before = preview.active_hashes(root)
    held_ids = candidate_evidence_holds.evidence_hold_ids(root)
    rows = preview.read_jsonl(input_path)
    seen: set[str] = set()
    finalized_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    for row in rows:
        identity = preview.identity_for(row, "visualdiff")
        if not identity or identity in seen:
            raise ValueError(f"missing or duplicate VisualDiff identity: {identity}")
        seen.add(identity)
        finalized, reasons = finalize_row(row, held_ids, evidence, input_sha256)
        if finalized is None:
            deferred_rows.append({"pair_id": identity, "reasons": reasons, "row": row})
        else:
            finalized_rows.append(finalized)

    output.mkdir(parents=True)
    finalized_path = output / "finalized_visualdiff_reviewed.jsonl"
    deferred_path = output / "deferred_visualdiff.jsonl"
    preview.write_jsonl(finalized_path, finalized_rows)
    preview.write_jsonl(deferred_path, deferred_rows)
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active release changed during read-only finalization")

    result = {
        "goal": "Gold v2.0 Global",
        "status": "FINALIZED_PENDING_STRICT_PROMOTION_PREVIEW",
        "input_rows": len(rows),
        "finalized_rows": len(finalized_rows),
        "deferred_rows": len(deferred_rows),
        "deferred_reasons": dict(
            sorted(Counter(reason for row in deferred_rows for reason in row["reasons"]).items())
        ),
        "evidence_artifacts": evidence,
        "machine_evidence_holds_consulted": len(held_ids),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "active_hashes_before": before,
        "active_hashes_after": after,
        "input_hashes": {
            str(report_path): p.sha256_file(report_path),
            str(input_path): input_sha256,
            str(root / candidate_evidence_holds.CURRENT_HOLDS): p.sha256_file(
                root / candidate_evidence_holds.CURRENT_HOLDS
            ),
        },
        "output_hashes": {
            finalized_path.name: p.sha256_file(finalized_path),
            deferred_path.name: p.sha256_file(deferred_path),
        },
        "interpretation": (
            "Only uncertainty wording was normalized. No row is promoted until "
            "the strict preview and atomic transaction gates pass."
        ),
    }
    preview.write_json(output / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-artifact", action="append", type=Path, default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    result = run(
        root,
        root / args.reconciliation,
        root / args.output_dir,
        args.evidence_artifact,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "input_rows",
                    "finalized_rows",
                    "deferred_rows",
                    "deferred_reasons",
                    "active_gold_modified",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
