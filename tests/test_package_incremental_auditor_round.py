import tempfile
import unittest
from pathlib import Path

from tools import package_incremental_auditor_round as package


class PackageIncrementalAuditorRoundTests(unittest.TestCase):
    def test_ensure_child_accepts_nested_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "a" / "b"
            package.ensure_child(nested, root)

    def test_ensure_child_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "allowed"
            with self.assertRaises(ValueError):
                package.ensure_child(root.parent / "outside", root)

    def test_package_accepts_configurable_auditor_count(self):
        self.assertIn("expected_auditor_count", package.package.__annotations__)

    def test_guide_terms_are_derived_from_payload(self):
        terms = package.guide_required_terms(
            {
                "advanced_auditors": [1, 2, 12],
                "preserved_auditors": [6, 11],
            }
        )
        self.assertIn("01、02、12", terms)
        self.assertIn("保持不变的复核员是 06、11", terms)
        self.assertNotIn("05 和 10", terms)

    def test_continuation_prefill_expectations_are_filename_scoped(self):
        expectations = package.continuation_prefill_expectations(
            {
                "allowed_prefilled_workbooks": {
                    "AUDITOR_11_ROUND1_REVIEW_12.xlsx": 11,
                }
            },
            {"AUDITOR_11_ROUND1_REVIEW_12.xlsx"},
        )
        self.assertEqual(
            expectations, {"AUDITOR_11_ROUND1_REVIEW_12.xlsx": 11}
        )

    def test_continuation_prefill_expectations_reject_unknown_workbook(self):
        with self.assertRaises(ValueError):
            package.continuation_prefill_expectations(
                {"allowed_prefilled_workbooks": {"unknown.xlsx": 11}},
                {"AUDITOR_11_ROUND1_REVIEW_12.xlsx"},
            )

    def test_continuation_prefill_expectations_require_partial_count(self):
        for count in (0, 12):
            with self.subTest(count=count), self.assertRaises(ValueError):
                package.continuation_prefill_expectations(
                    {
                        "allowed_prefilled_workbooks": {
                            "AUDITOR_11_ROUND1_REVIEW_12.xlsx": count,
                        }
                    },
                    {"AUDITOR_11_ROUND1_REVIEW_12.xlsx"},
                )

    def test_continuation_prefill_expectations_support_extended_workbooks(self):
        payload = {
            "auditors": [
                {
                    "workbook": "AUDITOR_11_CURRENT_REVIEW_24.xlsx",
                    "rows": [{} for _ in range(24)],
                }
            ],
            "allowed_prefilled_workbooks": {
                "AUDITOR_11_CURRENT_REVIEW_24.xlsx": 11,
            },
        }
        self.assertEqual(
            package.continuation_prefill_expectations(
                payload, {"AUDITOR_11_CURRENT_REVIEW_24.xlsx"}
            ),
            {"AUDITOR_11_CURRENT_REVIEW_24.xlsx": 11},
        )
        payload["allowed_prefilled_workbooks"][
            "AUDITOR_11_CURRENT_REVIEW_24.xlsx"
        ] = 24
        with self.assertRaises(ValueError):
            package.continuation_prefill_expectations(
                payload, {"AUDITOR_11_CURRENT_REVIEW_24.xlsx"}
            )


if __name__ == "__main__":
    unittest.main()
