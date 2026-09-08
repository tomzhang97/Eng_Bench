from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


IDENTIFIER_RE = re.compile(r"^[A-Z]{2,5}\d{2}(?:-\d+)?$")
RANGE_RE = re.compile(
    r"(?i)(?:^|\s)[-+−–]?\d[\d.,×x^−–-]*\s+to\s+"
    r"[-+−–]?\d[\d.,×x^−–-]*(?:\s|$)"
)
UNIT_RE = re.compile(
    r"(?i)(?:psia?|torr|m?a|vdc|kv|kw|w|l/min|°c|℃|rpm|sccm|slpm|"
    r"ohm|ω|hz|in\.|mm|cm|ft)\b"
)
LINE_RE = re.compile(
    r"(?i)\b(?:supply|vent|return|exhaust|sample line|coolant|hydrogen|"
    r"inert gas|vacuum line)\b"
)
EQUIPMENT_RE = re.compile(
    r"(?i)\b(?:arc reactor|vacuum pump|turbomolecular pump|chiller|"
    r"calorimeter|power supply|heater assembly|reactor isolation|"
    r"roughing pump|coolant diverter|electronic module|power module)\b"
)
GENERIC_EQUIPMENT = {
    "heater",
    "motor",
    "pump",
    "reactor",
    "valve",
    "vacuum pump",
}


def parse_pages(value: str) -> set[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(f"invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    if not pages:
        raise argparse.ArgumentTypeError("at least one page is required")
    return pages


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_input_row"] = line_number
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            clean = {key: value for key, value in row.items() if not key.startswith("_")}
            handle.write(json.dumps(clean, ensure_ascii=False) + "\n")


def normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def bbox_x(row: dict[str, Any]) -> tuple[float, float]:
    bbox = row.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return -1.0, -1.0
    try:
        return float(bbox[0]), float(bbox[2])
    except (TypeError, ValueError):
        return -1.0, -1.0


def classify_row(
    row: dict[str, Any],
    *,
    pid_pages: set[int],
    table_pages: set[int],
    identifier_x_min: float,
    identifier_x_max: float,
) -> tuple[str | None, str]:
    page = int(row.get("page_index", -1))
    text = re.sub(r"\s+", " ", str(row.get("proposed_text") or "").strip())
    x0, x1 = bbox_x(row)

    if (
        page in table_pages
        and identifier_x_min <= x0
        and x1 <= identifier_x_max
        and IDENTIFIER_RE.fullmatch(text)
    ):
        return "instrument_tag", "pid_identifier_column"

    if page in table_pages and len(text) <= 80 and RANGE_RE.search(text) and UNIT_RE.search(text):
        return "process_value", "complete_unit_bearing_range"

    if page in pid_pages and 4 <= len(text) <= 80 and LINE_RE.search(text):
        return "pipe_line_tag", "complete_pid_line_label"

    if (
        page in pid_pages
        and 6 <= len(text) <= 80
        and EQUIPMENT_RE.search(text)
        and normalized_text(text) not in GENERIC_EQUIPMENT
        and not text.endswith(("/", "-"))
    ):
        return "equipment_tag", "specific_pid_equipment_label"

    return None, "outside_guarded_shortlist"


def build_shortlist(
    rows: list[dict[str, Any]],
    *,
    pid_pages: set[int],
    table_pages: set[int],
    identifier_x_min: float,
    identifier_x_max: float,
    category_caps: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    selected_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    for row in rows:
        category, reason = classify_row(
            row,
            pid_pages=pid_pages,
            table_pages=table_pages,
            identifier_x_min=identifier_x_min,
            identifier_x_max=identifier_x_max,
        )
        if category is None:
            row["machine_hold_reason"] = reason
            held.append(row)
            reason_counts[reason] += 1
            continue

        answer_key = (category, normalized_text(row.get("proposed_text")))
        cap = category_caps.get(category, 0)
        if answer_key in seen:
            row["machine_hold_reason"] = "duplicate_category_answer"
            held.append(row)
            reason_counts["duplicate_category_answer"] += 1
            continue
        if cap and selected_counts[category] >= cap:
            row["machine_hold_reason"] = "category_cap"
            held.append(row)
            reason_counts["category_cap"] += 1
            continue

        row["category"] = category
        row["review_status"] = "needs_review"
        row["machine_qa_status"] = "shortlisted_needs_visual_qa"
        row["safe_to_merge_gold"] = False
        notes = str(row.get("review_notes") or "").strip()
        note = f"shortlist_rule:{reason}"
        row["review_notes"] = f"{notes}; {note}".strip("; ")
        selected.append(row)
        seen.add(answer_key)
        selected_counts[category] += 1
        reason_counts[reason] += 1

    report = {
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_categories": dict(sorted(selected_counts.items())),
        "reasons": dict(sorted(reason_counts.items())),
        "pid_pages": sorted(pid_pages),
        "table_pages": sorted(table_pages),
        "identifier_x_range": [identifier_x_min, identifier_x_max],
        "interpretation": (
            "This is a deterministic machine shortlist, not visual approval, human approval, or Gold."
        ),
    }
    return selected, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    categories = report["selected_categories"]
    lines = [
        "# P&ID Table Microtext Shortlist",
        "",
        f"- Input rows: {report['input_rows']}",
        f"- Selected rows: {report['selected_rows']}",
        f"- Held rows: {report['held_rows']}",
        "- Selected categories: "
        + (", ".join(f"{key}={value}" for key, value in categories.items()) or "none"),
        "- Status: machine shortlist only; full visual QA and human review remain required.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shortlist guarded MicroText candidates from P&ID pages and companion tables."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pid-pages", type=parse_pages, required=True)
    parser.add_argument("--table-pages", type=parse_pages, required=True)
    parser.add_argument("--identifier-x-min", type=float, default=400.0)
    parser.add_argument("--identifier-x-max", type=float, default=580.0)
    parser.add_argument("--instrument-cap", type=int, default=100)
    parser.add_argument("--process-cap", type=int, default=100)
    parser.add_argument("--line-cap", type=int, default=50)
    parser.add_argument("--equipment-cap", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    selected, held, report = build_shortlist(
        read_jsonl(args.input),
        pid_pages=args.pid_pages,
        table_pages=args.table_pages,
        identifier_x_min=args.identifier_x_min,
        identifier_x_max=args.identifier_x_max,
        category_caps={
            "instrument_tag": args.instrument_cap,
            "process_value": args.process_cap,
            "pipe_line_tag": args.line_cap,
            "equipment_tag": args.equipment_cap,
        },
    )
    write_jsonl(args.output, selected)
    write_jsonl(args.held_output, held)
    write_report(args.report_json, report)
    write_markdown(args.report_md, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
