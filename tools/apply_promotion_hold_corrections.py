#!/usr/bin/env python3
"""Apply a completed promotion-hold CSV to a new reviewed JSONL.

The command never modifies the original hold JSONL, completed CSV, or active
Gold files. It validates human corrections and writes an auditable replacement
review stream that can be passed back to preview_reviewed_gold_promotion.py.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(TOOLS_DIR))

import visualdiff_merge


FINAL_STATUSES = {"accepted", "edited", "valid", "edit"}
REJECT_STATUSES = {"reject", "rejected", "reject_unclear", "reject_no_change"}
HOLD_STATUSES = {"needs_full_page", "hold", "held"}
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ENGLISH_PROSE_WORDS = {
    "added", "appears", "changed", "deleted", "from", "has", "label",
    "moved", "new", "old", "removed", "replaced", "the", "to", "value",
    "was", "were", "with",
}
PROTECTED_FIELDS = (
    "task",
    "identity",
    "reasons",
    "source_path",
    "project_id",
    "doc_id",
    "reserved_split",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def require_quality_output(root: Path, path: Path) -> Path:
    resolved = resolve(root, path).resolve()
    allowed = (root / "derived" / "quality").resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError("outputs must be under derived/quality")
    return resolved


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def split_change_types(value: str) -> list[str]:
    text = value.strip().lower()
    if not text:
        return []
    if text.startswith("["):
        try:
            decoded = json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            decoded = None
        values = decoded if isinstance(decoded, list) else [text]
    else:
        values = re.split(r"[+,;/|]", text)
    normalized: list[str] = []
    for value_part in values:
        part = str(value_part).strip().lower()
        aliases = visualdiff_merge.CHANGE_TYPE_ALIASES.get(
            part,
            [part] if part in visualdiff_merge.CANONICAL_CHANGE_TYPES else [],
        )
        for item in aliases:
            if item != "unknown" and item not in normalized:
                normalized.append(item)
    return normalized


def lacks_english_prose_around_cjk(text: str) -> bool:
    if not CJK_RE.search(text):
        return False
    words = set(re.findall(r"[A-Za-z]+", text.lower()))
    return len(words & ENGLISH_PROSE_WORDS) < 2


def status_field(task: str) -> str:
    return "human_review_status" if task == "visualdiff" else "review_status"


def apply_corrections(
    holds_path: Path,
    completed_csv: Path,
    output_jsonl: Path,
    report_path: Path,
    *,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    holds_before = file_sha256(holds_path)
    csv_before = file_sha256(completed_csv)
    holds = read_jsonl(holds_path)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[str] = []
    for hold in holds:
        key = (str(hold.get("task") or "").strip(), str(hold.get("identity") or "").strip())
        if not all(key):
            errors.append(f"hold_missing_key:{key[0]}:{key[1]}")
        elif key in by_key:
            errors.append(f"duplicate_hold_key:{key[0]}:{key[1]}")
        else:
            by_key[key] = hold

    with completed_csv.open(encoding="utf-8-sig", newline="") as handle:
        completed = list(csv.DictReader(handle))
    seen: set[tuple[str, str]] = set()
    output_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for line_number, completed_row in enumerate(completed, start=2):
        key = (
            str(completed_row.get("task") or "").strip(),
            str(completed_row.get("identity") or "").strip(),
        )
        hold = by_key.get(key)
        if hold is None:
            errors.append(f"csv_row_{line_number}:unknown_hold:{key[0]}:{key[1]}")
            continue
        if key in seen:
            errors.append(f"csv_row_{line_number}:duplicate_hold:{key[0]}:{key[1]}")
            continue
        seen.add(key)
        original = hold["row"]
        for field in PROTECTED_FIELDS:
            if field in {"task", "identity", "reasons", "source_path"}:
                expected = str(hold.get(field) or "")
            elif field == "doc_id":
                expected = str(original.get("doc_id") or original.get("source_doc_id") or "")
            else:
                expected = str(original.get(field) or "")
            if field == "reasons":
                expected = ";".join(hold.get("reasons") or [])
            actual = str(completed_row.get(field) or "")
            if actual != expected:
                errors.append(f"csv_row_{line_number}:protected_field_changed:{field}")

        decision = str(completed_row.get("human_status") or "").strip().lower()
        if not decision:
            counts["incomplete"] += 1
            if not allow_incomplete:
                errors.append(f"csv_row_{line_number}:missing_human_status")
            output_rows.append(dict(original))
            continue

        row = dict(original)
        reasons = set(hold.get("reasons") or [])
        notes = str(completed_row.get("reviewer_notes") or "").strip()
        if decision in FINAL_STATUSES:
            if key[0] == "visualdiff":
                english = str(completed_row.get("corrected_english_description") or "").strip()
                if reasons & {
                    "visualdiff_description_requires_english_localization",
                    "missing_visualdiff_description",
                }:
                    if not english:
                        errors.append(f"csv_row_{line_number}:missing_corrected_english_description")
                    elif lacks_english_prose_around_cjk(english):
                        errors.append(f"csv_row_{line_number}:english_description_lacks_english_prose")
                if english:
                    row["human_description"] = english
                    row["change_desc_gt"] = english
                confirmed_type = str(completed_row.get("confirmed_change_type") or "").strip()
                parsed_types = split_change_types(confirmed_type)
                if "unresolved_visualdiff_change_type" in reasons and not parsed_types:
                    errors.append(f"csv_row_{line_number}:missing_or_invalid_confirmed_change_type")
                if confirmed_type and not parsed_types:
                    errors.append(f"csv_row_{line_number}:invalid_confirmed_change_type")
                elif parsed_types:
                    row["human_change_type"] = parsed_types
                row["human_review_status"] = "edit" if (english or parsed_types) else decision
            else:
                corrected_text = str(completed_row.get("corrected_text") or "").strip()
                corrected_category = str(completed_row.get("corrected_category") or "").strip()
                if "missing_microtext_answer" in reasons and not corrected_text:
                    errors.append(f"csv_row_{line_number}:missing_corrected_text")
                if "unresolved_microtext_category" in reasons and not corrected_category:
                    errors.append(f"csv_row_{line_number}:missing_corrected_category")
                if corrected_text:
                    row["corrected_text"] = corrected_text
                    row["proposed_text"] = corrected_text
                if corrected_category:
                    row["corrected_category"] = corrected_category
                    row["category"] = corrected_category
                row["review_status"] = "edited" if (corrected_text or corrected_category) else decision
            counts["final"] += 1
        elif decision in REJECT_STATUSES:
            if not notes:
                errors.append(f"csv_row_{line_number}:rejection_requires_reviewer_notes")
            row[status_field(key[0])] = "rejected"
            counts["rejected"] += 1
        elif decision in HOLD_STATUSES:
            if not notes:
                errors.append(f"csv_row_{line_number}:hold_requires_reviewer_notes")
            row[status_field(key[0])] = "needs_full_page"
            counts["held"] += 1
        else:
            errors.append(f"csv_row_{line_number}:invalid_human_status:{decision}")
        if notes:
            row["human_review_notes"] = notes
        row["promotion_hold_correction_source"] = completed_csv.as_posix()
        row["safe_to_merge_gold"] = False
        output_rows.append(row)

    missing = sorted(set(by_key) - seen)
    if missing and not allow_incomplete:
        errors.extend(f"missing_csv_row:{task}:{identity}" for task, identity in missing)
    elif missing:
        for key in missing:
            output_rows.append(dict(by_key[key]["row"]))
            counts["incomplete"] += 1
    if errors:
        raise ValueError("; ".join(errors))

    if output_jsonl.resolve() in {holds_path.resolve(), completed_csv.resolve()}:
        raise ValueError("output_jsonl must not overwrite an input")
    write_jsonl(output_jsonl, output_rows)
    source_unchanged = (
        file_sha256(holds_path) == holds_before and file_sha256(completed_csv) == csv_before
    )
    report = {
        "mode": "non_destructive_human_correction_import",
        "holds_jsonl": holds_path.as_posix(),
        "completed_csv": completed_csv.as_posix(),
        "output_jsonl": output_jsonl.as_posix(),
        "source_inputs_unchanged": source_unchanged,
        "counts": {
            "hold_rows": len(holds),
            "csv_rows": len(completed),
            "output_rows": len(output_rows),
            **dict(sorted(counts.items())),
        },
        "output_sha256": file_sha256(output_jsonl),
        "next_step": (
            "Run preview_reviewed_gold_promotion.py on output_jsonl; do not apply to Gold "
            "unless the strict preview reports ready_for_apply=true."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--holds-jsonl", type=Path, required=True)
    parser.add_argument("--completed-csv", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    holds_jsonl = resolve(root, args.holds_jsonl).resolve()
    completed_csv = resolve(root, args.completed_csv).resolve()
    output_jsonl = require_quality_output(root, args.output_jsonl)
    report_path = require_quality_output(root, args.report)
    report = apply_corrections(
        holds_jsonl,
        completed_csv,
        output_jsonl,
        report_path,
        allow_incomplete=args.allow_incomplete,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
