from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.build_agreement_pre_promotion_cohort import build_cohort


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class BuildAgreementPrePromotionCohortTest(unittest.TestCase):
    def fixture(self, *, fingerprint: str = "microtext:sha256:abc"):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "eng_bench.jsonl").write_text("{}\n", encoding="utf-8")
        ledger = root / "ledger.jsonl"
        micro = root / "micro.jsonl"
        visual = root / "visual.jsonl"
        write_jsonl(
            ledger,
            [
                {
                    "record_id": "candidate_1",
                    "task": "microtext",
                    "candidate_pair_pass": True,
                    "candidate_dual_check_complete": True,
                    "candidate_screen_decision_code": "1",
                    "candidate_screen_reviewer_id": "auditor_01",
                    "primary_decision_code": "1",
                    "reserved_split": "test",
                    "agreement_evidence_fingerprint": "microtext:sha256:abc",
                    "agreement_contract_index": 7,
                    "agreement_contract_sha256": "frozen",
                }
            ],
        )
        write_jsonl(
            micro,
            [
                {
                    "candidate_id": "candidate_1",
                    "task": "microtext",
                    "review_status": "accepted",
                    "reserved_split": "test",
                    "replacement_evidence_fingerprint": fingerprint,
                    "safe_to_merge_gold": False,
                }
            ],
        )
        write_jsonl(visual, [])
        return temp, root, ledger, micro, visual

    def test_binds_matching_double_pass_without_mutating_gold(self) -> None:
        temp, root, ledger, micro, visual = self.fixture()
        self.addCleanup(temp.cleanup)
        before = (root / "eng_bench.jsonl").read_bytes()

        report = build_cohort(
            root, ledger, micro, visual, root / "out", "fixture"
        )

        self.assertEqual(1, report["counts"]["ready_rows"])
        self.assertEqual(0, report["counts"]["held_rows"])
        self.assertFalse(report["active_gold_modified"])
        row = json.loads(
            (root / "out/agreement_pass_microtext_pending_gates.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertEqual("primary_plus_independent_candidate_screen", row["review_depth"])
        self.assertFalse(row["formal_detailed_agreement_complete"])
        self.assertFalse(row["safe_to_merge_gold"])
        self.assertEqual(before, (root / "eng_bench.jsonl").read_bytes())

    def test_holds_fingerprint_mismatch(self) -> None:
        temp, root, ledger, micro, visual = self.fixture(
            fingerprint="microtext:sha256:different"
        )
        self.addCleanup(temp.cleanup)

        report = build_cohort(
            root, ledger, micro, visual, root / "out", "fixture"
        )

        self.assertEqual(0, report["counts"]["ready_rows"])
        self.assertEqual(1, report["counts"]["held_rows"])
        self.assertEqual(1, report["hold_reason_counts"]["evidence_fingerprint_mismatch"])


if __name__ == "__main__":
    unittest.main()
