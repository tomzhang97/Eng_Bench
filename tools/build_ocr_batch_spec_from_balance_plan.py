#!/usr/bin/env python3
"""Build a RapidOCR batch spec from a MicroText balance conversion plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DOMAIN_PROFILES = {
    "civil_architectural": ["architectural_room_labels"],
    "pid": ["pid_labels"],
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_spec(plan: dict[str, Any], *, version_id: str) -> dict[str, Any]:
    if not version_id.strip():
        raise ValueError("version_id is required")
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    domains: Counter[str] = Counter()
    for row in plan.get("selected_sources") or []:
        if str(row.get("conversion_mode") or "") != "ocr_or_manual_region_recovery":
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id:
            raise ValueError("selected OCR source lacks doc_id")
        if doc_id in seen:
            raise ValueError(f"duplicate selected OCR source: {doc_id}")
        seen.add(doc_id)
        domain = str(row.get("domain") or "unknown").strip() or "unknown"
        domains[domain] += 1
        documents.append(
            {
                "doc_id": doc_id,
                "version_id": version_id.strip(),
                "profiles": list(DOMAIN_PROFILES.get(domain, [])),
                "page_indices": [],
                "rotate_cw_degrees": 0,
            }
        )
    if not documents:
        raise ValueError("plan contains no selected OCR sources")
    return {
        "goal": "Gold v2.0 Global",
        "source_plan_date_label": str(plan.get("date_label") or ""),
        "documents": documents,
        "summary": {
            "documents": len(documents),
            "domains": dict(sorted(domains.items())),
            "safe_to_merge_gold": False,
        },
        "interpretation": (
            "This spec runs review-only OCR proposals. Every output still requires deduplication, "
            "pixel evidence, full visual QA, human acceptance, and strict promotion."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    spec = build_spec(plan, version_id=args.version_id)
    spec["source_plan"] = args.plan.as_posix()
    spec["source_plan_sha256"] = file_sha256(args.plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(spec["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
