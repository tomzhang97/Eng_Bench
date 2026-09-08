#!/usr/bin/env python3
"""Shared source-rights policy for public Eng_Bench release gates."""
from __future__ import annotations


BLOCKING_STATUS_MARKERS = (
    "unknown",
    "restricted",
    "proprietary",
    "reference_only",
    "not_promoted",
    "rights_uncertain",
    "release_review_needed",
    "internal_only",
    "cc_by_nd",
    "cc-by-nd",
    "by_nd",
    "attribution-noderivatives",
    "no_derivatives",
    "no-derivatives",
    "cc_by_nc",
    "cc-by-nc",
    "by_nc",
    "attribution-noncommercial",
    "noncommercial",
    "non-commercial",
)

# These markers encode an affirmative redistribution license or public-domain
# basis. Merely being publicly downloadable or vendor-hosted is not enough.
EXPLICIT_RELEASE_STATUS_MARKERS = (
    "apache_",
    "bsd_",
    "cern_ohl_",
    "cc0",
    "cc_by_",
    "cc_by_sa",
    "gfdl",
    "gpl_",
    "government_public",
    "mit_",
    "open_government",
    "public_agency_source_url_terms_sha256",
    "public_domain",
    "tapr_ohl_",
)


def rights_blocker(status: str | None) -> str:
    """Return a stable blocker when release rights are absent or insufficient."""
    lowered = str(status or "").strip().lower()
    if not lowered:
        return "missing_public_status"
    blocker = next((marker for marker in BLOCKING_STATUS_MARKERS if marker in lowered), "")
    if blocker:
        return blocker
    if not any(marker in lowered for marker in EXPLICIT_RELEASE_STATUS_MARKERS):
        return "license_evidence_missing"
    return ""


def is_release_safe_status(status: str | None) -> bool:
    return not rights_blocker(status)
