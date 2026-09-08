#!/usr/bin/env python3
"""Apply deterministic, semantically equivalent question-template variation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


MICROTEXT_TEMPLATES = {
    "component_value": [
        "What component value is shown in this region?",
        "Read the electrical component value at this location.",
        "What exact component value is printed here?",
        "Transcribe the component value in this area.",
        "Which electrical value appears in the marked region?",
        "Give the component value shown at this spot.",
        "What component rating or value is visible here?",
        "Read the small component-value text in this crop.",
    ],
    "pin_label": [
        "What pin or component label is shown in this marked region?",
        "Read the pin or component label in this region.",
        "Which pin/component label appears at this location?",
        "Transcribe the visible pin or component label here.",
        "What label identifies the pin or component in this crop?",
        "Give the exact pin/component label shown in this area.",
        "What is the component or pin label at the indicated spot?",
        "Read the small pin/component text in this region.",
    ],
    "dimension_value": [
        "What dimension value is shown in this region?",
        "Read the dimension annotation at this location.",
        "What exact dimension is printed here?",
        "Transcribe the dimension value in this area.",
        "Which measurement value appears in the marked region?",
        "Give the engineering dimension shown at this spot.",
        "What numeric dimension text is visible here?",
        "Read the printed measurement in this region.",
    ],
    "equipment_tag": [
        "What equipment tag is shown in this region?",
        "Read the equipment label at this location.",
        "Which equipment identifier appears here?",
        "Transcribe the equipment tag in the marked area.",
        "What equipment code is printed at this spot?",
        "Give the exact equipment label shown here.",
        "What equipment designation is visible in this region?",
        "Read the equipment text in this crop.",
    ],
    "instrument_tag": [
        "What instrument tag is shown in this region?",
        "Read the instrument identifier at this location.",
        "Which instrument label appears here?",
        "Transcribe the instrument tag in the marked area.",
        "What instrumentation code is printed at this spot?",
        "Give the exact instrument tag shown here.",
        "What control/instrument label is visible in this region?",
        "Read the instrument text in this crop.",
    ],
    "pipe_line_tag": [
        "What pipe or process line tag is shown in this region?",
        "Read the line identifier at this location.",
        "Which process-line tag appears here?",
        "Transcribe the pipe or line tag in this area.",
        "What line designation is printed at this spot?",
        "Give the exact pipe/process line label shown here.",
        "What process line text is visible in this region?",
        "Read the pipe-line identifier in this crop.",
    ],
    "process_value": [
        "What process value is shown in this region?",
        "Read the process value at this location.",
        "Which process setting appears here?",
        "Transcribe the process value in this area.",
        "What operating value is printed at this spot?",
        "Give the exact process value shown here.",
        "What process annotation value is visible in this region?",
        "Read the process-value text in this crop.",
    ],
    "process_label": [
        "What process step or stream label is shown in this region?",
        "Read the process label at this location.",
        "Which process step or material stream appears here?",
        "Transcribe the process label in this area.",
        "What process-stage text is printed at this spot?",
        "Give the exact process label shown here.",
        "What process flow label is visible in this region?",
        "Read the process step or stream text in this crop.",
    ],
    "room_label": [
        "What room label is shown in this region?",
        "Read the room name at this location.",
        "Which room label appears here?",
        "Transcribe the room label in the marked area.",
        "What space name is printed at this spot?",
        "Give the exact room label shown here.",
        "What architectural room text is visible in this region?",
        "Read the room label in this crop.",
    ],
    "tolerance_value": [
        "What tolerance is specified in this small text region?",
        "Read the tolerance value at this location.",
        "Which tolerance notation appears here?",
        "Transcribe the tolerance text in the marked area.",
        "What tolerance callout is printed at this spot?",
        "Give the exact tolerance value shown here.",
        "What fit or tolerance text is visible in this region?",
        "Read the tolerance annotation in this crop.",
    ],
    "unknown": [
        "What text is shown in this region?",
        "Read the small text at this location.",
        "Which label appears here?",
        "Transcribe the text in the marked area.",
        "What exact text is printed at this spot?",
        "Give the visible text shown here.",
        "What engineering text is visible in this region?",
        "Read the text in this crop.",
    ],
}

VISUALDIFF_TEMPLATES = [
    "What changed in this region between {old} and {new}?",
    "Describe the visible change from {old} to {new} in this area.",
    "How does the highlighted component differ between {old} and {new}?",
    "What revision difference is visible here from {old} to {new}?",
    "Identify the change in this marked region between {old} and {new}.",
    "What was modified at this location from {old} to {new}?",
    "Compare this area across {old} and {new}; what changed?",
    "What engineering-document change appears in this region between {old} and {new}?",
    "Summarize the local difference between {old} and {new}.",
    "What changed about the shown component or annotation from {old} to {new}?",
    "Describe the old-versus-new difference visible at this spot.",
    "What local update is present when comparing {old} against {new}?",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_index(key: str, count: int) -> int:
    numbers = re.findall(r"\d+", key)
    if numbers:
        return int(numbers[-1]) % count
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % count


def first_item_id(question: dict[str, Any]) -> str:
    item_ids = question.get("item_ids")
    if isinstance(item_ids, list) and item_ids:
        return str(item_ids[0])
    return str(question.get("item_id") or "")


def category_for(question: dict[str, Any], items_by_id: dict[str, dict[str, Any]]) -> str:
    item = items_by_id.get(first_item_id(question), {})
    return str(question.get("category") or item.get("category") or "unknown")


def apply_microtext_templates(
    questions: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    updated = []
    stats = {"updated": 0}
    for question in questions:
        out = dict(question)
        category = category_for(question, items_by_id)
        templates = MICROTEXT_TEMPLATES.get(category, MICROTEXT_TEMPLATES["unknown"])
        key = str(out.get("question_id") or first_item_id(out) or len(updated))
        out["query_text"] = templates[stable_index(key, len(templates))]
        out["template_family"] = category
        stats["updated"] += 1
        updated.append(out)
    return updated, stats


def pair_versions(question: dict[str, Any], pair: dict[str, Any]) -> tuple[str, str]:
    old = question.get("version_id_old") or pair.get("version_id_old") or "old"
    new = question.get("version_id_new") or pair.get("version_id_new") or "new"
    return str(old), str(new)


def apply_visualdiff_templates(
    questions: list[dict[str, Any]],
    pairs_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    updated = []
    stats = {"updated": 0, "missing_pair": 0}
    for question in questions:
        out = dict(question)
        pair = pairs_by_id.get(str(out.get("pair_id") or ""), {})
        if not pair:
            stats["missing_pair"] += 1
        old, new = pair_versions(out, pair)
        key = str(out.get("question_id") or out.get("pair_id") or len(updated))
        template = VISUALDIFF_TEMPLATES[stable_index(key, len(VISUALDIFF_TEMPLATES))]
        out["query_text"] = template.format(old=old, new=new)
        out["template_family"] = "visualdiff_local_change"
        stats["updated"] += 1
        updated.append(out)
    return updated, stats


def apply_templates(root: Path) -> dict[str, int]:
    micro_items = load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    micro_questions = load_jsonl(root / "microtext" / "annotations" / "microtext_questions.jsonl")
    visual_pairs = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    visual_questions = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl")

    updated_micro, micro_stats = apply_microtext_templates(
        micro_questions,
        {str(item.get("item_id")): item for item in micro_items},
    )
    updated_visual, visual_stats = apply_visualdiff_templates(
        visual_questions,
        {str(pair.get("pair_id")): pair for pair in visual_pairs},
    )
    save_jsonl(root / "microtext" / "annotations" / "microtext_questions.jsonl", updated_micro)
    save_jsonl(root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl", updated_visual)
    return {
        "microtext_updated": micro_stats["updated"],
        "visualdiff_updated": visual_stats["updated"],
        "visualdiff_missing_pair": visual_stats["missing_pair"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply deterministic question-template variation.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    args = parser.parse_args(argv)

    stats = apply_templates(Path(args.root))
    print(f"[OK] Updated microtext questions: {stats['microtext_updated']}")
    print(f"[OK] Updated visualdiff questions: {stats['visualdiff_updated']}")
    if stats["visualdiff_missing_pair"]:
        print(f"[WARN] Visualdiff questions without pairs: {stats['visualdiff_missing_pair']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
