"""Fail-closed geometry checks for a reviewed crop contained by one exact span."""
from __future__ import annotations

import math
from typing import Any


POLICY_VERSION = "active_visualdiff_contained_exact_span_finality_v1"
BOX_TOLERANCE_PX = 2.0
MAX_SPAN_CENTER_DISTANCE_PX = 100.0
MIN_SPAN_COVERAGE_BY_CROP = 0.25


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    return result if result[2] > result[0] and result[3] > result[1] else None


def span_contains_crop(span_bbox: Any, crop_bbox: Any) -> bool:
    span = _bbox(span_bbox)
    crop = _bbox(crop_bbox)
    if span is None or crop is None:
        return False
    return (
        crop[0] >= span[0] - BOX_TOLERANCE_PX
        and crop[1] >= span[1] - BOX_TOLERANCE_PX
        and crop[2] <= span[2] + BOX_TOLERANCE_PX
        and crop[3] <= span[3] + BOX_TOLERANCE_PX
    )


def qualifying_contained_span(probe: dict[str, Any], crop_bbox: Any) -> bool:
    if probe.get("status") != "observed":
        return False
    if int(probe.get("page_matches") or 0) != 1:
        return False
    if int(probe.get("nearby_matches") or 0) != 1:
        return False
    best = probe.get("best") or {}
    if not best or not span_contains_crop(best.get("bbox_px"), crop_bbox):
        return False
    if float(best.get("distance_px") or float("inf")) > MAX_SPAN_CENTER_DISTANCE_PX:
        return False
    return float(best.get("original_crop_span_coverage") or 0) >= MIN_SPAN_COVERAGE_BY_CROP


def validate_qualifying_contained_span(probe: dict[str, Any], crop_bbox: Any) -> None:
    if not qualifying_contained_span(probe, crop_bbox):
        raise ValueError("contained_span_not_qualifying")
