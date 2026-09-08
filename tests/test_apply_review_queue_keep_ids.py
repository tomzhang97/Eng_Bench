from __future__ import annotations

import json
from pathlib import Path

from tools import apply_review_queue_keep_ids


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_curation_keeps_selected_ids_and_holds_the_rest(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    keep_ids = tmp_path / "keep.txt"
    write_jsonl(
        queue,
        [
            {"candidate_id": "a", "doc_id": "doc-1"},
            {"candidate_id": "b", "doc_id": "doc-1"},
            {"candidate_id": "c", "doc_id": "doc-2"},
        ],
    )
    keep_ids.write_text("# reviewed contact sheet\nb\nc\n", encoding="utf-8")

    report = apply_review_queue_keep_ids.build_report(
        tmp_path,
        input_jsonl=queue,
        keep_ids_file=keep_ids,
        hold_reason="not_text",
    )

    assert [row["candidate_id"] for row in report["kept_rows"]] == ["b", "c"]
    assert [row["candidate_id"] for row in report["held_rows"]] == ["a"]
    assert report["held_rows"][0]["machine_hold_reason"] == "not_text"
    assert report["totals"] == {
        "input_rows": 3,
        "keep_ids": 2,
        "kept_rows": 2,
        "held_rows": 1,
    }


def test_curation_fails_when_keep_id_is_missing(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    keep_ids = tmp_path / "keep.txt"
    write_jsonl(queue, [{"candidate_id": "a", "doc_id": "doc-1"}])
    keep_ids.write_text("missing\n", encoding="utf-8")

    try:
        apply_review_queue_keep_ids.build_report(
            tmp_path,
            input_jsonl=queue,
            keep_ids_file=keep_ids,
            hold_reason="not_text",
        )
    except ValueError as exc:
        assert "missing from queue" in str(exc)
    else:
        raise AssertionError("missing keep ID should fail")


def test_curation_supports_visualdiff_pair_ids(tmp_path: Path) -> None:
    queue = tmp_path / "visualdiff.jsonl"
    keep_ids = tmp_path / "keep.txt"
    write_jsonl(
        queue,
        [
            {"pair_id": "vdiff-a", "project_id": "family-a"},
            {"pair_id": "vdiff-b", "project_id": "family-b"},
        ],
    )
    keep_ids.write_text("vdiff-b\n", encoding="utf-8")

    report = apply_review_queue_keep_ids.build_report(
        tmp_path,
        input_jsonl=queue,
        keep_ids_file=keep_ids,
        hold_reason="low_value_metadata_only",
    )

    assert [row["pair_id"] for row in report["kept_rows"]] == ["vdiff-b"]
    assert [row["pair_id"] for row in report["held_rows"]] == ["vdiff-a"]
    assert report["kept_by_doc"] == {"family-b": 1}


def test_curation_can_match_a_preserved_alias_field(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    keep_ids = tmp_path / "keep.txt"
    write_jsonl(
        queue,
        [
            {
                "candidate_id": "padded-a",
                "pre_padding_candidate_id": "original-a",
                "doc_id": "doc-1",
            },
            {
                "candidate_id": "padded-b",
                "pre_padding_candidate_id": "original-b",
                "doc_id": "doc-1",
            },
        ],
    )
    keep_ids.write_text("original-b\n", encoding="utf-8")

    report = apply_review_queue_keep_ids.build_report(
        tmp_path,
        input_jsonl=queue,
        keep_ids_file=keep_ids,
        hold_reason="not_selected_before_padding",
        row_id_field="pre_padding_candidate_id",
    )

    assert [row["candidate_id"] for row in report["kept_rows"]] == ["padded-b"]
    assert report["row_id_field"] == "pre_padding_candidate_id"
