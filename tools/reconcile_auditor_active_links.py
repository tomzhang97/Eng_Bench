#!/usr/bin/env python3
"""Reconcile auditor returns with promoted identities before counting new capacity."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from . import ingest_auditor_return_batch as ingestion
    from . import microtext_merge, visualdiff_merge
except ImportError:
    import ingest_auditor_return_batch as ingestion
    import microtext_merge
    import visualdiff_merge

p = ingestion.parser
CURRENT_REPORT = "derived/quality/current_auditor_reconciliation.json"


def release_constraint(root: Path) -> dict:
    """Fail closed on stale audit links; observation holds need explicit resolution."""
    root = root.resolve()
    pointer = root / CURRENT_REPORT
    result = {"current": "not assessed", "target": "0 unresolved active audit flags",
              "passes": False, "issues": [], "active_audit_flags": None}
    if not pointer.is_file():
        result["issues"].append("current_auditor_reconciliation_not_configured")
        return result
    try:
        reference = json.loads(pointer.read_text(encoding="utf-8"))
        path = (root / reference["report_path"]).resolve()
        if not path.is_relative_to(root / "derived/quality"):
            raise ValueError("report path outside derived/quality")
        if p.sha256_file(path) != reference["report_sha256"]:
            raise ValueError("auditor reconciliation report hash mismatch")
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("status") != "PASS" or report.get("active_gold_modified") is not False:
            raise ValueError("auditor reconciliation not valid")
        if report["active_gold_hashes_after"] != p.active_gold_hashes(root):
            raise ValueError("active Gold changed since auditor reconciliation")
        if len(report["active_gold_hashes_after"]) != len(p.ACTIVE_GOLD_PATHS):
            raise ValueError("incomplete active hash coverage")
        for name, expected in report["input_hashes"].items():
            if p.sha256_file(Path(name)) != expected:
                raise ValueError(f"auditor reconciliation input changed: {name}")
        registered = {str(path.resolve()) for path in (root / "derived/human_adjudication/processed_returns").glob("**/*auditor_decisions*.jsonl")}
        pinned = {str(Path(name).resolve()) for name in report["input_hashes"] if Path(name).match("*auditor_decisions*.jsonl")}
        if registered != pinned:
            raise ValueError("audit registry changed since reconciliation")
        if "registry_active_audit_rechecks.jsonl" not in report["output_hashes"]:
            raise ValueError("registry-wide active audit holds not assessed")
        for name, expected in report["output_hashes"].items():
            if p.sha256_file(path.parent / name) != expected:
                raise ValueError(f"auditor reconciliation output changed: {name}")
        holds = ingestion.read_jsonl(path.parent / "registry_active_audit_rechecks.jsonl")
        if len(holds) != report["registry_active_audit_flags"]:
            raise ValueError("active audit flag count mismatch")
        result.update({"current": len(holds), "active_audit_flags": len(holds),
                       "passes": not holds, "report_path": reference["report_path"],
                       "by_task": report["registry_active_flags_by_task"],
                       "by_split": report["registry_active_flags_by_split"],
                       "scope": "all registered auditor decisions; historical flags require explicit resolution"})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["issues"].append(str(error))
    return result


def registry_active_holds(history: list[dict], active: dict) -> list[dict]:
    """A later positive vote is not an adjudication of an earlier adverse vote."""
    grouped = {}
    for observation in history:
        source = active.get(observation["record_id"])
        if source is None or str(observation["decision_code"]) not in {"2", "3"}:
            continue
        task = "microtext" if source.get("item_id") else "visualdiff"
        identity = str(source.get("item_id") or source.get("pair_id"))
        entry = grouped.setdefault((task, identity), {
            "task_type": task, "active_gold_identity": identity,
            "active_split": source.get("split"), "active_row": source,
            "observations": [], "safe_to_merge_gold": False,
            "resolution_status": "requires_explicit_evidence_reconciliation",
        })
        entry["observations"].append(observation)
    return [grouped[key] for key in sorted(grouped)]


def compare_reviewed_content(active: dict, primary: dict, task: str) -> dict:
    if not primary:
        return {"status": "primary_record_missing", "changed_fields": [], "semantic_certification": False}
    if task == "microtext":
        fields = {
            "doc_id": (active.get("doc_id"), primary.get("doc_id")),
            "page_index": (int(active.get("page_index", 0)), int(primary.get("page_index", 0))),
            "bbox": (microtext_merge.bbox_tuple(active), microtext_merge.bbox_tuple(primary)),
            "text": (str(active.get("text_gt") or ""), microtext_merge.answer_text(primary)),
            "category": (active.get("category"), primary.get("corrected_category") or primary.get("category")),
        }
    else:
        fields = {key: (active.get(key), primary.get(key)) for key in ("project_id", "bbox_old", "bbox_new", "image_old", "image_new")}
        fields["description"] = (visualdiff_merge.description(active), visualdiff_merge.description(primary))
        fields["change_type"] = (visualdiff_merge.normalized_change_type(active), visualdiff_merge.normalized_change_type(primary))
    changes = {key: {"active": values[0], "primary": values[1]}
               for key, values in fields.items() if values[0] != values[1]}
    return {
        "status": "reviewed_fields_differ" if changes else "reviewed_fields_match",
        "changed_fields": changes, "semantic_certification": False,
        "limitation": "Field equality links provenance; it does not override auditor disagreement or certify visual truth.",
    }


def collect_supported_candidates(
    actions: list[dict], primary: dict[str, dict], nonpass: set[str]
) -> tuple[dict[str, list[dict]], list[dict], list[dict]]:
    """Deduplicate positive candidate support and hold incomplete legacy proof."""
    prepared: dict[str, dict[str, dict]] = {"microtext": {}, "visualdiff": {}}
    historical_holds: list[dict] = []
    evidence_holds: list[dict] = []
    support_fields = (
        "evidence_sha256",
        "source_workbook_sha256",
        "assignment_payload_sha256",
    )
    for action in actions:
        if action.get("action") != "primary_and_auditor_support_pending_release_gates":
            continue
        identity = action["record_id"]
        if identity in nonpass:
            historical_holds.append(
                {
                    **action,
                    "hold_reason": "another_registered_audit_is_negative_or_uncertain",
                }
            )
            continue
        missing = [field for field in support_fields if not action.get(field)]
        if missing:
            evidence_holds.append(
                {
                    **action,
                    "hold_reason": "legacy_audit_provenance_incomplete",
                    "missing_support_fields": missing,
                }
            )
            continue
        task = action["task_type"]
        support = {
            "reviewer_id": action["reviewer_id"],
            "decision_code": action["decision_code"],
            "decision_source": action.get("decision_source", "returned_workbook"),
            "evidence_sha256": action["evidence_sha256"],
            "source_workbook_sha256": action["source_workbook_sha256"],
            "assignment_payload_sha256": action["assignment_payload_sha256"],
        }
        existing = prepared[task].get(identity)
        if existing is None:
            reviewed = primary[identity]
            existing = {
                **reviewed,
                "safe_to_merge_gold": False,
                "independent_audit_support": support,
                "independent_audit_supports": [],
            }
            prepared[task][identity] = existing
        if support not in existing["independent_audit_supports"]:
            existing["independent_audit_supports"].append(support)
    return (
        {
            task: [rows[key] for key in sorted(rows)]
            for task, rows in prepared.items()
        },
        historical_holds,
        evidence_holds,
    )


def reconcile(
    root: Path,
    processed: Path,
    output: Path,
    *,
    registry_wide_actions: bool = False,
) -> dict:
    root, processed, output = root.resolve(), processed.resolve(), output.resolve()
    if not output.is_relative_to(root / "derived/quality") or output.exists():
        raise ValueError("output must be a new directory under derived/quality")
    report_path = processed / "processing_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise ValueError("source processing report must pass")
    inputs = {str(report_path): p.sha256_file(report_path)}
    for name, expected in report["output_hashes"].items():
        path = processed / name
        if p.sha256_file(path) != expected:
            raise ValueError(f"processing output changed: {name}")
        inputs[str(path)] = expected
    old_report_path = Path(report["source_batch_report"]) if report.get("source_batch_report") else report_path
    if report.get("source_batch_report_sha256") and p.sha256_file(old_report_path) != report["source_batch_report_sha256"]:
        raise ValueError("source batch report changed")
    inputs[str(old_report_path)] = p.sha256_file(old_report_path)
    original = json.loads(old_report_path.read_text(encoding="utf-8"))
    primary = {}
    for entry in original["primary_inputs"]:
        path = Path(entry["path"])
        if p.sha256_file(path) != entry["sha256"]:
            raise ValueError(f"primary source changed: {path}")
        inputs[str(path)] = entry["sha256"]
        for row in ingestion.read_jsonl(path):
            key = p.row_identifier(row)
            if key in primary:
                raise ValueError(f"ambiguous primary identity: {key}")
            primary[key] = {**row, "reconciliation_source": str(path)}
    before = p.active_gold_hashes(root)
    active = ingestion.active_identity_index(root)
    observation_name = "effective_observations.jsonl" if "effective_observations.jsonl" in report["output_hashes"] else "observations.jsonl"
    batch_observations = ingestion.read_jsonl(processed / observation_name)
    history = []
    for path in sorted((root / "derived/human_adjudication/processed_returns").glob("**/*auditor_decisions*.jsonl")):
        inputs[str(path)] = p.sha256_file(path)
        history.extend(ingestion.read_jsonl(path))
    observations = history if registry_wide_actions else batch_observations
    actions = ingestion.summarize_actions(observations, active, primary)
    nonpass = {row["record_id"] for row in history if str(row["decision_code"]) in {"2", "3"}}
    registry_holds = registry_active_holds(history, active)
    prepared, historical_holds, evidence_holds = collect_supported_candidates(
        actions, primary, nonpass
    )
    linked, active_holds, staged_holds = [], [], []
    for action in actions:
        identity = action["record_id"]
        reviewed = primary.get(identity, {})
        if action["active_gold_match"]:
            source = active[identity]
            entry = {**action, "active_split": source.get("split"), "active_row": source,
                     "primary_row": reviewed,
                     "content_comparison": compare_reviewed_content(source, reviewed, action["task_type"])}
            linked.append(entry)
            if action["decision_code"] in {"2", "3"}:
                active_holds.append(entry)
        elif action["decision_code"] in {"2", "3"}:
            staged_holds.append(action)
    after = p.active_gold_hashes(root)
    if before != after:
        raise ValueError("active Gold changed during read-only reconciliation")
    output.mkdir(parents=True)
    artifacts = {
        "candidate_actions.jsonl": actions, "linked_active_audits.jsonl": linked,
        "active_audit_rechecks.jsonl": active_holds, "staged_audit_holds.jsonl": staged_holds,
        "historical_audit_conflict_holds.jsonl": historical_holds,
        "legacy_support_evidence_holds.jsonl": evidence_holds,
        "supported_not_active_microtext.jsonl": prepared["microtext"],
        "supported_not_active_visualdiff.jsonl": prepared["visualdiff"],
        "registry_active_audit_rechecks.jsonl": registry_holds,
    }
    for name, rows in artifacts.items():
        p.write_jsonl(output / name, rows)
    counts = Counter(row["action"] for row in actions)
    result = {
        "goal": "Gold v2.0 Global", "status": "PASS", "observations": len(observations),
        "action_scope": "registry_wide" if registry_wide_actions else "processed_batch",
        "action_counts": dict(counts), "linked_active_rows": len(linked),
        "provenance_alias_links": sum(not r["active_gold_direct_identity_match"] for r in linked),
        "active_audit_flags": len(active_holds),
        "active_flags_by_task": dict(Counter(r["task_type"] for r in active_holds)),
        "active_flags_by_split": dict(Counter(r["active_split"] for r in active_holds)),
        "active_flag_content_comparison": dict(Counter(r["content_comparison"]["status"] for r in active_holds)),
        "registered_audit_decisions": len(history),
        "registry_active_audit_flags": len(registry_holds),
        "registry_active_flags_by_task": dict(Counter(r["task_type"] for r in registry_holds)),
        "registry_active_flags_by_split": dict(Counter(r["active_split"] for r in registry_holds)),
        "staged_audit_flags": len(staged_holds), "historical_conflict_holds": len(historical_holds),
        "legacy_support_evidence_holds": len(evidence_holds),
        "supported_not_active_microtext": len(prepared["microtext"]),
        "supported_not_active_visualdiff": len(prepared["visualdiff"]),
        "active_gold_hashes_before": before, "active_gold_hashes_after": after,
        "active_gold_modified": False, "gold_rows_modified": 0, "safe_to_merge_gold": False,
        "input_hashes": inputs,
        "output_hashes": {name: p.sha256_file(output / name) for name in artifacts},
        "interpretation": "Corrected active/staged bookkeeping only. Source aliases establish identity, not semantic correctness. New candidates still require strict preview; negative and unclear votes are never overridden.",
    }
    ingestion.write_json(output / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--activate-release-report", action="store_true")
    parser.add_argument(
        "--registry-wide-actions",
        action="store_true",
        help="Recompute candidate actions from every registered auditor decision.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    report = reconcile(
        root,
        root / args.processed_dir,
        root / args.output_dir,
        registry_wide_actions=args.registry_wide_actions,
    )
    if args.activate_release_report:
        path = (root / args.output_dir / "report.json").resolve()
        ingestion.write_json(root / CURRENT_REPORT, {"report_path": path.relative_to(root).as_posix(), "report_sha256": p.sha256_file(path)})
    print(json.dumps({k: v for k, v in report.items() if k not in {"input_hashes", "output_hashes", "active_gold_hashes_before", "active_gold_hashes_after"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
