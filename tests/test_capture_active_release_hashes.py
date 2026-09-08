from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.capture_active_release_hashes import ACTIVE_RELEASE_PATHS, build_reference


class CaptureActiveReleaseHashesTest(unittest.TestCase):
    def test_build_reference_captures_all_active_files_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, relative in enumerate(ACTIVE_RELEASE_PATHS):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = b'{"id": 1}\n{"id": 2}\n' if relative == "eng_bench.jsonl" else f"{index}\n".encode()
                path.write_bytes(payload)

            report = build_reference(root, "fixture", "derived/quality/apply.json")

            self.assertTrue(report["valid"])
            self.assertEqual(2, report["active_gold_rows"])
            self.assertEqual(len(ACTIVE_RELEASE_PATHS), len(report["files"]))
            self.assertEqual(
                hashlib.sha256(b"0\n").hexdigest().upper(),
                report["files"][0]["sha256"],
            )
            self.assertEqual("derived/quality/apply.json", report["source_transaction"])

    def test_missing_active_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileNotFoundError, "missing_active_release_file"):
                build_reference(Path(temp), "fixture")


if __name__ == "__main__":
    unittest.main()
