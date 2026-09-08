#!/usr/bin/env python3
"""Audit Eng_Bench manuscript headline claims against paper-table evidence."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


MACRO_PATTERN = re.compile(r"\\newcommand\{\\([A-Za-z]+)\}\{([^{}]*)\}")
REQUIRED_SECTIONS = {
    "Benchmark Tasks",
    "Dataset Construction",
    "Evaluation Protocol",
    "Current Audited Snapshot",
    "Comparison and Positioning",
    "Limitations and Ethics",
    "Release Status and Next Gates",
}
REQUIRED_CITATIONS = {"docvqa2020challenge", "hotpotqa2018", "musique2022"}


def macros(text: str) -> dict[str, str]:
    return {name: value.strip() for name, value in MACRO_PATTERN.findall(text)}


def expected_macros(report: dict[str, Any]) -> dict[str, str]:
    active = report["active_gold"]
    gates = report["formal_gate_status"]
    provenance_totals = report["provenance"].get("totals", {})
    gate_rows = gates["gates"]
    capacity = report["staged_capacity_projection"]["targets"]
    agreement_current = str(gate_rows["human_agreement_audit"]["current"]).split("/", 1)[0]
    agreement_target = str(gate_rows["human_agreement_audit"]["current"]).split("/", 1)[1].split()[0]
    return {
        "SnapshotLabel": str(report["date_label"]),
        "ActiveRows": str(active["rows"]),
        "MicrotextRows": str(active["task_counts"]["microtext"]),
        "VisualdiffRows": str(active["task_counts"]["visualdiff"]),
        "TrainRows": str(active["split_counts"]["train"]),
        "DevRows": str(active["split_counts"]["dev"]),
        "TestRows": str(active["split_counts"]["test"]),
        "ActiveSourceDocs": str(provenance_totals["active_source_docs"]),
        "PaperReadyPayloads": str(gate_rows["gold_source_docs"]["current"]),
        "PaperReadyActiveDocs": str(gate_rows["paper_ready_provenance"]["current"]).split("/", 1)[0],
        "RevisionFamilies": str(gate_rows["visualdiff_revision_families"]["current"]),
        "BaselineCount": str(gate_rows["baselines_or_external_submissions"]["current"]),
        "LeaderboardInfrastructure": str(gate_rows["leaderboard_infrastructure"]["current"]),
        "ReleaseSafeInventory": str(gate_rows["release_safe_inventory_docs"]["current"]),
        "FormalGatesPassed": str(gates["passed"]),
        "FormalGatesTotal": str(gates["total"]),
        "AgreementComplete": agreement_current,
        "AgreementTarget": agreement_target,
        "StagedUpperRows": str(capacity["rows"]["all_staged_upper_bound"]),
        "StagedUpperTestRows": str(capacity["test_rows"]["explicit_all_staged_upper_bound"]),
        "CanonicalNonPinGap": str(
            report["staged_capacity_projection"]["row_target_projection"][
                "additional_canonical_non_pin_rows_needed"
            ]
        ),
    }


def build_report(paper_path: Path, table_path: Path) -> dict[str, Any]:
    text = paper_path.read_text(encoding="utf-8")
    tables = json.loads(table_path.read_text(encoding="utf-8"))
    found = macros(text)
    expected = expected_macros(tables)
    issues: list[dict[str, str]] = []
    for name, value in expected.items():
        actual = found.get(name)
        if actual != value:
            issues.append({"issue": "macro_mismatch", "macro": name, "expected": value, "actual": str(actual)})

    release_ready = found.get("ReleaseClaimReady", "").lower()
    all_gates_pass = int(expected["FormalGatesPassed"]) == int(expected["FormalGatesTotal"])
    if release_ready not in {"true", "false"}:
        issues.append({"issue": "invalid_release_claim_macro", "actual": release_ready})
    if not all_gates_pass and release_ready != "false":
        issues.append({"issue": "premature_release_claim", "actual": release_ready})
    if not all_gates_pass and "NOT RELEASE CLAIM READY" not in text:
        issues.append({"issue": "missing_draft_warning"})

    sections = set(re.findall(r"\\section\{([^{}]+)\}", text))
    for section in sorted(REQUIRED_SECTIONS - sections):
        issues.append({"issue": "missing_required_section", "section": section})
    citations = set(re.findall(r"\\cite\{([^{}]+)\}", text))
    for citation in sorted(REQUIRED_CITATIONS - citations):
        issues.append({"issue": "missing_required_citation", "citation": citation})

    return {
        "goal": "Gold v2.0 Global",
        "paper": paper_path.as_posix(),
        "paper_tables": table_path.as_posix(),
        "release_claim_ready": release_ready == "true",
        "formal_gates": f"{expected['FormalGatesPassed']}/{expected['FormalGatesTotal']}",
        "macro_checks": len(expected),
        "required_sections": len(REQUIRED_SECTIONS),
        "required_citations": len(REQUIRED_CITATIONS),
        "issues": issues,
        "valid": not issues,
        "interpretation": (
            "A valid audit proves manuscript headline macros match the selected audited snapshot. "
            "It does not make an incomplete Gold release complete."
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Eng_Bench Paper Snapshot Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Formal gates: `{report['formal_gates']}`",
        f"- Release claim ready: `{str(report['release_claim_ready']).lower()}`",
        f"- Macro checks: `{report['macro_checks']}`",
        f"- Required sections: `{report['required_sections']}`",
        f"- Required citations: `{report['required_citations']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "",
        "## Issues",
        "",
    ]
    if report["issues"]:
        lines.extend(f"- `{issue}`" for issue in report["issues"])
    else:
        lines.append("- None.")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--paper", type=Path, required=True)
    parser.add_argument("--paper-tables", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    report = build_report(resolve(args.paper), resolve(args.paper_tables))
    output_json = resolve(args.output_json)
    output_md = resolve(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    output_md.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({
        "valid": report["valid"],
        "formal_gates": report["formal_gates"],
        "macro_checks": report["macro_checks"],
        "issues": len(report["issues"]),
    }, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
