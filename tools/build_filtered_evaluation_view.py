#!/usr/bin/env python3
"""Build an internal diagnostic subset without changing frozen challenge membership."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from . import evaluation_release_guard as guard
    from . import make_public_private_split as split
except ImportError:
    import evaluation_release_guard as guard
    import make_public_private_split as split

PUBLIC = "release/public_inputs/eng_bench_public_test_inputs.jsonl"
HIDDEN = "release/public_inputs/eng_bench_hidden_test_inputs.jsonl"


def filter_frozen(active: list[dict], public: list[dict], hidden: list[dict], assessments: list[dict]) -> dict:
    canonical = guard.unique_index(active, "id")
    checked = guard.unique_index(assessments, "id")
    membership, outputs, ledger = {}, {}, []
    for challenge, rows in (("public_test", public), ("hidden_test", hidden)):
        guard.unique_index(rows, "id")
        inputs, labels = [], []
        for row in rows:
            identifier = row["id"]
            if identifier in membership:
                raise ValueError(f"duplicate frozen challenge membership: {identifier}")
            source = canonical.get(identifier)
            if not source or source.get("split") != "test" or split.strip_labels(source, challenge) != row:
                raise ValueError(f"frozen input does not match current test evidence: {identifier}")
            assessment = checked.get(identifier)
            if assessment is None:
                raise ValueError(f"frozen row not safety-assessed: {identifier}")
            membership[identifier] = challenge
            if assessment["reason_codes"]:
                ledger.append({**assessment, "challenge_split": challenge, "disposition": "quarantined"})
            else:
                inputs.append(row)
                labels.append(split.labeled_row(source, challenge))
        outputs[challenge + "_inputs.jsonl"] = inputs
        outputs[challenge + "_labels_private.jsonl"] = labels
    for row in assessments:
        if row["id"] not in membership:
            ledger.append({**row, "challenge_split": None, "disposition": "not_in_existing_freeze"})
    outputs["quarantine_and_unassigned.jsonl"] = ledger
    return {"outputs": outputs, "frozen_membership": membership}


def build(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    before = {name: guard.provenance.file_sha256(root / name) for name in (PUBLIC, HIDDEN)}
    active = split.load_jsonl(root / "eng_bench.jsonl")
    screening = guard.assess(root, active)
    filtered = filter_frozen(active, split.load_jsonl(root / PUBLIC), split.load_jsonl(root / HIDDEN), screening["assessments"])
    pinned = {**before, **screening["input_hashes"]}
    if pinned != {name: guard.provenance.file_sha256(root / name) for name in pinned}:
        raise ValueError("inputs changed during diagnostic view construction")
    output.mkdir(parents=True)
    artifacts = filtered["outputs"]
    for name, rows in artifacts.items():
        split.write_jsonl(output / name, rows)
    (output / "screening_report.json").write_text(json.dumps(screening, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if pinned != {name: guard.provenance.file_sha256(root / name) for name in pinned}:
        raise ValueError("inputs changed while writing diagnostic artifacts; no passing report produced")
    ledger = artifacts["quarantine_and_unassigned.jsonl"]
    report = {"goal": "Gold v2.0 Global", "status": "INTERNAL_DIAGNOSTIC_ONLY",
              "gold_certified": False, "active_gold_modified": False, "existing_release_modified": False,
              "frozen_public": len(split.load_jsonl(root / PUBLIC)), "frozen_hidden": len(split.load_jsonl(root / HIDDEN)),
              "diagnostic_public": len(artifacts["public_test_inputs.jsonl"]),
              "diagnostic_hidden": len(artifacts["hidden_test_inputs.jsonl"]),
              "diagnostic_by_task": dict(Counter(r["task"] for name in ("public_test_inputs.jsonl", "hidden_test_inputs.jsonl") for r in artifacts[name])),
              "quarantined_frozen": sum(r["disposition"] == "quarantined" for r in ledger),
              "active_test_not_in_freeze": sum(r["disposition"] == "not_in_existing_freeze" for r in ledger),
              "quarantined_by_reason": dict(Counter(reason for r in ledger if r["disposition"] == "quarantined" for reason in r["reason_codes"])),
              "input_hashes": pinned, "audit_report_sha256": screening["audit_report_sha256"],
              "output_hashes": {name: guard.provenance.file_sha256(output / name) for name in [*artifacts, "screening_report.json"]},
              "limitation": "Convenience subset for internal regression tests, not unbiased benchmark performance. Exclusions change the task mix. Preserve original IDs/membership; keep *_labels_private.jsonl private."}
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Internal Diagnostic Evaluation View\n\n"
        "This is NOT Gold v2.0 and not a new public release. Do not publish scores as full-benchmark results.\n\n"
        "Use public_test_inputs.jsonl or hidden_test_inputs.jsonl as model inputs. Resolve image paths against the Eng_Bench root. "
        "Keep both *_labels_private.jsonl files on the scoring side; never include them in a model request.\n\n"
        "Rows retain their previous public/hidden assignment and question IDs. New unassigned rows are not silently allocated. "
        "The quarantine ledger lists unresolved audit, tentative-description and source-provenance issues. "
        "No human votes or active Gold annotations were changed. Rebuild after any audit or Gold change.\n\n"
        f"Eligible frozen inputs: {report['diagnostic_public']} public + {report['diagnostic_hidden']} hidden. "
        f"Quarantined frozen rows: {report['quarantined_frozen']}. "
        f"Active test rows absent from the previous freeze: {report['active_test_not_in_freeze']}.\n\n"
        f"Task mix: {json.dumps(report['diagnostic_by_task'], sort_keys=True)}. "
        "This filtered task mix is not representative of the full benchmark.\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.root, args.root / args.output_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("[BLOCKED] " + str(error))
        return 1
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("hashes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
