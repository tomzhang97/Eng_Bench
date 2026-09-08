from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_active_release_unchanged


class ActiveReleaseHashTest(unittest.TestCase):
    def test_detects_unchanged_and_changed_release_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "eng_bench.jsonl"
            active.write_text('{"id":"one"}\n', encoding="utf-8")
            digest = hashlib.sha256(active.read_bytes()).hexdigest().upper()
            reference = root / "reference.json"
            reference.write_text(
                json.dumps(
                    {
                        "date_label": "unit-test",
                        "active_gold_rows": 1,
                        "files": [{"path": "eng_bench.jsonl", "sha256": digest}],
                    }
                ),
                encoding="utf-8",
            )

            clean = verify_active_release_unchanged.verify(root, reference)
            active.write_text('{"id":"two"}\n', encoding="utf-8")
            changed = verify_active_release_unchanged.verify(root, reference)

            self.assertTrue(clean["valid"])
            self.assertFalse(changed["valid"])
            self.assertIn("hash mismatch", changed["issues"][0])


if __name__ == "__main__":
    unittest.main()
