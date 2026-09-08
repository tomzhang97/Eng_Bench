from __future__ import annotations

import unittest

from tools.probe_legacy_pid_line_ocr import build_probe, probe_reason


class ProbeLegacyPidLineOcrTests(unittest.TestCase):
    def test_probe_reason_accepts_guarded_legacy_forms(self) -> None:
        self.assertEqual(
            "compact_legacy_line_identifier",
            probe_reason("207-1/2-P"),
        )
        self.assertEqual(
            "compact_legacy_line_with_missing_size_probe",
            probe_reason("308--Q"),
        )
        self.assertEqual(
            "compact_legacy_line_with_probable_q_ocr_confusion",
            probe_reason("325-2-0"),
        )

    def test_probe_reason_rejects_loose_numeric_and_prose_forms(self) -> None:
        for value in ("1-2-3", "0-01-201", "BY-PASS - 20 GPM", "105-10-", "-2-P"):
            with self.subTest(value=value):
                self.assertIsNone(probe_reason(value))

    def test_opt_in_clipped_probe_keeps_incomplete_context_candidates(self) -> None:
        self.assertEqual(
            "clipped_compact_legacy_line_needs_context_probe",
            probe_reason("207-/-P", include_clipped=True),
        )
        self.assertEqual(
            "clipped_compact_legacy_line_needs_context_probe",
            probe_reason("105-10-", include_clipped=True),
        )
        self.assertIsNone(probe_reason("207-/-P"))

    def test_build_probe_preserves_unknown_category_and_holds_others(self) -> None:
        rows = [
            {"candidate_id": "a", "category": "unknown_microtext", "proposed_text": "325-2-0"},
            {"candidate_id": "b", "category": "unknown_microtext", "proposed_text": "1-2-3"},
            {"candidate_id": "c", "category": "pipe_line_tag", "proposed_text": "207-1/2-P"},
        ]

        selected, held, report = build_probe(rows)

        self.assertEqual(["a"], [row["candidate_id"] for row in selected])
        self.assertEqual("unknown_microtext", selected[0]["category"])
        self.assertFalse(selected[0]["safe_to_merge_gold"])
        self.assertEqual(2, len(held))
        self.assertEqual(1, report["selected_rows"])
        self.assertEqual(2, report["held_rows"])


if __name__ == "__main__":
    unittest.main()
