import tempfile
import unittest
from pathlib import Path

from tools.finalize_historical_machine_certification import active_hashes


class HistoricalMachineFinalizationTests(unittest.TestCase):
    def test_active_hashes_covers_release_mutables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = {
                "visualdiff/annotations/visualdiff_pairs.jsonl",
                "visualdiff/annotations/visualdiff_questions.jsonl",
                "microtext/annotations/microtext_items.jsonl",
                "microtext/annotations/microtext_questions.jsonl",
                "eng_bench.jsonl",
            }
            for relative in expected:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            self.assertEqual(expected, set(active_hashes(root)))


if __name__ == "__main__":
    unittest.main()
