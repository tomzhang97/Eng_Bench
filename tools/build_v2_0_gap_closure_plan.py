#!/usr/bin/env python3
"""Build a conservative v2.0 gap-closure forecast for Eng_Bench."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    import audit_v2_0_gate
except ModuleNotFoundError:  # pragma: no cover - used only in unusual package loaders
    from tools import audit_v2_0_gate  # type: ignore


ACCEPTANCE_RATES = (1.0, 0.75, 0.5, 0.25)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def latest_path(root: Path, patterns: tuple[str, ...]) -> Path | None:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend((root / "derived" / "quality").glob(pattern))
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name)) if paths else None


def target_minimum(target: Any) -> int | None:
    if isinstance(target, bool):
        return None
    if isinstance(target, int):
        return target
    if isinstance(target, float):
        return int(target)
    text = str(target or "").strip()
    if not text:
        return None
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else None


def numeric_current(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else None


def gate_summary(gates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name, row in gates.items():
        if not isinstance(row, dict):
            continue
        current = numeric_current(row.get("current"))
        target = target_minimum(row.get("target"))
        remaining = None
        if current is not None and target is not None:
            remaining = max(0, target - current)
        summary[name] = {
            "current": row.get("current"),
            "target": row.get("target"),
            "passes": bool(row.get("passes")),
            "remaining_to_target": remaining,
        }
        for key in ("incomplete_rows", "report_path"):
            if key in row:
                summary[name][key] = row[key]
    return summary


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def task_for_checklist(path: Path, rows: list[dict[str, str]]) -> str:
    name = path.name.lower()
    text_path = path.as_posix().lower()
    if "visualdiff" in name or "visualdiff" in text_path:
        return "visualdiff"
    if "microtext" in name or "microtext" in text_path:
        return "microtext"
    if rows:
        keys = set(rows[0])
        if "pair_id" in keys:
            return "visualdiff"
        if "candidate_id" in keys or "doc_id" in keys:
            return "microtext"
    return "unknown"


def packet_name_for(path: Path) -> str:
    parts = path.parts
    for part in parts:
        if part.startswith("p") and part[1:].isdigit():
            return part
    return "unknown"


def summarize_handoff_checklists(root: Path, handoff_root: Path | None) -> dict[str, Any]:
    if handoff_root is None or not handoff_root.exists():
        return {
            "checklist_files_scanned": 0,
            "checklist_rows_scanned": 0,
            "rows_by_task": {},
            "rows_by_split": {},
            "rows_by_category": {},
            "rows_by_packet": {},
            "visualdiff_families": 0,
            "microtext_doc_ids": 0,
        }
    files = sorted((handoff_root / "01_packets").rglob("*validation_checklist.csv"))
    rows_by_task: Counter[str] = Counter()
    rows_by_split: Counter[str] = Counter()
    rows_by_category: Counter[str] = Counter()
    rows_by_packet: Counter[str] = Counter()
    visualdiff_families: set[str] = set()
    microtext_doc_ids: set[str] = set()
    total_rows = 0
    for path in files:
        rows = csv_rows(path)
        task = task_for_checklist(path, rows)
        packet = packet_name_for(path.relative_to(handoff_root))
        for row in rows:
            total_rows += 1
            rows_by_task[task] += 1
            rows_by_packet[packet] += 1
            split = str(row.get("split") or "unknown").strip() or "unknown"
            rows_by_split[split] += 1
            category = str(row.get("category") or row.get("change_type") or "unknown").strip() or "unknown"
            rows_by_category[category] += 1
            if task == "visualdiff":
                family = str(row.get("project_id") or "").strip()
                if not family and row.get("pair_id"):
                    family = "__".join(str(row["pair_id"]).split("__")[:4])
                if family:
                    visualdiff_families.add(family)
            elif task == "microtext" and row.get("doc_id"):
                microtext_doc_ids.add(str(row["doc_id"]))
    return {
        "checklist_files_scanned": len(files),
        "checklist_rows_scanned": total_rows,
        "rows_by_task": dict(sorted(rows_by_task.items())),
        "rows_by_split": dict(sorted(rows_by_split.items())),
        "rows_by_category": dict(sorted(rows_by_category.items())),
        "rows_by_packet": dict(sorted(rows_by_packet.items())),
        "visualdiff_families": len(visualdiff_families),
        "microtext_doc_ids": len(microtext_doc_ids),
    }


def handoff_summary(root: Path, handoff_report: dict[str, Any]) -> dict[str, Any]:
    handoff_dir = handoff_report.get("handoff_dir") if isinstance(handoff_report.get("handoff_dir"), dict) else {}
    split_dir = handoff_report.get("split_dir") if isinstance(handoff_report.get("split_dir"), dict) else {}
    totals = handoff_dir.get("totals") if isinstance(handoff_dir.get("totals"), dict) else {}
    handoff_path_text = str(handoff_dir.get("path") or "")
    handoff_root = root / handoff_path_text if handoff_path_text else None
    checklist_summary = summarize_handoff_checklists(root, handoff_root)
    return {
        "valid": bool(handoff_report.get("valid", handoff_dir.get("valid", False))),
        "path": handoff_path_text,
        "packet_count": int(handoff_dir.get("packet_folders") or len(handoff_dir.get("packets") or [])),
        "ready_packet_count": sum(1 for packet in handoff_dir.get("packets", []) if packet.get("valid"))
        if isinstance(handoff_dir.get("packets"), list)
        else int(handoff_dir.get("packet_folders") or 0),
        "review_rows": int(handoff_dir.get("worklist_review_rows") or 0),
        "checklist_rows": int(totals.get("checklist_rows") or 0),
        "png_files": int(totals.get("png_files") or 0),
        "split_zip_count": int(split_dir.get("zip_count") or 0),
        **checklist_summary,
    }


def count_source_validation(root: Path) -> dict[str, Any]:
    path = root / "SOURCE_CANDIDATE_VALIDATION.csv"
    if not path.exists():
        return {"rows": 0, "next_actions": {}, "recommended_next_steps": {}, "validity": {}, "release_posture": {}}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    next_actions = Counter(
        row.get("next_action") or row.get("validation_next_action") or "unknown"
        for row in rows
    )
    recommended_steps = Counter(
        row.get("recommended_next_step") or row.get("next_step") or "unknown"
        for row in rows
    )
    return {
        "rows": len(rows),
        "next_actions": dict(sorted(next_actions.items())),
        "recommended_next_steps": dict(sorted(recommended_steps.items())),
        "validity": dict(sorted(Counter(row.get("source_validity") or "unknown" for row in rows).items())),
        "release_posture": dict(sorted(Counter(row.get("release_posture") or "unknown" for row in rows).items())),
    }


def source_readiness_summary(
    root: Path,
    source_readiness_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = None
    if source_readiness_report is None:
        path = latest_path(root, ("source_conversion_readiness_*.json",))
        source_readiness_report = load_json(path) if path else {}
    totals = source_readiness_report.get("totals") if isinstance(source_readiness_report.get("totals"), dict) else {}
    return {
        "report_path": path.as_posix() if path else "",
        "date_label": totals.get("date_label", ""),
        "active_packet_index_label": totals.get("active_packet_index_label", ""),
        "active_packet_row_keys": int(totals.get("active_packet_row_keys") or 0),
        "open_review_rows_by_local_source": int(totals.get("open_review_rows_by_local_source") or 0),
        "packeted_open_review_rows_by_local_source": int(
            totals.get("packeted_open_review_rows_by_local_source") or 0
        ),
        "unpacketed_open_review_rows_by_local_source": int(
            totals.get("unpacketed_open_review_rows_by_local_source") or 0
        ),
        "fresh_open_review_rows_by_local_source": int(totals.get("fresh_open_review_rows_by_local_source") or 0),
        "fresh_open_review_rows_by_candidate": int(totals.get("fresh_open_review_rows_by_candidate") or 0),
        "local_by_next_step": dict(totals.get("local_by_next_step") or {}),
        "candidate_by_next_step": dict(totals.get("candidate_by_next_step") or {}),
    }


def active_packet_index_summary(
    root: Path,
    packet_index_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = None
    if packet_index_report is None:
        path = latest_path(root, ("human_packet_index_*.json", "supplemental_review_pack_index_*.json"))
        packet_index_report = load_json(path) if path else {}
    totals = packet_index_report.get("totals") if isinstance(packet_index_report.get("totals"), dict) else {}
    packets = packet_index_report.get("packets") if isinstance(packet_index_report.get("packets"), list) else None
    if packets is None:
        packets = packet_index_report.get("packs") if isinstance(packet_index_report.get("packs"), list) else []
    ready_packets = [
        {
            "packet_id": packet.get("packet_id", ""),
            "review_rows": int(packet.get("review_rows") or packet.get("manifest_rows") or packet.get("checklist_rows") or 0),
            "gold_expansion_rows": int(
                packet.get("gold_expansion_rows")
                if "gold_expansion_rows" in packet
                else packet.get("review_rows") or packet.get("manifest_rows") or packet.get("checklist_rows") or 0
            ),
            "return_command": packet.get("return_command", ""),
        }
        for packet in packets
        if packet.get("ready_to_send")
    ]
    review_rows = int(totals.get("review_rows") or totals.get("manifest_rows") or totals.get("checklist_rows") or 0)
    gold_expansion_rows = int(
        totals.get("gold_expansion_rows")
        if "gold_expansion_rows" in totals
        else review_rows
    )
    return {
        "report_path": path.as_posix() if path else "",
        "date_label": packet_index_report.get("date_label", ""),
        "base_date_label": packet_index_report.get("base_date_label", ""),
        "packets": int(totals.get("packets") or totals.get("packs") or len(packets)),
        "ready_to_send": int(totals.get("ready_to_send") or len(ready_packets)),
        "verified_packets": int(totals.get("verified_packets") or 0),
        "review_rows": review_rows,
        "gold_expansion_rows": gold_expansion_rows,
        "crop_files": int(totals.get("crop_files") or 0),
        "page_files": int(totals.get("page_files") or 0),
        "missing_evidence_refs": int(totals.get("missing_evidence_refs") or 0),
        "overlap_count": int(totals.get("overlap_count") or 0),
        "mergeable_rows": int(totals.get("mergeable_rows") or 0),
        "ready_packets": ready_packets[:10],
    }


def acceptance_scenarios(
    gates: dict[str, dict[str, Any]],
    handoff: dict[str, Any],
    packet_index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    total_current = numeric_current(gates.get("total_rows", {}).get("current")) or 0
    total_target = target_minimum(gates.get("total_rows", {}).get("target")) or 0
    test_current = numeric_current(gates.get("hidden_public_test_examples", {}).get("current")) or 0
    test_target = target_minimum(gates.get("hidden_public_test_examples", {}).get("target")) or 0
    packet_index = packet_index or {}
    handoff_rows = int(handoff.get("review_rows") or 0)
    packet_rows = int(packet_index.get("review_rows") or 0)
    handoff_expansion_rows = int(
        handoff.get("gold_expansion_rows")
        if "gold_expansion_rows" in handoff
        else handoff_rows
    )
    packet_expansion_rows = int(
        packet_index.get("gold_expansion_rows")
        if "gold_expansion_rows" in packet_index
        else packet_rows
    )
    expansion_rows = handoff_expansion_rows if handoff_rows else packet_expansion_rows
    row_source = "active_handoff" if handoff_rows else ("active_packet_index" if packet_rows else "none")
    known_test_rows = int(handoff.get("rows_by_split", {}).get("test", 0))
    scenarios = []
    for rate in ACCEPTANCE_RATES:
        accepted_rows = math.floor(expansion_rows * rate)
        accepted_test_rows = math.floor(known_test_rows * rate)
        total_after = total_current + accepted_rows
        test_after = test_current + accepted_test_rows
        scenarios.append(
            {
                "acceptance_rate": rate,
                "label": f"{int(rate * 100)}% acceptance",
                "row_source": row_source,
                "accepted_rows": accepted_rows,
                "accepted_known_test_rows": accepted_test_rows,
                "total_rows_after": total_after,
                "total_rows_remaining_to_v2_min": max(0, total_target - total_after),
                "hidden_public_test_examples_after_known_test_rows": test_after,
                "hidden_public_test_examples_remaining": max(0, test_target - test_after),
            }
        )
    return scenarios


def next_human_actions(
    handoff: dict[str, Any],
    gates: dict[str, dict[str, Any]],
    packet_index: dict[str, Any] | None = None,
) -> list[str]:
    actions = [
        "Human returns must fill the compatible handoff CSVs first; review rows are not current gold until processed and adjudicated.",
    ]
    packet_index = packet_index or {}
    if packet_index.get("ready_to_send") and packet_index.get("review_rows"):
        actions.append(
            f"Return the active human packets: {packet_index['ready_to_send']} ready packets with {packet_index['review_rows']} review rows, {packet_index.get('missing_evidence_refs', 0)} missing evidence refs, and {packet_index.get('overlap_count', 0)} overlaps."
        )
    agreement = gates.get("human_agreement_audit", {})
    if not agreement.get("passes", False):
        actions.append(
            f"Complete the independent agreement audit ({agreement.get('current', 'unknown')} now) and resolve any adjudication queue before a gold release."
        )
    if handoff.get("checklist_rows_scanned") and handoff.get("review_rows"):
        actions.append(
            f"Prioritize packet order p01-p{int(handoff.get('packet_count') or 0):02d}; the current package has {handoff['review_rows']} worklist rows and {handoff['checklist_rows_scanned']} checklist rows scanned."
        )
    return actions


def next_machine_actions(
    gates: dict[str, dict[str, Any]],
    source_validation: dict[str, Any],
    scenarios: list[dict[str, Any]],
    source_readiness: dict[str, Any] | None = None,
    packet_index: dict[str, Any] | None = None,
) -> list[str]:
    actions = []
    source_readiness = source_readiness or {}
    packet_index = packet_index or {}
    fresh_local = int(source_readiness.get("fresh_open_review_rows_by_local_source") or 0)
    fresh_candidate = int(source_readiness.get("fresh_open_review_rows_by_candidate") or 0)
    if packet_index.get("review_rows") and fresh_local == 0 and fresh_candidate == 0:
        actions.append(
            f"Wait for active human returns before packeting more rows; source readiness shows 0 fresh actionable unpacketed rows while {packet_index['review_rows']} rows are already in active human packets."
        )
    elif fresh_local or fresh_candidate:
        actions.append(
            f"Build the next review packet from fresh actionable source-readiness rows ({fresh_local} local-source rows, {fresh_candidate} candidate rows)."
        )
    if scenarios and scenarios[0]["total_rows_remaining_to_v2_min"] > 0:
        scenario_source = "active packet set" if scenarios[0].get("row_source") == "active_packet_index" else "active handoff"
        actions.append(
            f"Keep converting release-safe sources into review packets; even 100% acceptance of the {scenario_source} leaves {scenarios[0]['total_rows_remaining_to_v2_min']} rows to reach the v2.0 minimum."
        )
    source_gap = gates.get("gold_source_docs", {}).get("remaining_to_target")
    if isinstance(source_gap, int) and source_gap > 0:
        actions.append(
            f"Promote reviewed rows from more source documents after human approval; active gold is short by {source_gap} source docs."
        )
    family_gap = gates.get("visualdiff_revision_families", {}).get("remaining_to_target")
    if isinstance(family_gap, int) and family_gap > 0:
        actions.append(
            f"Continue visualdiff revision-family imports; active gold is short by {family_gap} revision families."
        )
    intake_first = int(source_validation.get("next_actions", {}).get("intake_first", 0))
    if intake_first:
        actions.append(
            f"Use the {intake_first} browser-validated `intake_first` candidates as the next machine conversion pool."
        )
    return actions


def build_report(
    root: str | Path,
    *,
    gate_status: dict[str, Any] | None = None,
    handoff_report: dict[str, Any] | None = None,
    source_readiness_report: dict[str, Any] | None = None,
    packet_index_report: dict[str, Any] | None = None,
    date_label: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    gate_status = gate_status or audit_v2_0_gate.collect_status(root)
    if handoff_report is None:
        handoff_path = latest_path(
            root,
            (
                "compatible_handoff_verification_*.json",
                "compat_handoff_verification_*.json",
            ),
        )
        handoff_report = load_json(handoff_path) if handoff_path else {}
    gates = gate_summary(gate_status.get("gates", {}))
    handoff = handoff_summary(root, handoff_report)
    source_validation = count_source_validation(root)
    source_readiness = source_readiness_summary(root, source_readiness_report)
    packet_index = active_packet_index_summary(root, packet_index_report)
    scenarios = acceptance_scenarios(gates, handoff, packet_index)
    return {
        "date_label": date_label or date.today().isoformat(),
        "review_rows_counted_as_current_gold": False,
        "current_gates": gates,
        "active_handoff": handoff,
        "active_packet_index": packet_index,
        "source_readiness": source_readiness,
        "source_validation": source_validation,
        "acceptance_scenarios": scenarios,
        "next_human_actions": next_human_actions(handoff, gates, packet_index),
        "next_machine_actions": next_machine_actions(
            gates,
            source_validation,
            scenarios,
            source_readiness,
            packet_index,
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    gates = report["current_gates"]
    handoff = report["active_handoff"]
    packet_index = report.get("active_packet_index", {})
    readiness = report.get("source_readiness", {})
    lines = [
        "# Eng_Bench v2.0 Gap Closure Plan",
        "",
        f"- Date label: `{report['date_label']}`",
        "- Review rows are not current gold; they are counted only in explicit acceptance scenarios.",
        f"- Active handoff rows: `{handoff.get('review_rows', 0)}`",
        f"- Active handoff packets: `{handoff.get('ready_packet_count', 0)}` / `{handoff.get('packet_count', 0)}`",
        f"- Split ZIP count: `{handoff.get('split_zip_count', 0)}`",
        "",
        "## Current Failed Gates",
        "",
        "| Gate | Current | Target | Remaining | Pass |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for name, row in gates.items():
        if row.get("passes"):
            continue
        remaining = row.get("remaining_to_target")
        lines.append(
            f"| {name} | {row.get('current')} | {row.get('target')} | {remaining if remaining is not None else 'n/a'} | `{row.get('passes')}` |"
        )
    lines.extend(
        [
            "",
            "## Active Handoff Composition",
            "",
            f"- Checklist rows scanned: `{handoff.get('checklist_rows_scanned', 0)}`",
            f"- Rows by task: `{handoff.get('rows_by_task', {})}`",
            f"- Rows by split: `{handoff.get('rows_by_split', {})}`",
            f"- Visualdiff families in packet CSVs: `{handoff.get('visualdiff_families', 0)}`",
            f"- Microtext doc IDs in packet CSVs: `{handoff.get('microtext_doc_ids', 0)}`",
            "",
            "## Active Packet Index",
            "",
            f"- Packet index date: `{packet_index.get('date_label') or 'none'}`",
            f"- Ready packets: `{packet_index.get('ready_to_send', 0)}` / `{packet_index.get('packets', 0)}`",
            f"- Review rows in ready packet set: `{packet_index.get('review_rows', 0)}`",
            f"- Potential new-gold rows in ready packet set: `{packet_index.get('gold_expansion_rows', packet_index.get('review_rows', 0))}`",
            f"- Missing evidence refs: `{packet_index.get('missing_evidence_refs', 0)}`",
            f"- Overlap count: `{packet_index.get('overlap_count', 0)}`",
            "",
            "## Source Readiness",
            "",
            f"- Source-readiness date: `{readiness.get('date_label') or 'none'}`",
            f"- Active packet index loaded by readiness: `{readiness.get('active_packet_index_label') or 'none'}`",
            f"- Active packet row identities: `{readiness.get('active_packet_row_keys', 0)}`",
            f"- Local fresh actionable unpacketed rows: `{readiness.get('fresh_open_review_rows_by_local_source', 0)}`",
            f"- Candidate fresh actionable unpacketed rows: `{readiness.get('fresh_open_review_rows_by_candidate', 0)}`",
            f"- Local rows already packeted: `{readiness.get('packeted_open_review_rows_by_local_source', 0)}`",
            f"- Local rows not yet packeted: `{readiness.get('unpacketed_open_review_rows_by_local_source', 0)}`",
            "",
            "## Acceptance Scenarios",
            "",
            "| Scenario | Row Source | Accepted Rows | Total Rows After | Remaining To 25k | Known Test Rows After | Remaining Test Examples |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for scenario in report["acceptance_scenarios"]:
        lines.append(
            f"| {scenario['label']} | {scenario.get('row_source', 'unknown')} | "
            f"{scenario['accepted_rows']} | {scenario['total_rows_after']} | "
            f"{scenario['total_rows_remaining_to_v2_min']} | "
            f"{scenario['hidden_public_test_examples_after_known_test_rows']} | "
            f"{scenario['hidden_public_test_examples_remaining']} |"
        )
    lines.extend(["", "## Human Next Actions", ""])
    lines.extend(f"- {item}" for item in report["next_human_actions"])
    lines.extend(["", "## Machine Next Actions", ""])
    lines.extend(f"- {item}" for item in report["next_machine_actions"])
    lines.extend(["", "## Source Validation Pool", ""])
    validation = report.get("source_validation", {})
    lines.append(f"- Browser-validation rows: `{validation.get('rows', 0)}`")
    lines.append(f"- Next actions: `{validation.get('next_actions', {})}`")
    lines.append(f"- Recommended next steps: `{validation.get('recommended_next_steps', {})}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a conservative v2.0 gap closure plan.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--gate-report", help="Optional existing v2.0 gate JSON report.")
    parser.add_argument("--handoff-report", help="Optional compatible handoff verification JSON report.")
    parser.add_argument("--packet-index-report", help="Optional existing human packet index JSON report.")
    parser.add_argument("--source-readiness-report", help="Optional existing source-conversion readiness JSON report.")
    parser.add_argument(
        "--output-json",
        default="derived/quality/v2_0_gap_closure_plan_2026-06-15.json",
    )
    parser.add_argument(
        "--output-md",
        default="derived/quality/v2_0_gap_closure_plan_2026-06-15.md",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    gate_status = load_json(Path(args.gate_report)) if args.gate_report else None
    handoff_report = load_json(Path(args.handoff_report)) if args.handoff_report else None
    packet_index_report = load_json(Path(args.packet_index_report)) if args.packet_index_report else None
    source_readiness_report = load_json(Path(args.source_readiness_report)) if args.source_readiness_report else None
    report = build_report(
        root,
        gate_status=gate_status,
        handoff_report=handoff_report,
        packet_index_report=packet_index_report,
        source_readiness_report=source_readiness_report,
        date_label=args.date_label,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(json.dumps({"active_handoff": report["active_handoff"], "acceptance_scenarios": report["acceptance_scenarios"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
