import unittest

from tools.prepare_primary_intern_catchup_packet import (
    MICRO_ENGINEERING_QUOTAS,
    round_robin,
    select_micro_engineering,
    select_visual_engineering,
)


class PrimaryInternCatchupPacketTests(unittest.TestCase):
    def test_round_robin_spreads_sources(self) -> None:
        rows = [
            {"candidate_id": "a1", "doc_id": "a"},
            {"candidate_id": "a2", "doc_id": "a"},
            {"candidate_id": "b1", "doc_id": "b"},
            {"candidate_id": "c1", "doc_id": "c"},
        ]
        selected = round_robin(rows, 3)
        self.assertEqual({row["doc_id"] for row in selected}, {"a", "b", "c"})

    def test_micro_engineering_quotas_are_exact(self) -> None:
        rows = []
        for category, quota in MICRO_ENGINEERING_QUOTAS.items():
            for index in range(quota):
                rows.append(
                    {
                        "candidate_id": f"{category}-{index}",
                        "doc_id": f"doc-{index % 5}",
                        "category": category,
                    }
                )
        selected = select_micro_engineering(rows)
        self.assertEqual(len(selected), 100)

    def test_visual_engineering_keeps_all_signal_rows(self) -> None:
        rows = []
        for index in range(120):
            row = {
                "pair_id": f"pair-{index}",
                "project_id": f"family-{index % 12}",
                "change_type": "text",
            }
            if index < 32:
                row["confidence"] = 0.35
            rows.append(row)
        selected = select_visual_engineering(rows)
        self.assertEqual(len(selected), 100)
        self.assertTrue({f"pair-{index}" for index in range(32)} <= set(selected))


if __name__ == "__main__":
    unittest.main()
