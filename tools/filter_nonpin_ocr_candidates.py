#!/usr/bin/env python3
"""Conservatively shortlist non-pin OCR candidates for human confirmation."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


ALLOWED_CATEGORIES = {
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "tolerance_value",
}
QUESTION_BY_CATEGORY = {
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pipe_line_tag": "What pipe or line tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "tolerance_value": "What tolerance value is shown in this region?",
}
INSTRUMENT_TAG_RE = re.compile(
    r"^(?:SOV|HOV|ROV|PT|TS|TC|DP|DPT|FC|FCV|FV|PV|RV|QM|QG|FO|FI|FQ|"
    r"TRV|VMI|MLI|LC|LV|LY|PCV|PSV|SVC)\d{1,4}[A-Z]?$",
    re.IGNORECASE,
)
BARE_INSTRUMENT_TAG_RE = re.compile(r"^(?:PT|TC|DP|DPT|TS)$", re.IGNORECASE)
EQUIPMENT_TAG_RE = re.compile(
    r"^(?:HX|CHX|HTR|SMR|TK|IRGA|P|F|M|V)\d{1,4}(?:-\d{1,3})?$",
    re.IGNORECASE,
)
PROCESS_VALUE_RE = re.compile(
    r"^(?:"
    r"[PTW]\s*=\s*\d+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?\s*-?\s*(?:PSI|PSIG|PSIA|PSID|PPS|LB/S|GPM|GPH|"
    r"KPA|MPA|BAR|ATM|°F|°C|DEG\.?\s*[FC])"
    r")$",
    re.IGNORECASE,
)
GENERIC_EQUIPMENT_LABELS = {
    "accumulator",
    "after-cooler",
    "air exchanger",
    "boiler",
    "catch and weigh tank",
    "centrifugal pump",
    "burst disc",
    "check valve",
    "collector/throttle valve",
    "combustion chamber",
    "condenser",
    "cooler",
    "diffuser",
    "dome regulator",
    "drive motor",
    "filter",
    "flow control valve",
    "gear-box",
    "gear pump",
    "generator",
    "hand regulator",
    "hand valve",
    "heat sink",
    "heat exchanger",
    "heat exchangers",
    "heater",
    "hot water bath heat exchanger",
    "hydraulic pumps",
    "injector module",
    "main cooler",
    "oil sump",
    "orifice",
    "plenum",
    "pneumatic valve",
    "power supply",
    "primary heater",
    "pump",
    "radiator",
    "reactor",
    "relief valve",
    "run tank",
    "screen device",
    "sight glass",
    "solenoid operated valve",
    "solenoid valve",
    "straightener screen",
    "strainer",
    "superheater",
    "test cell",
    "test condenser",
    "test radiator",
    "test stage",
    "turbine",
    "vacuum chamber",
    "vacuum pumps",
    "vacuum sphere",
    "voltage regulator",
}
GENERIC_INSTRUMENT_LABELS = {
    "flowmeter",
    "load cell",
    "pressure gauge",
    "pressure transducers",
    "pressure transducer",
    "quality meter",
    "temperature sensor",
    "voltmeter",
    "volumetric flowmeter",
}
PROCESS_LABELS = {
    "air in",
    "altitude exhaust",
    "atmospheric exhaust",
    "cabin air inlet",
    "cabin air outlet",
    "cell ventilation",
    "combustion air",
    "cooling air in",
    "flow venturi",
    "ground fill",
    "h2 in",
    "inlet",
    "inlet manifold",
    "lab exhaust",
    "lh2 fill/drain",
    "mli wrapping",
    "n2 in",
    "outlet",
    "primary quench",
    "return",
    "secondary quench",
    "test section",
    "vac jacketing",
    "vacuum connection",
    "vent",
    "water in",
    "water",
    "water quench",
    "water spray",
}
BAD_TEXT_MARKERS = ("\ufffd", "<svg", "<text", "<tspan")
CAPTION_RE = re.compile(r"^(?:fig(?:ure)?\.?|table|nasa/|phase\s+[ivx]+\s+flow\s+schematic)", re.I)
CAPTION_FRAGMENT_RE = re.compile(r"(?:subsystem|system)\s+schematic$", re.I)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" |_~`.,;:")


def normalized_text(value: Any) -> str:
    ascii_text = unicodedata.normalize("NFKD", clean_text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_text.casefold())


def supplemental_category(text: str) -> str:
    if INSTRUMENT_TAG_RE.fullmatch(text) or BARE_INSTRUMENT_TAG_RE.fullmatch(text):
        return "instrument_tag"
    if EQUIPMENT_TAG_RE.fullmatch(text):
        return "equipment_tag"
    if PROCESS_VALUE_RE.fullmatch(text):
        return "process_value"
    if text.casefold() in GENERIC_INSTRUMENT_LABELS:
        return "instrument_tag"
    if text.casefold() in GENERIC_EQUIPMENT_LABELS:
        return "equipment_tag"
    if text.casefold() in PROCESS_LABELS:
        return "process_label"
    return ""


def bbox(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def overlap_over_smaller(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    ix = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    iy = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = ix * iy
    if intersection <= 0:
        return 0.0
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / min(left_area, right_area)


def text_is_usable(text: str) -> bool:
    if not 2 <= len(text) <= 64:
        return False
    lowered = text.casefold()
    if any(marker in lowered for marker in BAD_TEXT_MARKERS):
        return False
    if CAPTION_RE.match(text) or CAPTION_FRAGMENT_RE.search(text):
        return False
    if text.endswith("-") or len(re.findall(r"[A-Za-z0-9]+", text)) > 7:
        return False
    return bool(re.search(r"[A-Za-z0-9]", text))


def evidence_confidence(row: dict[str, Any], text: str) -> tuple[float, str]:
    """Return confidence without pretending exact embedded text is OCR."""
    source = str(row.get("source") or "").strip()
    raw = clean_text(row.get("raw_text"))
    target = clean_text(row.get("target_text"))
    proposed = clean_text(row.get("proposed_text"))
    if (
        source == "textlayer_full_span_candidate"
        and raw
        and raw == target == proposed == text
    ):
        return 1.0, "exact_embedded_textlayer"
    for field in ("machine_confidence", "ocr_confidence"):
        try:
            value = float(row.get(field) or 0.0)
        except (TypeError, ValueError):
            continue
        if value:
            return value, field
    return 0.0, "missing"


def shortlist(
    rows: list[dict[str, Any]],
    *,
    min_confidence: float,
    max_per_answer_page: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    provisional: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()

    def hold(row: dict[str, Any], reason: str) -> None:
        output = dict(row)
        output["machine_qa_status"] = "machine_held_nonpin_shortlist"
        output["machine_hold_reason"] = reason
        output["safe_to_merge_gold"] = False
        held.append(output)
        hold_reasons[reason] += 1

    seen_ids: set[str] = set()
    for source_row in rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen_ids:
            hold(row, "missing_or_duplicate_candidate_id")
            continue
        seen_ids.add(candidate_id)
        region = bbox(row)
        if region is None:
            hold(row, "invalid_bbox")
            continue
        text = clean_text(row.get("proposed_text") or row.get("target_text"))
        if not text_is_usable(text):
            hold(row, "text_quality_or_narrative_hold")
            continue
        confidence, confidence_source = evidence_confidence(row, text)
        if confidence < min_confidence:
            hold(row, "below_confidence_floor")
            continue
        original_category = str(row.get("category") or "").strip()
        lexical_category = supplemental_category(text)
        if (
            str(row.get("source") or "") == "textlayer_full_span_candidate"
            and original_category == "process_label"
            and not lexical_category
        ):
            hold(row, "textlayer_process_label_not_explicit")
            continue
        category = lexical_category or (
            original_category if original_category in ALLOWED_CATEGORIES else ""
        )
        reason = "recognized_nonpin_category"
        if lexical_category and lexical_category != original_category:
            reason = "explicit_pattern_rescue"
        if not category:
            hold(row, "not_explicit_nonpin_engineering_label")
            continue
        row["original_category"] = original_category
        row["category"] = category
        row["proposed_text"] = text
        row["question_text"] = QUESTION_BY_CATEGORY[category]
        row["machine_qa_status"] = "machine_shortlisted_nonpin"
        row["machine_shortlist_reason"] = reason
        row["machine_evidence_confidence"] = confidence
        row["machine_evidence_confidence_source"] = confidence_source
        row["review_status"] = "needs_review"
        row["promotion_state"] = "unreviewed_candidate"
        row["safe_to_merge_gold"] = False
        provisional.append(row)

    ranked = sorted(
        provisional,
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            str(row.get("category") or ""),
            -len(normalized_text(row.get("proposed_text"))),
            -float(row.get("machine_evidence_confidence") or 0.0),
            str(row.get("candidate_id") or ""),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_by_scope: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    answer_counts: Counter[tuple[str, int, str, str]] = Counter()
    for row in ranked:
        doc_id = str(row.get("doc_id") or "")
        page_index = int(row.get("page_index") or 0)
        category = str(row.get("category") or "")
        text_key = normalized_text(row.get("proposed_text"))
        answer_key = (doc_id, page_index, category, text_key)
        if max_per_answer_page and answer_counts[answer_key] >= max_per_answer_page:
            hold(row, "repeated_answer_cap")
            continue
        duplicate = False
        current_box = bbox(row)
        assert current_box is not None
        for prior in selected_by_scope[(doc_id, page_index)]:
            prior_text = normalized_text(prior.get("proposed_text"))
            shorter, longer = sorted((text_key, prior_text), key=len)
            containment_match = (
                len(shorter) >= 3
                and shorter in longer
                and len(shorter) / len(longer) >= 0.5
            )
            near_match = (
                min(len(text_key), len(prior_text)) >= 3
                and len(text_key) == len(prior_text)
                and difflib.SequenceMatcher(None, text_key, prior_text).ratio() >= 0.66
            )
            if text_key != prior_text and not containment_match and not near_match:
                continue
            prior_box = bbox(prior)
            assert prior_box is not None
            if overlap_over_smaller(current_box, prior_box) >= 0.70:
                duplicate = True
                break
        if duplicate:
            hold(row, "overlapping_tile_duplicate")
            continue
        selected.append(row)
        selected_by_scope[(doc_id, page_index)].append(row)
        answer_counts[answer_key] += 1

    selected.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or []),
            str(row.get("candidate_id") or ""),
        )
    )
    held.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or []),
            str(row.get("candidate_id") or ""),
        )
    )
    report = {
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_by_category": dict(sorted(Counter(row["category"] for row in selected).items())),
        "selected_by_document": dict(sorted(Counter(row["doc_id"] for row in selected).items())),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "min_confidence": min_confidence,
        "max_per_answer_page": max_per_answer_page,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }
    if len(selected) + len(held) != len(rows):
        raise AssertionError("shortlist accounting mismatch")
    return selected, held, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--selected-output", required=True)
    parser.add_argument("--held-output", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--min-confidence", type=float, default=0.86)
    parser.add_argument("--max-per-answer-page", type=int, default=4)
    args = parser.parse_args(argv)
    if not 0.0 <= args.min_confidence <= 1.0:
        raise ValueError("--min-confidence must be between 0 and 1")
    if args.max_per_answer_page < 0:
        raise ValueError("--max-per-answer-page must be nonnegative")

    input_path = Path(args.input)
    rows = read_jsonl(input_path)
    selected, held, report = shortlist(
        rows,
        min_confidence=args.min_confidence,
        max_per_answer_page=args.max_per_answer_page,
    )
    write_jsonl(Path(args.selected_output), selected)
    write_jsonl(Path(args.held_output), held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": sha256(input_path),
            "selected_output": Path(args.selected_output).as_posix(),
            "selected_sha256": sha256(Path(args.selected_output)),
            "held_output": Path(args.held_output).as_posix(),
            "held_sha256": sha256(Path(args.held_output)),
        }
    )
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = [
        "# Non-Pin OCR Shortlist",
        "",
        f"- Input rows: {report['input_rows']}",
        f"- Selected rows: {report['selected_rows']}",
        f"- Held rows: {report['held_rows']}",
        f"- Selected categories: `{json.dumps(report['selected_by_category'], sort_keys=True)}`",
        f"- Hold reasons: `{json.dumps(report['hold_reasons'], sort_keys=True)}`",
        "- Safe to merge Gold: `false`",
        "- Active Gold modified: `false`",
        "",
        "Selected rows are machine-prefilled review candidates, not certified labels.",
    ]
    md_path = Path(args.report_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("input_rows", "selected_rows", "held_rows", "selected_by_category")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
