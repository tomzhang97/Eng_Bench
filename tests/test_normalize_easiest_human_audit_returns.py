from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import normalize_easiest_human_audit_returns as normalizer


class NormalizeEasiestHumanAuditReturnsTests(unittest.TestCase):
    def test_reviewer_role(self) -> None:
        self.assertEqual(
            normalizer.reviewer_role(Path("PRIMARY_REVIEW_498.xlsx")),
            ("primary", "primary_reviewer"),
        )
        self.assertEqual(
            normalizer.reviewer_role(Path("AUDITOR_07_REVIEW_12.xlsx")),
            ("auditor", "auditor_07"),
        )
        with self.assertRaises(ValueError):
            normalizer.reviewer_role(Path("renamed.xlsx"))

    def test_chinese_decisions_have_stable_machine_mappings(self) -> None:
        self.assertEqual(normalizer.PRIMARY_MICRO_MAP["正确"], "accepted")
        self.assertEqual(normalizer.PRIMARY_MICRO_MAP["需修改"], "edited")
        self.assertEqual(normalizer.AUDITOR_MICRO_MAP["有问题"], "issue")
        self.assertEqual(normalizer.VISUAL_MAP["无有效变化"], "reject_unclear")

    def test_ultrafast_codes_have_stable_machine_mappings(self) -> None:
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["1"], "accepted")
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["2"], "edited")
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["3"], "rejected")
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["4"], "needs_full_page")
        self.assertEqual(normalizer.ULTRA_VISUAL_MAP["1"], "edit")
        self.assertEqual(normalizer.ULTRA_VISUAL_MAP["2"], "reject_unclear")
        self.assertEqual(normalizer.ULTRA_AUDITOR_MAP["3"], "needs_full_page")
        # Preserve compatibility with already-started letter-coded workbooks.
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["A"], "accepted")
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["E"], "edited")
        self.assertEqual(normalizer.ULTRA_PRIMARY_MICRO_MAP["R"], "rejected")
        self.assertEqual(normalizer.ULTRA_VISUAL_MAP["Y"], "edit")
        self.assertEqual(normalizer.ULTRA_VISUAL_MAP["N"], "reject_unclear")
        self.assertEqual(normalizer.ULTRA_AUDITOR_MAP["U"], "needs_full_page")
        self.assertEqual(
            normalizer.ULTRA_VISUAL_SUGGESTION,
            "\u673a\u5668\u63d0\u793a\uff08\u53ea\u770b\u5bf9\u6bd4\u533a\u57df\uff09",
        )

    def test_simplest_primary_uses_one_meaning_for_all_four_keys(self) -> None:
        self.assertEqual(normalizer.SIMPLEST_PRIMARY_VISUAL_MAP["1"], "edit")
        self.assertEqual(normalizer.SIMPLEST_PRIMARY_VISUAL_MAP["2"], "edit")
        self.assertEqual(
            normalizer.SIMPLEST_PRIMARY_VISUAL_MAP["3"], "reject_unclear"
        )
        self.assertEqual(
            normalizer.SIMPLEST_PRIMARY_VISUAL_MAP["4"], "needs_full_page"
        )
        self.assertIn("1\u901a\u8fc7", normalizer.SIMPLEST_PRIMARY_DECISION)
        self.assertEqual(
            normalizer.SINGLE_PRIMARY_SUGGESTION_LEAN,
            "\u95ee\u9898 + \u673a\u5668\u5185\u5bb9\uff08\u52ff\u6539\uff09",
        )

    def test_iter_workbooks_sorts_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "AUDITOR_02_REVIEW_12.xlsx"
            second = root / "AUDITOR_01_REVIEW_12.xlsx"
            first.touch()
            second.touch()
            paths = normalizer.iter_workbooks([root, first])
            self.assertEqual([path.name for path in paths], [second.name, first.name])


if __name__ == "__main__":
    unittest.main()
