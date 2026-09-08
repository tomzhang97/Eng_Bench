import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build_next_review_batch.py"


def load_module():
    tools_path = str(ROOT / "tools")
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    spec = importlib.util.spec_from_file_location("build_next_review_batch_docs", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildNextReviewBatchDocsUnittest(unittest.TestCase):
    def test_human_steps_explain_visualdiff_identical_shift_and_object_move_rules(self) -> None:
        module = load_module()
        rows = [
            {
                "pack_name": "visualdiff_demo_pack",
                "rows": 7,
                "checklist": "visualdiff_demo_validation_checklist.csv",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "packet"
            module.write_batch_docs(out_dir, rows, "2026-07-04")

            steps = (out_dir / "HUMAN_REVIEW_STEPS.md").read_text(encoding="utf-8")

        self.assertIn("old/new crops are visually identical", steps)
        self.assertIn("reject_unclear", steps)
        self.assertIn("whole-crop/page alignment shift", steps)
        self.assertIn("Use `edit`, not `layout`", steps)
        self.assertIn("specific object, label, symbol, wire, table cell", steps)


if __name__ == "__main__":
    unittest.main()
