from __future__ import annotations

import unittest

from tools.build_agreement_visualdiff_bridge import diverse_visual_order


def row(pair_id: str, project_id: str) -> dict[str, str]:
    return {"pair_id": pair_id, "project_id": project_id}


class AgreementVisualDiffBridgeTests(unittest.TestCase):
    def test_diverse_order_uses_distinct_families_first(self) -> None:
        ordered = diverse_visual_order(
            [row("a2", "family-a"), row("a1", "family-a"), row("b1", "family-b")]
        )
        self.assertEqual([item["pair_id"] for item in ordered], ["a1", "b1", "a2"])

    def test_diverse_order_is_deterministic(self) -> None:
        rows = [row("b2", "family-b"), row("a2", "family-a"), row("a1", "family-a")]
        self.assertEqual(diverse_visual_order(rows), diverse_visual_order(reversed(rows)))

    def test_rows_without_family_are_excluded(self) -> None:
        ordered = diverse_visual_order([row("blank", ""), row("valid", "family-a")])
        self.assertEqual([item["pair_id"] for item in ordered], ["valid"])


if __name__ == "__main__":
    unittest.main()
