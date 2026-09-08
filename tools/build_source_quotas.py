#!/usr/bin/env python3
"""Build source-domain quota status for Eng_Bench expansion planning."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from source_rights import is_release_safe_status

DOMAINS = (
    "pcb_schematic",
    "datasheet_spec",
    "mechanical_cad",
    "civil_architectural",
    "pid",
)
V1_0_ACTIVE_TARGET_PER_DOMAIN = 7
V1_5_ACTIVE_TARGET_PER_DOMAIN = 15
V1_5_CANDIDATE_TARGET_PER_DOMAIN = 2 * V1_5_ACTIVE_TARGET_PER_DOMAIN
def load_csv(path: str | Path) -> list[dict[str, str]]:
    if not Path(path).exists():
        return []
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def is_release_safe_active(row: dict[str, str]) -> bool:
    if row.get("task") == "reference":
        return False
    status = str(row.get("public_status") or row.get("rights_tier") or "").lower()
    return is_release_safe_status(status)


def validation_action_by_candidate(validations: list[dict[str, str]]) -> dict[str, str]:
    return {
        row["candidate_id"]: row.get("next_action", "")
        for row in validations
        if row.get("candidate_id")
    }


def quota_domain(raw_domain: str | None) -> str:
    domain = raw_domain or "unknown"
    if domain in {"civil", "architectural"}:
        return "civil_architectural"
    return domain


def summarize_quotas(
    inventory: list[dict[str, str]],
    candidates: list[dict[str, str]],
    validations: list[dict[str, str]],
) -> list[dict[str, int | str]]:
    validation_actions = validation_action_by_candidate(validations)
    inventory_total = Counter(quota_domain(row.get("domain")) for row in inventory)
    safe_active = Counter(
        quota_domain(row.get("domain")) for row in inventory if is_release_safe_active(row)
    )
    candidate_total = Counter(quota_domain(row.get("domain")) for row in candidates)
    intake_first = Counter(
        quota_domain(row.get("domain"))
        for row in candidates
        if validation_actions.get(row.get("candidate_id", "")) == "intake_first"
    )

    rows: list[dict[str, int | str]] = []
    for domain in DOMAINS:
        active = safe_active[domain]
        rows.append(
            {
                "domain": domain,
                "inventory_total": inventory_total[domain],
                "release_safe_active": active,
                "candidate_total": candidate_total[domain],
                "intake_first": intake_first[domain],
                "remaining_to_v1_0": max(0, V1_0_ACTIVE_TARGET_PER_DOMAIN - active),
                "remaining_to_v1_5": max(0, V1_5_ACTIVE_TARGET_PER_DOMAIN - active),
                "candidate_target_v1_5": V1_5_CANDIDATE_TARGET_PER_DOMAIN,
                "remaining_candidate_gap_v1_5": max(
                    0, V1_5_CANDIDATE_TARGET_PER_DOMAIN - candidate_total[domain]
                ),
            }
        )
    return rows


def render_markdown(rows: list[dict[str, int | str]]) -> str:
    lines = [
        "# Eng_Bench Source Quotas",
        "",
        "This tracks source-domain breadth for v1.0 and v1.5 expansion. Counts use release-safe active inventory rows; reference-only, unknown, restricted, and proprietary-marked rows do not count as active release-safe sources.",
        "",
        "| Domain | Inventory Total | Release-Safe Active | Candidates | Intake-First | remaining_to_v1_0 | remaining_to_v1_5 | candidate_target_v1_5 | remaining_candidate_gap_v1_5 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['domain']} | {row['inventory_total']} | {row['release_safe_active']} | "
            f"{row['candidate_total']} | {row['intake_first']} | "
            f"{row['remaining_to_v1_0']} | {row['remaining_to_v1_5']} | "
            f"{row['candidate_target_v1_5']} | {row['remaining_candidate_gap_v1_5']} |"
        )
    lines.extend(
        [
            "",
            "Next import priority should favor domains with high `remaining_to_v1_0` and available `intake_first` candidates.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Eng_Bench source quota report.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="docs/SOURCE_QUOTAS.md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = summarize_quotas(
        load_csv(root / "SOURCE_INVENTORY.csv"),
        load_csv(root / "SOURCE_CANDIDATES.csv"),
        load_csv(root / "SOURCE_CANDIDATE_VALIDATION.csv"),
    )
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(rows), encoding="utf-8")
    print(f"[OK] Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
