#!/usr/bin/env python3
"""Hold VisualDiff review rows that overlap active or previously staged work.

The filter is intentionally conservative.  It compares record identifiers,
exact old/new image regions, and recorded evidence fingerprints across active
Gold, review queues, review-pack manifests, processed returns, and human
assignment payloads.  Passing rows remain non-Gold and still require visual
QA plus human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_JSONL_GLOBS = (
    "visualdiff/annotations/*.jsonl",
    "derived/review_queues/**/*.jsonl",
    "derived/review_packs/**/manifest.jsonl",
    "derived/human_adjudication/processed_returns/**/*.jsonl",
)
DEFAULT_JSON_GLOBS = (
    "derived/quality/*human*audit*payload*.json",
    "derived/quality/primary_intern_*payload*.json",
)
ACTIVE_GOLD = Path("visualdiff/annotations/visualdiff_pairs.jsonl")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            yield value


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def row_identifier(row: dict[str, Any]) -> str:
    return str(
        row.get("pair_id")
        or row.get("candidate_id")
        or row.get("record_id")
        or row.get("id")
        or ""
    ).strip()


def evidence_fingerprint(row: dict[str, Any]) -> str:
    return str(
        row.get("replacement_evidence_fingerprint")
        or row.get("visual_evidence_fingerprint")
        or row.get("evidence_fingerprint")
        or ""
    ).strip()


def normalized_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def bbox_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bbox = tuple(int(round(float(item))) for item in value)
    except (TypeError, ValueError):
        return None
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return None
    return bbox


def page_number(row: dict[str, Any], key: str) -> int | None:
    try:
        return int(row.get(key))
    except (TypeError, ValueError):
        return None


def region_key(row: dict[str, Any]) -> tuple[Any, ...] | None:
    old_path = normalized_path(row.get("image_old"))
    new_path = normalized_path(row.get("image_new"))
    old_bbox = bbox_tuple(row.get("bbox_old"))
    new_bbox = bbox_tuple(row.get("bbox_new"))
    old_page = page_number(row, "page_old")
    new_page = page_number(row, "page_new")
    if not old_path or not new_path or old_bbox is None or new_bbox is None:
        return None
    return (old_path, old_page, old_bbox, new_path, new_page, new_bbox)


def row_like_visualdiff(row: dict[str, Any]) -> bool:
    if not row_identifier(row):
        return False
    if row.get("pair_id"):
        return True
    return str(row.get("task") or "").strip().casefold() == "visualdiff"


def nested_visualdiff_rows(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if row_like_visualdiff(value):
            yield value
        for child in value.values():
            yield from nested_visualdiff_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_visualdiff_rows(child)


def classify_history_source(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    if path.resolve() == (root / ACTIVE_GOLD).resolve():
        return "active_gold"
    if relative.startswith("derived/review_queues/"):
        return "review_queue"
    if relative.startswith("derived/review_packs/"):
        return "review_pack"
    if "/processed_returns/" in f"/{relative}":
        return "processed_return"
    if "payload" in path.name.casefold():
        return "assignment_payload"
    if relative.startswith("visualdiff/annotations/"):
        return "visualdiff_annotation"
    return "human_packet"


def discover_paths(root: Path, globs: Iterable[str], excluded: set[Path]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if path.is_file() and path.resolve() not in excluded:
                paths.add(path.resolve())
    return sorted(paths)


class HistoryIndex:
    def __init__(self) -> None:
        self.ids: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.regions: dict[tuple[Any, ...], set[tuple[str, str]]] = defaultdict(set)
        self.fingerprints: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.rows = 0

    def add(self, row: dict[str, Any], source_class: str, source_path: str) -> None:
        if not row_like_visualdiff(row):
            return
        self.rows += 1
        source = (source_class, source_path)
        identifier = row_identifier(row)
        if identifier:
            self.ids[identifier].add(source)
        region = region_key(row)
        if region is not None:
            self.regions[region].add(source)
        fingerprint = evidence_fingerprint(row)
        if fingerprint:
            self.fingerprints[fingerprint].add(source)


def load_history(
    root: Path,
    input_path: Path,
    *,
    jsonl_globs: Iterable[str] = DEFAULT_JSONL_GLOBS,
    json_globs: Iterable[str] = DEFAULT_JSON_GLOBS,
    excluded_paths: Iterable[Path] = (),
) -> tuple[HistoryIndex, dict[str, Any]]:
    index = HistoryIndex()
    issues: list[str] = []
    excluded = {input_path.resolve()}
    excluded.update(path.resolve() for path in excluded_paths)
    active_gold = (root / ACTIVE_GOLD).resolve()
    jsonl_paths = discover_paths(root, jsonl_globs, excluded)
    if active_gold.is_file() and active_gold not in jsonl_paths:
        jsonl_paths.insert(0, active_gold)
    json_paths = discover_paths(root, json_globs, excluded)
    rows_by_source: Counter[str] = Counter()

    for path in jsonl_paths:
        source_class = classify_history_source(root, path)
        relative = path.relative_to(root).as_posix()
        try:
            before = index.rows
            for row in read_jsonl(path):
                index.add(row, source_class, relative)
            rows_by_source[source_class] += index.rows - before
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"{relative}: {exc}")

    for path in json_paths:
        source_class = classify_history_source(root, path)
        relative = path.relative_to(root).as_posix()
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            before = index.rows
            for row in nested_visualdiff_rows(value):
                index.add(row, source_class, relative)
            rows_by_source[source_class] += index.rows - before
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"{relative}: {exc}")

    return index, {
        "history_jsonl_files": len(jsonl_paths),
        "history_json_files": len(json_paths),
        "history_visualdiff_rows": index.rows,
        "history_rows_by_source_class": dict(sorted(rows_by_source.items())),
        "history_unique_ids": len(index.ids),
        "history_unique_regions": len(index.regions),
        "history_unique_fingerprints": len(index.fingerprints),
        "history_parse_issues": issues,
        "excluded_history_files": sorted(
            path.relative_to(root).as_posix()
            if path.is_relative_to(root)
            else path.as_posix()
            for path in excluded
        ),
    }


def matched_sources(
    index: HistoryIndex, row: dict[str, Any]
) -> tuple[list[str], list[tuple[str, str]]]:
    matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    identifier = row_identifier(row)
    if identifier:
        for source in index.ids.get(identifier, set()):
            matches[source].add("record_id")
    region = region_key(row)
    if region is not None:
        for source in index.regions.get(region, set()):
            matches[source].add("exact_region")
    fingerprint = evidence_fingerprint(row)
    if fingerprint:
        for source in index.fingerprints.get(fingerprint, set()):
            matches[source].add("evidence_fingerprint")
    reasons = sorted({reason for values in matches.values() for reason in values})
    sources = sorted(matches)
    return reasons, sources


def filter_rows(
    rows: list[dict[str, Any]], index: HistoryIndex
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for source_row in rows:
        row = dict(source_row)
        reasons, sources = matched_sources(index, row)
        identifier = row_identifier(row)
        if not identifier:
            reasons.append("missing_record_id")
        elif identifier in seen_ids:
            reasons.append("duplicate_input_record_id")
        seen_ids.add(identifier)
        reasons = sorted(set(reasons))
        for reason in reasons:
            reason_counts[reason] += 1
        for source_class, _ in sources:
            source_counts[source_class] += 1
        audit = {
            "decision": "hold" if reasons else "keep",
            "reasons": reasons,
            "matched_source_classes": sorted({item[0] for item in sources}),
            "matched_source_paths": [item[1] for item in sources[:20]],
            "matched_source_path_count": len(sources),
        }
        row["review_history_filter"] = audit
        row["safe_to_merge_gold"] = False
        if reasons:
            row["machine_qa_status"] = "machine_held_existing_review_history"
            row["review_status"] = "machine_held"
            held.append(row)
        else:
            row["machine_qa_status"] = "history_unique_needs_signal_review"
            row["review_status"] = "needs_machine_review"
            passing.append(row)

    report = {
        "input_rows": len(rows),
        "passing_rows": len(passing),
        "held_rows": len(held),
        "rows_by_overlap_reason": dict(sorted(reason_counts.items())),
        "overlap_hits_by_source_class": dict(sorted(source_counts.items())),
        "unique_input_record_ids": len({row_identifier(row) for row in rows if row_identifier(row)}),
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }
    return passing, held, report


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Review History Filter",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: `{report['input_rows']}`",
        f"- Passing history-unique rows: `{report['passing_rows']}`",
        f"- Held overlap rows: `{report['held_rows']}`",
        f"- History VisualDiff rows indexed: `{report['history_visualdiff_rows']}`",
        f"- History files scanned: `{report['history_jsonl_files'] + report['history_json_files']}`",
        f"- History parse issues: `{len(report['history_parse_issues'])}`",
        "- Gold rows modified: `0`",
        "- Human review is still required: `yes`",
        "",
        "## Overlap Reasons",
        "",
    ]
    if report["rows_by_overlap_reason"]:
        for name, count in report["rows_by_overlap_reason"].items():
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--history-jsonl-glob", action="append")
    parser.add_argument("--history-json-glob", action="append")
    parser.add_argument(
        "--exclude-history",
        type=Path,
        action="append",
        default=[],
        help=(
            "Known mirror of the current input to omit from history indexing; may be "
            "repeated. Active Gold is always indexed even if listed here."
        ),
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    excluded_paths = [
        path if path.is_absolute() else root / path for path in args.exclude_history
    ]
    rows = list(read_jsonl(input_path))
    index, history_report = load_history(
        root,
        input_path,
        jsonl_globs=args.history_jsonl_glob or DEFAULT_JSONL_GLOBS,
        json_globs=args.history_json_glob or DEFAULT_JSON_GLOBS,
        excluded_paths=excluded_paths,
    )
    passing, held, report = filter_rows(rows, index)
    report.update(history_report)
    report.update(
        {
            "goal": "Gold v2.0 Global",
            "input": input_path.relative_to(root).as_posix(),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "valid": (
                not history_report["history_parse_issues"]
                and len(passing) + len(held) == len(rows)
                and report["unique_input_record_ids"] == len(rows)
            ),
        }
    )

    output = args.output if args.output.is_absolute() else root / args.output
    hold_output = args.hold_output if args.hold_output.is_absolute() else root / args.hold_output
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_jsonl(output, passing)
    write_jsonl(hold_output, held)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "valid": report["valid"],
                "passing_rows": len(passing),
                "held_rows": len(held),
                "history_parse_issues": len(history_report["history_parse_issues"]),
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
