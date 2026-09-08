import unittest
from collections import Counter

from tools import prepare_paired_auditor_round as paired


class PreparePairedAuditorRoundTests(unittest.TestCase):
    def test_pair_schedule_is_balanced_and_broad(self) -> None:
        pairs = paired.pair_schedule()
        degrees = Counter(number for pair in pairs for number in pair)
        pair_counts = Counter(pairs)

        self.assertEqual(144, len(pairs))
        self.assertEqual({24}, set(degrees.values()))
        self.assertEqual(set(range(1, 13)), set(degrees))
        self.assertEqual(66, len(pair_counts))
        self.assertEqual(2, min(pair_counts.values()))
        self.assertEqual(3, max(pair_counts.values()))

    def test_target_quotas_trim_only_to_unique_target(self) -> None:
        rows = []
        for task, split, count in (
            ("microtext", "train", 41),
            ("microtext", "dev", 19),
            ("microtext", "test", 21),
            ("visualdiff", "train", 8),
            ("visualdiff", "dev", 8),
            ("visualdiff", "test", 50),
        ):
            rows.extend(
                {"task": task, "reserved_split": split} for _ in range(count)
            )

        quotas = paired.target_quotas(rows)

        self.assertEqual(144, sum(quotas.values()))
        self.assertEqual(47, quotas[("visualdiff", "test")])
        self.assertEqual(41, quotas[("microtext", "train")])

    def test_assign_pairs_replicates_each_row_twice(self) -> None:
        rows = [
            {
                "candidate_id": f"row_{index:03d}",
                "task": "microtext" if index % 2 else "visualdiff",
                "reserved_split": ("train", "dev", "test")[index % 3],
            }
            for index in range(144)
        ]

        assignments = paired.assign_pairs(rows)
        ids = Counter(
            paired.fresh.row_identifier(row)
            for auditor_rows in assignments.values()
            for row in auditor_rows
        )

        self.assertEqual(set(range(1, 13)), set(assignments))
        self.assertEqual({24}, {len(rows) for rows in assignments.values()})
        self.assertEqual({2}, set(ids.values()))
        for auditor_rows in assignments.values():
            self.assertEqual(24, len({paired.fresh.row_identifier(row) for row in auditor_rows}))


if __name__ == "__main__":
    unittest.main()
