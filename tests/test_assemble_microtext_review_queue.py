import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from assemble_microtext_review_queue import build_report, partition_rows


class AssembleMicrotextReviewQueueTest(unittest.TestCase):
    def _fixture(
        self,
        duplicate: bool = False,
        blocked: bool = False,
        manifest_candidate_id: str = "candidate_a",
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "microtext" / "annotations").mkdir(parents=True)
        (root / "derived" / "pages_300dpi" / "doc_a").mkdir(parents=True)
        (root / "source.bin").write_bytes(b"source")
        sha256 = hashlib.sha256(b"source").hexdigest()
        Image.new("RGB", (30, 30), "white").save(
            root / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
        )
        manifest = {
            "type": "doc",
            "doc_id": "doc_a",
            "path": "source.bin",
            "sha256": sha256,
            "source_url": "https://example.test/source",
            "public_status": "public_domain",
        }
        if manifest_candidate_id:
            manifest["source_candidate_id"] = manifest_candidate_id
        (root / "manifest.jsonl").write_text(
            json.dumps(manifest)
            + "\n",
            encoding="utf-8",
        )
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["doc_id", "public_status", "source_url"])
            writer.writeheader()
            writer.writerow(
                {
                    "doc_id": "doc_a",
                    "public_status": "rights_uncertain" if blocked else "public_domain",
                    "source_url": "https://example.test/source",
                }
            )
        (root / "microtext" / "annotations" / "microtext_items.jsonl").write_text("", encoding="utf-8")
        row = {
            "candidate_id": "candidate_001",
            "doc_id": "doc_a",
            "page_index": 0,
            "bbox": [1, 1, 10, 10],
            "proposed_text": "ROOM 1",
            "category": "room_label",
            "review_status": "needs_review",
            "image_path": "derived/pages_300dpi/doc_a/page_000.png",
        }
        input_path = root / "input.jsonl"
        input_rows = [row, dict(row)] if duplicate else [row]
        input_path.write_text("".join(json.dumps(value) + "\n" for value in input_rows), encoding="utf-8")
        return temp, root, input_path

    def test_valid_queue_inherits_source_candidate_lineage(self) -> None:
        temp, root, input_path = self._fixture()
        self.addCleanup(temp.cleanup)

        rows, report = build_report(root, [input_path], "2026-07-31")

        self.assertTrue(report["passes"])
        self.assertEqual(rows[0]["source_candidate_id"], "candidate_a")
        self.assertEqual(report["totals"]["paper_ready_source_documents"], 1)

    def test_duplicates_and_rights_block_queue(self) -> None:
        temp, root, input_path = self._fixture(duplicate=True, blocked=True)
        self.addCleanup(temp.cleanup)

        _rows, report = build_report(root, [input_path], "2026-07-31")

        self.assertFalse(report["passes"])
        self.assertEqual(report["issue_counts"]["duplicate_candidate_id"], 2)
        self.assertIn("rights_blocked:rights_uncertain", report["documents"][0]["issues"])

    def test_unique_release_candidate_mapping_recovers_missing_manifest_lineage(self) -> None:
        temp, root, input_path = self._fixture(manifest_candidate_id="")
        self.addCleanup(temp.cleanup)
        with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["candidate_id", "release_posture", "linked_local_doc_ids"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "candidate_id": "candidate_from_ledger",
                    "release_posture": "release_candidate",
                    "linked_local_doc_ids": "doc_a;doc_b",
                }
            )

        rows, report = build_report(root, [input_path], "2026-08-04")

        self.assertTrue(report["passes"])
        self.assertEqual(rows[0]["source_candidate_id"], "candidate_from_ledger")
        self.assertEqual(report["totals"]["validation_ledger_lineage_rows"], 1)
        self.assertTrue(report["rows"][0]["source_candidate_lineage_recovered"])
        self.assertEqual(
            report["documents"][0]["source_candidate_id_origin"],
            "validation_ledger_unique_release_candidate",
        )

    def test_ambiguous_release_candidate_mapping_remains_blocked(self) -> None:
        temp, root, input_path = self._fixture(manifest_candidate_id="")
        self.addCleanup(temp.cleanup)
        with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["candidate_id", "release_posture", "linked_local_doc_ids"],
            )
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "candidate_id": "candidate_one",
                        "release_posture": "release_candidate",
                        "linked_local_doc_ids": "doc_a",
                    },
                    {
                        "candidate_id": "candidate_two",
                        "release_posture": "release_candidate",
                        "linked_local_doc_ids": "doc_a",
                    },
                ]
            )

        rows, report = build_report(root, [input_path], "2026-08-04")

        self.assertFalse(report["passes"])
        self.assertNotIn("source_candidate_id", rows[0])
        self.assertIn(
            "ambiguous_source_candidate_mapping", report["documents"][0]["issues"]
        )
        self.assertEqual(report["issue_counts"]["source_not_paper_ready"], 1)
        self.assertEqual(report["issue_counts"]["missing_source_candidate_id"], 1)

    def test_partition_rows_preserves_passing_and_blocked_order(self) -> None:
        rows = [{"candidate_id": "one"}, {"candidate_id": "two"}, {"candidate_id": "three"}]
        report = {
            "rows": [
                {"passes": True},
                {"passes": False, "issues": ["region_collides_with_gold"]},
                {"passes": True},
            ]
        }

        passing, blocked = partition_rows(rows, report)

        self.assertEqual([row["candidate_id"] for row in passing], ["one", "three"])
        self.assertEqual([row["candidate_id"] for row in blocked], ["two"])
        self.assertEqual(blocked[0]["review_status"], "machine_held")
        self.assertEqual(blocked[0]["machine_hold_reason"], "region_collides_with_gold")

    def test_blocks_canonical_question_from_a_different_category(self) -> None:
        temp, root, input_path = self._fixture()
        self.addCleanup(temp.cleanup)
        row = json.loads(input_path.read_text(encoding="utf-8"))
        row["category"] = "component_value"
        row["question_text"] = "What dimension value is shown in this region?"
        input_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        _rows, report = build_report(root, [input_path], "2026-08-09")

        self.assertFalse(report["passes"])
        self.assertEqual(report["issue_counts"]["question_category_mismatch"], 1)
        self.assertIn("question_category_mismatch", report["rows"][0]["issues"])


if __name__ == "__main__":
    unittest.main()
