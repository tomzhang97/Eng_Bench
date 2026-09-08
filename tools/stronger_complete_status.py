#!/usr/bin/env python3
"""Report progress against the Eng_Bench Stronger Complete target."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import audit_v1_5_gate
import audit_v2_0_gate
import benchmark_health_report


TARGET_DOMAINS = {"pcb_schematic", "pid", "mechanical_cad", "civil", "architectural"}
TARGET_MICROTEXT_ITEMS = 1000
TARGET_MICROTEXT_CATEGORIES = 6
DATE_LABEL_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def relative_to_or_abs(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def dated_compat_label(path: Path) -> str:
    match = DATE_LABEL_RE.search(path.stem)
    return f"{match.group(1)}-compatible" if match else path.stem


def compatible_handoff_index(path: Path, root: Path) -> dict[str, Any]:
    payload = load_json(path)
    handoff = payload.get("handoff_dir")
    if isinstance(handoff, dict):
        split_dir = payload.get("split_dir") if isinstance(payload.get("split_dir"), dict) else {}
        if not handoff.get("valid") or (split_dir and not split_dir.get("valid")):
            return {}
        packet_count = int(handoff.get("packet_folders") or len(handoff.get("packets") or []))
        review_rows = int(handoff.get("worklist_review_rows") or 0)
        totals = handoff.get("totals") if isinstance(handoff.get("totals"), dict) else {}
        return {
            "date_label": dated_compat_label(path),
            "source": "compatible_handoff_verification",
            "source_report": relative_to_or_abs(path, root),
            "folder_path": str(handoff.get("path") or ""),
            "totals": {
                "review_rows": review_rows,
                "packets": packet_count,
                "ready_to_send": packet_count,
                "missing_evidence_refs": 0,
                "overlap_count": 0,
                "checklist_rows": int(totals.get("checklist_rows") or 0),
                "png_files": int(totals.get("png_files") or 0),
                "split_zip_count": int(split_dir.get("zip_count") or 0),
            },
        }

    if payload.get("valid_crc") is True and str(payload.get("windows_expand_archive") or "") == "ok":
        packet_count = int(payload.get("packet_folders") or 0)
        return {
            "date_label": dated_compat_label(path),
            "source": "compat_handoff_verification",
            "source_report": relative_to_or_abs(path, root),
            "folder_path": str(payload.get("handoff_folder") or ""),
            "zip_path": str(payload.get("output_zip") or ""),
            "totals": {
                "review_rows": int(payload.get("review_rows") or 0),
                "packets": packet_count,
                "ready_to_send": packet_count,
                "missing_evidence_refs": 0,
                "overlap_count": 0,
            },
        }
    return {}


def latest_human_packet_index(root: Path) -> dict[str, Any]:
    # Sort by label stem, not filename: same-day follow-on labels such as
    # "2026-06-11-feather" supersede "2026-06-11", but ".json" would sort
    # after "-feather" and silently pick the stale plain-date index.
    quality = root / "derived" / "quality"
    candidates: list[tuple[str, int, str, dict[str, Any]]] = []
    for path in quality.glob("human_packet_index_*.json"):
        payload = load_json(path)
        label = str(payload.get("date_label") or path.stem.replace("human_packet_index_", ""))
        candidates.append((label, path.stat().st_mtime_ns, path.name, payload))
    for pattern in ("compatible_handoff_verification_*.json", "compat_handoff_verification_*.json"):
        for path in quality.glob(pattern):
            payload = compatible_handoff_index(path, root)
            if payload:
                candidates.append(
                    (
                        str(payload["date_label"]),
                        path.stat().st_mtime_ns,
                        path.name,
                        payload,
                    )
                )
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3] if candidates else {}


def load_inventory(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_handoff_csvs(root: Path, pattern: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    handoff = root / "derived" / "human_adjudication" / "2026-05-18_v1_expansion_handoff"
    for path in sorted(handoff.glob(pattern)):
        rows.extend(load_csv_rows(path))
    return rows


def load_manifest(root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = load_jsonl(root / "manifest.jsonl")
    doc_by_id = {
        str(row.get("doc_id")): row
        for row in rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pair_by_id = {
        str(row.get("pair_id")): row
        for row in rows
        if row.get("type") == "pair" and row.get("pair_id")
    }
    return rows, doc_by_id, pair_by_id


def visualdiff_doc_for_version(
    doc_id: str,
    version_id: str,
    doc_by_id: dict[str, dict[str, Any]],
) -> str | None:
    for manifest_doc_id, row in doc_by_id.items():
        if row.get("task") != "visualdiff":
            continue
        same_model = str(row.get("same_model_id", "")).lower()
        version = row.get("version") or {}
        if doc_id == "viola" and "viola" in same_model:
            if version.get("pcb_version") == version_id:
                return manifest_doc_id
        if doc_id == "bbb" and "beagle" in same_model:
            if version.get("sch_rev") == version_id:
                return manifest_doc_id
    return None


def active_gold_doc_ids(
    visualdiff_pairs: list[dict[str, Any]],
    microtext_items: list[dict[str, Any]],
    doc_by_id: dict[str, dict[str, Any]],
    pair_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    active_docs = {
        str(row.get("doc_id"))
        for row in microtext_items
        if row.get("doc_id")
    }
    mapped_projects: set[str] = set()
    for project_id in {str(row.get("project_id")) for row in visualdiff_pairs if row.get("project_id")}:
        pair_row = pair_by_id.get(project_id)
        if not pair_row:
            continue
        mapped_projects.add(project_id)
        for key in ("from_doc_id", "to_doc_id"):
            if pair_row.get(key):
                active_docs.add(str(pair_row[key]))

    for row in visualdiff_pairs:
        if str(row.get("project_id")) in mapped_projects:
            continue
        old_doc = visualdiff_doc_for_version(
            str(row.get("doc_id", "")),
            str(row.get("version_id_old", "")),
            doc_by_id,
        )
        new_doc = visualdiff_doc_for_version(
            str(row.get("doc_id", "")),
            str(row.get("version_id_new", "")),
            doc_by_id,
        )
        if old_doc:
            active_docs.add(old_doc)
        if new_doc:
            active_docs.add(new_doc)
    return active_docs


def inventory_by_doc_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {str(row.get("doc_id")): row for row in rows if row.get("doc_id")}


def source_blocker(row: dict[str, str], manifest_row: dict[str, Any] | None = None) -> str | None:
    manifest_row = manifest_row or {}
    status = str(row.get("public_status") or manifest_row.get("public_status") or "unknown").strip()
    source_url = str(row.get("source_url") or manifest_row.get("source_url") or "").strip()
    status_lower = status.lower()
    if status_lower in {"", "unknown", "none"}:
        return "missing_or_unknown_public_status"
    if "release_review_needed" in status_lower:
        return "release_review_needed"
    if "rights_uncertain" in status_lower:
        return "rights_uncertain"
    if "internal" in status_lower:
        return "internal_only"
    if not source_url:
        return "missing_source_url"
    return None


def release_gate_status(root: Path) -> dict[str, Any]:
    packaging = benchmark_health_report.compute_health(root, release_target="v0.95")[
        "release_gate_status"
    ]
    gold = benchmark_health_report.compute_health(root, release_target="v1.0")[
        "release_gate_status"
    ]
    public = audit_v1_5_gate.collect_status(root)
    global_gate = audit_v2_0_gate.collect_status(root)
    return {
        "packaging_clean_v0_95": {
            "passes": bool(packaging["passed"]),
            "blockers": list(packaging["blockers"]),
        },
        "gold_v1_0": {
            "passes": bool(gold["passed"]),
            "blockers": list(gold["blockers"]),
        },
        "public_v1_5": {
            "passes": bool(public["v1_5_public_complete"]),
            "failed_gates": [
                name for name, row in public["gates"].items() if not row["passes"]
            ],
            "gates": public["gates"],
        },
        "global_v2_0": {
            "passes": bool(global_gate["v2_0_global_complete"]),
            "failed_gates": [
                name for name, row in global_gate["gates"].items() if not row["passes"]
            ],
            "gates": global_gate["gates"],
            "release_constraints": global_gate.get("release_constraints", {}),
        },
    }


def compute_status(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    visualdiff_pairs = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    microtext_items = load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    microtext_candidates = load_jsonl(
        root / "microtext" / "annotations" / "microtext_candidates.jsonl"
    )
    review_batch_rows: list[dict[str, Any]] = []
    for path in sorted((root / "microtext" / "annotations").glob("microtext_review_batch_*.jsonl")):
        review_batch_rows.extend(load_jsonl(path))
    handoff_microtext_rows = load_handoff_csvs(root, "microtext_*_validation_checklist.csv")
    handoff_visualdiff_rows = load_handoff_csvs(root, "visualdiff_*_validation_checklist.csv")
    inventory = load_inventory(root / "SOURCE_INVENTORY.csv")
    source_candidates = load_inventory(root / "SOURCE_CANDIDATES.csv")
    source_validation = load_inventory(root / "SOURCE_CANDIDATE_VALIDATION.csv")
    packet_index = latest_human_packet_index(root)
    packet_totals = packet_index.get("totals") if isinstance(packet_index.get("totals"), dict) else {}
    _, doc_by_id, pair_by_id = load_manifest(root)
    inventory_docs = inventory_by_doc_id(inventory)

    todo_dev_test = sum(
        1
        for row in visualdiff_pairs
        if row.get("split") in {"dev", "test"}
        and row.get("change_desc_gt") in {"", "CHANGE_DESC_GT_TODO"}
    )
    todo_total = sum(
        1 for row in visualdiff_pairs if row.get("change_desc_gt") in {"", "CHANGE_DESC_GT_TODO"}
    )

    categories = Counter(str(row.get("category", "unknown")) for row in microtext_items)
    microtext_split_counts = Counter(str(row.get("split", "unknown")) for row in microtext_items)
    microtext_categories_by_split: dict[str, Counter[str]] = {}
    for row in microtext_items:
        split = str(row.get("split", "unknown"))
        if split not in microtext_categories_by_split:
            microtext_categories_by_split[split] = Counter()
        microtext_categories_by_split[split][str(row.get("category", "unknown"))] += 1
    candidate_categories = Counter(str(row.get("category", "unknown")) for row in microtext_candidates)
    review_batch_categories = Counter(str(row.get("category", "unknown")) for row in review_batch_rows)
    handoff_microtext_categories = Counter(
        str(row.get("category", "unknown")) for row in handoff_microtext_rows
    )
    domains = {row.get("domain", "unknown") for row in inventory if row.get("domain")}
    candidate_domains = {row.get("domain", "unknown") for row in source_candidates if row.get("domain")}
    candidate_rights_tiers = Counter(
        str(row.get("rights_tier") or row.get("public_status") or "unknown")
        for row in source_candidates
    )
    candidate_task_fit = Counter(
        str(row.get("task_fit") or "unknown") for row in source_candidates
    )
    validation_next_actions = Counter(
        str(row.get("next_action") or "unknown") for row in source_validation
    )
    validation_validity = Counter(
        str(row.get("source_validity") or "unknown") for row in source_validation
    )
    public_unknown = sum(1 for row in inventory if row.get("public_status", "unknown") == "unknown")
    public_candidate = sum(
        1
        for row in source_candidates
        if row.get("public_status") == "public_domain_candidate"
        or row.get("rights_tier") in {"public_candidate", "misc_public_candidate"}
    )
    active_docs = active_gold_doc_ids(visualdiff_pairs, microtext_items, doc_by_id, pair_by_id)
    active_source_blockers = []
    active_public_status = Counter()
    for doc_id in sorted(active_docs):
        manifest_row = doc_by_id.get(doc_id, {})
        row = inventory_docs.get(doc_id, {})
        status_text = str(row.get("public_status") or manifest_row.get("public_status") or "unknown")
        active_public_status[status_text] += 1
        reason = source_blocker(row, manifest_row)
        if reason:
            active_source_blockers.append(
                {
                    "doc_id": doc_id,
                    "reason": reason,
                    "public_status": status_text,
                    "source_url": str(row.get("source_url") or manifest_row.get("source_url") or ""),
                    "path": str(row.get("path") or manifest_row.get("path") or ""),
                }
            )

    status = {
        "visualdiff": {
            "pairs": len(visualdiff_pairs),
            "todo_total": todo_total,
            "todo_dev_test": todo_dev_test,
            "dev_test_todo_clear": todo_dev_test == 0,
        },
        "microtext": {
            "items": len(microtext_items),
            "items_remaining_to_1000": max(0, TARGET_MICROTEXT_ITEMS - len(microtext_items)),
            "categories": dict(sorted(categories.items())),
            "split_counts": dict(sorted(microtext_split_counts.items())),
            "categories_by_split": {
                split: dict(sorted(counter.items()))
                for split, counter in sorted(microtext_categories_by_split.items())
            },
            "category_count": len(categories),
            "categories_remaining_to_6": max(0, TARGET_MICROTEXT_CATEGORIES - len(categories)),
            "candidate_items": len(microtext_candidates),
            "candidate_categories": dict(sorted(candidate_categories.items())),
            "candidate_category_count": len(candidate_categories),
            "review_batch_items": len(review_batch_rows),
            "review_batch_categories": dict(sorted(review_batch_categories.items())),
            "review_batch_category_count": len(review_batch_categories),
            "handoff_items": len(handoff_microtext_rows),
            "handoff_categories": dict(sorted(handoff_microtext_categories.items())),
            "handoff_category_count": len(handoff_microtext_categories),
        },
        "handoff": {
            "microtext_rows": len(handoff_microtext_rows),
            "visualdiff_rows": len(handoff_visualdiff_rows),
            "active_packet_date_label": str(packet_index.get("date_label") or ""),
            "active_packet_review_rows": int(packet_totals.get("review_rows") or 0),
            "active_packet_count": int(packet_totals.get("packets") or 0),
            "active_packets_ready": int(packet_totals.get("ready_to_send") or 0),
            "active_packet_missing_evidence_refs": int(
                packet_totals.get("missing_evidence_refs") or 0
            ),
            "active_packet_overlap_count": int(packet_totals.get("overlap_count") or 0),
        },
        "domains": {
            "present": sorted(domains),
            "target_present": sorted(domains & TARGET_DOMAINS),
            "target_missing": sorted(TARGET_DOMAINS - domains),
            "target_count": len(domains & TARGET_DOMAINS),
            "candidate_present": sorted(candidate_domains & TARGET_DOMAINS),
            "missing_after_candidates": sorted(TARGET_DOMAINS - (domains | candidate_domains)),
        },
        "sources": {
            "inventory_rows": len(inventory),
            "unknown_public_status": public_unknown,
            "active_gold_doc_ids": sorted(active_docs),
            "active_public_status": dict(sorted(active_public_status.items())),
            "active_source_blockers": active_source_blockers,
            "active_source_blocker_count": len(active_source_blockers),
            "candidate_rows": len(source_candidates),
            "public_domain_candidates": public_candidate,
            "browser_validation_rows": len(source_validation),
            "browser_validation_next_actions": dict(sorted(validation_next_actions.items())),
            "browser_validation_validity": dict(sorted(validation_validity.items())),
            "candidate_rights_tiers": dict(sorted(candidate_rights_tiers.items())),
            "candidate_task_fit": dict(sorted(candidate_task_fit.items())),
        },
    }
    legacy_stronger_complete = (
        status["visualdiff"]["dev_test_todo_clear"]
        and status["microtext"]["items"] >= TARGET_MICROTEXT_ITEMS
        and status["microtext"]["category_count"] >= TARGET_MICROTEXT_CATEGORIES
        and status["domains"]["target_count"] >= len(TARGET_DOMAINS)
        and status["sources"]["active_source_blocker_count"] == 0
    )
    status["legacy_stronger_complete"] = legacy_stronger_complete
    status["stronger_complete"] = legacy_stronger_complete
    status["release_gates"] = release_gate_status(root)
    return status


def gate_remaining(gate: dict[str, Any]) -> str:
    remaining = list(gate.get("blockers") or gate.get("failed_gates") or [])
    remaining.extend(
        name for name, row in gate.get("release_constraints", {}).items()
        if not row["passes"]
    )
    return ", ".join(str(item) for item in remaining) or "none"


def markdown_report(status: dict[str, Any]) -> str:
    v = status["visualdiff"]
    m = status["microtext"]
    h = status["handoff"]
    d = status["domains"]
    s = status["sources"]
    release_gates = status["release_gates"]
    global_gates = release_gates["global_v2_0"]["gates"]
    global_gates_passed = sum(1 for gate in global_gates.values() if gate.get("passes"))
    next_work = []
    if not v["dev_test_todo_clear"]:
        next_work.append("- Clear all visualdiff dev/test TODO labels through human review.")
    if m["items"] < TARGET_MICROTEXT_ITEMS or m["category_count"] < TARGET_MICROTEXT_CATEGORIES:
        next_work.append("- Expand microtext to at least 1000 reviewed items across at least 6 categories.")
    else:
        next_work.append("- Preserve the frozen microtext split while expanding non-PCB train/dev coverage.")
    if d["target_missing"]:
        next_work.append("- Add or classify sources so all 5 target domains are covered.")
    if s["active_source_blocker_count"]:
        next_work.append("- Resolve active gold source blockers before any public release.")
    elif s["unknown_public_status"]:
        next_work.append("- Resolve inactive/reference unknown public status before packaging cleanup.")
    if s["browser_validation_next_actions"].get("intake_first", 0):
        next_work.append("- Continue importing browser-validated `intake_first` sources, then render, text-extract, mine, and review candidates.")
    if not release_gates["gold_v1_0"]["passes"]:
        next_work.append(
            "- Gold v1.0 remains blocked by: "
            + gate_remaining(release_gates["gold_v1_0"])
            + "."
        )
    if not release_gates["global_v2_0"]["passes"]:
        next_work.append(
            "- Global v2.0 remains blocked by: "
            + gate_remaining(release_gates["global_v2_0"])
            + "."
        )
    return "\n".join(
        [
            "# Eng_Bench Maturity And Stronger-Slice Status",
            "",
            "## Summary",
            f"- Legacy stronger slice complete (not a release gate): `{status['legacy_stronger_complete']}`",
            f"- Packaging-Clean v0.95 complete: `{release_gates['packaging_clean_v0_95']['passes']}`",
            f"- Gold v1.0 complete: `{release_gates['gold_v1_0']['passes']}`",
            f"- Public v1.5 complete: `{release_gates['public_v1_5']['passes']}`",
            f"- Global v2.0 complete: `{release_gates['global_v2_0']['passes']}`",
            f"- Gold v2.0 Global gates passing: `{global_gates_passed}/{len(global_gates)}`",
            f"- Active Gold rows: `{global_gates['total_rows']['current']}` / `{global_gates['total_rows']['target']}`",
            f"- Paper-ready Gold payloads: `{global_gates['gold_source_docs']['current']}` / `{global_gates['gold_source_docs']['target']}`",
            f"- VisualDiff revision families: `{global_gates['visualdiff_revision_families']['current']}` / `{global_gates['visualdiff_revision_families']['target']}`",
            f"- Test examples: `{global_gates['hidden_public_test_examples']['current']}` / `{global_gates['hidden_public_test_examples']['target']}`",
            f"- Formal agreement: `{global_gates['human_agreement_audit']['current']}`",
            f"- Active provenance: `{global_gates['paper_ready_provenance']['current']}`",
            f"- Visualdiff dev/test TODO remaining: `{v['todo_dev_test']}`",
            f"- Microtext items: `{m['items']}` / 1000",
            f"- Microtext candidates awaiting review: `{m['candidate_items']}`",
            f"- Microtext rows staged in review batches: `{m['review_batch_items']}`",
            f"- Active human packet date label: `{h['active_packet_date_label'] or 'not available'}`",
            f"- Active human packet review rows: `{h['active_packet_review_rows']}`",
            f"- Active human packets ready: `{h['active_packets_ready']}` / `{h['active_packet_count']}`",
            f"- Active packet missing evidence refs: `{h['active_packet_missing_evidence_refs']}`",
            f"- Active packet overlap count: `{h['active_packet_overlap_count']}`",
            f"- Historical 2026-05-18 handoff CSV rows: microtext `{h['microtext_rows']}`, visualdiff `{h['visualdiff_rows']}`",
            f"- Microtext categories: `{m['category_count']}` / 6",
            f"- Candidate categories: `{m['candidate_category_count']}`",
            f"- Target domains present: `{d['target_count']}` / 5",
            f"- Source inventory rows: `{s['inventory_rows']}`",
            f"- Source candidate rows: `{s['candidate_rows']}`",
            f"- Browser-validated source candidates: `{s['browser_validation_rows']}`",
            f"- Intake-first browser-validated candidates: `{s['browser_validation_next_actions'].get('intake_first', 0)}`",
            f"- Sources with unknown public status: `{s['unknown_public_status']}`",
            f"- Active gold source blockers: `{s['active_source_blocker_count']}`",
            "",
            "## Release Gate Snapshot",
            "",
            "| Gate | Pass | Remaining blockers or failed checks |",
            "| --- | --- | --- |",
            f"| Packaging-Clean v0.95 | `{release_gates['packaging_clean_v0_95']['passes']}` | {gate_remaining(release_gates['packaging_clean_v0_95'])} |",
            f"| Gold v1.0 | `{release_gates['gold_v1_0']['passes']}` | {gate_remaining(release_gates['gold_v1_0'])} |",
            f"| Public v1.5 | `{release_gates['public_v1_5']['passes']}` | {gate_remaining(release_gates['public_v1_5'])} |",
            f"| Global v2.0 | `{release_gates['global_v2_0']['passes']}` | {gate_remaining(release_gates['global_v2_0'])} |",
            "",
            "## Additional Global Release Constraints",
            *[
                f"- {name}: `{row['current']}` / `{row['target']}`; pass `{row['passes']}`"
                for name, row in release_gates['global_v2_0'].get('release_constraints', {}).items()
            ],
            "",
            "## Active Gold Sources",
            f"- Active source docs: {', '.join(s['active_gold_doc_ids']) or 'none'}",
            *[f"- {name}: {count}" for name, count in s["active_public_status"].items()],
            "",
            "## Active Source Blockers",
            *([
                "- {doc_id}: {reason} ({public_status})".format(**row)
                for row in s["active_source_blockers"]
            ] or ["- none"]),
            "",
            "## Source Candidate Rights Tiers",
            *[f"- {name}: {count}" for name, count in s["candidate_rights_tiers"].items()],
            "",
            "## Source Candidate Task Fit",
            *[f"- {name}: {count}" for name, count in s["candidate_task_fit"].items()],
            "",
            "## Browser Validation Next Actions",
            *[f"- {name}: {count}" for name, count in s["browser_validation_next_actions"].items()],
            "",
            "## Domain Gaps",
            f"- Present target domains: {', '.join(d['target_present']) or 'none'}",
            f"- Missing target domains: {', '.join(d['target_missing']) or 'none'}",
            f"- Target domains covered by source candidates: {', '.join(d['candidate_present']) or 'none'}",
            f"- Missing after candidates: {', '.join(d['missing_after_candidates']) or 'none'}",
            "",
            "## Microtext Categories",
            *[f"- {name}: {count}" for name, count in m["categories"].items()],
            "",
            "## Microtext Split Counts",
            *[f"- {name}: {count}" for name, count in m["split_counts"].items()],
            "",
            "## Microtext Categories By Split",
            *[
                f"- {split}: "
                + ", ".join(f"{name}={count}" for name, count in categories.items())
                for split, categories in m["categories_by_split"].items()
            ],
            "",
            "## Microtext Candidate Categories",
            *[f"- {name}: {count}" for name, count in m["candidate_categories"].items()],
            "",
            "## Microtext Review Batch Categories",
            *[f"- {name}: {count}" for name, count in m["review_batch_categories"].items()],
            "",
            "## Microtext Handoff Categories",
            *[f"- {name}: {count}" for name, count in m["handoff_categories"].items()],
            "",
            "## Next Required Work",
            *next_work,
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Stronger Complete progress")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="STRONGER_COMPLETE_STATUS.md")
    args = parser.parse_args()

    status = compute_status(Path(args.root))
    Path(args.output).write_text(markdown_report(status), encoding="utf-8")
    print(f"[OK] Wrote stronger-complete status to {args.output}")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
