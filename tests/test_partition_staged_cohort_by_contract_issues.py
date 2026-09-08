import csv
from pathlib import Path

from tools.partition_staged_cohort_by_contract_issues import fatal_findings, partition_rows


def row(candidate_id: str, x: int) -> dict:
    return {
        "task": "microtext",
        "candidate_id": candidate_id,
        "doc_id": "doc",
        "page": 0,
        "bbox": [x, 0, x + 10, 10],
    }


def test_partition_rows_holds_only_fatal_findings(tmp_path: Path) -> None:
    issues = tmp_path / "issues.csv"
    with issues.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["severity", "issue", "cohort", "task", "identity", "detail"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "severity": "fatal",
                "issue": "missing_reserved_split",
                "cohort": "x",
                "task": "microtext",
                "identity": "microtext-region:doc:p0:0,0,10,10",
                "detail": "",
            }
        )
        writer.writerow(
            {
                "severity": "warning",
                "issue": "review_recommended",
                "cohort": "x",
                "task": "microtext",
                "identity": "microtext:candidate-b",
                "detail": "",
            }
        )

    ready, held, reasons = partition_rows(
        [row("candidate-a", 0), row("candidate-b", 20)], fatal_findings(issues)
    )

    assert [item["candidate_id"] for item in ready] == ["candidate-b"]
    assert [item["candidate_id"] for item in held] == ["candidate-a"]
    assert held[0]["promotion_contract_hold_reasons"] == ["missing_reserved_split"]
    assert reasons == {"missing_reserved_split": 1}
    assert all(item["safe_to_merge_gold"] is False for item in ready + held)
