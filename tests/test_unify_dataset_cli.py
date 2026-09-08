import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import unify_dataset


ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class UnifyDatasetCliTest(unittest.TestCase):
    def test_write_jsonl_uses_canonical_utf8_sorted_lf_serialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "out.jsonl"

            unify_dataset.write_jsonl(output, [{"z": "°F", "a": 1}])

            self.assertEqual(b'{"a": 1, "z": "\xc2\xb0F"}\n', output.read_bytes())

    def test_help_does_not_write_active_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "unify_dataset.py"), "--help"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode)
            self.assertIn("--output", completed.stdout)
            self.assertFalse((Path(temp_dir) / "eng_bench.jsonl").exists())

    def test_plain_human_review_metadata_does_not_change_public_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc0",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "text_gt": "A1",
                        "category": "equipment_tag",
                        "split": "train",
                        "review_source": "human",
                        "human_reviewed": True,
                    }
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {
                        "question_id": "q0",
                        "item_id": "mt0",
                        "query_text": "Read the label.",
                        "answer_text": "A1",
                    }
                ],
            )

            metadata = unify_dataset.process_microtext(root)[0]["metadata"]

            self.assertNotIn("review_source", metadata)
            self.assertNotIn("human_reviewed", metadata)

    def test_machine_certification_metadata_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc0",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "text_gt": "1.25",
                        "category": "dimension_value",
                        "split": "train",
                        "review_source": "machine_certification_policy",
                        "human_reviewed": False,
                        "certification_method": "machine_verified",
                    }
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {
                        "question_id": "q0",
                        "item_id": "mt0",
                        "query_text": "Read the value.",
                        "answer_text": "1.25",
                    }
                ],
            )

            metadata = unify_dataset.process_microtext(root)[0]["metadata"]

            self.assertEqual("machine_certification_policy", metadata["review_source"])
            self.assertEqual("machine_verified", metadata["certification_method"])
            self.assertFalse(metadata["human_reviewed"])


if __name__ == "__main__":
    unittest.main()
