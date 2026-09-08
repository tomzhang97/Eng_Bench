#!/usr/bin/env python3
"""Classify microtext taxonomy holds without promoting them into active gold."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def classify_hold_row(row: dict[str, str]) -> dict[str, str]:
    issues = str(row.get("issues") or "")
    output = dict(row)
    if "civil_standard_code_mislabeled_instrument_tag" in issues or "civil_type_code_mislabeled_dimension_value" in issues:
        proposed = "standard_id"
        disposition = "rights_and_taxonomy_hold"
        rationale = "Identifier for a civil standard drawing, standard sheet, or standard type; not a P&ID instrument tag or geometric dimension."
    elif "pcb_electrical_value_mislabeled_" in issues:
        proposed = "electrical_value"
        disposition = "taxonomy_confirmation_ready"
        rationale = "Electrical component value or rating such as resistance, voltage, or current; not a drawing dimension or process value."
    elif "pid_equipment_tag_mislabeled_pin_label" in issues:
        proposed = "equipment_tag"
        disposition = "existing_category_confirmation_ready"
        rationale = "P&ID equipment identifier already covered by the existing equipment_tag category."
    else:
        proposed = ""
        disposition = "unresolved_taxonomy_hold"
        rationale = "No deterministic taxonomy rule is defined for this issue."
    output.update(
        {
            "proposed_category": proposed,
            "next_disposition": disposition,
            "human_confirmation_required": "yes",
            "rights_clearance_required": (
                "yes" if "rights_clearance_required" in issues else "no"
            ),
            "taxonomy_rationale": rationale,
        }
    )
    return output


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_plan(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    taxonomy_holds = [row for row in rows if row.get("disposition") == "taxonomy_hold"]
    classified = [classify_hold_row(row) for row in taxonomy_holds]
    summary = {
        "rows": len(classified),
        "proposed_categories": dict(
            sorted(Counter(row["proposed_category"] or "unresolved" for row in classified).items())
        ),
        "next_dispositions": dict(
            sorted(Counter(row["next_disposition"] for row in classified).items())
        ),
        "rights_clearance_required": sum(
            row["rights_clearance_required"] == "yes" for row in classified
        ),
        "human_confirmation_required": len(classified),
        "unresolved_rows": sum(
            row["next_disposition"] == "unresolved_taxonomy_hold" for row in classified
        ),
        "active_gold_mutated": False,
    }
    return classified, summary


def render_markdown(summary: dict[str, Any]) -> str:
    return f"""# Microtext Taxonomy v2 Staging Proposal

This proposal resolves the shape of the current taxonomy backlog without
promoting any held row into active gold.

## Minimal Extension

Add two categories:

| Category | Definition | Current staged rows |
| --- | --- | ---: |
| `standard_id` | Civil/structural standard drawing IDs, standard sheet codes, and standard type identifiers such as `BC-700M`, `BD-600M`, or `DI-1`. | {summary['proposed_categories'].get('standard_id', 0)} |
| `electrical_value` | Electrical component values and ratings such as `1K`, `1M`, `6-20V`, or `50mA`. | {summary['proposed_categories'].get('electrical_value', 0)} |

Two P&ID rows map to the existing `equipment_tag` category and do not require a
new label.

## Why This Is Minimal

- `standard_id` separates drawing/document identifiers from P&ID instrument tags.
- `electrical_value` separates electrical ratings from geometric dimensions and process values.
- A separate `component_value` category is deferred because the current backlog
  mixes component values with board-level voltage/current ratings; splitting it
  now would create tiny, unstable evaluation slices.

## Promotion Rules

1. Every row still requires focused human category confirmation.
2. The {summary['rights_clearance_required']} PennDOT-derived rows remain blocked until redistribution rights are documented.
3. The rights-clear rows may enter a focused taxonomy-confirmation packet.
4. New categories remain diagnostic until they have at least 100 examples in a reported split.
5. Do not recategorize existing active-gold rows automatically.

## Current Backlog

- Total taxonomy holds: `{summary['rows']}`
- Rights and taxonomy hold: `{summary['next_dispositions'].get('rights_and_taxonomy_hold', 0)}`
- New-category confirmation ready: `{summary['next_dispositions'].get('taxonomy_confirmation_ready', 0)}`
- Existing-category confirmation ready: `{summary['next_dispositions'].get('existing_category_confirmation_ready', 0)}`
- Unresolved: `{summary['unresolved_rows']}`

Row-level decisions are stored in the generated taxonomy hold plan CSV. No
active annotation file is changed by this process.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    classified, summary = build_plan(read_csv(resolve(args.input)))
    write_csv(resolve(args.output_csv), classified)
    resolve(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    resolve(args.output_json).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    resolve(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    resolve(args.output_md).write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["unresolved_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
