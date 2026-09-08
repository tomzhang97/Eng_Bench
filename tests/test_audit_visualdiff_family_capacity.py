import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_visualdiff_family_capacity import build_report


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class VisualDiffFamilyCapacityTest(unittest.TestCase):
    def test_counts_distinct_union_and_cohort_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [{"pair_id": "vdiff__active__v1__to__v2__000"}])
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            write_jsonl(
                first,
                [
                    {"pair_id": "vdiff__new_a__v1__to__v2__000"},
                    {"pair_id": "vdiff__new_b__v1__to__v2__000"},
                ],
            )
            write_jsonl(
                second,
                [
                    {"pair_id": "vdiff__new_b__v1__to__v2__001"},
                    {"pair_id": "vdiff__new_c__v1__to__v2__000"},
                ],
            )

            report = build_report(root, [("first", first), ("second", second)], target_families=4)

            self.assertEqual(report["totals"]["active_gold_families"], 1)
            self.assertEqual(report["totals"]["distinct_staged_new_families"], 3)
            self.assertEqual(report["totals"]["staged_family_buffer_above_gap"], 0)
            self.assertEqual(report["pairwise_overlaps"][0]["overlap_families"], 1)


if __name__ == "__main__":
    unittest.main()
