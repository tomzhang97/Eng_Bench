import unittest

from tools.merge_machine_ocr_caches import merge_caches


class MergeMachineOcrCachesTest(unittest.TestCase):
    def test_latest_result_replaces_prior_evidence_result(self) -> None:
        key = "a" * 64
        base = {"evidence_sha256": key, "ocr_text": "PI", "ocr_confidence": 0.9}
        rescue = {"evidence_sha256": key, "ocr_text": "P1", "ocr_confidence": 0.99}

        rows, counts = merge_caches([("base", [base]), ("rescue", [rescue])])

        self.assertEqual([rescue], rows)
        self.assertEqual(2, counts["input_rows"])
        self.assertEqual(1, counts["output_rows"])
        self.assertEqual(1, counts["replacement_rows"])

    def test_rejects_missing_evidence_hash(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid evidence_sha256"):
            merge_caches([("bad", [{"ocr_text": "P1"}])])


if __name__ == "__main__":
    unittest.main()
