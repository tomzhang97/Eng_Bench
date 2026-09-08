"""Recognize known tentative generator answers, not uncertainty in quoted labels."""
from __future__ import annotations

import re
import json
from collections import Counter
from typing import Any


TEXT_TEMPLATE = re.compile(
    r"Localized text may have been (added|removed): (['\"])(.+)\2\.", re.DOTALL
)
TEXT_CHANGE_TEMPLATE = re.compile(
    r"Localized text may have changed from (['\"])(.+)\1 to (['\"])(.+)\3\.", re.DOTALL
)
GRAPHIC_TEMPLATE = "A localized graphic or schematic-symbol difference may be present."


def tentative_description_details(description: str) -> dict[str, str] | None:
    text = description.strip()
    match = TEXT_TEMPLATE.fullmatch(text)
    if match:
        return {"kind": "text_" + match[1], "target": match[3]}
    change = TEXT_CHANGE_TEMPLATE.fullmatch(text)
    if change:
        return {"kind": "text_changed", "target": change[4],
                "target_old": change[2], "target_new": change[4]}
    if text == GRAPHIC_TEMPLATE:
        return {"kind": "graphic_uncertain", "target": ""}
    return None


def definitive_description(details: dict[str, str]) -> str:
    """Remove only template uncertainty while preserving the reviewed tokens."""
    kind = details.get("kind")
    if kind == "text_added":
        return f"The engineering text {json.dumps(details['target'], ensure_ascii=False)} was added."
    if kind == "text_removed":
        return f"The engineering text {json.dumps(details['target'], ensure_ascii=False)} was removed."
    if kind == "text_changed":
        old = json.dumps(details["target_old"], ensure_ascii=False)
        new = json.dumps(details["target_new"], ensure_ascii=False)
        return f"The engineering text changed from {old} to {new}."
    raise ValueError(f"unsupported tentative description kind: {kind}")


def visualdiff_description_finality(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    findings = []
    for row in pairs:
        details = tentative_description_details(str(row.get("change_desc_gt") or ""))
        if details:
            findings.append({
                "pair_id": str(row.get("pair_id") or row.get("id") or ""),
                "split": str(row.get("split") or "unknown"),
                **details,
            })
    return {
        "current": len(pairs) - len(findings),
        "target": len(pairs),
        "passes": not findings,
        "tentative_rows": len(findings),
        "by_kind": dict(sorted(Counter(row["kind"] for row in findings).items())),
        "by_split": dict(sorted(Counter(row["split"] for row in findings).items())),
        "findings": findings,
        "scope": "Known generator-template finality only; not semantic certification.",
    }
