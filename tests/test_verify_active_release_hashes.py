from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.verify_active_release_hashes import build_report


class VerifyActiveReleaseHashesTests(unittest.TestCase):
    def test_accepts_matching_files_and_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b'{"id":"a"}\n'
            (root / "eng_bench.jsonl").write_bytes(payload)
            reference = root / "reference.json"
            reference.write_text(
                json.dumps(
                    {
                        "date_label": "fixture",
                        "active_gold_rows": 1,
                        "files": [
                            {
                                "path": "eng_bench.jsonl",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, reference)

            self.assertTrue(report["valid"])
            self.assertEqual([], report["issues"])
            self.assertTrue(report["files"][0]["matches"])

    def test_reports_hash_and_row_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "eng_bench.jsonl").write_text("a\nb\n", encoding="utf-8")
            reference = root / "reference.json"
            reference.write_text(
                json.dumps(
                    {
                        "active_gold_rows": 1,
                        "files": [{"path": "eng_bench.jsonl", "sha256": "0" * 64}],
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root, reference)

            self.assertFalse(report["valid"])
            self.assertIn("sha256_mismatch:eng_bench.jsonl", report["issues"])
            self.assertIn("active_gold_rows:2!=1", report["issues"])


if __name__ == "__main__":
    unittest.main()
