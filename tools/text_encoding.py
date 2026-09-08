#!/usr/bin/env python3
"""Conservative detection of common UTF-8 mojibake."""
from __future__ import annotations

import re


_CP1252_UTF8_TRAIL = (
    "\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192"
    "\u2013\u2014\u2018\u2019\u201a\u201c\u201d\u201e\u2020\u2021"
    "\u2022\u2026\u2030\u2039\u203a\u20ac\u2122"
)
_MOJIBAKE_PATTERNS = (
    ("replacement_character", re.compile("\ufffd")),
    ("latin1_utf8_lead_c2", re.compile(f"\u00c2[{_CP1252_UTF8_TRAIL}]")),
    ("latin1_utf8_lead_c3", re.compile(f"\u00c3[{_CP1252_UTF8_TRAIL}]")),
    (
        "cp1252_utf8_punctuation",
        re.compile(f"\u00e2(?:\u20ac|\u0080)[{_CP1252_UTF8_TRAIL}]"),
    ),
)


def mojibake_signatures(text: str) -> tuple[str, ...]:
    """Return high-confidence mojibake signatures present in *text*."""
    return tuple(name for name, pattern in _MOJIBAKE_PATTERNS if pattern.search(text))
