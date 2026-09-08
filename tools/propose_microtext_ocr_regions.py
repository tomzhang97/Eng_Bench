#!/usr/bin/env python3
"""Propose review-only microtext regions with an optional RapidOCR runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


EQUIPMENT_TERMS = (
    "air pump",
    "alternator",
    "blower",
    "boiler",
    "compressor",
    "condenser",
    "dynamo",
    "engine",
    "evaporator",
    "fan",
    "filter",
    "furnace",
    "generator",
    "governor",
    "heater",
    "hoist",
    "motor",
    "pump",
    "reactor",
    "reboiler",
    "reservoir",
    "separator",
    "switchboard",
    "tank",
    "transformer",
    "turbine",
    "valve",
    "vessel",
    "winch",
)
ROOM_TERMS = (
    "bay",
    "chamber",
    "deck",
    "engine room",
    "gate house",
    "gallery",
    "machine room",
    "mill room",
    "pump room",
    "room",
    "turbine room",
)
ARCHITECTURAL_ROOM_LABELS = {
    "ATTIC",
    "BATH",
    "BATH ROOM",
    "BED ROOM",
    "BEDROOM",
    "BREAKFAST ROOM",
    "CELLAR",
    "CLOSET",
    "CLOS",
    "COAL BIN",
    "DINING RM",
    "DINING ROOM",
    "DRESSING ROOM",
    "ENTRY",
    "FOYER",
    "GARAGE",
    "HALL",
    "KITCHEN",
    "LAUNDRY",
    "LIBRARY",
    "LINEN CLOS",
    "LIVING RM",
    "LIVING ROOM",
    "NURSERY",
    "OFFICE",
    "PANTRY",
    "PORCH",
    "RECEPTION ROOM",
    "SERVANT'S ROOM",
    "SERVANTS ROOM",
    "STORE ROOM",
    "SUN ROOM",
    "TOILET",
    "VESTIBULE",
}
STRUCTURAL_TERMS = (
    "bearing",
    "beam",
    "brace",
    "chord",
    "column",
    "diagonal",
    "girder",
    "post",
    "rafter",
    "stiffener",
    "strut",
    "truss",
)
INSTRUMENT_PREFIXES = {
    "AI", "AIT", "AL", "AO", "AT", "CAH", "CS", "DPS", "FAH", "FAL",
    "FE", "FI", "FIC", "FIT", "FO", "FQ", "FR", "FS", "FT", "IT",
    "LAH", "LAL", "LIC", "LIT", "LS", "LSH", "LSL", "LT", "ON", "PAH",
    "PAL", "PCV", "PI", "PIC", "PIT", "PS", "PSV", "PT", "QC", "SOV",
    "TIC", "TIT", "TT",
}
PID_INSTRUMENT_PREFIXES = INSTRUMENT_PREFIXES | {
    "HIC", "HMS", "HMY", "HS", "HV", "LAHH", "LC", "LV", "LY", "TC", "TV",
}
EQUIPMENT_PREFIXES = {"C", "E", "F", "M", "P", "R", "T", "TK", "V"}
COMPONENT_PREFIXES = {
    "C", "D", "FB", "J", "JP", "L", "P", "Q", "R", "RN", "SW", "T",
    "TP", "U", "Y",
}
PCB_PIN_SIGNALS = {
    "3V3",
    "3V3(OUT)",
    "3V3_EN",
    "ADC_VREF",
    "AGND",
    "BOOTSEL",
    "GND",
    "RUN",
    "SWCLK",
    "SWDIO",
    "VBUS",
    "VSYS",
}
TAG_RE = re.compile(r"^([A-Z]{1,4})[- ]?(\d{1,4}[A-Z]?)$")
STACKED_PID_NUMBER_RE = re.compile(r"^\d{1,3}[A-Z]?$", re.IGNORECASE)
PROCESS_VALUE_RE = re.compile(
    r"^(?:"
    r"(?:SETPOINT\s+)?(?:GPM|GPH|PSI|PSIA|PSID|PSIG|MGD|PPM|MG/L|KG/H|M3/H|G/S|SLPM|BAR|KPA|MPA)"
    r"\s*[:=]?\s*(?:ATM|\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?)"
    r"(?:\s+(?:MIN|MAX|REQ|AVAIL))?|"
    r"(?:SETPOINT\s+)?(?:ATM|\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?)"
    r"\s*(?:GPM|GPH|PSI|PSIA|PSID|PSIG|MGD|PPM|MG/L|KG/H|M3/H|G/S|SLPM|BAR|KPA|MPA)|"
    r"\d[\d,]*(?:\.\d+)?\s+TO\s+\d[\d,]*(?:\.\d+)?\s*"
    r"(?:PSI|PSIA|PSID|PSIG|G/S|SLPM|%)|"
    r"(?:\d+(?:\.\d+)?)\s*(?:DEG\.?\s*[FC]|°\s*[FC]|[℃℉]|MA|VDC)"
    r")$",
    re.IGNORECASE,
)
TOLERANCE_NUMBER_PATTERN = r"(?:\d+(?:\.\d+)?|\.\d+)"
TOLERANCE_VALUE_RE = re.compile(
    rf"^(?:"
    rf"(?:±|\+/-)\s*{TOLERANCE_NUMBER_PATTERN}|"
    rf"\+\s*{TOLERANCE_NUMBER_PATTERN}\s*/\s*[-−]\s*{TOLERANCE_NUMBER_PATTERN}|"
    rf"\+\s*{TOLERANCE_NUMBER_PATTERN}\s+[-−]\s*{TOLERANCE_NUMBER_PATTERN}|"
    rf"{TOLERANCE_NUMBER_PATTERN}\s+\+\s*{TOLERANCE_NUMBER_PATTERN}"
    rf"\s*(?:/|\s)\s*[-−]\s*{TOLERANCE_NUMBER_PATTERN}|"
    rf"[A-Z]\d{{1,2}}\s*/\s*[a-z]\d{{1,2}}"
    rf")(?:\s*(?:MM|CM|IN\.?|INCH(?:ES)?))?$",
    re.IGNORECASE,
)
PERCENT_LIMIT_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*%\s*(?:MIN(?:IMUM)?|MAX(?:IMUM)?)(?:\.)?$",
    re.IGNORECASE,
)
PLAIN_PERCENT_RE = re.compile(r"^\d+(?:\.\d+)?\s*%$", re.IGNORECASE)
HVAC_CONTROL_TAG_RE = re.compile(r"^[A-Z]{1,8}(?:-[A-Z0-9]{1,8}){1,4}$")
HVAC_EQUIPMENT_TAG_RE = re.compile(r"^(?:D|V|MS)-\d{1,3}$")
HVAC_PIPE_TAGS = {
    "CWR",
    "CWS",
    "DTWR",
    "DTWS",
    "HTHWR",
    "HTHWS",
    "HWR",
    "HWS",
}
HVAC_CONTROL_EXCLUSIONS = {
    "ALL-AIR",
    "CLOSE-OFF",
    "DUCT-MOUNTED",
    "DUAL-TEMP",
    "GAS-FIRED",
    "H-O-A",
    "M-F",
    "NON-CONSTANT",
    "TWO-POSITION",
    "WARM-UP",
    "W-X-Y-Z",
}
DIMENSION_RE = re.compile(
    r"^(?:Ø\s*)?[0-9]+(?:[./-][0-9]+)*(?:\s*(?:IN\.?|INCH(?:ES)?|MM|CM|M|FT|FEET|°|DEG))$",
    re.IGNORECASE,
)
FEET_INCH_RE = re.compile(r"^[0-9]+\s*['′]\s*-?\s*[0-9]+(?:\s*[0-9]+/[0-9]+)?\s*[\"″]$")
SINGLE_IMPERIAL_UNIT_RE = re.compile(
    r"^(?:[Ø⌀]\s*)?(?:[0-9]+(?:[- ]+[0-9]+/[0-9]+)?|[0-9]+/[0-9]+)"
    r"\s*(?:['′’]|[\"″”])(?:\s*(?:TYP\.?|O\.C\.?))?$",
    re.IGNORECASE,
)
ANNOTATED_IMPERIAL_DIMENSION_RE = re.compile(
    r"^(?:@\s*|#\d+\s*@\s*|\d+\s*-\s*#\d+\s*@\s*)?"
    r"[0-9]+(?:\.[0-9]+)?\s*(?:['′’]|[\"″”])\s*"
    r"(?:MIN|MAX|COVER|(?:O|0)\.?\s*C\.?)$",
    re.IGNORECASE,
)
UNITLESS_DECIMAL_DIMENSION_RE = re.compile(
    r"^(?:R\s*)?[0-9]+\.[0-9]+(?:\s*\(\s*TYP\.?\s*\))?$",
    re.IGNORECASE,
)
UNITLESS_INTEGER_DIMENSION_RE = re.compile(
    r"^(?:[Ø⌀R]\s*)?[1-9][0-9]{0,2}$",
    re.IGNORECASE,
)
PCB_PIN_SIGNAL_RE = re.compile(
    r"^(?:"
    r"GP(?:IO)?[0-9]{1,2}|ADC[0-9]{1,2}|PIN[0-9]{1,2}|"
    r"UART[0-9]+\s+(?:TX|RX)|"
    r"I2C[0-9]+\s+(?:SDA|SCL)|"
    r"SPI[0-9]+\s+(?:RX|TX|SCK|CSN)"
    r")$",
    re.IGNORECASE,
)
PID_LINE_DESIGNATOR_RE = re.compile(
    r'^[A-Z]{1,5}\d{2,6}-\d+(?:\.\d+)?(?:"|IN)?-'
    r'[A-Z0-9]{3,8}(?:-[A-Z0-9]{1,8}){1,3}$'
)
PID_NUMBER_LEADING_LINE_DESIGNATOR_RE = re.compile(
    r"^\d{1,3}-(?:[A-Z]{2,6}-\d{2,6}|\d{2,6}-[A-Z]{2,6})"
    r"(?:-[A-Z0-9]{1,10}){1,8}$"
)
# Historic P&IDs often encode line number, nominal size, and service in three fields.
# A terminal zero is retained as an OCR ambiguity for visual review; it is not
# silently normalized to the visually similar service code O.
PID_COMPACT_LINE_DESIGNATOR_RE = re.compile(
    r"^\d{1,3}-(?:\d{1,2}(?:/\d{1,2})?|[YV][248])-[A-Z0]$"
)
PID_EQUIPMENT_TRIM_DESIGNATOR_RE = re.compile(
    r"^[A-Z]{1,4}-\d{2}[A-Z]\d{2,4}-[A-Z0-9]{3,8}"
    r"(?:-[A-Z0-9]{1,8}){1,2}$"
)
PID_EQUIPMENT_LABEL_TERMS = (
    "cleanout",
    "diversion structure",
    "flow sensor",
    "lower explosive limit sensor",
    "manhole",
    "overflow sensor",
    "trash gate",
    "trash rake",
    "vault",
    "weir",
)
PID_PIPE_LABEL_ENDINGS = {"drain", "pipe", "sewer"}
PID_DESIGNATOR_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u201c": '"',
        "\u201d": '"',
        "\u2033": '"',
    }
)
LEADING_FRAGMENT_WORDS = {
    "and", "at", "by", "deliver", "for", "from", "in", "of", "shown", "this", "to", "with",
}
TRAILING_FRAGMENT_WORDS = {"and", "for", "from", "of", "or", "to", "with"}
NARRATIVE_WORDS = {
    "all", "appendix", "auto", "contains", "control", "data", "deliver", "dictate",
    "dynamic", "head", "manual", "maximum", "minimum", "mode", "more", "operating",
    "operation", "overload", "philosophy", "rating", "recir", "regarding", "revolves",
    "rotations", "select", "selected", "sequence", "setpoint", "sheet", "shoul", "shown",
    "shutoff", "speed", "start", "status", "stop", "than", "this", "total", "typ",
    "type", "typical", "used", "will", "with",
}


def clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.strip("|_~`.,;:")


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).casefold())


def normalize_pid_designator(value: Any) -> str:
    text = clean_text(value).upper().translate(PID_DESIGNATOR_TRANSLATION)
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r'\s*"\s*', '"', text)


def classify_engineering_text(
    value: Any,
    *,
    include_unitless_dimensions: bool = False,
    include_unitless_integer_dimensions: bool = False,
    include_pcb_pin_signals: bool = False,
    include_architectural_room_labels: bool = False,
    include_pid_labels: bool = False,
    include_hvac_control_labels: bool = False,
    include_civil_slope_values: bool = False,
) -> str:
    text = clean_text(value)
    short_integer_dimension = (
        include_unitless_integer_dimensions
        and UNITLESS_INTEGER_DIMENSION_RE.fullmatch(text) is not None
    )
    if (
        (len(text) < 2 and not short_integer_dimension)
        or len(text) > 80
        or not re.search(r"[A-Za-z0-9]", text)
    ):
        return ""
    if include_pid_labels and PID_NUMBER_LEADING_LINE_DESIGNATOR_RE.fullmatch(
        normalize_pid_designator(text)
    ):
        return "pipe_line_tag"
    if include_pid_labels and PID_COMPACT_LINE_DESIGNATOR_RE.fullmatch(
        normalize_pid_designator(text)
    ):
        return "pipe_line_tag"
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words or len(words) > 8:
        return ""
    if (
        TOLERANCE_VALUE_RE.fullmatch(text)
        or PERCENT_LIMIT_RE.fullmatch(text)
        or (include_civil_slope_values and PLAIN_PERCENT_RE.fullmatch(text))
    ):
        return "tolerance_value"
    if (
        DIMENSION_RE.fullmatch(text)
        or FEET_INCH_RE.fullmatch(text)
        or SINGLE_IMPERIAL_UNIT_RE.fullmatch(text)
        or ANNOTATED_IMPERIAL_DIMENSION_RE.fullmatch(text)
    ):
        return "dimension_value"
    if include_unitless_dimensions and UNITLESS_DECIMAL_DIMENSION_RE.fullmatch(text):
        return "dimension_value"
    if (
        include_unitless_integer_dimensions
        and UNITLESS_INTEGER_DIMENSION_RE.fullmatch(text)
    ):
        return "dimension_value"
    upper = text.upper()
    if include_architectural_room_labels and upper in ARCHITECTURAL_ROOM_LABELS:
        return "room_label"
    if include_pcb_pin_signals and (
        upper in PCB_PIN_SIGNALS or PCB_PIN_SIGNAL_RE.fullmatch(upper)
    ):
        return "pin_label"
    if PROCESS_VALUE_RE.fullmatch(text):
        return "process_value"
    if include_hvac_control_labels:
        if upper in HVAC_PIPE_TAGS:
            return "pipe_line_tag"
        if HVAC_EQUIPMENT_TAG_RE.fullmatch(upper):
            return "equipment_tag"
        if (
            HVAC_CONTROL_TAG_RE.fullmatch(upper)
            and upper not in HVAC_CONTROL_EXCLUSIONS
            and "XX" not in upper
            and not re.fullmatch(r"M-\d{3,4}[A-Z0-9]*", upper)
        ):
            return "instrument_tag"
    if include_pid_labels:
        pid_designator = normalize_pid_designator(text)
        if (
            PID_LINE_DESIGNATOR_RE.fullmatch(pid_designator)
            or PID_NUMBER_LEADING_LINE_DESIGNATOR_RE.fullmatch(pid_designator)
            or PID_COMPACT_LINE_DESIGNATOR_RE.fullmatch(pid_designator)
        ):
            return "pipe_line_tag"
        if PID_EQUIPMENT_TRIM_DESIGNATOR_RE.fullmatch(pid_designator):
            return "equipment_tag"
    lowered_words = [word.casefold() for word in words]
    if (
        lowered_words[0] in LEADING_FRAGMENT_WORDS
        or lowered_words[-1] in TRAILING_FRAGMENT_WORDS
        or any(word in NARRATIVE_WORDS for word in lowered_words)
        or re.match(r"^\s*\d+(?:[.)]\s*|\s+|(?=[A-Za-z]))[A-Za-z]", text)
        or (len(words[0]) <= 2 and words[0].islower())
        or text.rstrip().endswith("-")
    ):
        return ""
    lowered = text.casefold()
    if include_pid_labels:
        if any(contains_term(lowered, term) for term in PID_EQUIPMENT_LABEL_TERMS):
            return "equipment_tag"
        if lowered_words[-1] in PID_PIPE_LABEL_ENDINGS:
            return "pipe_line_tag"
    tag = TAG_RE.fullmatch(upper)
    if tag:
        prefix = tag.group(1)
        if prefix in INSTRUMENT_PREFIXES or (
            include_pid_labels and prefix in PID_INSTRUMENT_PREFIXES
        ):
            return "instrument_tag"
        if not re.search(r"[- ]", upper) and prefix in COMPONENT_PREFIXES:
            return "pin_label"
        if prefix in EQUIPMENT_PREFIXES:
            return "equipment_tag"
    if lowered.endswith("pump station") and not re.search(r"\d", text):
        return ""
    if lowered_words[-1] in {"bay", "chamber", "deck", "gallery", "house", "room"}:
        return "room_label"
    if any(contains_term(lowered, term) for term in ROOM_TERMS):
        return ""
    if "to" in lowered_words or "from" in lowered_words:
        return ""
    if any(contains_term(lowered, term) for term in EQUIPMENT_TERMS):
        return "equipment_tag"
    if any(contains_term(lowered, term) for term in STRUCTURAL_TERMS):
        return "pin_label"
    return ""


def contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term.casefold()).replace(r"\ ", r"\s+")
    suffix = r"(?:s|es)?" if " " not in term else ""
    return re.search(rf"\b{escaped}{suffix}\b", text.casefold()) is not None


def tile_starts(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= 0 or tile_size <= 0:
        raise ValueError("length and tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be non-negative and smaller than tile_size")
    if length <= tile_size:
        return [0]
    step = tile_size - overlap
    starts = list(range(0, max(1, length - tile_size + 1), step))
    final = length - tile_size
    if starts[-1] != final:
        starts.append(final)
    return starts


def polygon_bbox(
    polygon: Any,
    *,
    offset_x: int,
    offset_y: int,
    padding: int,
    image_width: int,
    image_height: int,
) -> list[int]:
    points = list(polygon) if polygon is not None else []
    if not points:
        raise ValueError("OCR polygon is empty")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    x1 = max(0, math.floor(min(xs)) + offset_x - padding)
    y1 = max(0, math.floor(min(ys)) + offset_y - padding)
    x2 = min(image_width, math.ceil(max(xs)) + offset_x + padding)
    y2 = min(image_height, math.ceil(max(ys)) + offset_y + padding)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("OCR polygon produced an empty bbox")
    return [x1, y1, x2, y2]


def join_stacked_pid_instruments(
    detections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join vertically stacked P&ID balloon codes such as PI over 8."""
    prefixes = [
        (index, row)
        for index, row in enumerate(detections)
        if str(row.get("text") or "").upper() in PID_INSTRUMENT_PREFIXES
    ]
    numbers = [
        (index, row)
        for index, row in enumerate(detections)
        if STACKED_PID_NUMBER_RE.fullmatch(str(row.get("text") or ""))
    ]
    possible: list[tuple[float, int, int, dict[str, Any], dict[str, Any]]] = []
    for prefix_index, prefix in prefixes:
        px1, py1, px2, py2 = prefix["bbox"]
        prefix_width = px2 - px1
        prefix_height = py2 - py1
        prefix_center = (px1 + px2) / 2.0
        for number_index, number in numbers:
            nx1, ny1, nx2, ny2 = number["bbox"]
            number_width = nx2 - nx1
            number_height = ny2 - ny1
            vertical_gap = ny1 - py2
            if vertical_gap < -0.25 * prefix_height:
                continue
            if vertical_gap > 1.75 * max(prefix_height, number_height) + 12:
                continue
            number_center = (nx1 + nx2) / 2.0
            center_delta = abs(prefix_center - number_center)
            if center_delta > max(prefix_width, number_width, prefix_height * 1.5):
                continue
            distance = vertical_gap + center_delta
            possible.append((distance, prefix_index, number_index, prefix, number))

    joined: list[dict[str, Any]] = []
    used_prefixes: set[int] = set()
    used_numbers: set[int] = set()
    for _, prefix_index, number_index, prefix, number in sorted(possible, key=lambda row: row[0]):
        if prefix_index in used_prefixes or number_index in used_numbers:
            continue
        used_prefixes.add(prefix_index)
        used_numbers.add(number_index)
        px1, py1, px2, py2 = prefix["bbox"]
        nx1, ny1, nx2, ny2 = number["bbox"]
        joined.append(
            {
                "text": f"{str(prefix['text']).upper()} {str(number['text']).upper()}",
                "bbox": [min(px1, nx1), min(py1, ny1), max(px2, nx2), max(py2, ny2)],
                "score": min(float(prefix["score"]), float(number["score"])),
            }
        )
    return joined


def bbox_iou(left: list[int], right: list[int]) -> float:
    ix1 = max(left[0], right[0])
    iy1 = max(left[1], right[1])
    ix2 = min(left[2], right[2])
    iy2 = min(left[3], right[3])
    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if intersection == 0:
        return 0.0
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    return intersection / max(1, left_area + right_area - intersection)


def candidate_id(row: dict[str, Any]) -> str:
    material = "|".join(
        [
            str(row.get("doc_id") or ""),
            str(row.get("version_id") or ""),
            str(row.get("page_index", 0)),
            ",".join(str(value) for value in row.get("bbox", [])),
            normalize_text(row.get("proposed_text")),
        ]
    )
    return "ocrcand__" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def deduplicate_rows(rows: list[dict[str, Any]], iou_threshold: float = 0.5) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get("ocr_confidence", 0.0)),
            str(row.get("doc_id") or ""),
            int(row.get("page_index", 0)),
            row.get("bbox", [0, 0, 0, 0]),
        ),
    )
    selected: list[dict[str, Any]] = []
    for row in ordered:
        duplicate = False
        for existing in selected:
            if row.get("doc_id") != existing.get("doc_id"):
                continue
            if int(row.get("page_index", 0)) != int(existing.get("page_index", 0)):
                continue
            if normalize_text(row.get("proposed_text")) != normalize_text(existing.get("proposed_text")):
                continue
            if bbox_iou(row["bbox"], existing["bbox"]) >= iou_threshold:
                duplicate = True
                break
        if not duplicate:
            selected.append(row)
    selected.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index", 0)),
            row["bbox"][1],
            row["bbox"][0],
            normalize_text(row.get("proposed_text")),
        )
    )
    return selected


def load_rapidocr() -> tuple[Any, Any]:
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "RapidOCR runtime unavailable. Install requirements-ocr.txt in an isolated Python 3.12 environment."
        ) from exc
    return RapidOCR(), np


def restore_rotated_bbox(
    bbox: list[int],
    *,
    original_width: int,
    original_height: int,
    rotate_cw_degrees: int,
) -> list[int]:
    x1, y1, x2, y2 = bbox
    if rotate_cw_degrees == 0:
        return bbox
    if rotate_cw_degrees == 90:
        return [y1, original_height - x2, y2, original_height - x1]
    if rotate_cw_degrees == 180:
        return [
            original_width - x2,
            original_height - y2,
            original_width - x1,
            original_height - y1,
        ]
    if rotate_cw_degrees == 270:
        return [original_width - y2, x1, original_width - y1, x2]
    raise ValueError("rotate_cw_degrees must be one of 0, 90, 180, or 270")


def restore_analysis_polygon(
    polygon: Any,
    *,
    scale_x: float,
    scale_y: float,
) -> list[list[float]]:
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("analysis scales must be positive")
    return [
        [float(point[0]) / scale_x, float(point[1]) / scale_y]
        for point in polygon
    ]


def propose_page_rows(
    *,
    root: Path,
    image_path: Path,
    doc_id: str,
    version_id: str,
    page_index: int,
    engine: Any,
    np_module: Any,
    tile_size: int,
    overlap: int,
    min_confidence: float,
    padding: int,
    limit_per_page: int,
    max_image_pixels: int,
    ocr_scale: float = 1.0,
    include_unitless_dimensions: bool = False,
    include_unitless_integer_dimensions: bool = False,
    include_pcb_pin_signals: bool = False,
    include_architectural_room_labels: bool = False,
    include_pid_labels: bool = False,
    include_hvac_control_labels: bool = False,
    include_civil_slope_values: bool = False,
    include_unclassified: bool = False,
    rotate_cw_degrees: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if rotate_cw_degrees not in {0, 90, 180, 270}:
        raise ValueError("rotate_cw_degrees must be one of 0, 90, 180, or 270")
    if not 0 < ocr_scale <= 4:
        raise ValueError("ocr_scale must be greater than 0 and at most 4")
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        with Image.open(image_path) as source:
            source = source.convert("RGB")
            original_width, original_height = source.size
            if rotate_cw_degrees:
                source = source.rotate(-rotate_cw_degrees, expand=True)
            width, height = source.size
            x_starts = tile_starts(width, tile_size, overlap)
            y_starts = tile_starts(height, tile_size, overlap)
            rows: list[dict[str, Any]] = []
            detections = 0
            for y in y_starts:
                for x in x_starts:
                    x2 = min(width, x + tile_size)
                    y2 = min(height, y + tile_size)
                    tile = source.crop((x, y, x2, y2))
                    analysis_tile = tile
                    if ocr_scale != 1:
                        analysis_size = (
                            max(1, round(tile.width * ocr_scale)),
                            max(1, round(tile.height * ocr_scale)),
                        )
                        analysis_tile = tile.resize(analysis_size, Image.Resampling.LANCZOS)
                    scale_x = analysis_tile.width / tile.width
                    scale_y = analysis_tile.height / tile.height
                    result = engine(np_module.asarray(analysis_tile))
                    raw_boxes = getattr(result, "boxes", None)
                    raw_texts = getattr(result, "txts", None)
                    raw_scores = getattr(result, "scores", None)
                    boxes = list(raw_boxes) if raw_boxes is not None else []
                    texts = list(raw_texts) if raw_texts is not None else []
                    scores = list(raw_scores) if raw_scores is not None else []
                    detections += len(texts)
                    tile_detections: list[dict[str, Any]] = []
                    for polygon, raw_text, raw_score in zip(boxes, texts, scores):
                        score = float(raw_score)
                        if score < min_confidence:
                            continue
                        text = clean_text(raw_text)
                        try:
                            source_polygon = restore_analysis_polygon(
                                polygon,
                                scale_x=scale_x,
                                scale_y=scale_y,
                            )
                            unpadded_bbox = polygon_bbox(
                                source_polygon,
                                offset_x=x,
                                offset_y=y,
                                padding=0,
                                image_width=width,
                                image_height=height,
                            )
                        except (TypeError, ValueError):
                            continue
                        tile_detections.append(
                            {"text": text, "score": score, "bbox": unpadded_bbox}
                        )
                        category = classify_engineering_text(
                            text,
                            include_unitless_dimensions=include_unitless_dimensions,
                            include_unitless_integer_dimensions=(
                                include_unitless_integer_dimensions
                            ),
                            include_pcb_pin_signals=include_pcb_pin_signals,
                            include_architectural_room_labels=include_architectural_room_labels,
                            include_pid_labels=include_pid_labels,
                            include_hvac_control_labels=include_hvac_control_labels,
                            include_civil_slope_values=include_civil_slope_values,
                        )
                        if (
                            not category
                            and include_unclassified
                            and 2 <= len(text) <= 80
                            and re.search(r"[A-Za-z0-9]", text)
                        ):
                            category = "unknown_microtext"
                        if not category:
                            continue
                        try:
                            rotated_bbox = polygon_bbox(
                                source_polygon,
                                offset_x=x,
                                offset_y=y,
                                padding=padding,
                                image_width=width,
                                image_height=height,
                            )
                            bbox = restore_rotated_bbox(
                                rotated_bbox,
                                original_width=original_width,
                                original_height=original_height,
                                rotate_cw_degrees=rotate_cw_degrees,
                            )
                        except (TypeError, ValueError):
                            continue
                        row = {
                            "doc_id": doc_id,
                            "version_id": version_id,
                            "page_index": page_index,
                            "bbox": bbox,
                            "target_text": "",
                            "proposed_text": text,
                            "category": category,
                            "source": "rapidocr_region_proposal",
                            "review_status": "needs_review",
                            "corrected_text": "",
                            "question_text": "What text is shown in this small engineering label region?",
                            "image_path": image_path.relative_to(root).as_posix(),
                            "text_context": "",
                            "ocr_confidence": round(score, 5),
                            "review_notes": (
                                f"RapidOCR candidate only; confidence={score:.5f}; "
                                f"tile=[{x},{y},{x2},{y2}]; ocr_scale={ocr_scale}; "
                                f"rotate_cw_degrees={rotate_cw_degrees}; "
                                "requires human verification"
                            ),
                        }
                        row["candidate_id"] = candidate_id(row)
                        rows.append(row)
                    if include_pid_labels:
                        for joined in join_stacked_pid_instruments(tile_detections):
                            jx1, jy1, jx2, jy2 = joined["bbox"]
                            rotated_bbox = [
                                max(0, jx1 - padding),
                                max(0, jy1 - padding),
                                min(width, jx2 + padding),
                                min(height, jy2 + padding),
                            ]
                            bbox = restore_rotated_bbox(
                                rotated_bbox,
                                original_width=original_width,
                                original_height=original_height,
                                rotate_cw_degrees=rotate_cw_degrees,
                            )
                            score = float(joined["score"])
                            text = str(joined["text"])
                            row = {
                                "doc_id": doc_id,
                                "version_id": version_id,
                                "page_index": page_index,
                                "bbox": bbox,
                                "target_text": "",
                                "proposed_text": text,
                                "category": "instrument_tag",
                                "source": "rapidocr_stacked_pid_instrument_proposal",
                                "review_status": "needs_review",
                                "corrected_text": "",
                                "question_text": "What text is shown in this small engineering label region?",
                                "image_path": image_path.relative_to(root).as_posix(),
                                "text_context": "",
                                "ocr_confidence": round(score, 5),
                                "review_notes": (
                                    f"RapidOCR stacked P&ID candidate only; confidence={score:.5f}; "
                                    f"tile=[{x},{y},{x2},{y2}]; ocr_scale={ocr_scale}; "
                                    f"rotate_cw_degrees={rotate_cw_degrees}; "
                                    "requires human verification"
                                ),
                            }
                            row["candidate_id"] = candidate_id(row)
                            rows.append(row)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    deduped = deduplicate_rows(rows)
    if limit_per_page > 0:
        deduped = sorted(
            deduped,
            key=lambda row: (-float(row["ocr_confidence"]), row["bbox"][1], row["bbox"][0]),
        )[:limit_per_page]
        deduped.sort(key=lambda row: (row["bbox"][1], row["bbox"][0]))
    return deduped, {
        "doc_id": doc_id,
        "page_index": page_index,
        "image_path": image_path.relative_to(root).as_posix(),
        "width": original_width,
        "height": original_height,
        "ocr_width": width,
        "ocr_height": height,
        "rotate_cw_degrees": rotate_cw_degrees,
        "ocr_scale": ocr_scale,
        "tiles": len(x_starts) * len(y_starts),
        "ocr_detections": detections,
        "classified_before_dedup": len(rows),
        "review_candidates": len(deduped),
    }


def page_index(path: Path) -> int:
    value = path.stem.removeprefix("page_")
    if not value.isdigit():
        raise ValueError(f"Could not parse page index from {path}")
    return int(value)


def select_page_paths(paths: Iterable[Path], selected_indices: Iterable[int]) -> list[Path]:
    indexed = {page_index(path): path for path in paths}
    selected = set(selected_indices)
    if not selected:
        return [indexed[index] for index in sorted(indexed)]
    missing = sorted(selected - set(indexed))
    if missing:
        raise ValueError(f"Requested page indices are unavailable: {missing}")
    return [indexed[index] for index in sorted(selected)]


def resolve_doc_ids(root: Path, doc_ids: list[str], doc_prefixes: list[str]) -> list[str]:
    resolved = {value.strip() for value in doc_ids if value.strip()}
    page_root = root / "derived" / "pages_300dpi"
    for prefix in doc_prefixes:
        resolved.update(path.name for path in page_root.glob(f"{prefix}*") if path.is_dir())
    if not resolved:
        raise ValueError("Provide at least one --doc-id or --doc-prefix")
    return sorted(resolved)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# OCR Microtext Proposal Report",
        "",
        "Review-only candidates. No row in this report is gold.",
        "",
        f"- Documents: `{totals['documents']}`",
        f"- Pages: `{totals['pages']}`",
        f"- OCR tiles: `{totals['tiles']}`",
        f"- OCR detections: `{totals['ocr_detections']}`",
        f"- Review candidates: `{totals['review_candidates']}`",
        f"- Categories: `{totals['categories']}`",
        "",
        "## Pages",
        "",
        "| Document | Page | Tiles | OCR detections | Review candidates |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for page in report["pages"]:
        lines.append(
            f"| `{page['doc_id']}` | {page['page_index']} | {page['tiles']} | "
            f"{page['ocr_detections']} | {page['review_candidates']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--doc-id", action="append", default=[])
    parser.add_argument("--doc-prefix", action="append", default=[])
    parser.add_argument(
        "--page-index",
        action="append",
        default=[],
        type=int,
        help="Process only this zero-based rendered page index; repeat as needed.",
    )
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--tile-size", type=int, default=5000)
    parser.add_argument("--overlap", type=int, default=512)
    parser.add_argument("--min-confidence", type=float, default=0.92)
    parser.add_argument("--padding", type=int, default=24)
    parser.add_argument("--limit-per-page", type=int, default=300)
    parser.add_argument("--max-total", type=int, default=0)
    parser.add_argument("--max-image-pixels", type=int, default=300_000_000)
    parser.add_argument(
        "--ocr-scale",
        type=float,
        default=1.0,
        help=(
            "Rescale each detector tile while preserving original-page bbox coordinates; "
            "useful for downscaling large sparse drawings or upscaling degraded scans. "
            "Must be greater than 0 and at most 4."
        ),
    )
    parser.add_argument(
        "--rotate-cw-degrees",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help=(
            "Rotate pages clockwise for OCR, then restore candidate boxes to the "
            "original rendered-page coordinate space."
        ),
    )
    parser.add_argument(
        "--include-unitless-dimensions",
        action="store_true",
        help=(
            "Opt in to decimal-only mechanical dimensions such as 17.78 or R0.8. "
            "Use only on drawing pages where those values are dimensions."
        ),
    )
    parser.add_argument(
        "--include-unitless-integer-dimensions",
        action="store_true",
        help=(
            "Opt in to bare integer mechanical dimensions such as 3, 22, or R10. "
            "Use only on dimensioned drawing sheets where isolated integers are "
            "dimension callouts. Values are limited to 1-999."
        ),
    )
    parser.add_argument(
        "--include-pcb-pin-signals",
        action="store_true",
        help=(
            "Opt in to standard PCB pin and signal labels such as GP0, VBUS, "
            "I2C0 SDA, and SPI1 RX."
        ),
    )
    parser.add_argument(
        "--include-architectural-room-labels",
        action="store_true",
        help=(
            "Opt in to atomic architectural room labels such as KITCHEN, "
            "PANTRY, HALL, PORCH, and BATH. Use only on plan sheets."
        ),
    )
    parser.add_argument(
        "--include-pid-labels",
        action="store_true",
        help=(
            "Opt in to complete P&ID line and equipment-trim designators such as "
            "PG05001-6\"-AAAA2-ST-SR and VT-05C205-AAAA2-I. Use only on P&ID "
            "or equipment-designator sheets."
        ),
    )
    parser.add_argument(
        "--include-hvac-control-labels",
        action="store_true",
        help=(
            "Opt in to HVAC control-point tags such as SA-T-SP, MINOA-D-C, "
            "and SEC-PMP-C, plus standard hydronic pipe abbreviations. Use only "
            "on control schematics, logic diagrams, or points schedules."
        ),
    )
    parser.add_argument(
        "--include-civil-slope-values",
        action="store_true",
        help=(
            "Opt in to plain percentage labels as civil slope tolerances. Use only "
            "on grading, curb-ramp, roadway, or accessibility detail sheets."
        ),
    )
    parser.add_argument(
        "--include-unclassified",
        action="store_true",
        help=(
            "Emit otherwise unclassified OCR detections as unknown_microtext for "
            "diagnostic contact-sheet review. This is a broad review-only mode and "
            "must not be treated as category-ready output."
        ),
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    doc_ids = resolve_doc_ids(root, args.doc_id, args.doc_prefix)
    engine, np_module = load_rapidocr()
    rows: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        page_dir = root / "derived" / "pages_300dpi" / doc_id
        page_paths = select_page_paths(page_dir.glob("page_*.png"), args.page_index)
        for image_path in page_paths:
            proposed, page_report = propose_page_rows(
                root=root,
                image_path=image_path,
                doc_id=doc_id,
                version_id=args.version_id,
                page_index=page_index(image_path),
                engine=engine,
                np_module=np_module,
                tile_size=args.tile_size,
                overlap=args.overlap,
                min_confidence=args.min_confidence,
                padding=args.padding,
                limit_per_page=args.limit_per_page,
                max_image_pixels=args.max_image_pixels,
                ocr_scale=args.ocr_scale,
                include_unitless_dimensions=args.include_unitless_dimensions,
                include_unitless_integer_dimensions=(
                    args.include_unitless_integer_dimensions
                ),
                include_pcb_pin_signals=args.include_pcb_pin_signals,
                include_architectural_room_labels=args.include_architectural_room_labels,
                include_pid_labels=args.include_pid_labels,
                include_hvac_control_labels=args.include_hvac_control_labels,
                include_civil_slope_values=args.include_civil_slope_values,
                include_unclassified=args.include_unclassified,
                rotate_cw_degrees=args.rotate_cw_degrees,
            )
            rows.extend(proposed)
            pages.append(page_report)
            print(
                f"[OCR] {doc_id} page {page_report['page_index']}: "
                f"{page_report['review_candidates']} review candidates"
            )
    rows = deduplicate_rows(rows)
    if args.max_total > 0:
        rows = sorted(rows, key=lambda row: -float(row["ocr_confidence"]))[: args.max_total]
        rows.sort(key=lambda row: (row["doc_id"], row["page_index"], row["bbox"][1], row["bbox"][0]))

    category_counts = Counter(str(row["category"]) for row in rows)
    report = {
        "version_id": args.version_id,
        "runtime": {
            "engine": "RapidOCR",
            "tile_size": args.tile_size,
            "overlap": args.overlap,
            "ocr_scale": args.ocr_scale,
            "rotate_cw_degrees": args.rotate_cw_degrees,
            "include_unitless_dimensions": args.include_unitless_dimensions,
            "include_unitless_integer_dimensions": (
                args.include_unitless_integer_dimensions
            ),
            "include_pcb_pin_signals": args.include_pcb_pin_signals,
            "include_architectural_room_labels": args.include_architectural_room_labels,
            "include_pid_labels": args.include_pid_labels,
            "include_hvac_control_labels": args.include_hvac_control_labels,
            "include_civil_slope_values": args.include_civil_slope_values,
            "include_unclassified": args.include_unclassified,
        },
        "pages": pages,
        "totals": {
            "documents": len(doc_ids),
            "pages": len(pages),
            "tiles": sum(int(page["tiles"]) for page in pages),
            "ocr_detections": sum(int(page["ocr_detections"]) for page in pages),
            "review_candidates": len(rows),
            "categories": dict(sorted(category_counts.items())),
        },
        "gold_rows_added": 0,
    }
    output_jsonl = args.output_jsonl if args.output_jsonl.is_absolute() else root / args.output_jsonl
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_jsonl(output_jsonl, rows)
    write_report(report_json, report)
    write_markdown(report_md, report)
    print(json.dumps(report["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
