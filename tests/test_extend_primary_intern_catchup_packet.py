import unittest

from tools.extend_primary_intern_catchup_packet import select_micro_extension


class ExtendPrimaryInternCatchupPacketTests(unittest.TestCase):
    def test_micro_extension_keeps_scarce_categories_and_balances_pin_sources(self) -> None:
        rows = [
            {
                "candidate_id": f"dimension-{index}",
                "doc_id": f"dimension-doc-{index % 2}",
                "category": "dimension_value",
            }
            for index in range(3)
        ]
        rows.extend(
            {
                "candidate_id": f"pin-{doc}-{index}",
                "doc_id": f"pin-doc-{doc}",
                "category": "pin_label",
            }
            for doc in range(4)
            for index in range(4)
        )

        selected = select_micro_extension(rows, 11)

        self.assertEqual(len(selected), 11)
        self.assertEqual(
            {row["candidate_id"] for row in selected if row["category"] == "dimension_value"},
            {"dimension-0", "dimension-1", "dimension-2"},
        )
        self.assertEqual(
            len({row["doc_id"] for row in selected if row["category"] == "pin_label"}),
            4,
        )


if __name__ == "__main__":
    unittest.main()
