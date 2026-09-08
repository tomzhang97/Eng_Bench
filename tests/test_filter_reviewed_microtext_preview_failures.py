from tools.filter_reviewed_microtext_preview_failures import (
    collect_preview_failures,
    partition,
)


def reviewed(candidate_id: str) -> dict:
    return {"candidate_id": candidate_id, "task": "microtext"}


def unified(item_id: str, answer: str, question: str) -> dict:
    return {
        "id": f"q_{item_id}",
        "task": "microtext",
        "split": "train",
        "question": question,
        "answer": answer,
        "metadata": {"item_id": item_id},
    }


def test_collects_explicit_duplicate_and_leakage_holds() -> None:
    reviewed_rows = [reviewed("cand-a"), reviewed("cand-b"), reviewed("cand-c")]
    prepared = [
        {"item_id": "item-a", "source_candidate_id": "cand-a"},
        {"item_id": "item-b", "source_candidate_id": "cand-b"},
    ]
    unified_rows = [
        unified("active-item", "D5", "What pin label is shown?"),
        unified("item-a", "D5", "What pin label is shown?"),
        unified("item-b", "REG", "What REG tag is shown?"),
    ]
    preview_holds = [
        {"task": "microtext", "identity": "cand-c", "reasons": ["evidence_hold"]}
    ]

    reasons, errors = collect_preview_failures(
        reviewed_rows, prepared, unified_rows, preview_holds
    )
    ready, held, counts = partition(reviewed_rows, reasons)

    assert errors == []
    assert ready == []
    assert [row["candidate_id"] for row in held] == ["cand-a", "cand-b", "cand-c"]
    assert counts == {
        "strict_duplicate_qa_collision": 1,
        "question_answer_leakage": 1,
        "evidence_hold": 1,
    }


def test_reports_unaccounted_reviewed_row() -> None:
    reasons, errors = collect_preview_failures(
        [reviewed("cand-a")], [], [], []
    )
    assert reasons == {}
    assert errors == ["reviewed_row_unaccounted:cand-a"]
