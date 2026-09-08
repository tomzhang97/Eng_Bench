#!/usr/bin/env python3
"""Build a prioritized pack-level control sheet for the next human review tranche."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


CSV_FIELDS = [
    "priority",
    "packet_id",
    "kind",
    "domain",
    "suggested_rows",
    "blank_rows",
    "manifest_rows",
    "folder_path",
    "checklist_hint",
    "index_hint",
    "reason",
]


DOMAIN_ORDER = {
    "visualdiff": 0,
    "pid": 1,
    "mechanical": 2,
    "civil_arch": 3,
    "pcb_datasheet": 4,
    "microtext_general": 5,
    "other": 6,
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def infer_domain(packet_id: str, kind: str) -> str:
    text = packet_id.lower()
    if kind == "visualdiff":
        return "visualdiff"
    if any(token in text for token in ("pid", "pfd", "pump", "refinery", "process", "wikimedia_nat_gas")):
        return "pid"
    if any(token in text for token in ("mechanical", "voron", "tolerances", "faunce", "hunt", "cornell", "reid")):
        return "mechanical"
    if any(token in text for token in ("civil", "fdot", "vdot", "nysdot", "wsdot", "caltrans", "loc_", "habs", "haer", "arch", "floor_plan", "dairy")):
        return "civil_arch"
    if any(token in text for token in ("pcb", "pin", "datasheet", "ds025", "feather", "arduino", "pixhawk", "adafruit", "olimex", "jetson", "rpi")):
        return "pcb_datasheet"
    if kind == "microtext":
        return "microtext_general"
    return "other"


def checklist_hint(folder_path: str) -> str:
    return f"{folder_path.rstrip('/')}/<fill the *_validation_checklist.csv file>"


def eligible_pack(row: dict[str, Any]) -> tuple[bool, str]:
    if not as_bool(row.get("ready_to_send")):
        return False, "not_ready_to_send"
    if as_bool(row.get("human_complete")):
        return False, "human_complete"
    if as_int(row.get("blank_rows")) <= 0:
        return False, "no_blank_rows"
    if as_int(row.get("missing_evidence_refs")) > 0:
        return False, "missing_evidence_refs"
    if as_int(row.get("invalid_rows")) > 0:
        return False, "invalid_existing_checklist_rows"
    return True, ""


def rank_pack(row: dict[str, Any]) -> tuple[int, int, int, str]:
    packet_id = str(row.get("packet_id", ""))
    kind = str(row.get("kind", ""))
    domain = infer_domain(packet_id, kind)
    blank_rows = as_int(row.get("blank_rows"))
    return (DOMAIN_ORDER.get(domain, 9), blank_rows, as_int(row.get("manifest_rows")), packet_id)


def build_tranche(
    *,
    root: Path,
    index_path: Path,
    row_budget: int,
    max_rows_per_pack: int,
    date_label: str = "manual",
) -> dict[str, Any]:
    root = root.resolve()
    index = read_json(index_path if index_path.is_absolute() else root / index_path)
    packs = list(index.get("packs", []))
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for row in packs:
        ok, reason = eligible_pack(row)
        packet_id = str(row.get("packet_id", ""))
        kind = str(row.get("kind", "unknown"))
        domain = infer_domain(packet_id, kind)
        if ok:
            enriched = dict(row)
            enriched["domain"] = domain
            eligible.append(enriched)
        else:
            excluded.append(
                {
                    "packet_id": packet_id,
                    "kind": kind,
                    "domain": domain,
                    "reason": reason,
                }
            )

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        groups.setdefault(str(row["domain"]), []).append(row)
    for rows in groups.values():
        rows.sort(key=rank_pack)

    selected: list[dict[str, Any]] = []
    selected_rows = 0
    selected_rows_by_domain: Counter[str] = Counter()
    domain_order = sorted(groups, key=lambda domain: DOMAIN_ORDER.get(domain, 9))
    domain_target = max(1, row_budget // max(1, len(domain_order)))

    def add_pack(row: dict[str, Any], domain_cap: int | None = None) -> bool:
        nonlocal selected_rows
        if selected_rows >= row_budget:
            return False
        blank_rows = as_int(row.get("blank_rows"))
        cap = min(blank_rows, max_rows_per_pack, row_budget - selected_rows)
        if domain_cap is not None:
            cap = min(cap, max(0, domain_cap - selected_rows_by_domain[str(row["domain"])]))
        if cap <= 0:
            return False
        folder_path = str(row.get("folder_path", "")).replace("\\", "/")
        selected.append(
            {
                "priority": len(selected) + 1,
                "packet_id": row.get("packet_id", ""),
                "kind": row.get("kind", ""),
                "domain": row.get("domain", ""),
                "suggested_rows": cap,
                "blank_rows": blank_rows,
                "manifest_rows": as_int(row.get("manifest_rows")),
                "folder_path": folder_path,
                "checklist_hint": checklist_hint(folder_path),
                "index_hint": f"{folder_path.rstrip('/')}/index.html",
                "reason": selection_reason(row),
            }
        )
        selected_rows += cap
        selected_rows_by_domain[str(row["domain"])] += cap
        return True

    while selected_rows < row_budget and any(
        groups.get(domain) and selected_rows_by_domain[domain] < domain_target for domain in domain_order
    ):
        progressed = False
        for domain in domain_order:
            if selected_rows >= row_budget:
                break
            if not groups.get(domain) or selected_rows_by_domain[domain] >= domain_target:
                continue
            row = groups[domain].pop(0)
            progressed = add_pack(row, domain_cap=domain_target) or progressed
        if not progressed:
            break

    while selected_rows < row_budget and any(groups.get(domain) for domain in domain_order):
        progressed = False
        for domain in domain_order:
            if selected_rows >= row_budget:
                break
            if not groups.get(domain):
                continue
            row = groups[domain].pop(0)
            progressed = add_pack(row) or progressed
        if not progressed:
            break

    by_kind = Counter(str(row["kind"]) for row in selected)
    by_domain = Counter(str(row["domain"]) for row in selected)
    return {
        "date_label": date_label,
        "source_index": str(index_path).replace("\\", "/"),
        "row_budget": row_budget,
        "max_rows_per_pack": max_rows_per_pack,
        "selected_packs": selected,
        "excluded_packs": excluded,
        "totals": {
            "candidate_packs": len(packs),
            "eligible_packs": len(eligible),
            "excluded_packs": len(excluded),
            "selected_packs": len(selected),
            "selected_rows": selected_rows,
            "selected_packs_by_kind": dict(sorted(by_kind.items())),
            "selected_packs_by_domain": dict(sorted(by_domain.items())),
            "selected_rows_by_domain": dict(sorted(selected_rows_by_domain.items())),
        },
    }


def selection_reason(row: dict[str, Any]) -> str:
    domain = str(row.get("domain", ""))
    if domain == "visualdiff":
        return "Prioritize VisualDiff revision-family and hidden-test scale gates."
    if domain == "pid":
        return "Add process/PID/equipment diversity for non-PCB benchmark coverage."
    if domain == "mechanical":
        return "Add mechanical drawing and tolerance coverage."
    if domain == "civil_arch":
        return "Add civil/architectural document diversity."
    if domain == "pcb_datasheet":
        return "Add PCB/datasheet/pin-label coverage while preserving source diversity."
    return "Fill ready review capacity from evidence-complete packs."


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Next Human Review Tranche",
        "",
        f"- Date label: `{report['date_label']}`",
        "- Final target: Gold v2.0 Global",
        "- This is a review control sheet only; it does not merge rows into gold.",
        f"- Source index: `{report['source_index']}`",
        f"- Selected rows: `{totals['selected_rows']}`",
        f"- Selected packs: `{totals['selected_packs']}`",
        f"- Eligible packs: `{totals['eligible_packs']}`",
        "",
        "## How To Use",
        "",
        "1. Work down the table in priority order.",
        "2. Open each pack folder and browse `index.html`.",
        "3. Fill the checklist CSV in that same folder.",
        "4. Stop after the suggested row count for that pack unless asked to continue.",
        "5. Do not edit JSONL files by hand.",
        "",
        "## Selected Packs",
        "",
        "| Priority | Pack | Kind | Domain | Suggested Rows | Folder |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for row in report["selected_packs"]:
        lines.append(
            f"| {row['priority']} | `{row['packet_id']}` | {row['kind']} | {row['domain']} | "
            f"{row['suggested_rows']} | `{row['folder_path']}` |"
        )
    lines.extend(["", "## Notes", "", "- Rows remain unmerged until returned checklists pass strict processing gates.", ""])
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_md: Path,
    output_csv: Path,
) -> None:
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["selected_packs"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--index", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--row-budget", type=int, default=600)
    parser.add_argument("--max-rows-per-pack", type=int, default=100)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_tranche(
        root=root,
        index_path=Path(args.index),
        row_budget=args.row_budget,
        max_rows_per_pack=args.max_rows_per_pack,
        date_label=args.date_label,
    )
    write_outputs(
        report,
        output_json=root / args.output_json,
        output_md=root / args.output_md,
        output_csv=root / args.output_csv,
    )
    print(f"[OK] Wrote {args.output_json}")
    print(f"[OK] Wrote {args.output_md}")
    print(f"[OK] Wrote {args.output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
