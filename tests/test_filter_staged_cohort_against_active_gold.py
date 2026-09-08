from __future__ import annotations

import json
from pathlib import Path

from tools.filter_staged_cohort_against_active_gold import sanitize_cohort


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def microtext(candidate_id: str, bbox: list[int]) -> dict:
    return {
        "task": "microtext",
        "candidate_id": candidate_id,
        "doc_id": "doc_a",
        "page_index": 0,
        "bbox": bbox,
    }


def visualdiff(pair_id: str) -> dict:
    return {"task": "visualdiff", "pair_id": pair_id}


def test_sanitize_cohort_holds_active_and_within_cohort_collisions(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
        [microtext("active_mt", [0, 0, 100, 100])],
    )
    write_jsonl(
        tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
        [visualdiff("active_vd")],
    )
    rows = [
        microtext("active_mt", [200, 200, 300, 300]),
        microtext("near_active", [2, 2, 102, 102]),
        microtext("kept_mt", [400, 400, 500, 500]),
        microtext("near_kept", [402, 402, 502, 502]),
        visualdiff("active_vd"),
        visualdiff("kept_vd"),
    ]

    kept, held, report = sanitize_cohort(tmp_path, rows)

    assert [row.get("candidate_id") or row.get("pair_id") for row in kept] == [
        "kept_mt",
        "kept_vd",
    ]
    assert report["valid"] is True
    assert report["hold_reasons"] == {
        "active_gold_identity_overlap": 2,
        "active_gold_near_region_overlap": 1,
        "within_cohort_near_region_overlap": 1,
    }
    assert all(row["safe_to_merge_gold"] is False for row in kept + held)


def test_sanitize_cohort_holds_shifted_same_payload_label(tmp_path: Path) -> None:
    active = microtext("active_mt", [100, 100, 180, 140])
    active.update(
        {
            "doc_id": "canonical_doc",
            "text_gt": "MK 01 S",
            "category": "equipment_tag",
        }
    )
    write_jsonl(
        tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
        [active],
    )
    write_jsonl(
        tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", []
    )
    alias_report = tmp_path / "payload_aliases.json"
    alias_report.write_text(
        json.dumps(
            {
                "duplicate_groups": [
                    {
                        "canonical_doc_id": "canonical_doc",
                        "doc_ids": ["canonical_doc", "legacy_alias"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    duplicate = microtext("shifted_duplicate", [100, 300, 180, 340])
    duplicate.update(
        {
            "doc_id": "legacy_alias",
            "proposed_text": "mk  01 s",
            "category": "equipment_tag",
        }
    )
    distinct = microtext("distinct", [300, 300, 380, 340])
    distinct.update(
        {
            "doc_id": "legacy_alias",
            "proposed_text": "MK 02 S",
            "category": "equipment_tag",
        }
    )

    kept, held, report = sanitize_cohort(
        tmp_path, [duplicate, distinct], payload_alias_report=alias_report
    )

    assert [row["candidate_id"] for row in kept] == ["distinct"]
    assert [row["candidate_id"] for row in held] == ["shifted_duplicate"]
    assert report["hold_reasons"] == {
        "active_gold_payload_alias_text_category_overlap": 1
    }
