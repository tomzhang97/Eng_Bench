from __future__ import annotations

import unittest
from pathlib import Path

from tools.regenerate_baseline_suite import (
    baseline_specs,
    command_for_generator,
    command_for_scorer,
)


class RegenerateBaselineSuiteTests(unittest.TestCase):
    def test_counted_suite_has_unique_names_and_expected_groups(self) -> None:
        specs = baseline_specs()

        self.assertEqual(len(specs), 29)
        self.assertEqual(len({spec.name for spec in specs}), 29)
        self.assertEqual({spec.group for spec in specs}, {"quick", "textlayer", "pixel"})

    def test_commands_target_canonical_prediction_and_report_paths(self) -> None:
        root = Path("C:/benchmark")
        spec = next(row for row in baseline_specs() if row.name == "weak_center_test")

        generator = command_for_generator(root, spec)
        scorer = command_for_scorer(root, spec, 200)

        self.assertIn("results/baselines/weak_center_test_predictions.jsonl", generator)
        self.assertIn(
            str(root / "results" / "baselines" / "weak_center_test_report.json"),
            scorer,
        )
        self.assertEqual(scorer[-1], "200")


if __name__ == "__main__":
    unittest.main()
