"""Reject known unresolved test rows without changing annotations or votes."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

try:
    from . import audit_active_gold_provenance as provenance
    from .reconcile_auditor_active_links import release_constraint
    from .visualdiff_description_finality import tentative_description_details
except ImportError:
    import audit_active_gold_provenance as provenance
    from reconcile_auditor_active_links import release_constraint
    from visualdiff_description_finality import tentative_description_details


def unique_index(rows: list[dict], key: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"missing or duplicate {key}: {identifier}")
        result[identifier] = row
    return result


def classify_rows(rows: list[dict], active: list[dict], items: list[dict], pairs: list[dict],
                  holds: list[dict], source_report: dict, docs: dict, manifest_pairs: dict) -> list[dict]:
    """Return reason codes for canonical current test rows; never adjudicate a hold."""
    unified = unique_index(active, "id")
    unique_index(rows, "id")
    micro = unique_index(items, "item_id")
    visual = unique_index(pairs, "pair_id")
    held = {(h["task_type"], h["active_gold_identity"]) for h in holds}
    ready_docs = {d["doc_id"] for d in source_report["documents"] if d["paper_ready"]}
    unresolved = {(r["task"], r["id"]) for r in source_report["unresolved_rows"]}
    assessments = []
    for row in rows:
        identifier = row["id"]
        if unified.get(identifier) != row:
            raise ValueError(f"input is not the exact current unified record: {identifier}")
        if row.get("split") != "test":
            continue
        metadata, task = row.get("metadata") or {}, row.get("task")
        if task == "microtext":
            identity = metadata.get("item_id")
            annotation = micro.get(identity)
            doc_ids = [annotation.get("doc_id")] if annotation else []
            resolve_error = "" if all(doc_ids) else "missing_doc_id"
            answer_field = "text_gt"
        elif task == "visualdiff":
            identity = metadata.get("pair_id")
            annotation = visual.get(identity)
            doc_ids, resolve_error = provenance.resolve_visualdiff_docs(annotation or {}, docs, manifest_pairs)
            answer_field = "change_desc_gt"
        else:
            raise ValueError(f"unsupported evaluation task: {task}")
        if not annotation or annotation.get("split") != "test" or annotation.get(answer_field) != row.get("answer"):
            raise ValueError(f"annotation/unified identity, answer or split mismatch: {identifier}")
        reasons = []
        if (task, identity) in held:
            reasons.append("unresolved_independent_audit")
        if task == "visualdiff" and tentative_description_details(str(row.get("answer") or "")):
            reasons.append("tentative_visualdiff_description")
        unready = sorted(str(doc) for doc in doc_ids if doc not in ready_docs)
        if resolve_error or (task, identity) in unresolved or unready:
            reasons.append("source_provenance_not_paper_ready")
        assessments.append({"id": identifier, "task": task, "active_identity": identity,
                            "doc_ids": doc_ids, "unready_doc_ids": unready,
                            "source_resolution_issue": resolve_error,
                            "reason_codes": reasons, "eligible_for_diagnostic_view": not reasons})
    return assessments


def assess(root: Path, rows: list[dict]) -> dict:
    root = root.resolve()
    audit = release_constraint(root)
    if audit["issues"] or not audit.get("report_path"):
        raise ValueError(f"audit reconciliation missing or stale: {audit['issues']}")
    path = root / audit["report_path"]
    holds = provenance.read_jsonl(path.parent / "registry_active_audit_rechecks.jsonl")
    before = {name: provenance.file_sha256(root / name) for name in (
        "eng_bench.jsonl", "manifest.jsonl", "SOURCE_INVENTORY.csv",
        "microtext/annotations/microtext_items.jsonl", "visualdiff/annotations/visualdiff_pairs.jsonl")}
    source_report = provenance.build_report(root)
    docs, manifest_pairs = provenance.manifest_maps(root)
    assessments = classify_rows(rows, provenance.read_jsonl(root / "eng_bench.jsonl"),
        provenance.read_jsonl(root / "microtext/annotations/microtext_items.jsonl"),
        provenance.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl"),
        holds, source_report, docs, manifest_pairs)
    if before != {name: provenance.file_sha256(root / name) for name in before}:
        raise ValueError("release inputs changed during assessment")
    after_audit = release_constraint(root)
    if after_audit != audit:
        raise ValueError("audit reconciliation changed during assessment")
    blocked = [r for r in assessments if r["reason_codes"]]
    return {"goal": "Gold v2.0 Global", "status": "BLOCKED" if blocked else "KNOWN_HOLDS_CLEAR",
            "source_test_rows": len(assessments), "blocked_test_rows": len(blocked),
            "eligible_test_rows": len(assessments) - len(blocked),
            "blocked_by_reason": dict(Counter(reason for r in blocked for reason in r["reason_codes"])),
            "assessments": assessments, "input_hashes": before,
            "source_provenance": source_report,
            "audit_report_path": audit["report_path"], "audit_report_sha256": provenance.file_sha256(path),
            "gold_certified": False,
            "limitation": "Known-hold screening only, not semantic certification or a Gold v2.0 release gate pass."}


def assert_release_eligible(root: Path, rows: list[dict]) -> dict:
    report = assess(root, rows)
    if report["blocked_test_rows"]:
        raise ValueError(f"{report['blocked_test_rows']} unresolved test rows: "
                         f"{json.dumps(report['blocked_by_reason'], sort_keys=True)}")
    return {key: report[key] for key in ("status", "source_test_rows", "blocked_test_rows", "input_hashes",
            "audit_report_path", "audit_report_sha256", "gold_certified", "limitation")}
