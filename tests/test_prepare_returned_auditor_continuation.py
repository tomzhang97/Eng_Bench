import unittest

from tools import prepare_returned_auditor_continuation as continuation


class PrepareReturnedAuditorContinuationTests(unittest.TestCase):
    def test_selects_balanced_unique_rows_without_previous_assignments(self):
        pool = []
        for task, split in continuation.STRATA:
            for index in range(8):
                row = {
                    "reserved_split": split,
                    "doc_id": f"doc-{split}-{index}",
                    "primary_pool_index": len(pool) + 1,
                }
                if task == "visualdiff":
                    row.update({"pair_id": f"pair-{index}", "project_id": f"family-{index}"})
                else:
                    row["candidate_id"] = f"candidate-{split}-{index}"
                pool.append(row)
        used = {"candidate-train-0", "pair-0"}

        selected = continuation.select_new_assignments(pool, used, [5, 10])

        self.assertEqual(set(selected), {5, 10})
        self.assertTrue(all(len(rows) == 12 for rows in selected.values()))
        identifiers = [
            continuation.incremental.identifier(row)
            for rows in selected.values()
            for row in rows
        ]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertFalse(set(identifiers) & used)

    def test_assignment_ids_collects_all_auditor_rows(self):
        payloads = [
            {"auditors": [{"rows": [{"candidate_id": "a"}, {"pair_id": "b"}]}]},
            {"auditors": [{"rows": [{"candidate_id": "c"}]}]},
        ]
        self.assertEqual(continuation.assignment_ids(payloads), {"a", "b", "c"})

    def test_guide_names_dynamic_advanced_and_preserved_auditors(self):
        auditors = [
            {"number": number, "workbook": f"auditor-{number:02d}.xlsx"}
            for number in range(1, 13)
        ]
        text = continuation.guide_text(
            auditors,
            [1, 2, 3, 4, 5, 7, 8, 9, 10, 12],
        )
        self.assertIn("01、02、03、04、05、07、08、09、10、12", text)
        self.assertIn("保持不变的复核员是 06、11", text)
        self.assertNotIn("其余 10 人", text)


if __name__ == "__main__":
    unittest.main()
