"""Fail-closed primitives for same-slot VisualDiff text replacement."""
from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from typing import Any


POLICY_VERSION = "active_visualdiff_same_slot_text_replacement_v1"
MAX_BOX_BIND_DISTANCE_PX = 25.0
MAX_SLOT_EDGE_DELTA_PX = 1.5
MAX_FONT_SIZE_DELTA = 0.001
MIN_NORMALIZED_TEXT_LENGTH = 2
MIN_RELATED_TEXT_SIMILARITY = 0.55


def normalized_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def texts_are_related(old_text: str, new_text: str) -> bool:
    old = normalized_text(old_text)
    new = normalized_text(new_text)
    if min(len(old), len(new)) < MIN_NORMALIZED_TEXT_LENGTH or old == new:
        return False
    return (
        old in new
        or new in old
        or SequenceMatcher(None, old, new).ratio() >= MIN_RELATED_TEXT_SIMILARITY
    )


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("invalid replacement bbox")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError("non-finite replacement bbox")
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("degenerate replacement bbox")
    return result


def same_slot_matches(old_best: dict[str, Any], new_best: dict[str, Any]) -> bool:
    try:
        old = _bbox(old_best.get("bbox_px"))
        new = _bbox(new_best.get("bbox_px"))
    except (TypeError, ValueError):
        return False
    if str(old_best.get("font") or "") != str(new_best.get("font") or ""):
        return False
    if int(old_best.get("flags") or 0) != int(new_best.get("flags") or 0):
        return False
    if abs(float(old_best.get("size") or 0) - float(new_best.get("size") or 0)) > MAX_FONT_SIZE_DELTA:
        return False
    # Replacement text may be wider or narrower. The left edge and vertical
    # baseline must remain fixed so this cannot silently certify a relocation.
    return max(abs(old[0] - new[0]), abs(old[1] - new[1]), abs(old[3] - new[3])) <= MAX_SLOT_EDGE_DELTA_PX


def replacement_description(old_text: str, new_text: str) -> str:
    if not texts_are_related(old_text, new_text):
        raise ValueError("replacement texts are not sufficiently related")
    return (
        f"The engineering text {json.dumps(old_text, ensure_ascii=False)} changed to "
        f"{json.dumps(new_text, ensure_ascii=False)}."
    )
