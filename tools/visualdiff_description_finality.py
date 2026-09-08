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
PLACEHOLDER_DESCRIPTIONS = {
    "CHANGE_DESC_GT_TODO",
    "CHANGE_DESC_TODO",
    "TODO",
    "TBD",
}
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
GENERIC_MACHINE_PREFIXES = (
    "Highlighted graphic/symbol appearance changed",
    "Highlighted visual content changed",
    "Highlighted text or graphic content was added",
)
UNVALIDATED_MACHINE_VISUAL_SOURCE = "codex_assisted_visual_review"


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


def machine_known_description_issue(
    description: str,
    *,
    desc_source: str = "",
) -> str | None:
    """Return objective release debts that do not require an audit vote."""
    text = description.strip()
    source = desc_source.strip().lower()
    if not text:
        return "blank"
    if text.upper() in PLACEHOLDER_DESCRIPTIONS:
        return "placeholder"
    if CJK_CHARACTER.search(text):
        return "non_english"
    if source == UNVALIDATED_MACHINE_VISUAL_SOURCE:
        return "unvalidated_machine_visual"
    if not source.startswith("human") and text.startswith(GENERIC_MACHINE_PREFIXES):
        return "generic_machine_description"
    return None


def description_release_issue(row: dict[str, Any]) -> dict[str, str] | None:
    """Classify known nonfinal descriptions while preserving tentative details."""
    description = str(row.get("change_desc_gt") or row.get("answer") or "")
    tentative = tentative_description_details(description)
    if tentative:
        return {"reason": "tentative_generator_template", **tentative}
    issue = machine_known_description_issue(
        description,
        desc_source=str(row.get("desc_source") or ""),
    )
    if issue:
        return {"reason": issue, "kind": issue, "target": ""}
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
        details = description_release_issue(row)
        if details:
            findings.append({
                "pair_id": str(row.get("pair_id") or row.get("id") or ""),
                "split": str(row.get("split") or "unknown"),
                "desc_source": str(row.get("desc_source") or ""),
                **details,
            })
    tentative = [row for row in findings if row["reason"] == "tentative_generator_template"]
    machine_known = [row for row in findings if row["reason"] != "tentative_generator_template"]
    return {
        "current": len(pairs) - len(findings),
        "target": len(pairs),
        "passes": not findings,
        "nonfinal_rows": len(findings),
        "tentative_rows": len(tentative),
        "machine_known_nonrelease_rows": len(machine_known),
        "by_reason": dict(sorted(Counter(row["reason"] for row in findings).items())),
        "by_kind": dict(sorted(Counter(row["kind"] for row in tentative).items())),
        "by_split": dict(sorted(Counter(row["split"] for row in findings).items())),
        "findings": findings,
        "scope": (
            "Known machine-detectable finality, localization, placeholder, and "
            "unvalidated visual-description debt; not full semantic certification."
        ),
    }
