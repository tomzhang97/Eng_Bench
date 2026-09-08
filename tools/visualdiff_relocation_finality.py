"""Deterministic descriptions for uniquely matched VisualDiff text relocation."""
from __future__ import annotations

import json
import math
import re
from typing import Any


POLICY_VERSION = "active_visualdiff_unique_text_relocation_v1"
MIN_MOVE_DISTANCE_PX = 40.0
MAX_MOVE_DISTANCE_PX = 200.0
MAX_BOX_BIND_DISTANCE_PX = 25.0
MAX_SPAN_SHAPE_RELATIVE_DELTA = 0.05
MIN_NORMALIZED_TARGET_LENGTH = 3


def normalized_target(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def bbox_center(bbox: list[float] | tuple[float, ...]) -> tuple[float, float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("invalid relocation bbox")
    values = [float(value) for value in bbox]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite relocation bbox")
    if values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError("degenerate relocation bbox")
    return (values[0] + values[2]) / 2, (values[1] + values[3]) / 2


def span_shape_matches(old_bbox: list[float], new_bbox: list[float]) -> bool:
    old_width = float(old_bbox[2]) - float(old_bbox[0])
    old_height = float(old_bbox[3]) - float(old_bbox[1])
    new_width = float(new_bbox[2]) - float(new_bbox[0])
    new_height = float(new_bbox[3]) - float(new_bbox[1])
    if min(old_width, old_height, new_width, new_height) <= 0:
        return False
    width_delta = abs(new_width - old_width) / max(old_width, new_width)
    height_delta = abs(new_height - old_height) / max(old_height, new_height)
    return max(width_delta, height_delta) <= MAX_SPAN_SHAPE_RELATIVE_DELTA


def movement_from_probes(
    old_probe: dict[str, Any], new_probe: dict[str, Any]
) -> dict[str, float | str]:
    old_best = old_probe.get("best") or {}
    new_best = new_probe.get("best") or {}
    old_center = bbox_center(old_best.get("bbox_px"))
    new_center = bbox_center(new_best.get("bbox_px"))
    delta_x = new_center[0] - old_center[0]
    delta_y = new_center[1] - old_center[1]
    distance = math.hypot(delta_x, delta_y)
    horizontal = "right" if delta_x > 0 else "left"
    vertical = "down" if delta_y > 0 else "up"
    if abs(delta_x) >= 2 * abs(delta_y):
        direction = horizontal
    elif abs(delta_y) >= 2 * abs(delta_x):
        direction = vertical
    else:
        direction = f"{vertical} and {horizontal}"
    return {
        "delta_x_px": round(delta_x, 4),
        "delta_y_px": round(delta_y, 4),
        "distance_px": round(distance, 4),
        "direction": direction,
    }


def relocation_description(target: str, movement: dict[str, Any]) -> str:
    direction = str(movement.get("direction") or "").strip()
    if direction not in {
        "left", "right", "up", "down",
        "up and left", "up and right", "down and left", "down and right",
    }:
        raise ValueError("invalid relocation direction")
    return (
        f"The engineering text {json.dumps(target, ensure_ascii=False)} "
        f"moved {direction}."
    )
