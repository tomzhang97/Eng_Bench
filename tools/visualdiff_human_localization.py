"""Deterministic English localizations for exact human-reviewed VisualDiff text."""
from __future__ import annotations


POLICY_VERSION = "active_visualdiff_exact_human_localization_v1"
PREVIEW_MODE = "read_only_active_visualdiff_localization_correction_preview"
DESC_SOURCE = "human_semantics_machine_english_localized"
METHOD = "exact_machine_translation_of_human_reviewed_description"

HUMAN_DELETION_ZH = "NEW 相比 OLD 删除了红框内的有效工程文字、器件标注或图形对象。"
HUMAN_DELETION_EN = (
    "A valid engineering text label, component annotation, or graphical object "
    "inside the highlighted box was removed in the new revision."
)

EXACT_LOCALIZATIONS = {
    HUMAN_DELETION_ZH: HUMAN_DELETION_EN,
}


def localized_description(original: str) -> str:
    """Return the approved literal localization or fail closed."""
    try:
        return EXACT_LOCALIZATIONS[original]
    except KeyError as exc:
        raise ValueError("unsupported_human_localization_text") from exc
