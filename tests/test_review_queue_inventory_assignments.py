import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "review_queue_inventory_assignments",
        TOOLS / "review_queue_inventory.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class ReviewQueueInventoryAssignmentTests(unittest.TestCase):
    def test_default_assignment_discovery_excludes_current_handoffs(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "microtext/annotations/microtext_review_demo.jsonl",
                [
                    {"candidate_id": "in_pack", "review_status": "needs_review"},
                    {"candidate_id": "in_primary", "review_status": "needs_review"},
                    {"candidate_id": "fresh", "review_status": "needs_review"},
                ],
            )
            write_jsonl(
                root / "derived/review_packs/demo/manifest.jsonl",
                [{"candidate_id": "in_pack"}],
            )
            write_jsonl(
                root
                / "derived/quality/primary_continuation_controller_latest/processed_primary/current_assignment_capacity.jsonl",
                [{"candidate_id": "in_primary", "task": "microtext"}],
            )

            report = mod.inventory(root)
            item = report["files"][0]

            self.assertEqual(report["additional_assignment_row_keys"], 2)
            self.assertEqual(item["assigned_open_rows"], 2)
            self.assertEqual(item["fresh_open_rows"], 1)
            self.assertEqual(item["actionable_fresh_open_rows"], 1)

    def test_explicit_assignment_glob_fails_closed_when_empty(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                mod.inventory(
                    Path(temp_dir),
                    assignment_globs=["derived/missing/**/*.jsonl"],
                )


if __name__ == "__main__":
    unittest.main()
