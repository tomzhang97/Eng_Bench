import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_visualdiff_release_descriptions import ACTIVE_PATHS, build_queue


class VisualDiffReleaseDescriptionAuditTest(unittest.TestCase):
    def make_root(self, root: Path) -> Path:
        for name in ACTIVE_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return root / "visualdiff/annotations/visualdiff_pairs.jsonl"

    def test_builds_actionable_read_only_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pairs = self.make_root(root)
            rows = [
                {"pair_id": "todo", "split": "train", "change_desc_gt": "TODO"},
                {
                    "pair_id": "visual",
                    "split": "test",
                    "change_desc_gt": "Highlighted visual content changed.",
                    "desc_source": "codex_assisted_visual_review",
                },
                {
                    "pair_id": "good",
                    "split": "dev",
                    "change_desc_gt": "The resistor value changed from 10k to 20k.",
                    "desc_source": "human",
                },
            ]
            pairs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            before = pairs.read_bytes()
            report = build_queue(root, root / "derived/quality/queue")
            self.assertEqual("OPEN", report["status"])
            self.assertEqual(2, report["nonfinal_rows"])
            self.assertEqual(1, report["release_final_rows"])
            self.assertEqual({"placeholder": 1, "unvalidated_machine_visual": 1}, report["by_reason"])
            self.assertEqual({"conditional": 1, "yes": 1}, report["human_requirement"])
            self.assertFalse(report["active_gold_modified"])
            self.assertEqual(before, pairs.read_bytes())
            queue = [
                json.loads(line)
                for line in (root / report["queue_jsonl"]).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual("visual", queue[0]["pair_id"])
            self.assertEqual("human_semantic_confirmation", queue[0]["machine_lane"])

    def test_output_must_be_new_and_under_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_root(root)
            with self.assertRaises(ValueError):
                build_queue(root, root / "derived/not-quality/queue")
            build_queue(root, root / "derived/quality/queue")
            with self.assertRaises(ValueError):
                build_queue(root, root / "derived/quality/queue")


if __name__ == "__main__":
    unittest.main()
