#!/usr/bin/env python3
"""Resolve vague reviewed VisualDiff prose against pinned paired evidence."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from . import candidate_evidence_holds
    from . import localize_auditor_supported_visualdiff as support_validation
    from . import preview_reviewed_gold_promotion as preview
    from .reconcile_auditor_active_links import p
    from .visualdiff_description_finality import GRAPHIC_TEMPLATE
except ImportError:
    import candidate_evidence_holds
    import localize_auditor_supported_visualdiff as support_validation
    import preview_reviewed_gold_promotion as preview
    from reconcile_auditor_active_links import p
    from visualdiff_description_finality import GRAPHIC_TEMPLATE


METHOD = "machine_visual_reconciliation"


def reconcile_row(
    row: dict[str, Any],
    correction: dict[str, Any],
    held_ids: set[str],
    input_sha256: str,
    evidence: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    identity = preview.identity_for(row, "visualdiff")
    original = preview.visualdiff_merge.description(row)
    final = str(correction.get("final_english_description") or "").strip()
    reasons: list[str] = []
    if correction.get("pair_id") != identity:
        reasons.append("pair_id_mismatch")
    if str(correction.get("original_human_description") or "").strip() != original:
        reasons.append("original_human_description_mismatch")
    if original != GRAPHIC_TEMPLATE:
        reasons.append("original_description_is_not_graphic_template")
    if not final or preview.CJK_RE.search(final):
        reasons.append("final_description_not_english")
    if preview.tentative_description_details(final):
        reasons.append("final_description_remains_tentative")
    raw_change_type = str(row.get("change_type") or "").strip()
    if str(correction.get("expected_change_type") or "").strip() != raw_change_type:
        reasons.append("reviewed_change_type_mismatch")
    if not str(correction.get("machine_visual_reconciliation") or "").strip():
        reasons.append("machine_visual_reconciliation_missing")
    if preview.status_for(row, "visualdiff") not in preview.FINAL_VISUALDIFF:
        reasons.append("primary_review_not_final")
    if row.get("safe_to_merge_gold") is not False:
        reasons.append("staging_safety_flag_missing")
    if not support_validation.complete_support(row):
        reasons.append("hash_complete_independent_audit_support_missing")
    if candidate_evidence_holds.is_evidence_held(row, held_ids):
        reasons.append("unresolved_machine_evidence_hold")
    if reasons:
        return None, sorted(dict.fromkeys(reasons))

    output = dict(row)
    output["machine_reconciled_description"] = final
    output["semantic_reconciliation"] = {
        "method": METHOD,
        "original_human_description": original,
        "source_input_sha256": input_sha256,
        "evidence_artifact": evidence,
        "evidence_anchor": str(correction.get("evidence_anchor") or identity),
        "independent_audit_support": row["independent_audit_support"],
        "machine_visual_reconciliation": str(correction["machine_visual_reconciliation"]).strip(),
        "active_gold_modified": False,
    }
    output["safe_to_merge_gold"] = False
    return output, []


def run(root: Path, reconciliation: Path, corrections_path: Path, output: Path) -> dict[str, Any]:
    root, reconciliation, corrections_path, output = (
        root.resolve(),
        reconciliation.resolve(),
        corrections_path.resolve(),
        output.resolve(),
    )
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")

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

    specification = json.loads(corrections_path.read_text(encoding="utf-8"))
    if specification.get("schema_version") != 1:
        raise ValueError("unsupported semantic reconciliation schema")
    if specification.get("input_sha256") != input_sha256:
        raise ValueError("semantic reconciliation input hash mismatch")
    corrections = specification.get("corrections")
    if not isinstance(corrections, list) or not corrections:
        raise ValueError("semantic reconciliation specification is empty")
    by_id: dict[str, dict[str, Any]] = {}
    evidence_by_path: dict[str, dict[str, str]] = {}
    for correction in corrections:
        identity = str(correction.get("pair_id") or "").strip()
        if not identity or identity in by_id:
            raise ValueError(f"missing or duplicate semantic correction identity: {identity}")
        by_id[identity] = correction
        relative = str(correction.get("evidence_artifact") or "").replace("\\", "/")
        expected = str(correction.get("evidence_sha256") or "").lower()
        path = (root / relative).resolve()
        if (
            not relative.startswith("derived/quality/")
            or not path.is_file()
            or not path.is_relative_to(root / "derived/quality")
            or p.sha256_file(path) != expected
        ):
            raise ValueError(f"semantic reconciliation evidence mismatch: {relative}")
        evidence_by_path[relative] = {"path": relative, "sha256": expected}

    before = preview.active_hashes(root)
    held_ids = candidate_evidence_holds.evidence_hold_ids(root)
    rows = preview.read_jsonl(input_path)
    row_ids = {preview.identity_for(row, "visualdiff") for row in rows}
    missing = set(by_id) - row_ids
    if missing:
        raise ValueError(f"semantic correction identities missing from input: {sorted(missing)}")

    reconciled_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    for row in rows:
        identity = preview.identity_for(row, "visualdiff")
        correction = by_id.get(identity)
        if correction is None:
            deferred_rows.append(
                {"pair_id": identity, "reasons": ["not_selected_for_semantic_reconciliation"], "row": row}
            )
            continue
        relative = str(correction["evidence_artifact"]).replace("\\", "/")
        reconciled, reasons = reconcile_row(
            row, correction, held_ids, input_sha256, evidence_by_path[relative]
        )
        if reconciled is None:
            deferred_rows.append({"pair_id": identity, "reasons": reasons, "row": row})
        else:
            reconciled_rows.append(reconciled)

    output.mkdir(parents=True)
    reconciled_path = output / "reconciled_visualdiff_reviewed.jsonl"
    deferred_path = output / "deferred_visualdiff.jsonl"
    preview.write_jsonl(reconciled_path, reconciled_rows)
    preview.write_jsonl(deferred_path, deferred_rows)
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active release changed during read-only semantic reconciliation")

    result = {
        "goal": "Gold v2.0 Global",
        "status": "SEMANTICALLY_RECONCILED_PENDING_STRICT_PROMOTION_PREVIEW",
        "input_rows": len(rows),
        "requested_corrections": len(corrections),
        "reconciled_rows": len(reconciled_rows),
        "deferred_rows": len(deferred_rows),
        "deferred_reasons": dict(
            sorted(Counter(reason for row in deferred_rows for reason in row["reasons"]).items())
        ),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "active_hashes_before": before,
        "active_hashes_after": after,
        "input_hashes": {
            str(report_path): p.sha256_file(report_path),
            str(input_path): input_sha256,
            str(corrections_path): p.sha256_file(corrections_path),
            str(root / candidate_evidence_holds.CURRENT_HOLDS): p.sha256_file(
                root / candidate_evidence_holds.CURRENT_HOLDS
            ),
        },
        "output_hashes": {
            reconciled_path.name: p.sha256_file(reconciled_path),
            deferred_path.name: p.sha256_file(deferred_path),
        },
        "interpretation": (
            "Only selected vague descriptions received evidence-specific semantics. "
            "Active Gold remains unchanged until strict preview and atomic apply pass."
        ),
    }
    preview.write_json(output / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    result = run(
        root,
        root / args.reconciliation,
        root / args.corrections,
        root / args.output_dir,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "input_rows",
                    "requested_corrections",
                    "reconciled_rows",
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
