import json
import tempfile
import unittest
from pathlib import Path

from tools import summarize_source_atomic_provenance_slices as summary_tool


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class SummarizeSourceAtomicProvenanceSlicesTest(unittest.TestCase):
    def test_deduplicates_rows_and_prioritizes_smallest_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            slices = root / "derived" / "quality" / "slices"
            reports = [
                ("small", 2, ["shared", "small-only"]),
                ("large", 3, ["shared", "large-a", "large-b"]),
            ]
            for source, count, identities in reports:
                out = slices / source / "source_atomic_outstanding.jsonl"
                write_jsonl(out, [{"candidate_id": identity} for identity in identities])
                write_json(
                    slices / source / "source_atomic_slice_report.json",
                    {
                        "source_doc": source,
                        "active_source_references": count,
                        "affected_rows": count,
                        "selected_reviewed_replacements": 0,
                        "selected_outstanding_review_rows": count,
                        "unfillable_replacement_rows": 0,
                        "ready_for_migration_readiness_audit": False,
                        "artifacts": {
                            "outstanding": out.relative_to(root).as_posix(),
                        },
                    },
                )

            summary = summary_tool.build_summary(
                slices_dir=slices,
                output_dir=root / "out",
                date_label="fixture",
            )

            self.assertTrue(summary["valid"])
            self.assertEqual(4, summary["deduplicated_outstanding_review_rows"])
            queue = summary_tool.read_jsonl(
                root / "out" / "source_atomic_outstanding_deduplicated.jsonl"
            )
            self.assertEqual("shared", queue[0]["candidate_id"])
            self.assertEqual(["large", "small"], queue[0]["source_atomic_priority_docs"])
            self.assertEqual("small-only", queue[1]["candidate_id"])
            priority_csv = (root / "out" / "source_atomic_priority_rows.csv").read_text(
                encoding="utf-8-sig"
            )
            self.assertIn("priority_rank,candidate_id,primary_index", priority_csv)
            self.assertIn("1,shared,", priority_csv)


if __name__ == "__main__":
    unittest.main()
