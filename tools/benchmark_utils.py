#!/usr/bin/env python3
"""
benchmark_utils.py

Utility functions for Eng-Bench validation and normalization:
- Delta anchor detection (hierarchical precedence)
- Entity/question text normalization
- Dedup key generation
- Hard-negative construction helpers
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# --- Normalization regexes ---
RE_PAGE = re.compile(r"\bon page\s+\d+\b", re.I)
RE_VER = re.compile(r"\b(?:pcbV?\d+(?:\.\d+)?|rev[A-Z]\d*|docRev[A-Z]\d*)\b", re.I)
RE_WS = re.compile(r"\s+")
RE_PUNCT_EDGE = re.compile(r"^[\W_]+|[\W_]+$")
RE_COMPONENT = re.compile(r"did component (.+?) change between", re.I)

# Generic tokens that require delta anchor
GENERIC_TOKENS = {"gnd", "vcc", "note", "connector", "figure", "table", "see", "ref"}


def normalize_entity_text(s: str) -> str:
    """
    Make entities benchmark-stable:
    - lowercase, remove edge punctuation, collapse whitespace
    - de-dupe consecutive tokens
    """
    s = s.lower()
    s = RE_PUNCT_EDGE.sub("", s)
    s = RE_WS.sub(" ", s).strip()
    if not s:
        return s

    # token-level de-dupe
    toks = s.split()
    out = []
    for t in toks:
        if out and t == out[-1]:
            continue
        out.append(t)

    joined = " ".join(out)
    # special normalization
    joined = joined.replace("microsd micro sd", "micro sd")
    joined = joined.replace("micro sd microsd", "micro sd")
    return joined.strip()


def normalize_question_text(q: str) -> str:
    """
    Normalize question wording:
    - lowercase, replace page/version tokens, collapse whitespace
    """
    q = q.lower()
    q = RE_PAGE.sub("on page <p>", q)
    q = RE_VER.sub("<ver>", q)
    q = RE_WS.sub(" ", q).strip()
    return q


def quantize_bbox(b: List[int], step: int = 4) -> Tuple[int, int, int, int]:
    """Quantize bbox to reduce jitter duplicates."""
    x1, y1, x2, y2 = b
    def qv(v: int) -> int:
        return int(round(v / step) * step)
    return (qv(x1), qv(y1), qv(x2), qv(y2))


# --- Delta Anchor Detection ---

def get_anchor(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Delta Anchor precedence:
    1) delta_triangle_id / revision_cloud_id (schematics)
    2) change_id (canonical)
    3) candidate_id (pre-annotation)
    4) revision_history_bbox (datasheet-only)
    """
    if item.get("delta_triangle_id"):
        return ("delta_triangle_id", str(item["delta_triangle_id"]))
    if item.get("revision_cloud_id"):
        return ("revision_cloud_id", str(item["revision_cloud_id"]))
    if item.get("change_id"):
        return ("change_id", str(item["change_id"]))
    if item.get("candidate_id"):
        return ("candidate_id", str(item["candidate_id"]))
    if item.get("revision_history_bbox"):
        return ("revision_history_bbox", "rh")
    for ev in item.get("evidence", []) or []:
        if ev.get("note") in ("revision_history", "revision_history_bbox"):
            return ("revision_history_bbox", "rh")
    return (None, None)


def anchor_present(item: Dict[str, Any]) -> bool:
    """Check if item has any delta anchor."""
    kind, _ = get_anchor(item)
    return kind is not None


def is_generic_entity(entity: str) -> bool:
    """Check if entity is a generic token requiring delta anchor."""
    norm = normalize_entity_text(entity)
    return norm.lower() in GENERIC_TOKENS


# --- Entity Extraction ---

def extract_entity_from_question(question: str) -> str:
    """Fallback: extract entity from question text."""
    m = RE_COMPONENT.search(question)
    if not m:
        return ""
    return m.group(1).strip()


# --- Dedup Key ---

def dedup_key(item: Dict[str, Any]) -> Tuple:
    """
    Generate stable dedup key:
    (pair_id, split, qtype, page, anchor_kind, anchor_id, bbox, entity, question, coord_frame)
    """
    pair_id = item.get("pair_id", "")
    split = item.get("split", "")
    qtype = item.get("question_type", "")

    # page: prefer structured answer.page
    page = -1
    ans = item.get("answer", {})
    if isinstance(ans, dict) and ans.get("page") is not None:
        page = int(ans["page"])
    else:
        qm = re.search(r"\bon page\s+(\d+)\b", item.get("question", ""), re.I)
        if qm:
            page = int(qm.group(1))

    kind, aid = get_anchor(item)

    # bbox: prefer answer, else first evidence
    bbox_key = None
    if isinstance(ans, dict) and ans.get("bbox"):
        bbox_key = quantize_bbox(ans["bbox"])
    else:
        ev = item.get("evidence", []) or []
        if ev and ev[0].get("bbox"):
            bbox_key = quantize_bbox(ev[0]["bbox"])

    # entity: explicit or extracted
    entity_raw = item.get("entity") or extract_entity_from_question(item.get("question", ""))
    entity_norm = normalize_entity_text(entity_raw)

    q_norm = normalize_question_text(item.get("question", ""))
    coord_frame = item.get("coord_frame", "")

    return (pair_id, split, qtype, page, kind, aid, bbox_key, entity_norm, q_norm, coord_frame)


# --- Validation Helpers ---

def validate_generic_with_anchor(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check: generic tokens allowed only with delta anchor.
    Returns (is_valid, reason).
    """
    entity = item.get("entity") or extract_entity_from_question(item.get("question", ""))
    if is_generic_entity(entity) and not anchor_present(item):
        return (False, f"Generic entity '{entity}' without delta anchor")
    return (True, "")


def validate_bbox_bounds(item: Dict[str, Any], page_width: int, page_height: int) -> Tuple[bool, str]:
    """Check bbox is valid and within page bounds."""
    ans = item.get("answer", {})
    if not isinstance(ans, dict) or not ans.get("bbox"):
        return (True, "")  # no bbox to validate
    
    x1, y1, x2, y2 = ans["bbox"]
    if x2 <= x1 or y2 <= y1:
        return (False, f"Degenerate bbox: {ans['bbox']}")
    if x1 < 0 or y1 < 0 or x2 > page_width or y2 > page_height:
        return (False, f"Bbox out of bounds: {ans['bbox']}")
    return (True, "")
