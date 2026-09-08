#!/usr/bin/env python3
"""Certify machine-only bbox repairs while preserving reviewed semantics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from . import candidate_evidence_holds
    from . import preview_reviewed_gold_promotion as preview
except ImportError:
    import candidate_evidence_holds
    import preview_reviewed_gold_promotion as preview


REPAIRED_ROWS = "bbox_repaired_reviewed.jsonl"
RESOLUTION_ROWS = "resolved_candidate_evidence_holds.jsonl"


def hold_report_ids(path: Path) -> tuple[str, set[str]]:
    report_hash = candidate_evidence_holds.digest(path)
    report = json.loads(path.read_text(encoding="utf-8"))
    rows_name = report.get("hold_rows_artifact", "bbox_repair_proposals.jsonl")
    if Path(rows_name).name != rows_name:
        raise ValueError("hold report rows path invalid")
    rows_path = path.parent / rows_name
    if candidate_evidence_holds.digest(rows_path) != report["output_hashes"][rows_name]:
        raise ValueError("hold report rows changed")
    rows = preview.read_jsonl(rows_path)
    identities = {
        str(row.get("candidate_id") or row.get("source_candidate_id") or row.get("item_id") or row.get("pair_id") or "")
        for row in rows
    }
    if "" in identities:
        raise ValueError("hold report contains a row without identity")
    return report_hash, identities


def restore_reviewed_row(row: dict, decision: dict, contact_sheet: Path) -> dict:
    prior = row.get("prior_human_review") or {}
    review_status = str(prior.get("review_status") or "")
    human_status = str(prior.get("human_review_status") or review_status)
    if review_status not in preview.FINAL_MICROTEXT or human_status not in preview.FINAL_MICROTEXT:
        raise ValueError(f"prior human review is not final: {row.get('candidate_id')}")
    if decision.get("status") != "pass" or decision.get("bbox") != row.get("bbox"):
        raise ValueError(f"bbox verification missing or stale: {row.get('candidate_id')}")
    repair = dict(row.get("machine_bbox_repair") or {})
    repair["verification"] = {
        "status": "pass",
        "method": "exact_source_bbox_and_marked_context_visual_inspection",
        "contact_sheet": decision["contact_sheet"],
        "contact_sheet_sha256": candidate_evidence_holds.digest(contact_sheet),
        "verification_note": decision["verification_note"],
        "text_changed": False,
        "category_changed": False,
        "new_human_vote": False,
    }
    return {
        **row,
        "machine_bbox_repair": repair,
        "machine_qa_status": "machine_bbox_verified",
        "review_status": review_status,
        "human_review_status": human_status,
        "promotion_state": "human_semantics_and_machine_geometry_verified_pending_release_gates",
        "safe_to_merge_gold": False,
    }


def certify(
    root: Path,
    staging_dir: Path,
    verification_path: Path,
    contact_sheet: Path,
    output: Path,
) -> dict:
    root = root.resolve()
    staging_dir, verification_path, contact_sheet, output = (
        path.resolve() for path in (staging_dir, verification_path, contact_sheet, output)
    )
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    for path in (verification_path, contact_sheet):
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"verification evidence must be inside root: {path}")
    staging_report_path = staging_dir / "report.json"
    staging_report = json.loads(staging_report_path.read_text(encoding="utf-8"))
    proposals_path = staging_dir / "bbox_repair_proposals.jsonl"
    if (
        staging_report.get("status") != "PENDING_MACHINE_BBOX_RECONCILIATION"
        or staging_report.get("active_gold_modified") is not False
        or staging_report.get("safe_to_merge_gold") is not False
        or preview.file_sha256(proposals_path)
        != staging_report["output_hashes"][proposals_path.name]
    ):
        raise ValueError("bbox repair staging report is invalid or stale")
    for name, expected in staging_report.get("source_image_hashes", {}).items():
        if preview.file_sha256(root / name) != expected:
            raise ValueError(f"bbox repair source image changed: {name}")
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if candidate_evidence_holds.digest(contact_sheet) != verification["contact_sheet_sha256"]:
        raise ValueError("bbox verification contact sheet changed")
    decisions = {row["candidate_id"]: row for row in verification["records"]}
    proposals = preview.read_jsonl(proposals_path)
    proposal_ids = {row["candidate_id"] for row in proposals}
    if len(decisions) != len(verification["records"]) or set(decisions) != proposal_ids:
        raise ValueError("bbox verification decisions do not exactly cover proposals")

    hold_reports: dict[str, tuple[str, set[str]]] = {}
    for decision in decisions.values():
        for value in decision.get("resolves_reports", []):
            path = (root / value).resolve()
            if not path.is_file() or not path.is_relative_to(root / "derived/quality"):
                raise ValueError(f"hold report path invalid: {path}")
            hold_reports.setdefault(value, hold_report_ids(path))

    repaired, resolutions = [], []
    relative_contact = contact_sheet.relative_to(root).as_posix()
    for row in proposals:
        identity = row["candidate_id"]
        decision = decisions[identity]
        if decision.get("contact_sheet") != relative_contact:
            raise ValueError(f"contact sheet path mismatch: {identity}")
        resolved_hashes = []
        for value in decision.get("resolves_reports", []):
            report_hash, identities = hold_reports[value]
            if identity not in identities:
                raise ValueError(f"hold report does not contain candidate: {identity}")
            resolved_hashes.append(report_hash)
        if not resolved_hashes:
            raise ValueError(f"bbox repair has no evidence hold to resolve: {identity}")
        repaired.append(restore_reviewed_row(row, decision, contact_sheet))
        resolutions.append({
            "candidate_id": identity,
            "resolution_status": "resolved",
            "resolution_reason": "exact_bbox_geometry_repaired_and_verified",
            "resolved_report_sha256s": sorted(set(resolved_hashes)),
            "verified_bbox": row["bbox"],
            "contact_sheet": relative_contact,
            "contact_sheet_sha256": verification["contact_sheet_sha256"],
            "safe_to_merge_gold": False,
        })

    before = preview.active_hashes(root)
    output.mkdir(parents=True)
    preview.write_jsonl(output / REPAIRED_ROWS, repaired)
    preview.write_jsonl(output / RESOLUTION_ROWS, resolutions)
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active Gold changed during bbox certification")
    evidence_paths = [staging_report_path, verification_path, contact_sheet]
    report = {
        "goal": "Gold v2.0 Global",
        "status": "PASS",
        "certified_rows": len(repaired),
        "resolution_rows_artifact": RESOLUTION_ROWS,
        "resolution_count": len(resolutions),
        "evidence_artifacts": [
            {"path": path.relative_to(root).as_posix(), "sha256": preview.file_sha256(path)}
            for path in evidence_paths
        ],
        "resolved_hold_reports": [
            {"path": path, "sha256": value[0]} for path, value in sorted(hold_reports.items())
        ],
        "human_text_or_category_decisions_modified": False,
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "output_hashes": {
            name: preview.file_sha256(output / name)
            for name in (REPAIRED_ROWS, RESOLUTION_ROWS)
        },
        "interpretation": (
            "The machine certifies only crop geometry. Human-reviewed text and category values are preserved; "
            "promotion still requires a fresh strict preview and atomic transaction validation."
        ),
    }
    preview.write_json(output / "report.json", report)
    return report


def activate_resolution(root: Path, report_path: Path) -> tuple[set[str], set[str]]:
    root, report_path = root.resolve(), report_path.resolve()
    pointer = root / candidate_evidence_holds.CURRENT_HOLDS
    before = candidate_evidence_holds.evidence_hold_ids(root)
    original = pointer.read_bytes()
    config = json.loads(original.decode("utf-8"))
    reference = {
        "report_path": report_path.relative_to(root).as_posix(),
        "report_sha256": candidate_evidence_holds.digest(report_path),
    }
    resolutions = [
        entry for entry in config.get("resolutions", [])
        if entry["report_path"] != reference["report_path"]
    ]
    resolutions.append(reference)
    config.update({"schema_version": 2, "resolutions": resolutions})
    try:
        pointer.write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        after = candidate_evidence_holds.evidence_hold_ids(root)
    except Exception:
        pointer.write_bytes(original)
        raise
    if not after < before:
        pointer.write_bytes(original)
        raise ValueError("candidate evidence resolution did not reduce active holds")
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--activate-resolution", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    output = resolve(args.output_dir)
    report = certify(
        root,
        resolve(args.staging_dir),
        resolve(args.verification),
        resolve(args.contact_sheet),
        output,
    )
    active_counts = None
    if args.activate_resolution:
        before, after = activate_resolution(root, output / "report.json")
        active_counts = {"before": len(before), "after": len(after)}
    print(json.dumps({
        "status": report["status"],
        "certified_rows": report["certified_rows"],
        "active_gold_modified": report["active_gold_modified"],
        "active_hold_counts": active_counts,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
