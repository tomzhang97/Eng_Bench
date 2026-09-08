"""Fail-closed primitives for exact multi-span VisualDiff labels."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


POLICY_VERSION = "active_visualdiff_exact_multispan_cluster_finality_v1"
MIN_CLUSTER_SPANS = 2
MAX_CLUSTER_SPANS = 4
BOX_TOLERANCE_PX = 2.0
MAX_CLUSTER_CENTER_DISTANCE_PX = 25.0
MAX_ADJACENT_SPAN_GAP_PX = 8.0
MAX_FONT_SIZE_DELTA = 0.001
MIN_UNIQUE_ANCHOR_LENGTH = 3


def normalized_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


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


def _inside(inner: list[float], outer: list[float]) -> bool:
    return (
        inner[0] >= outer[0] - BOX_TOLERANCE_PX
        and inner[1] >= outer[1] - BOX_TOLERANCE_PX
        and inner[2] <= outer[2] + BOX_TOLERANCE_PX
        and inner[3] <= outer[3] + BOX_TOLERANCE_PX
    )


def _rect_gap(first: list[float], second: list[float]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(dx, dy)


def probe_exact_cluster(
    spans: list[dict[str, Any]], target: str, page: int, crop: Any
) -> dict[str, Any]:
    """Test whether every text span in a reviewed box exactly reconstructs a label."""
    crop_box = _bbox(crop)
    if crop_box is None:
        return {"status": "invalid_crop", "span_count_in_box": 0, "best": None}

    page_spans: list[dict[str, Any]] = []
    for span in spans:
        bbox = _bbox(span.get("bbox_px"))
        text = str(span.get("text") or "").strip()
        normalized = normalized_text(text)
        if span.get("page") != page or bbox is None or not normalized:
            continue
        page_spans.append({
            "text": text,
            "normalized_text": normalized,
            "bbox_px": bbox,
            "font": span.get("font"),
            "size": span.get("size"),
            "flags": span.get("flags"),
        })

    in_box = [span for span in page_spans if _inside(span["bbox_px"], crop_box)]
    in_box.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0], item["text"]))
    reconstructed = "".join(span["normalized_text"] for span in in_box)
    normalized_target = normalized_text(target)
    exact = reconstructed == normalized_target and bool(normalized_target)

    union = None
    center_distance = None
    max_gap = None
    uniform_style = False
    unique_anchors: list[str] = []
    if in_box:
        union = [
            min(span["bbox_px"][0] for span in in_box),
            min(span["bbox_px"][1] for span in in_box),
            max(span["bbox_px"][2] for span in in_box),
            max(span["bbox_px"][3] for span in in_box),
        ]
        center_distance = math.hypot(
            (union[0] + union[2] - crop_box[0] - crop_box[2]) / 2,
            (union[1] + union[3] - crop_box[1] - crop_box[3]) / 2,
        )
        max_gap = max(
            (_rect_gap(first["bbox_px"], second["bbox_px"])
             for first, second in zip(in_box, in_box[1:])),
            default=0.0,
        )
        fonts = {str(span.get("font") or "") for span in in_box}
        flags = {int(span.get("flags") or 0) for span in in_box}
        sizes = [float(span.get("size") or 0) for span in in_box]
        uniform_style = (
            len(fonts) == 1
            and len(flags) == 1
            and max(sizes, default=0.0) - min(sizes, default=0.0) <= MAX_FONT_SIZE_DELTA
        )
        page_counts = Counter(span["normalized_text"] for span in page_spans)
        unique_anchors = [
            span["text"] for span in in_box
            if len(span["normalized_text"]) >= MIN_UNIQUE_ANCHOR_LENGTH
            and page_counts[span["normalized_text"]] == 1
        ]

    qualifies = (
        exact
        and MIN_CLUSTER_SPANS <= len(in_box) <= MAX_CLUSTER_SPANS
        and bool(uniform_style)
        and center_distance is not None
        and center_distance <= MAX_CLUSTER_CENTER_DISTANCE_PX
        and max_gap is not None
        and max_gap <= MAX_ADJACENT_SPAN_GAP_PX
        and bool(unique_anchors)
    )
    best = None
    if union is not None:
        best = {
            "bbox_px": union,
            "distance_px": round(float(center_distance), 4),
            "font": in_box[0].get("font"),
            "size": in_box[0].get("size"),
            "flags": in_box[0].get("flags"),
        }
    return {
        "status": "observed",
        "target": target,
        "normalized_target": normalized_target,
        "span_count_in_box": len(in_box),
        "spans": in_box,
        "reconstructed_normalized_text": reconstructed,
        "exact_reconstruction": exact,
        "uniform_style": uniform_style,
        "max_adjacent_gap_px": round(float(max_gap), 4) if max_gap is not None else None,
        "cluster_center_distance_px": (
            round(float(center_distance), 4) if center_distance is not None else None
        ),
        "unique_anchor_texts": unique_anchors,
        "qualifies": qualifies,
        "best": best,
    }


def validate_qualifying_cluster(probe: dict[str, Any]) -> None:
    if probe.get("status") != "observed" or probe.get("qualifies") is not True:
        raise ValueError("multispan_cluster_not_qualifying")
    spans = probe.get("spans") or []
    if not MIN_CLUSTER_SPANS <= len(spans) <= MAX_CLUSTER_SPANS:
        raise ValueError("multispan_cluster_span_count_out_of_policy")
    if "".join(normalized_text(str(span.get("text") or "")) for span in spans) != str(
        probe.get("normalized_target") or ""
    ):
        raise ValueError("multispan_cluster_reconstruction_mismatch")
