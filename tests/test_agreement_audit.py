from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tools import agreement_audit


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def evidence(x0: int, y0: int, x1: int, y1: int) -> str:
    return json.dumps([{"bbox": [x0, y0, x1, y1], "image_index": 0}])


REFERENCE_FIELDS = [
    "id",
    "doc_id",
    "task",
    "stratum",
    "category",
    "question",
    "reference_answer",
    "reference_evidence_json",
    "primary_evidence_path",
    "page_path",
    "old_page_path",
    "new_page_path",
]

REVIEW_FIELDS = REFERENCE_FIELDS + [
    "answer_correct",
    "corrected_answer",
    "bbox_correct",
    "corrected_evidence_json",
    "accept_reject",
    "ambiguity",
    "rights_concern",
    "notes",
]


def reference_row(identifier: str, answer: str) -> dict[str, str]:
    return {
        "id": identifier,
        "doc_id": "doc_a",
        "task": "microtext",
        "stratum": "dev|microtext|dimension_value",
        "category": "dimension_value",
        "question": "Which value appears in the marked region?",
        "reference_answer": answer,
        "reference_evidence_json": evidence(10, 10, 40, 40),
        "primary_evidence_path": f"evidence/crops/{identifier}.png",
        "page_path": "evidence/pages/page_0000.png",
        "old_page_path": "",
        "new_page_path": "",
    }


def review_row(
    ref: dict[str, str],
    *,
    answer_correct: str = "yes",
    corrected_answer: str = "",
    bbox_correct: str = "yes",
    corrected_evidence_json: str = "",
    accept_reject: str = "accept",
    ambiguity: str = "no",
    rights_concern: str = "no",
    notes: str = "",
) -> dict[str, str]:
    row = dict(ref)
    row.update(
        {
            "answer_correct": answer_correct,
            "corrected_answer": corrected_answer,
            "bbox_correct": bbox_correct,
            "corrected_evidence_json": corrected_evidence_json,
            "accept_reject": accept_reject,
            "ambiguity": ambiguity,
            "rights_concern": rights_concern,
            "notes": notes,
        }
    )
    return row


def test_agreement_report_includes_actionable_adjudication_queue() -> None:
    references = [reference_row("q_clean", "12 mm"), reference_row("q_disagree", "18 mm")]
    reviewer_a = [
        review_row(references[0], notes="looks right"),
        review_row(references[1], notes="reference is correct"),
    ]
    reviewer_b = [
        review_row(references[0], notes="also right"),
        review_row(
            references[1],
            answer_correct="no",
            corrected_answer="16 mm",
            bbox_correct="no",
            corrected_evidence_json=evidence(80, 80, 100, 100),
            accept_reject="reject",
            notes="wrong callout",
        ),
    ]

    report = agreement_audit.compute_report(references, reviewer_a, reviewer_b)

    assert report["complete_rows"] == 2
    assert report["adjudication_rows"] == 1
    queued = report["adjudication_queue"][0]
    assert queued["id"] == "q_disagree"
    assert queued["task"] == "microtext"
    assert queued["a_answer"] == "18 mm"
    assert queued["b_answer"] == "16 mm"
    assert queued["primary_evidence_path"] == "evidence/crops/q_disagree.png"
    assert queued["recommended_action"] == "adjudicate_row"
    assert set(queued["reasons"].split(";")) == {
        "answer_disagreement",
        "evidence_disagreement",
        "accept_reject_disagreement",
        "reviewer_reject",
    }


def test_agreement_report_rejects_immutable_reviewer_tampering() -> None:
    references = [reference_row("q_clean", "12 mm")]
    reviewer_a = [review_row(references[0])]
    reviewer_b = [review_row(references[0])]
    reviewer_b[0]["reference_answer"] = "tampered"

    report = agreement_audit.compute_report(references, reviewer_a, reviewer_b)

    assert report["input_contract_valid"] is False
    assert report["complete_rows"] == 0
    assert report["agreement_gate_complete"] is False
    assert report["input_contract_issues"] == [
        "reviewer_b:q_clean:immutable_field_mismatch:reference_answer"
    ]


def test_agreement_report_rejects_duplicate_missing_and_reordered_rows() -> None:
    references = [reference_row("q_a", "12 mm"), reference_row("q_b", "18 mm")]
    reviewer_a = [review_row(references[1]), review_row(references[0])]
    reviewer_b = [review_row(references[0]), review_row(references[0])]

    report = agreement_audit.compute_report(references, reviewer_a, reviewer_b)

    assert report["input_contract_valid"] is False
    assert report["paired_rows"] == 0
    assert report["incomplete_rows"] == 2
    assert "reviewer_a:id_order_mismatch" in report["input_contract_issues"]
    assert "reviewer_b:duplicate_ids:q_a" in report["input_contract_issues"]
    assert "reviewer_b:missing_ids:q_b" in report["input_contract_issues"]
    assert "reviewer_b:id_order_mismatch" in report["input_contract_issues"]


def test_agreement_audit_cli_writes_adjudication_csv_and_markdown(tmp_path: Path) -> None:
    references = [reference_row("q_disagree", "18 mm")]
    reviewer_a = [review_row(references[0])]
    reviewer_b = [
        review_row(
            references[0],
            answer_correct="no",
            corrected_answer="16 mm",
            bbox_correct="no",
            corrected_evidence_json=evidence(80, 80, 100, 100),
            accept_reject="reject",
            ambiguity="yes",
            notes="wrong callout",
        )
    ]
    reference_path = tmp_path / "sample_reference.csv"
    reviewer_a_path = tmp_path / "reviewer_a_checklist.csv"
    reviewer_b_path = tmp_path / "reviewer_b_checklist.csv"
    out_json = tmp_path / "agreement_report.json"
    out_md = tmp_path / "agreement_report.md"
    out_csv = tmp_path / "adjudication_queue.csv"
    out_queue_md = tmp_path / "adjudication_queue.md"
    write_csv(reference_path, references, REFERENCE_FIELDS)
    write_csv(reviewer_a_path, reviewer_a, REVIEW_FIELDS)
    write_csv(reviewer_b_path, reviewer_b, REVIEW_FIELDS)

    exit_code = agreement_audit.main(
        [
            "--reference",
            str(reference_path),
            "--reviewer-a",
            str(reviewer_a_path),
            "--reviewer-b",
            str(reviewer_b_path),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--adjudication-csv",
            str(out_csv),
            "--adjudication-md",
            str(out_queue_md),
        ]
    )

    assert exit_code == 0
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["adjudication_rows"] == 1
    assert saved["inputs"]["reference_sha256"] == agreement_audit.file_sha256(
        reference_path
    )
    assert saved["inputs"]["reviewer_a_sha256"] == agreement_audit.file_sha256(
        reviewer_a_path
    )
    assert saved["inputs"]["reviewer_b_sha256"] == agreement_audit.file_sha256(
        reviewer_b_path
    )
    with out_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["id"] == "q_disagree"
    assert "reviewer_ambiguity" in rows[0]["reasons"]
    assert "q_disagree" in out_queue_md.read_text(encoding="utf-8")


def test_agreement_release_gate_uses_newest_processed_return_report(tmp_path: Path) -> None:
    old_report = tmp_path / "results" / "annotation" / "agreement_report.json"
    new_report = (
        tmp_path
        / "derived"
        / "human_adjudication"
        / "processed_returns"
        / "2026-06-15"
        / "agreement_audit"
        / "agreement_report.json"
    )
    old_report.parent.mkdir(parents=True, exist_ok=True)
    new_report.parent.mkdir(parents=True, exist_ok=True)
    old_report.write_text(
        json.dumps(
            {
                "reference_rows": 10,
                "complete_rows": 0,
                "incomplete_rows": 10,
                "agreement_gate_complete": False,
            }
        ),
        encoding="utf-8",
    )
    new_report.write_text(
        json.dumps(
            {
                "reference_rows": 10,
                "complete_rows": 10,
                "incomplete_rows": 0,
                "agreement_gate_complete": True,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    readiness = tmp_path / "derived" / "quality" / "agreement_sample_readiness.json"
    readiness.parent.mkdir(parents=True, exist_ok=True)
    readiness.write_text(
        json.dumps(
            {
                "reference_rows": 10,
                "release_ready_rows": 10,
                "non_release_ready_rows": 0,
                "fully_ready_rows": 10,
                "release_sample_ready": True,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    os.utime(old_report, (1000, 1000))
    os.utime(new_report, (2000, 2000))

    gate = agreement_audit.agreement_release_gate(tmp_path)

    assert gate["passes"] is True
    assert gate["current"] == "10/10 complete"
    assert gate["report_path"] == (
        "derived/human_adjudication/processed_returns/2026-06-15/"
        "agreement_audit/agreement_report.json"
    )
    assert gate["sample_release_ready"] is True


def test_agreement_release_gate_rejects_complete_non_release_safe_sample(
    tmp_path: Path,
) -> None:
    agreement_report = tmp_path / "results" / "annotation" / "agreement_report.json"
    agreement_report.parent.mkdir(parents=True, exist_ok=True)
    agreement_report.write_text(
        json.dumps(
            {
                "reference_rows": 10,
                "complete_rows": 10,
                "incomplete_rows": 0,
                "agreement_gate_complete": True,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    readiness = tmp_path / "derived" / "quality" / "agreement_sample_readiness.json"
    readiness.parent.mkdir(parents=True, exist_ok=True)
    readiness.write_text(
        json.dumps(
            {
                "reference_rows": 10,
                "release_ready_rows": 3,
                "non_release_ready_rows": 7,
                "fully_ready_rows": 10,
                "release_sample_ready": False,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )

    gate = agreement_audit.agreement_release_gate(tmp_path)

    assert gate["passes"] is False
    assert gate["sample_release_ready"] is False
    assert gate["non_release_ready_rows"] == 7
    assert gate["readiness_reason"] == "agreement sample is not release-ready"


def test_agreement_release_gate_reports_review_ready_sample_pending_reviewers(
    tmp_path: Path,
) -> None:
    agreement_report = tmp_path / "results" / "annotation" / "agreement_report.json"
    agreement_report.parent.mkdir(parents=True, exist_ok=True)
    agreement_report.write_text(
        json.dumps(
            {
                "reference_rows": 185,
                "complete_rows": 0,
                "incomplete_rows": 185,
                "agreement_gate_complete": False,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    readiness = tmp_path / "derived" / "quality" / "agreement_sample_readiness.json"
    readiness.parent.mkdir(parents=True, exist_ok=True)
    readiness.write_text(
        json.dumps(
            {
                "reference_rows": 185,
                "release_ready_rows": 185,
                "non_release_ready_rows": 0,
                "fully_ready_rows": 0,
                "sample_review_ready": True,
                "review_completion_pending": True,
                "release_sample_ready": False,
                "inputs": {"reference_sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )

    gate = agreement_audit.agreement_release_gate(tmp_path)

    assert gate["passes"] is False
    assert gate["sample_review_ready"] is True
    assert gate["sample_release_ready"] is False
    assert gate["readiness_reason"] == (
        "release-safe sample verified; reviewer completion pending"
    )
