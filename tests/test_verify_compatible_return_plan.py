from __future__ import annotations

import json
from pathlib import Path

from tools import verify_compatible_return_plan


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_verify_return_plan_accepts_safe_existing_inputs(tmp_path: Path) -> None:
    write(tmp_path / "tools" / "review_packet_status.py")
    write(tmp_path / "tools" / "process_next_review_batch_return.py")
    write(tmp_path / "derived" / "human_adjudication" / "returned" / "PACKET_WORKLIST.csv")
    write(tmp_path / "derived" / "human_adjudication" / "returned" / "01_packets" / "p02" / "NEXT_REVIEW_BATCH_MANIFEST.csv")
    plan = {
        "totals": {"uncovered_checklists": 0},
        "steps": [
            {
                "kind": "preflight",
                "packet": "all",
                "command": (
                    'python tools\\review_packet_status.py --handoff-root '
                    '"derived\\human_adjudication\\returned" --output-json '
                    '"derived\\quality\\returned.json" --output-md "derived\\quality\\returned.md"'
                ),
            },
            {
                "kind": "process_next_review_batch",
                "packet": "p02",
                "command": (
                    'python tools\\process_next_review_batch_return.py --root . --batch-root '
                    '"derived\\human_adjudication\\returned\\01_packets\\p02" --output-dir '
                    '"derived\\human_adjudication\\processed_returns\\r\\p02" --strict'
                ),
            },
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = verify_compatible_return_plan.verify_plan(tmp_path, plan_path)

    assert report["valid"]
    assert report["issues"] == []
    assert report["totals"]["steps"] == 2
    assert report["totals"]["checked_input_paths"] == 2
    assert report["totals"]["checked_output_paths"] == 3


def test_verify_return_plan_rejects_missing_input_and_unsafe_output(tmp_path: Path) -> None:
    write(tmp_path / "tools" / "microtext_merge.py")
    plan = {
        "totals": {"uncovered_checklists": 1},
        "steps": [
            {
                "kind": "unsafe",
                "packet": "p01",
                "command": (
                    'python tools\\microtext_merge.py --review-jsonl '
                    '"missing.jsonl" --output "microtext\\annotations\\gold.jsonl"'
                ),
            }
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = verify_compatible_return_plan.verify_plan(tmp_path, plan_path)

    assert not report["valid"]
    joined = "\n".join(report["issues"])
    assert "uncovered_checklists" in joined
    assert "blocked merge-like command" in joined
    assert "missing input" in joined
    assert "unsafe output" in joined


def test_verify_return_plan_checks_agreement_audit_inputs_and_outputs(tmp_path: Path) -> None:
    write(tmp_path / "tools" / "agreement_audit.py")
    agreement = tmp_path / "derived" / "human_adjudication" / "returned" / "02_agreement"
    write(agreement / "sample_reference.csv", "id\n1\n")
    write(agreement / "reviewer_a_checklist.csv", "id\n1\n")
    write(agreement / "reviewer_b_checklist.csv", "id\n1\n")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "totals": {"uncovered_checklists": 0},
                "steps": [
                    {
                        "kind": "agreement_audit",
                        "packet": "agreement",
                        "command": (
                            'python tools\\agreement_audit.py --reference '
                            '"derived\\human_adjudication\\returned\\02_agreement\\sample_reference.csv" '
                            '--reviewer-a '
                            '"derived\\human_adjudication\\returned\\02_agreement\\reviewer_a_checklist.csv" '
                            '--reviewer-b '
                            '"derived\\human_adjudication\\returned\\02_agreement\\reviewer_b_checklist.csv" '
                            '--output-json "derived\\human_adjudication\\processed_returns\\r\\agreement\\agreement_report.json" '
                            '--output-md "derived\\human_adjudication\\processed_returns\\r\\agreement\\agreement_report.md" '
                            '--adjudication-csv "derived\\human_adjudication\\processed_returns\\r\\agreement\\adjudication_queue.csv" '
                            '--adjudication-md "derived\\human_adjudication\\processed_returns\\r\\agreement\\adjudication_queue.md"'
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = verify_compatible_return_plan.verify_plan(tmp_path, plan_path)

    assert report["valid"]
    assert report["totals"]["checked_input_paths"] == 3
    assert report["totals"]["checked_output_paths"] == 4


def test_verify_return_plan_cli_writes_report(tmp_path: Path) -> None:
    write(tmp_path / "tools" / "review_packet_status.py")
    write(tmp_path / "derived" / "human_adjudication" / "returned" / "PACKET_WORKLIST.csv")
    plan_path = tmp_path / "plan.json"
    out_json = tmp_path / "report.json"
    plan_path.write_text(
        json.dumps(
            {
                "totals": {"uncovered_checklists": 0},
                "steps": [
                    {
                        "kind": "preflight",
                        "packet": "all",
                        "command": (
                            'python tools\\review_packet_status.py --handoff-root '
                            '"derived\\human_adjudication\\returned" --output-json '
                            '"derived\\quality\\returned.json"'
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = verify_compatible_return_plan.main(
        ["--root", str(tmp_path), "--plan", str(plan_path), "--output-json", str(out_json)]
    )

    assert exit_code == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["valid"] is True
