#!/usr/bin/env python3
"""Merge explicitly selected raster-region fragments into review candidates."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REQUIRED_SPEC_FIELDS = {
    "merged_candidate_id",
    "source_candidate_ids",
    "proposed_text",
    "category",
}

QUESTION_BY_CATEGORY = {
    "component_value": "What component value is shown in this region?",
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pin_label": "What pin or component label is shown in this marked region?",
    "pipe_line_tag": "What pipe or process line tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "room_label": "What room label is shown in this region?",
    "tolerance_value": "What tolerance is specified in this small text region?",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_specs(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_SPEC_FIELDS - fields)
        if missing:
            raise ValueError(f"merge spec is missing fields: {', '.join(missing)}")
        return list(reader)


def union_bbox(rows: list[dict[str, Any]]) -> list[int]:
    boxes = [row.get("bbox") for row in rows]
    if any(not isinstance(box, list) or len(box) != 4 for box in boxes):
        raise ValueError("all merge sources must have a four-value bbox")
    return [
        min(int(box[0]) for box in boxes),
        min(int(box[1]) for box in boxes),
        max(int(box[2]) for box in boxes),
        max(int(box[3]) for box in boxes),
    ]


def merge_candidates(
    candidates: list[dict[str, Any]], specs: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate row is missing candidate_id")
        if candidate_id in by_id:
            raise ValueError(f"duplicate candidate_id in input: {candidate_id}")
        by_id[candidate_id] = row

    merged: list[dict[str, Any]] = []
    merged_ids: set[str] = set()
    used_source_ids: set[str] = set()
    for spec_number, spec in enumerate(specs, start=2):
        merged_id = str(spec.get("merged_candidate_id") or "").strip()
        proposed_text = str(spec.get("proposed_text") or "").strip()
        category = str(spec.get("category") or "").strip()
        source_ids = [
            value.strip()
            for value in str(spec.get("source_candidate_ids") or "").split("|")
            if value.strip()
        ]
        if not merged_id or not proposed_text or not category:
            raise ValueError(f"incomplete merge spec at row {spec_number}")
        if merged_id in by_id or merged_id in merged_ids:
            raise ValueError(f"duplicate merged_candidate_id: {merged_id}")
        if len(source_ids) < 2:
            raise ValueError(f"merge spec needs at least two source IDs: {merged_id}")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"duplicate source ID inside merge spec: {merged_id}")
        missing = [source_id for source_id in source_ids if source_id not in by_id]
        if missing:
            raise ValueError(f"merge source IDs not found for {merged_id}: {', '.join(missing)}")
        overlap = sorted(set(source_ids) & used_source_ids)
        if overlap:
            raise ValueError(
                f"source candidate reused across merge specs for {merged_id}: {', '.join(overlap)}"
            )

        sources = [by_id[source_id] for source_id in source_ids]
        context_fields = ("doc_id", "version_id", "page_index", "image_path")
        for field in context_fields:
            values = {json.dumps(row.get(field), sort_keys=True) for row in sources}
            if len(values) != 1:
                raise ValueError(f"merge sources disagree on {field}: {merged_id}")

        result = dict(sources[0])
        result.update(
            candidate_id=merged_id,
            bbox=union_bbox(sources),
            target_text="",
            proposed_text=proposed_text,
            category=category,
            question_text=QUESTION_BY_CATEGORY.get(
                category,
                "What text is shown in this small engineering label region?",
            ),
            source="image_region_merge_proposal",
            review_status="needs_review",
            corrected_text="",
            merge_source_candidate_ids=source_ids,
            machine_qa_status="selected_for_human_review",
            machine_qa_notes=str(spec.get("notes") or "").strip(),
        )
        result["review_notes"] = (
            "Explicit union of machine-proposed raster fragments; sources="
            + "|".join(source_ids)
        )
        merged.append(result)
        merged_ids.add(merged_id)
        used_source_ids.update(source_ids)

    report = {
        "input_candidates": len(candidates),
        "merge_specs": len(specs),
        "merged_candidates": len(merged),
        "consumed_source_candidates": len(used_source_ids),
        "gold_rows_added": 0,
    }
    return merged, report


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Microtext Region Merge Report",
            "",
            f"- Input candidates: `{report['input_candidates']}`",
            f"- Merge specifications: `{report['merge_specs']}`",
            f"- Merged review candidates: `{report['merged_candidates']}`",
            f"- Consumed source fragments: `{report['consumed_source_candidates']}`",
            "- Gold rows added: `0`",
            "",
            "Merged rows remain `needs_review`; this tool never promotes annotations.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--merge-spec-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else args.root / path

    merged, report = merge_candidates(
        read_jsonl(resolve(args.input)), read_specs(resolve(args.merge_spec_csv))
    )
    write_jsonl(resolve(args.output), merged)
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
