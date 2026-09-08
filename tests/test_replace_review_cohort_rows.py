import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from replace_review_cohort_rows import build_replacement


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ReplaceReviewCohortRowsTest(unittest.TestCase):
    def test_replaces_held_row_and_preserves_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"paper-ready"
            source = root / "source.pdf"
            source.write_bytes(payload)
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "new_doc",
                        "path": "source.pdf",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "source_url": "https://example.test/source",
                        "public_status": "public_domain",
                    }
                ],
            )
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["doc_id", "source_path", "source_url", "public_status"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "new_doc",
                        "source_path": "source.pdf",
                        "source_url": "https://example.test/source",
                        "public_status": "public_domain",
                    }
                )
            write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [])
            original = root / "original.jsonl"
            replacements = root / "replacements.jsonl"
            write_jsonl(
                original,
                [
                    {"candidate_id": "keep", "doc_id": "keep_doc"},
                    {"candidate_id": "hold", "doc_id": "duplicate_doc"},
                ],
            )
            write_jsonl(replacements, [{"candidate_id": "new", "doc_id": "new_doc"}])
            output, remaining, held, report = build_replacement(
                root, original, replacements, {"duplicate_doc"}
            )
            self.assertTrue(report["valid"])
            self.assertEqual(len(output), 2)
            self.assertEqual(len(held), 1)
            self.assertEqual(remaining, [])
            self.assertEqual({row["candidate_id"] for row in output}, {"keep", "new"})


if __name__ == "__main__":
    unittest.main()
