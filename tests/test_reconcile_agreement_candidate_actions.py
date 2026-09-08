from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "reconcile_agreement_candidate_actions",
        ROOT / "tools" / "reconcile_agreement_candidate_actions.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract_row(row_id: str, channel: str) -> dict:
    return {
        "record_id": row_id,
        "task": "visualdiff",
        "agreement_channel": channel,
        "safe_to_merge_gold": False,
    }


def completion_row(
    row_id: str, screen: bool, primary: bool, pair_pass: bool = False
) -> dict:
    return {
        "record_id": row_id,
        "candidate_screen_complete": screen,
        "primary_promotion_review_complete": primary,
        "candidate_pair_pass": pair_pass,
    }


def test_reconciles_issued_unissued_and_primary_actions() -> None:
    mod = load_module()
    contract = [
        contract_row("done", "completed_independent_screen"),
        contract_row("issued", "pending_issued_independent_screen"),
        contract_row("new", "new_independent_screen_required"),
    ]
    completion = [
        completion_row("done", True, True, True),
        completion_row("issued", False, True),
        completion_row("new", False, False),
    ]
    issued = [
        {
            "record_id": "issued",
            "assignment_workbook": "AUDITOR_01.xlsx",
            "display_index": "3",
        }
    ]
    primary = [{"record_id": "new", "primary_index": 17}]

    report, artifacts = mod.reconcile_actions(
        contract, completion, issued, primary
    )

    assert report["valid"]
    assert report["candidate_dual_check_complete"] == 1
    assert report["candidate_pair_pass"] == 1
    assert report["candidate_pair_nonpass"] == 0
    assert report["screen_pending"] == 2
    assert report["screen_pending_already_issued"] == 1
    assert report["screen_pending_unissued"] == 1
    assert report["primary_pending"] == 1
    assert report["primary_pending_in_current_capacity"] == 1
    assert artifacts["screen_issued"][0]["issued_assignment_workbook"] == "AUDITOR_01.xlsx"
    assert artifacts["primary_pending"][0]["current_primary_index"] == 17
    assert all(not row["safe_to_merge_gold"] for row in artifacts["screen_pending"])


def test_missing_primary_capacity_fails_closed() -> None:
    mod = load_module()
    contract = [contract_row("missing", "completed_independent_screen")]
    completion = [completion_row("missing", True, False)]

    report, artifacts = mod.reconcile_actions(contract, completion, [], [])

    assert not report["valid"]
    assert "primary_pending_not_in_current_capacity:missing" in report["issues"]
    assert artifacts["primary_pending"][0]["current_primary_assignment_present"] is False


def test_returned_issued_screen_reduces_pending_without_failing() -> None:
    mod = load_module()
    contract = [contract_row("returned", "pending_issued_independent_screen"),
                contract_row("waiting", "pending_issued_independent_screen")]
    completion = [completion_row("returned", True, True, True),
                  completion_row("waiting", False, True)]
    issued = [{"record_id": "returned"}, {"record_id": "waiting"}]
    report, _ = mod.reconcile_actions(contract, completion, issued, [])
    assert report["valid"]
    assert report["screen_pending_already_issued"] == 1
    assert "1 still-pending" in report["next_actions"][0]


def test_newly_issued_supplemental_screen_is_no_longer_unissued() -> None:
    mod = load_module()
    contract = [contract_row("new", "new_independent_screen_required")]
    report, _ = mod.reconcile_actions(contract, [completion_row("new", False, True)],
                                      [{"record_id": "new"}], [])
    assert report["valid"]
    assert report["screen_pending_already_issued"] == 1
    assert report["screen_pending_unissued"] == 0


def test_missing_promised_pending_assignment_still_fails() -> None:
    mod = load_module()
    report, _ = mod.reconcile_actions(
        [contract_row("missing", "pending_issued_independent_screen")],
        [completion_row("missing", False, True)], [], [])
    assert not report["valid"]
    assert "pending_issued_screen_assignment_missing:missing" in report["issues"]
