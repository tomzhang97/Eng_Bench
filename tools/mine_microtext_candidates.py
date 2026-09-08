#!/usr/bin/env python3
"""Mine reviewable microtext candidates from existing textlayer JSONL files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


PID_INSTRUMENT_TOKENS = {
    "AAH",
    "AE",
    "AI",
    "AIT",
    "AL",
    "AO",
    "CP",
    "CR",
    "DI",
    "DO",
    "ES",
    "ETM",
    "FCV",
    "FM",
    "HMS",
    "HS",
    "KS",
    "FI",
    "FIC",
    "LE",
    "LI",
    "LIC",
    "LIT",
    "LKG",
    "LSH",
    "OL",
    "PA",
    "PI",
    "PIC",
    "PIT",
    "PCV",
    "POT",
    "PS",
    "RHI",
    "TA",
    "TAH",
    "TE",
    "TI",
    "TIC",
    "TMP",
    "TS",
    "TSH",
    "VIB",
}
MONTH_ABBREVIATIONS = {
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
}

PID_EQUIPMENT_TERMS = {
    "ABSORBER",
    "ACID GAS REMOVAL",
    "ALKYLATION",
    "AMINE TREATING",
    "ATMOSPHERIC DISTILLATION",
    "CONDENSER",
    "DEHYDRATION",
    "EYE WASH STATION",
    "FAN",
    "FRACTIONATION TRAIN",
    "GAS PROCESSING",
    "GASOLINE BLENDING POOL",
    "GENERATOR",
    "GRINDER",
    "HYDROCRACKER",
    "HYDROTREATER",
    "LOCAL CONTROL PANEL",
    "MCC",
    "MEROX TREATER",
    "MEROX TREATERS",
    "MIXER",
    "MERCURY REMOVAL",
    "NGL RECOVERY",
    "NITROGEN REJECTION",
    "PLC",
    "POWER SUPPLY UNIT",
    "PSU",
    "PUMP",
    "REBOILER",
    "REGENERATOR",
    "RVSS",
    "SOFT STARTER",
    "SOUR WATER STRIPPER",
    "SILO",
    "STEAM STRIPPER",
    "SUBMERSIBLE SEWAGE PUMP",
    "SULFUR UNIT",
    "SUMP PUMP",
    "SWEETENING UNITS",
    "TAIL GAS TREATING",
    "UNINTERRUPTIBLE POWER SUPPLY",
    "UPS",
    "VARIABLE FREQUENCY DRIVE",
    "VFD",
}

PID_INSTRUMENT_PATTERN = "|".join(sorted(map(re.escape, PID_INSTRUMENT_TOKENS), key=len, reverse=True))
PID_EQUIPMENT_PATTERN = "|".join(sorted(map(re.escape, PID_EQUIPMENT_TERMS), key=len, reverse=True))
PIN_LABEL_PATTERN = (
    r"(?:"
    r"(?:J|P|U|R|C|L|D|E|TP|CN)\d{1,4}[A-Z]?[+-]?|"
    r"P[A-K]\d{1,2}|"
    r"(?:GPIO|RTC|DAC|SW|A|T)\d{1,3}|"
    r"ADC\d+_CH\d+|"
    r"(?:H|V)SPI(?:CLK|CS0|D|HD|Q|WP)|"
    r"U\d+(?:CTS|RTS)|"
    r"HS[12]_(?:CLK|CMD|DATA\d+)|"
    r"32K_X[PN]|CLK_OUT\d+|"
    r"V(?:BUS|BAT|DET\d+|_NEOI2C)|"
    r"NEOPIXEL_I2C_POWER|VOLTAGE_MONITOR|"
    r"SCK|SCL|SDA|MOSI|MISO|RX|TX|GND|VDD|VDDA|AVDD|VSS|BOOT0|IOREF|RESET|SWDIO|SWCLK|EN|RST|BAT|USB|NC|MI|MO|3V|3\.3V"
    r")"
)
PIN_LABEL_RE = re.compile(
    rf"(?<![A-Za-z0-9_]){PIN_LABEL_PATTERN}(?![A-Za-z0-9_])",
    re.I,
)
SCHEMATIC_SIGNAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+\d+V\d?|[A-Z0-9]+(?:_[A-Z0-9]+){1,5})(?![A-Za-z0-9])"
)
SCHEMATIC_SIGNAL_EXCLUSIONS = {"PWR_FLAG"}
PART_NUMBER_SIGNAL_RE = re.compile(r"[A-Z0-9]+_[RCLDUQJ]\d+_\d+")
COMPONENT_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"\d{1,6}(?:\.\d+)?\s*(?:[pnum\u00b5]?[FfHh]|[kK](?:ohm)?|M(?:ohm)?|[Rr]|ohm|\u03a9)|"
    r"\d{1,6}(?:[RKM]\d+)+"
    r")(?![A-Za-z0-9])"
)

PROFILES: list[tuple[str, re.Pattern[str]]] = [
    (
        "pipe_line_tag",
        re.compile(
            r"\b(?:[A-Z]{2,5}-)?\d{2,3}-\d{2,4}-[A-Z]{1,5}(?:-[A-Z0-9]{1,5}){0,4}\b",
            re.I,
        ),
    ),
    (
        "process_value",
        re.compile(
            r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?\s+to\s+\d[\d,]*(?:\.\d+)?\s*"
            r"(?:psi[adg]?|bar(?:g|a)?|kpa|mpa|kg/h|m3/h|g/s|slpm|%)"
            r"(?![A-Za-z0-9])",
            re.I,
        ),
    ),
    (
        "process_value",
        re.compile(
            r"(?<![A-Za-z0-9])(?:Q|DP|P[AI]?|T[AI]?|F[AI]?|L[AI]?|V)\s*[:=]\s*"
            r"(?P<value>\d+(?:[.,]\d+)?\s*(?:"
            r"bar(?:g|a)?|barü|baru|psi[adg]?|kpa|mpa|°c|degc|c|"
            r"kg/h|m3/h|m³/h|g/s|slpm|l/min|gpm|m3|m³|%"
            r"))(?![A-Za-z0-9])",
            re.I,
        ),
    ),
    (
        "process_value",
        re.compile(
            r"\b(?:P[AI]?|T[AI]?|F[AI]?|L[AI]?)\s*[:=]\s*\d+(?:[.,]\d+)?\s*"
            r"(?:bar(?:g|a)?|barü|baru|psi[adg]?|kpa|mpa|°c|degc|c|kg/h|m3/h|g/s|slpm|%)\b|"
            r"\b\d+(?:[.,]\d+)?\s*(?:barg|bara|barü|baru|bar|psi[adg]?|kpa|mpa)\b",
            re.I,
        ),
    ),
    (
        "process_value",
        re.compile(
            r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:Â°\s*[CF]|°\s*[CF]|deg\s*[CF])"
            r"(?![A-Za-z0-9])",
            re.I,
        ),
    ),
    ("wire_number", re.compile(r"\bWIRE[-\s]?[A-Z]?\d{2,4}[A-Z]?\b", re.I)),
    (
        "equipment_tag",
        re.compile(
            rf"\b(?:WP|PMP|PUMP|VFD|MCC|PLC|HMI)-\d{{1,4}}[A-Z]?\b|"
            rf"\b(?:P|T|B|V|M|E|TK|HX|PMP)\d{{3,4}}[A-Z]?\b|"
            rf"\b(?:{PID_EQUIPMENT_PATTERN})\b",
            re.I,
        ),
    ),
    (
        "instrument_tag",
        re.compile(rf"\b[A-Z]{{2,4}}-\d{{1,4}}[A-Z]?\b|\b(?:{PID_INSTRUMENT_PATTERN})\b"),
    ),
    (
        "room_label",
        re.compile(
            r"\b(?:BEDROOM|BATH(?:ROOM)?|KITCHEN|DINING(?:[-\s]+ROOM)?|"
            r"LIVING(?:[-\s]+ROOM)?|LAUNDRY|PANTRY|CLOSET|GARAGE|PORCH|BASEMENT)\b",
            re.I,
        ),
    ),
    ("component_value", COMPONENT_VALUE_RE),
    (
        "tolerance_value",
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"(?:[±\u00B1]|\+/-)\s*(?:\d+(?:\.\d+)?|\.\d+)|"
            r"\+\s*(?:\d+(?:\.\d+)?|\.\d+)\s*/\s*-\s*(?:\d+(?:\.\d+)?|\.\d+)|"
            r"[A-Z]\d{1,2}\s*/\s*[a-z]\d{1,2}"
            r")(?![A-Za-z0-9])"
        ),
    ),
    (
        "dimension_value",
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"\d+(?:\s+\d+\s*/\s*\d+)?['’]\s*-\s*\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+)?[\"”]?|"
            r"(?:\d+(?:\.\d+)?|\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+)\s*"
            r"(?:mm|cm|m|in\.?|inch(?:es)?|ft\.?|foot|feet|deg|°|\u00B0|[\"”]|['’])"
            r")(?=\s|[,.;:)\]]|$)",
            re.I,
        ),
    ),
    ("gdandt_symbol", re.compile(r"(?:\bGD&T\b|[⌀Ø]\s*\d+(?:\.\d+)?)", re.I)),
    ("pin_label", SCHEMATIC_SIGNAL_RE),
    ("pin_label", PIN_LABEL_RE),
]
FULL_SPAN_CATEGORIES = {"process_label"}
MATCH_BBOX_PAD_PX = 8
EMBEDDED_MARKUP_RE = re.compile(r"<\s*/?\s*(?:svg|text|tspan)\b", re.I)
QUESTION_BY_CATEGORY = {
    "component_value": "What component value is shown in this region?",
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "gdandt_symbol": "What GD&T or tolerance symbol is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pin_label": "What pin or component label is shown in this marked region?",
    "pipe_line_tag": "What pipe or line tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "room_label": "What room label is shown in this region?",
    "tolerance_value": "What tolerance value is shown in this region?",
    "wire_number": "What wire number is shown in this region?",
}

PROCESS_LABEL_EXACT_EXCLUSIONS = {
    "and",
    "and/or",
    "approved by",
    "bottom",
    "date",
    "driver",
    "for",
    "function",
    "main",
    "others",
    "revision",
    "scale",
    "started",
    "symbols",
    "system",
    "test",
    "top",
    "type",
    "units",
}
PROCESS_LABEL_DOCUMENT_MARKERS = (
    "appendix ",
    "approved by",
    "categories of ",
    "chapter ",
    "city of ",
    "control philosophy",
    "design guideline",
    "drawing format",
    "drawn by",
    "electrical single line",
    "engineering fluid",
    "figure ",
    "instrument abbreviations",
    "instrumentation function",
    "introduction ",
    "not to scale",
    "page ",
    "p&id legend",
    "public works",
    "schematic",
    "specification",
    "standard drawing",
    "stormwater department",
    "views, and perspectives",
)
PROCESS_LABEL_PROSE_WORDS = {
    "assigned",
    "can",
    "craftsman",
    "included",
    "information",
    "illustrates",
    "must",
    "objectives",
    "opposed",
    "present",
    "provide",
    "several",
    "should",
    "specific",
    "systems",
    "the",
    "these",
    "this",
    "those",
}


def usable_process_label(text: str) -> bool:
    value = " ".join(text.strip().split())
    if not (2 <= len(value) <= 48) or not any(character.isalpha() for character in value):
        return False
    if len(value.split()) > 5 or value.endswith(("-", "/", ",", ":", ".", ";")):
        return False
    lowered = value.casefold()
    if any(marker in lowered for marker in ("http://", "https://", "www.", "@")):
        return False
    if lowered in PROCESS_LABEL_EXACT_EXCLUSIONS | {"unknown", "none", "n/a", "todo"}:
        return False
    if any(marker in lowered for marker in PROCESS_LABEL_DOCUMENT_MARKERS):
        return False
    if "(" in value or ")" in value:
        return False
    words = set(re.findall(r"[a-z]+", lowered))
    if words & PROCESS_LABEL_PROSE_WORDS:
        return False
    if re.fullmatch(r"(?:ft|gpm|psi|mm|cm|in|deg|[cf])", lowered):
        return False
    if re.fullmatch(r"[a-z]\s*=\s*[a-z]+", lowered):
        return False
    return True


def merge_process_label_spans(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    merged: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for index, row in enumerate(rows):
        if index in consumed:
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        current = text
        bbox = bbox_to_xyxy(row)
        parts = [text]
        next_index = index + 1
        while next_index < len(rows):
            following = rows[next_index]
            if int(following.get("page", 0)) != int(row.get("page", 0)):
                break
            following_text = str(following.get("text") or "").strip()
            following_bbox = bbox_to_xyxy(following)
            line_height = max(1, bbox[3] - bbox[1], following_bbox[3] - following_bbox[1])
            vertically_adjacent = -line_height <= following_bbox[1] - bbox[3] <= line_height
            horizontally_aligned = abs(following_bbox[0] - bbox[0]) <= max(60, line_height * 2)
            if not vertically_adjacent or not horizontally_aligned:
                break
            if current.endswith("-/") and following_text.endswith("-"):
                current += following_text
            elif current.endswith("-"):
                current = current[:-1] + following_text
            elif following_text.casefold().startswith("und "):
                current = f"{current} {following_text}"
            else:
                break
            bbox = [
                min(bbox[0], following_bbox[0]),
                min(bbox[1], following_bbox[1]),
                max(bbox[2], following_bbox[2]),
                max(bbox[3], following_bbox[3]),
            ]
            parts.append(following_text)
            consumed.update({index, next_index})
            next_index += 1
        if len(parts) > 1 and usable_process_label(current):
            combined = dict(row)
            combined["text"] = current
            combined["bbox_px"] = bbox
            combined["source_text_parts"] = parts
            merged.append(combined)
    return merged, consumed


def accepted_match(category: str, full_text: str, matched_text: str) -> bool:
    if category == "component_value":
        return matched_text.strip() == full_text.strip()

    if category == "equipment_tag":
        token = matched_text.upper()
        if re.fullmatch(r"(?:WP|PMP|PUMP|VFD|MCC|PLC|HMI)-\d{1,4}[A-Z]?", token):
            return True
        if re.fullmatch(r"(?:P|T|B|V|M|E|TK|HX|PMP)\d{3,4}[A-Z]?", token):
            return True
        text = full_text.strip().upper()
        return len(text) <= 40 and token == text and token in PID_EQUIPMENT_TERMS

    if category in {"pipe_line_tag", "process_value"}:
        return len(matched_text.strip()) <= 40

    if category == "instrument_tag":
        token = matched_text.upper()
        if token.split("-", 1)[0] in {"RGB"}:
            return False
        if re.fullmatch(r"(?:SD|SPS)-\d+[A-Z]?", token):
            return False
        if re.fullmatch(r"[A-Z]{3}-\d{2,4}", token):
            prefix = token.split("-", 1)[0]
            if prefix in MONTH_ABBREVIATIONS:
                return False
        if re.fullmatch(r"[A-Z]{2,4}-\d{1,4}[A-Z]?", token):
            return True
        text = full_text.strip().upper()
        return len(text) <= 24 and token == text and token in PID_INSTRUMENT_TOKENS

    if category == "room_label":
        text = full_text.strip()
        if len(text) > 32:
            return False
        normalized = text.strip(" \t\r\n.,;:()[]{}\"'")
        return matched_text.casefold() == normalized.casefold()

    if category == "dimension_value" and re.fullmatch(r"\d+(?:\.\d+)?M", matched_text):
        # Uppercase M without spacing is overwhelmingly a resistor value in
        # schematics. SI metre dimensions use lowercase m (usually with a gap).
        return False

    if category != "pin_label":
        return True

    # Pin labels are compact component references. Long address/title text can
    # contain tokens such as "L4V" that look like component IDs but are not.
    text = full_text.strip()
    if matched_text.isalpha() and matched_text != matched_text.upper():
        return False
    if matched_text == text and SCHEMATIC_SIGNAL_RE.fullmatch(text):
        if text in SCHEMATIC_SIGNAL_EXCLUSIONS or PART_NUMBER_SIGNAL_RE.fullmatch(text):
            return False
        return True
    if "/" in text:
        return False
    if len(text) > 64:
        return False
    matches = [match.group(0).upper() for match in PIN_LABEL_RE.finditer(text)]
    if len(matches) > 6:
        return False
    return matched_text.upper() in matches


def first_profile_match(text: str) -> tuple[str, str, int, int] | None:
    for category, pattern in PROFILES:
        for match in pattern.finditer(text):
            captured_value = match.groupdict().get("value")
            if captured_value is not None:
                matched_text = captured_value.strip()
                start, end = match.start("value"), match.end("value")
            else:
                matched_text = match.group(0).strip()
                start, end = match.start(), match.end()
            if accepted_match(category, text, matched_text):
                return category, matched_text, start, end
    return None


def all_profile_matches(text: str) -> list[tuple[str, str, int, int]]:
    accepted: list[tuple[str, str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for category, pattern in PROFILES:
        for match in pattern.finditer(text):
            captured_value = match.groupdict().get("value")
            if captured_value is not None:
                matched_text = captured_value.strip()
                start, end = match.start("value"), match.end("value")
            else:
                matched_text = match.group(0).strip()
                start, end = match.start(), match.end()
            if not accepted_match(category, text, matched_text):
                continue
            if any(start < prior_end and prior_start < end for prior_start, prior_end in occupied):
                continue
            accepted.append((category, matched_text, start, end))
            occupied.append((start, end))
    return sorted(accepted, key=lambda item: (item[2], item[3], item[0]))


def repair_common_mojibake(text: str) -> str:
    markers = ("Ã", "Â", "â€", "â€™", "â€œ", "â€�")

    markers = (*markers, "\u00c3", "\u00c2")

    def marker_score(value: str) -> int:
        return sum(value.count(marker) for marker in markers)

    if marker_score(text) == 0:
        return text
    candidates = [text]
    for encoding in ("latin-1", "cp1252"):
        try:
            candidates.append(text.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return min(candidates, key=lambda value: (marker_score(value), len(value)))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def bbox_to_xyxy(row: dict[str, Any]) -> list[int]:
    bbox = row.get("bbox_px") or row.get("bbox") or [0, 0, 0, 0]
    return [int(round(float(value))) for value in bbox]


def matched_bbox(
    row: dict[str, Any],
    start: int,
    end: int,
    text_override: str | None = None,
    pad_px: int = MATCH_BBOX_PAD_PX,
    full_span_pad_px: int = 0,
    full_span_pad_x_px: int | None = None,
    full_span_pad_y_px: int | None = None,
) -> list[int]:
    bbox = bbox_to_xyxy(row)
    text = text_override if text_override is not None else str(row.get("text") or "")
    if not text or start <= 0 and end >= len(text):
        pad_x = full_span_pad_px if full_span_pad_x_px is None else full_span_pad_x_px
        pad_y = full_span_pad_px if full_span_pad_y_px is None else full_span_pad_y_px
        if pad_x <= 0 and pad_y <= 0:
            return bbox
        x0, y0, x1, y1 = bbox
        return [
            max(0, x0 - max(0, pad_x)),
            max(0, y0 - max(0, pad_y)),
            x1 + max(0, pad_x),
            y1 + max(0, pad_y),
        ]
    if start < 0 or end <= start or end > len(text):
        return bbox
    x0, y0, x1, y1 = bbox
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    if width >= height:
        return [
            max(0, int(round(x0 + width * start / len(text))) - pad_px),
            y0,
            int(round(x0 + width * end / len(text))) + pad_px,
            y1,
        ]
    return [
        x0,
        max(0, int(round(y0 + height * start / len(text))) - pad_px),
        x1,
        int(round(y0 + height * end / len(text))) + pad_px,
    ]


def fingerprint_candidate_id(
    doc_id: str,
    version_id: str,
    page: int,
    category: str,
    target_text: str,
    bbox: list[int],
) -> str:
    payload = json.dumps(
        {
            "bbox": bbox,
            "category": category,
            "doc_id": doc_id,
            "page": page,
            "target_text": target_text,
            "version_id": version_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"mtcand__{doc_id}__{version_id}__p{page:04d}__fp_{digest}"


def mine_rows(
    rows: Iterable[dict[str, Any]],
    doc_id: str,
    version_id: str,
    candidate_id_mode: str = "sequential",
    include_all_profile_matches: bool = False,
    repair_mojibake: bool = False,
    match_bbox_pad_px: int = MATCH_BBOX_PAD_PX,
    full_span_bbox_pad_px: int = 0,
    full_span_bbox_pad_x_px: int | None = None,
    full_span_bbox_pad_y_px: int | None = None,
    full_span_categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, tuple[int, int, int, int]]] = set()
    source_rows = list(rows)
    merged_process_rows: list[dict[str, Any]] = []
    consumed_process_indexes: set[int] = set()
    if full_span_categories and "process_label" in full_span_categories:
        merged_process_rows, consumed_process_indexes = merge_process_label_spans(source_rows)
    for row_index, row in enumerate([*source_rows, *merged_process_rows]):
        source_text = str(row.get("text") or "").strip()
        text = repair_common_mojibake(source_text) if repair_mojibake else source_text
        if not text or EMBEDDED_MARKUP_RE.search(text):
            continue
        page = int(row.get("page", 0))
        if include_all_profile_matches:
            matches = all_profile_matches(text)
        else:
            first = first_profile_match(text)
            matches = [first] if first is not None else []
        original_process_span_consumed = row_index < len(source_rows) and row_index in consumed_process_indexes
        if (
            full_span_categories
            and "process_label" in full_span_categories
            and not original_process_span_consumed
            and usable_process_label(text)
        ):
            matches.append(("process_label", text, 0, len(text)))
        for category, matched_text, start, end in matches:
            pre_padding_bbox = matched_bbox(
                row,
                start,
                end,
                text_override=text,
                pad_px=max(0, match_bbox_pad_px),
            )
            bbox = matched_bbox(
                row,
                start,
                end,
                text_override=text,
                pad_px=max(0, match_bbox_pad_px),
                full_span_pad_px=max(0, full_span_bbox_pad_px),
                full_span_pad_x_px=full_span_bbox_pad_x_px,
                full_span_pad_y_px=full_span_bbox_pad_y_px,
            )
            bbox_key = tuple(bbox)
            key = (category, page, matched_text, bbox_key)
            if key in seen:
                continue
            seen.add(key)
            if candidate_id_mode == "fingerprint":
                candidate_id = fingerprint_candidate_id(
                    doc_id,
                    version_id,
                    page,
                    category,
                    matched_text,
                    bbox,
                )
            elif candidate_id_mode == "sequential":
                candidate_id = f"mtcand__{doc_id}__{version_id}__p{page:04d}__{len(candidates):06d}"
            else:
                raise ValueError(f"unsupported candidate_id_mode: {candidate_id_mode}")
            candidate = {
                "candidate_id": candidate_id,
                "doc_id": doc_id,
                "version_id": version_id,
                "page_index": page,
                "bbox": bbox,
                "target_text": matched_text,
                "raw_text": text,
                "category": category,
                "source": (
                    "textlayer_full_span_candidate"
                    if category in FULL_SPAN_CATEGORIES
                    else "textlayer_regex_candidate"
                ),
                "review_status": "candidate",
                "notes": "",
            }
            if candidate_id_mode == "fingerprint" and bbox != pre_padding_bbox:
                candidate["pre_padding_candidate_id"] = fingerprint_candidate_id(
                    doc_id,
                    version_id,
                    page,
                    category,
                    matched_text,
                    pre_padding_bbox,
                )
            if source_text != text:
                candidate["source_raw_text"] = source_text
            if row.get("source_text_parts"):
                candidate["source_text_parts"] = list(row["source_text_parts"])
            candidates.append(candidate)
    return candidates


def version_from_doc_id(doc_id: str) -> str:
    eagle_revision = re.search(r"_(\d+(?:_\d+)+)_eagle$", doc_id.lower())
    if eagle_revision:
        return eagle_revision.group(1).replace("_", ".")
    for token in (
        "v1.0",
        "v1.1",
        "v1.2",
        "v1.3",
        "iso",
        "sample",
        "cornell",
        "buttrich",
        "usda",
        "sandiego",
    ):
        if token in doc_id.lower():
            return token
    return "unknown"


def doc_id_from_textlayer(path: Path) -> str:
    return path.stem


def page_image_relpath(root: Path, doc_id: str, page_index: int, dpi: int = 300) -> str:
    base = root / "derived" / f"pages_{dpi}dpi" / doc_id
    for name in (
        f"page_{page_index:03d}.png",
        f"page_{page_index:04d}.png",
        f"p{page_index:04d}.png",
    ):
        path = base / name
        if path.exists():
            return path.relative_to(root).as_posix()
    return f"derived/pages_{dpi}dpi/{doc_id}/page_{page_index:03d}.png"


def scale_textlayer_rows_to_page_images(
    root: Path,
    doc_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    image_sizes: dict[int, tuple[int, int] | None] = {}
    scaled_rows: list[dict[str, Any]] = []
    for row in rows:
        updated = dict(row)
        page_index = int(updated.get("page", 0))
        if page_index not in image_sizes:
            image_path = root / page_image_relpath(root, doc_id, page_index)
            if image_path.is_file():
                with Image.open(image_path) as image:
                    image_sizes[page_index] = image.size
            else:
                image_sizes[page_index] = None
        target_size = image_sizes[page_index]
        source_width = int(updated.get("image_width_px") or 0)
        source_height = int(updated.get("image_height_px") or 0)
        bbox = updated.get("bbox_px")
        if (
            target_size
            and source_width > 0
            and source_height > 0
            and isinstance(bbox, list)
            and len(bbox) >= 4
            and target_size != (source_width, source_height)
        ):
            target_width, target_height = target_size
            scale_x = target_width / source_width
            scale_y = target_height / source_height
            updated["bbox_px"] = [
                float(bbox[0]) * scale_x,
                float(bbox[1]) * scale_y,
                float(bbox[2]) * scale_x,
                float(bbox[3]) * scale_y,
            ]
            updated["bbox_rescaled_from"] = [source_width, source_height]
            updated["image_width_px"] = target_width
            updated["image_height_px"] = target_height
            updated["bbox_coordinate_space"] = "rendered_image_px"
        scaled_rows.append(updated)
    return scaled_rows


def source_candidate_id_from_doc_id(doc_id: str) -> str:
    match = re.match(r"^([a-z]+_\d{3})", doc_id)
    return match.group(1) if match else ""


def manifest_source_candidate_ids(root: Path) -> dict[str, str]:
    path = root / "manifest.jsonl"
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for row in load_jsonl(path):
        if row.get("type") != "doc":
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        candidate_id = str(row.get("source_candidate_id") or "").strip()
        if doc_id and candidate_id:
            result[doc_id] = candidate_id
    return result


def normalize_version_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def manifest_version_ids(root: Path) -> dict[str, str]:
    path = root / "manifest.jsonl"
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for row in load_jsonl(path):
        if row.get("type") != "doc":
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        version = row.get("version")
        version_id = normalize_version_id(row.get("version_id"))
        if not version_id and isinstance(version, str):
            version_id = normalize_version_id(version)
        elif not version_id and isinstance(version, dict):
            for key in (
                "version_id",
                "revision",
                "rev",
                "board_revision",
                "release",
                "snapshot",
                "publication_year",
                "edition",
                "standard",
                "commons_sha1",
            ):
                version_id = normalize_version_id(version.get(key))
                if version_id:
                    break
        if doc_id and version_id:
            result[doc_id] = version_id
    return result


def enrich_for_review_pack(
    root: Path,
    candidate: dict[str, Any],
    manifest_candidate_id: str = "",
) -> dict[str, Any]:
    row = dict(candidate)
    category = str(row.get("category") or "")
    target_text = str(row.get("target_text") or "")
    doc_id = str(row.get("doc_id") or "")
    page_index = int(row.get("page_index", 0))
    row["review_status"] = "needs_review"
    row["proposed_text"] = target_text
    row["corrected_text"] = ""
    row["question_text"] = QUESTION_BY_CATEGORY.get(
        category,
        "What text is shown in this small engineering label region?",
    )
    row["image_path"] = page_image_relpath(root, doc_id, page_index)
    row["text_context"] = str(row.get("raw_text") or target_text)
    row["review_notes"] = ""
    row["source_candidate_id"] = manifest_candidate_id or source_candidate_id_from_doc_id(doc_id)
    return row


def mine_textlayers(
    root: Path,
    max_per_doc_category: int,
    doc_ids: set[str] | None = None,
    categories: set[str] | None = None,
    exact_span_only: bool = False,
    max_per_doc_page_category: int = 0,
    max_per_answer: int = 0,
    candidate_id_mode: str = "sequential",
    include_all_profile_matches: bool = False,
    repair_mojibake: bool = False,
    match_bbox_pad_px: int = MATCH_BBOX_PAD_PX,
    full_span_bbox_pad_px: int = 0,
    full_span_bbox_pad_x_px: int | None = None,
    full_span_bbox_pad_y_px: int | None = None,
) -> list[dict[str, Any]]:
    textlayer_dir = root / "derived" / "textlayer"
    source_candidate_ids = manifest_source_candidate_ids(root)
    manifest_versions = manifest_version_ids(root)
    all_candidates: list[dict[str, Any]] = []
    for path in sorted(textlayer_dir.glob("*.jsonl")):
        doc_id = doc_id_from_textlayer(path)
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        version_id = manifest_versions.get(doc_id) or version_from_doc_id(doc_id)
        rows = scale_textlayer_rows_to_page_images(root, doc_id, load_jsonl(path))
        mined = mine_rows(
            rows,
            doc_id=doc_id,
            version_id=version_id,
            candidate_id_mode=candidate_id_mode,
            include_all_profile_matches=include_all_profile_matches,
            repair_mojibake=repair_mojibake,
            match_bbox_pad_px=match_bbox_pad_px,
            full_span_bbox_pad_px=full_span_bbox_pad_px,
            full_span_bbox_pad_x_px=full_span_bbox_pad_x_px,
            full_span_bbox_pad_y_px=full_span_bbox_pad_y_px,
            full_span_categories=(categories or set()) & FULL_SPAN_CATEGORIES,
        )
        counts: Counter[str] = Counter()
        page_counts: Counter[tuple[int, str]] = Counter()
        answer_counts: Counter[tuple[str, str]] = Counter()
        for candidate in mined:
            category = candidate["category"]
            if categories is not None and category not in categories:
                continue
            if exact_span_only and str(candidate.get("raw_text") or "").strip() != str(
                candidate.get("target_text") or ""
            ).strip():
                continue
            if max_per_doc_category > 0 and counts[category] >= max_per_doc_category:
                continue
            page_key = (int(candidate.get("page_index", 0)), category)
            if max_per_doc_page_category > 0 and page_counts[page_key] >= max_per_doc_page_category:
                continue
            answer_key = (category, str(candidate.get("target_text") or "").strip().upper())
            if max_per_answer > 0 and answer_counts[answer_key] >= max_per_answer:
                continue
            counts[category] += 1
            page_counts[page_key] += 1
            answer_counts[answer_key] += 1
            all_candidates.append(
                enrich_for_review_pack(
                    root,
                    candidate,
                    manifest_candidate_id=source_candidate_ids.get(doc_id, ""),
                )
            )
    return all_candidates


def parse_csv_filter(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mine microtext expansion candidates")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="microtext/annotations/microtext_candidates.jsonl")
    parser.add_argument(
        "--max-per-doc-category",
        type=int,
        default=250,
        help="Optional cap per document and category; zero disables the cap.",
    )
    parser.add_argument("--doc-ids", help="Comma-separated doc_id filter")
    parser.add_argument("--categories", help="Comma-separated category filter")
    parser.add_argument(
        "--exact-span-only",
        action="store_true",
        help="Keep only candidates whose textlayer span exactly equals the proposed answer.",
    )
    parser.add_argument(
        "--max-per-doc-page-category",
        type=int,
        default=0,
        help="Optional cap per document, page, and category; zero disables the cap.",
    )
    parser.add_argument(
        "--max-per-answer",
        type=int,
        default=0,
        help="Optional per-document cap for repeated normalized answers; zero disables the cap.",
    )
    parser.add_argument(
        "--candidate-id-mode",
        choices=("sequential", "fingerprint"),
        default="sequential",
        help=(
            "Candidate ID strategy. Use fingerprint for new durable queues so IDs remain stable "
            "when mining rules or unrelated rows change."
        ),
    )
    parser.add_argument(
        "--all-profile-matches",
        action="store_true",
        help="Emit every non-overlapping accepted profile match from each text span.",
    )
    parser.add_argument(
        "--repair-mojibake",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Repair common UTF-8-as-Latin-1 mojibake before matching and proposing text "
            "(default: enabled; use --no-repair-mojibake only for forensic reproduction)."
        ),
    )
    parser.add_argument(
        "--match-bbox-pad-px",
        type=int,
        default=MATCH_BBOX_PAD_PX,
        help="Padding around proportional substring boxes; raise for loose or uneven glyph spacing.",
    )
    parser.add_argument(
        "--full-span-bbox-pad-px",
        type=int,
        default=0,
        help="Opt-in padding on every side of exact/full-span boxes; zero preserves legacy crops.",
    )
    parser.add_argument(
        "--full-span-bbox-pad-x-px",
        type=int,
        help="Optional horizontal full-span padding; overrides --full-span-bbox-pad-px on x.",
    )
    parser.add_argument(
        "--full-span-bbox-pad-y-px",
        type=int,
        help="Optional vertical full-span padding; overrides --full-span-bbox-pad-px on y.",
    )
    args = parser.parse_args()

    doc_filter = parse_csv_filter(args.doc_ids)
    category_filter = parse_csv_filter(args.categories)
    if category_filter and category_filter & FULL_SPAN_CATEGORIES and not doc_filter:
        parser.error("full-span categories require an explicit --doc-ids filter")

    candidates = mine_textlayers(
        Path(args.root),
        args.max_per_doc_category,
        doc_ids=doc_filter,
        categories=category_filter,
        exact_span_only=args.exact_span_only,
        max_per_doc_page_category=args.max_per_doc_page_category,
        max_per_answer=args.max_per_answer,
        candidate_id_mode=args.candidate_id_mode,
        include_all_profile_matches=args.all_profile_matches,
        repair_mojibake=args.repair_mojibake,
        match_bbox_pad_px=max(0, args.match_bbox_pad_px),
        full_span_bbox_pad_px=max(0, args.full_span_bbox_pad_px),
        full_span_bbox_pad_x_px=(
            max(0, args.full_span_bbox_pad_x_px)
            if args.full_span_bbox_pad_x_px is not None
            else None
        ),
        full_span_bbox_pad_y_px=(
            max(0, args.full_span_bbox_pad_y_px)
            if args.full_span_bbox_pad_y_px is not None
            else None
        ),
    )
    write_jsonl(Path(args.output), candidates)

    by_category = Counter(row["category"] for row in candidates)
    by_doc = Counter(row["doc_id"] for row in candidates)
    print(f"[OK] Wrote {len(candidates)} candidates to {args.output}")
    print(f"by_category: {dict(sorted(by_category.items()))}")
    print(f"docs: {len(by_doc)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
