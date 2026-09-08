#!/usr/bin/env python3
"""Normalize and merge reviewed VisualDiff rows into active benchmark JSONL.

The command is dry-run by default. Use --apply only after the prepared rows,
split policy, provenance, duplicate audit, and local evidence have been checked.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from apply_question_templates import VISUALDIFF_TEMPLATES, stable_index
    from visualdiff_description_finality import tentative_description_details
except ModuleNotFoundError:  # Imported as tools.visualdiff_merge in tests.
    from tools.apply_question_templates import VISUALDIFF_TEMPLATES, stable_index
    from tools.visualdiff_description_finality import tentative_description_details


MERGEABLE_STATUSES = {"accepted", "edited", "valid", "edit"}
TODO_DESCRIPTION = "CHANGE_DESC_GT_TODO"
CANONICAL_CHANGE_TYPES = {"addition", "deletion", "layout", "symbol", "text", "unknown", "value"}
CHANGE_TYPE_ALIASES = {
    "addition+text": ["addition", "text"],
    "deletion+text": ["deletion", "text"],
    "text_added_candidate": ["addition", "text"],
    "text_removed_candidate": ["deletion", "text"],
    "text_change_candidate": ["text"],
    "text_change": ["text"],
    "dimension_change_candidate": ["value"],
    "geometry_change_candidate": ["layout"],
    "geometry_change": ["layout"],
    "symbol_component_change": ["symbol"],
    "geometry_and_dimension_change_candidate": ["layout", "value"],
    "geometry_or_dimension_change_candidate": ["unknown"],
    "text_or_dimension_change_candidate": ["unknown"],
    "schematic_change_candidate": ["unknown"],
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_split_map(root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: list[str] = []
    for split in ("train", "dev", "test"):
        path = root / "splits" / f"visualdiff_{split}.txt"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            family = line.strip()
            if not family or family.startswith("#"):
                continue
            if family in mapping:
                duplicates.append(f"{family}: {mapping[family]} and {split}")
            mapping[family] = split
    if duplicates:
        raise ValueError("duplicate VisualDiff split assignments: " + "; ".join(duplicates))
    return mapping


def bbox_tuple(row: dict[str, Any], field: str) -> tuple[int, int, int, int] | None:
    bbox = row.get(field)
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        values = tuple(int(round(float(value))) for value in bbox)
    except (TypeError, ValueError):
        return None
    if values[0] >= values[2] or values[1] >= values[3]:
        return None
    return values


def page_index(row: dict[str, Any], side: str) -> int:
    for field in (f"page_index_{side}", f"page_{side}"):
        if field in row and row.get(field) not in (None, ""):
            return int(row[field])
    image_value = str(row.get(f"image_{side}") or "").strip()
    match = re.search(r"(?:page_|__p)(\d+)", Path(image_value).stem, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def region_key(row: dict[str, Any]) -> tuple[Any, ...] | None:
    old_bbox = bbox_tuple(row, "bbox_old")
    new_bbox = bbox_tuple(row, "bbox_new")
    if old_bbox is None or new_bbox is None:
        return None
    return (
        str(row.get("project_id") or ""),
        page_index(row, "old"),
        page_index(row, "new"),
        old_bbox,
        new_bbox,
    )


def review_status(row: dict[str, Any]) -> str:
    return str(
        row.get("human_review_status")
        or row.get("human_status")
        or row.get("review_status")
        or ""
    ).strip().lower()


def description(row: dict[str, Any]) -> str:
    return str(
        row.get("machine_reconciled_description")
        or row.get("machine_final_description")
        or row.get("localized_human_description")
        or row.get("human_description")
        or row.get("description")
        or row.get("change_desc_gt")
        or ""
    ).strip()


def normalized_change_type(row: dict[str, Any]) -> list[str]:
    """Preserve known semantics and fail closed on ambiguous proposals."""
    value = (
        row.get("reconciled_change_type")
        or row.get("human_change_type")
        or row.get("change_type")
    )
    if isinstance(value, list):
        raw_values = [str(item).strip().lower() for item in value]
    else:
        text = str(value or "").strip().lower()
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text.replace("'", '"'))
            except json.JSONDecodeError:
                decoded = None
            raw_values = [str(item).strip().lower() for item in decoded] if isinstance(decoded, list) else [text]
        elif "+" in text:
            raw_values = [part.strip() for part in text.split("+")]
        else:
            raw_values = [text]

    normalized: list[str] = []
    for raw in raw_values:
        mapped = CHANGE_TYPE_ALIASES.get(raw, [raw] if raw in CANONICAL_CHANGE_TYPES else ["unknown"])
        for item in mapped:
            if item not in normalized:
                normalized.append(item)
    return normalized or ["unknown"]


def manifest_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    docs = {
        str(row["doc_id"]): row
        for row in rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pairs = {
        str(row["pair_id"]): row
        for row in rows
        if row.get("type") == "pair" and row.get("pair_id")
    }
    return docs, pairs


def version_label(doc: dict[str, Any]) -> str:
    version = doc.get("version")
    if not isinstance(version, dict):
        return "unknown"
    for key in ("revision", "version", "release", "snapshot"):
        value = version.get(key)
        if value not in (None, "", []):
            if isinstance(value, list):
                return str(value[0]) if value else "unknown"
            return str(value)
    return "unknown"


def source_family(old_doc: dict[str, Any], new_doc: dict[str, Any], project_id: str) -> str:
    old_family = str(old_doc.get("same_model_id") or "")
    new_family = str(new_doc.get("same_model_id") or "")
    if old_family and old_family == new_family:
        return old_family
    prefix = project_id.removeprefix("vdiff__")
    return prefix.split("__", 1)[0] or "unknown"


def question_for(pair: dict[str, Any]) -> dict[str, Any]:
    pair_id = str(pair["pair_id"])
    old = str(pair["version_id_old"])
    new = str(pair["version_id_new"])
    template = VISUALDIFF_TEMPLATES[stable_index(pair_id, len(VISUALDIFF_TEMPLATES))]
    return {
        "question_id": f"q_{pair_id}",
        "pair_id": pair_id,
        "doc_id": pair["doc_id"],
        "version_id_old": old,
        "version_id_new": new,
        "query_text": template.format(old=old, new=new),
        "answer_text": pair["change_desc_gt"],
        "answer_type": "diff_description",
        "requires_connectivity": False,
        "requires_position": False,
        "split": pair["split"],
        "desc_source": pair["desc_source"],
        "template_family": "visualdiff_local_change",
    }


def normalize_reviewed_pair(
    row: dict[str, Any],
    split_map: dict[str, str],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    pair_id = str(row.get("pair_id") or row.get("id") or "").strip()
    project_id = str(row.get("project_id") or "").strip()
    status = review_status(row)
    answer = description(row)
    old_bbox = bbox_tuple(row, "bbox_old")
    new_bbox = bbox_tuple(row, "bbox_new")
    if not pair_id:
        errors.append("missing_pair_id")
    if not project_id:
        errors.append("missing_project_id")
    if status not in MERGEABLE_STATUSES:
        errors.append(f"non_mergeable_status:{status or 'blank'}")
    if not answer or answer == TODO_DESCRIPTION:
        errors.append("missing_human_description")
    elif tentative_description_details(answer):
        errors.append("tentative_visualdiff_description")
    if old_bbox is None:
        errors.append("invalid_bbox_old")
    if new_bbox is None:
        errors.append("invalid_bbox_new")
    split = split_map.get(project_id)
    if split is None:
        errors.append("missing_family_split_assignment")
    manifest_pair = manifest_pairs.get(project_id)
    if manifest_pair is None:
        errors.append("missing_manifest_pair")
        old_doc = {}
        new_doc = {}
    else:
        old_doc_id = str(manifest_pair.get("from_doc_id") or "")
        new_doc_id = str(manifest_pair.get("to_doc_id") or "")
        old_doc = docs.get(old_doc_id, {})
        new_doc = docs.get(new_doc_id, {})
        if not old_doc:
            errors.append(f"missing_manifest_doc:{old_doc_id or 'old'}")
        if not new_doc:
            errors.append(f"missing_manifest_doc:{new_doc_id or 'new'}")
    image_old = str(row.get("image_old") or "").strip()
    image_new = str(row.get("image_new") or "").strip()
    if not image_old:
        errors.append("missing_image_old")
    if not image_new:
        errors.append("missing_image_new")
    if errors:
        return None, errors

    assert old_bbox is not None and new_bbox is not None and split is not None
    old_version = version_label(old_doc)
    new_version = version_label(new_doc)
    family = source_family(old_doc, new_doc, project_id)
    old_page = page_index(row, "old")
    new_page = page_index(row, "new")
    human_notes = str(row.get("human_review_notes") or row.get("human_notes") or "").strip()
    source_path = str(row.get("human_completion_source_path") or "").replace("\\", "/")
    localized_description = str(row.get("localized_human_description") or "").strip()
    machine_final_description = str(row.get("machine_final_description") or "").strip()
    machine_reconciled_description = str(row.get("machine_reconciled_description") or "").strip()
    normalized = {
        "pair_id": pair_id,
        "project_id": project_id,
        "doc_id": family,
        "board_id": None,
        "eco_id": None,
        "version_id_old": old_version,
        "version_id_new": new_version,
        "page_index_old": old_page,
        "page_index_new": new_page,
        "page_id_old": f"{family}__{old_version}__p{old_page:04d}",
        "page_id_new": f"{family}__{new_version}__p{new_page:04d}",
        "bbox_old": list(old_bbox),
        "bbox_new": list(new_bbox),
        "object_id_old": None,
        "object_id_new": None,
        "change_type": normalized_change_type(row),
        "severity": "unknown",
        "is_titleblock": False,
        "entity_id": None,
        "entity_kind": None,
        "change_desc_gt": answer,
        "notes": human_notes,
        "source": {
            "type": "human_reviewed_source_expansion",
            "source_candidate_id": row.get("source_candidate_id"),
            "review_jsonl": source_path,
            "original_source": row.get("source"),
        },
        "split": split,
        "desc_source": (
            "human_semantics_machine_localized"
            if localized_description
            else (
                "human_semantics_machine_visual_reconciled"
                if machine_reconciled_description
                else (
                    "human_semantics_machine_epistemic_normalized"
                    if machine_final_description
                    else "human"
                )
            )
        ),
        "review_confidence": "high",
        "review_status": status,
        "human_review_status": status,
        "human_completion_date_label": row.get("human_completion_date_label"),
        "image_old": image_old,
        "image_new": image_new,
        "review_evidence": {
            "human_review_notes": human_notes,
            "original_human_description": row.get("original_human_description")
            or row.get("human_description"),
            "localized_human_description": localized_description,
            "machine_final_description": machine_final_description,
            "machine_reconciled_description": machine_reconciled_description,
            "localization_method": row.get("localization_method"),
            "reconciliation_evidence_sheet": row.get("reconciliation_evidence_sheet"),
            "description_finalization": row.get("description_finalization"),
            "independent_audit_support": row.get("independent_audit_support"),
            "localization_provenance": row.get("localization_provenance"),
            "semantic_reconciliation": row.get("semantic_reconciliation"),
            "image_old": image_old,
            "image_new": image_new,
        },
    }
    return normalized, []


def merge_reviewed_rows(
    existing_pairs: list[dict[str, Any]],
    existing_questions: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
    split_map: dict[str, str],
    manifest_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    docs, manifest_pairs = manifest_indexes(manifest_rows)
    merged_pairs = list(existing_pairs)
    merged_questions = list(existing_questions)
    accepted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    active_ids = {str(row.get("pair_id") or "") for row in existing_pairs}
    active_regions = {key for row in existing_pairs if (key := region_key(row)) is not None}
    question_ids = {str(row.get("question_id") or "") for row in existing_questions}
    stats: Counter[str] = Counter(reviewed_rows=len(reviewed_rows))

    for source_row in reviewed_rows:
        pair, errors = normalize_reviewed_pair(source_row, split_map, docs, manifest_pairs)
        identifier = str(source_row.get("pair_id") or source_row.get("id") or "")
        if pair is None:
            stats["held"] += 1
            held.append({"pair_id": identifier, "reasons": errors})
            continue
        key = region_key(pair)
        if pair["pair_id"] in active_ids:
            stats["duplicate_pair_id"] += 1
            continue
        if key is not None and key in active_regions:
            stats["duplicate_region"] += 1
            continue
        question = question_for(pair)
        if question["question_id"] in question_ids:
            stats["duplicate_question_id"] += 1
            held.append({"pair_id": pair["pair_id"], "reasons": ["duplicate_question_id"]})
            continue
        merged_pairs.append(pair)
        merged_questions.append(question)
        accepted.append(pair)
        active_ids.add(pair["pair_id"])
        if key is not None:
            active_regions.add(key)
        question_ids.add(question["question_id"])
        stats["accepted"] += 1

    report = {
        "safe_to_merge_gold": False,
        "counts": dict(sorted(stats.items())),
        "held_rows": held,
    }
    return merged_pairs, merged_questions, accepted, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--reviewed", required=True)
    parser.add_argument("--pairs", default="visualdiff/annotations/visualdiff_pairs.jsonl")
    parser.add_argument("--questions", default="visualdiff/annotations/visualdiff_questions.jsonl")
    parser.add_argument("--prepared-pairs")
    parser.add_argument("--prepared-questions")
    parser.add_argument("--combined-pairs")
    parser.add_argument("--combined-questions")
    parser.add_argument("--report")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    resolve = lambda value: Path(value) if Path(value).is_absolute() else root / value
    pairs_path = resolve(args.pairs)
    questions_path = resolve(args.questions)
    merged_pairs, merged_questions, accepted, report = merge_reviewed_rows(
        read_jsonl(pairs_path),
        read_jsonl(questions_path),
        read_jsonl(resolve(args.reviewed)),
        read_split_map(root),
        read_jsonl(root / "manifest.jsonl"),
    )
    report.update(
        {
            "applied": bool(args.apply),
            "active_pairs_before": len(read_jsonl(pairs_path)),
            "active_pairs_after": len(merged_pairs),
            "prepared_pair_ids": [row["pair_id"] for row in accepted],
        }
    )
    if args.prepared_pairs:
        write_jsonl(resolve(args.prepared_pairs), accepted)
    if args.prepared_questions:
        accepted_ids = {row["pair_id"] for row in accepted}
        write_jsonl(
            resolve(args.prepared_questions),
            [row for row in merged_questions if row.get("pair_id") in accepted_ids],
        )
    if args.combined_pairs:
        write_jsonl(resolve(args.combined_pairs), merged_pairs)
    if args.combined_questions:
        write_jsonl(resolve(args.combined_questions), merged_questions)
    if args.apply:
        write_jsonl(pairs_path, merged_pairs)
        write_jsonl(questions_path, merged_questions)
        report["safe_to_merge_gold"] = not report["held_rows"]
    if args.report:
        write_json(resolve(args.report), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and report["held_rows"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
