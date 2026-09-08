#!/usr/bin/env python3
"""Localize CJK VisualDiff semantics with pinned visual and audit evidence."""
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
except ImportError:
    import candidate_evidence_holds
    import preview_reviewed_gold_promotion as preview
    from reconcile_auditor_active_links import p


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
LOCALIZATION_METHOD = "machine_translation_and_visual_reconciliation"


def complete_support(row: dict[str, Any]) -> bool:
    support = row.get("independent_audit_support")
    return isinstance(support, dict) and support.get("decision_code") == "1" and all(
        SHA256_RE.fullmatch(str(support.get(field) or "").lower())
        for field in (
            "assignment_payload_sha256",
            "evidence_sha256",
            "source_workbook_sha256",
        )
    )


def localize_row(
    row: dict[str, Any],
    correction: dict[str, Any],
    held_ids: set[str],
    input_sha256: str,
    evidence: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    identity = preview.identity_for(row, "visualdiff")
    original = preview.visualdiff_merge.description(row)
    localized = str(correction.get("localized_english_description") or "").strip()
    reasons: list[str] = []
    if correction.get("pair_id") != identity:
        reasons.append("pair_id_mismatch")
    if str(correction.get("original_human_description") or "").strip() != original:
        reasons.append("original_human_description_mismatch")
    if not CJK_RE.search(original):
        reasons.append("original_description_is_not_cjk")
    if not localized or CJK_RE.search(localized):
        reasons.append("localized_description_not_english")
    if preview.tentative_description_details(localized):
        reasons.append("localized_description_remains_tentative")
    if preview.visualdiff_description_requires_english_localization(localized):
        reasons.append("localized_description_requires_further_localization")
    if preview.status_for(row, "visualdiff") not in preview.FINAL_VISUALDIFF:
        reasons.append("primary_review_not_final")
    if row.get("safe_to_merge_gold") is not False:
        reasons.append("staging_safety_flag_missing")
    if not complete_support(row):
        reasons.append("hash_complete_independent_audit_support_missing")
    if candidate_evidence_holds.is_evidence_held(row, held_ids):
        reasons.append("unresolved_machine_evidence_hold")
    if reasons:
        return None, sorted(dict.fromkeys(reasons))

    output = dict(row)
    output["original_human_description"] = original
    output["localized_human_description"] = localized
    output["localization_method"] = LOCALIZATION_METHOD
    output["reconciliation_evidence_sheet"] = (
        f"{evidence['path']}#{str(correction.get('evidence_anchor') or identity)}"
    )
    output["localization_provenance"] = {
        "method": LOCALIZATION_METHOD,
        "source_input_sha256": input_sha256,
        "evidence_artifact": evidence,
        "independent_audit_support": row["independent_audit_support"],
        "machine_visual_reconciliation": str(
            correction.get("machine_visual_reconciliation") or ""
        ).strip(),
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
        raise ValueError("unsupported localization schema")
    if specification.get("input_sha256") != input_sha256:
        raise ValueError("localization specification input hash mismatch")

    corrections = specification.get("localizations")
    if not isinstance(corrections, list) or not corrections:
        raise ValueError("localization specification is empty")
    by_id: dict[str, dict[str, Any]] = {}
    for correction in corrections:
        identity = str(correction.get("pair_id") or "").strip()
        if not identity or identity in by_id:
            raise ValueError(f"missing or duplicate localization identity: {identity}")
        by_id[identity] = correction

    evidence_by_path: dict[str, dict[str, str]] = {}
    for correction in corrections:
        relative = str(correction.get("evidence_artifact") or "").replace("\\", "/")
        expected = str(correction.get("evidence_sha256") or "").lower()
        path = (root / relative).resolve()
        if (
            not relative.startswith("derived/quality/")
            or not path.is_file()
            or not path.is_relative_to(root / "derived/quality")
            or p.sha256_file(path) != expected
        ):
            raise ValueError(f"localization evidence mismatch: {relative}")
        evidence_by_path[relative] = {"path": relative, "sha256": expected}

    before = preview.active_hashes(root)
    held_ids = candidate_evidence_holds.evidence_hold_ids(root)
    rows = preview.read_jsonl(input_path)
    row_ids = {preview.identity_for(row, "visualdiff") for row in rows}
    missing = set(by_id) - row_ids
    if missing:
        raise ValueError(f"localization identities missing from input: {sorted(missing)}")

    localized_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    for row in rows:
        identity = preview.identity_for(row, "visualdiff")
        correction = by_id.get(identity)
        if correction is None:
            deferred_rows.append(
                {"pair_id": identity, "reasons": ["not_selected_for_localization"], "row": row}
            )
            continue
        relative = str(correction["evidence_artifact"]).replace("\\", "/")
        localized, reasons = localize_row(
            row, correction, held_ids, input_sha256, evidence_by_path[relative]
        )
        if localized is None:
            deferred_rows.append({"pair_id": identity, "reasons": reasons, "row": row})
        else:
            localized_rows.append(localized)

    output.mkdir(parents=True)
    localized_path = output / "localized_visualdiff_reviewed.jsonl"
    deferred_path = output / "deferred_visualdiff.jsonl"
    preview.write_jsonl(localized_path, localized_rows)
    preview.write_jsonl(deferred_path, deferred_rows)
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active release changed during read-only localization")

    result = {
        "goal": "Gold v2.0 Global",
        "status": "LOCALIZED_PENDING_STRICT_PROMOTION_PREVIEW",
        "input_rows": len(rows),
        "requested_localizations": len(corrections),
        "localized_rows": len(localized_rows),
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
            localized_path.name: p.sha256_file(localized_path),
            deferred_path.name: p.sha256_file(deferred_path),
        },
        "interpretation": (
            "Machine localization is grounded in human semantics, independent audit "
            "support, and pinned visual evidence. Active Gold remains unchanged."
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
                    "requested_localizations",
                    "localized_rows",
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
