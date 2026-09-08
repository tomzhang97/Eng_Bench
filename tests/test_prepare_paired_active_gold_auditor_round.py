import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import prepare_paired_active_gold_auditor_round as active


class PreparePairedActiveGoldAuditorRoundTests(unittest.TestCase):
    def test_select_rows_balances_all_task_split_strata(self) -> None:
        rows = []
        for task, split in active.STRATA:
            for index in range(active.ROWS_PER_STRATUM + 3):
                rows.append(
                    {
                        "task": task,
                        "reserved_split": split,
                        "record_id": f"{task}_{split}_{index}",
                        "source_group": f"source_{index % 7}",
                        "evidence_sha256": f"hash_{task}_{split}_{index}",
                    }
                )
        selected = active.select_rows(rows)
        self.assertEqual(active.UNIQUE_ROWS, len(selected))
        for key in active.STRATA:
            self.assertEqual(
                active.ROWS_PER_STRATUM,
                sum((row["task"], row["reserved_split"]) == key for row in selected),
            )

    def test_select_rows_rejects_underfilled_stratum(self) -> None:
        rows = [
            {
                "task": task,
                "reserved_split": split,
                "record_id": f"{task}_{split}_{index}",
                "source_group": f"source_{index}",
                "evidence_sha256": f"hash_{task}_{split}_{index}",
            }
            for task, split in active.STRATA
            for index in range(active.ROWS_PER_STRATUM - (1 if (task, split) == active.STRATA[0] else 0))
        ]
        with self.assertRaisesRegex(ValueError, "lacks required strata"):
            active.select_rows(rows)

    def test_normalized_assignment_preserves_machine_identity_fields(self) -> None:
        row = {
            "primary_index": 17,
            "task": "microtext",
            "record_id": "mt_17",
            "candidate_id": "mt_17",
        }
        normalized = active.normalize_assignment(row, 3)
        self.assertEqual(17, normalized["primary_index"])
        self.assertEqual("3", normalized["display_index"])
        self.assertEqual("paired_active_gold_release_recheck", normalized["assignment_origin"])

    def test_tentative_visualdiff_rows_are_excluded_from_new_rounds(self) -> None:
        rows = [
            {
                "task": "visualdiff",
                "record_id": "vd_tentative",
                "change_description": 'Localized text may have been added: "FIXTURE_R23".',
            },
            {
                "task": "visualdiff",
                "record_id": "vd_final",
                "change_description": 'The engineering text "FIXTURE_R23" was added.',
            },
            {
                "task": "microtext",
                "record_id": "mt_same_words",
                "change_description": 'Localized text may have been added: "FIXTURE_R23".',
            },
        ]
        self.assertEqual(
            {"vd_tentative"},
            active.tentative_visualdiff_ids(rows),
        )

    def test_machine_known_description_debts_are_excluded_from_audit(self) -> None:
        rows = [
            {
                "task": "visualdiff",
                "record_id": "vd_todo",
                "change_description": "CHANGE_DESC_GT_TODO",
            },
            {
                "task": "visualdiff",
                "record_id": "vd_generic",
                "change_description": (
                    "Highlighted visual content changed from rev A to rev B."
                ),
            },
            {
                "task": "visualdiff",
                "record_id": "vd_generic_graphic",
                "change_description": (
                    "Highlighted graphic/symbol appearance changed from rev A to rev B."
                ),
                "desc_source": "codex_assisted_visual_review",
            },
            {
                "task": "visualdiff",
                "record_id": "vd_non_english",
                "change_description": "NEW 相比 OLD 删除了工程标签。",
            },
            {
                "task": "visualdiff",
                "record_id": "vd_final",
                "change_description": 'The engineering text "FIXTURE_R23" was added.',
            },
            {
                "task": "microtext",
                "record_id": "mt_todo",
                "change_description": "CHANGE_DESC_GT_TODO",
            },
        ]
        ids, reasons = active.machine_known_nonrelease_visualdiff(rows)
        self.assertEqual(
            {"vd_todo", "vd_generic", "vd_generic_graphic", "vd_non_english"},
            ids,
        )
        self.assertEqual(
            {
                "generic_machine_description": 1,
                "non_english": 1,
                "placeholder": 1,
                "unvalidated_machine_visual": 1,
            },
            dict(reasons),
        )
        self.assertIsNone(
            active.machine_known_description_issue(
                'The engineering text "FIXTURE_R23" was added.'
            )
        )

    def test_render_evidence_creates_microtext_and_visualdiff_panels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_dir = root / "images"
            image_dir.mkdir()
            for name, color in (("one.png", "white"), ("two.png", "lightgray")):
                Image.new("RGB", (300, 200), color).save(image_dir / name)
            micro = {
                "record_id": "mt_1",
                "task": "microtext",
                "images": ["images/one.png"],
                "benchmark_evidence": [{"image_index": 0, "bbox": [20, 30, 100, 80]}],
            }
            visual = {
                "record_id": "vd_1",
                "task": "visualdiff",
                "images": ["images/one.png", "images/two.png"],
                "benchmark_evidence": [
                    {"image_index": 0, "bbox": [20, 30, 100, 80]},
                    {"image_index": 1, "bbox": [25, 30, 105, 80]},
                ],
            }
            active.render_evidence(root, micro, root / "micro.png", 1_000_000)
            active.render_evidence(root, visual, root / "visual.png", 1_000_000)
            with Image.open(root / "micro.png") as rendered:
                self.assertEqual((1200, 440), rendered.size)
            with Image.open(root / "visual.png") as rendered:
                self.assertEqual((1200, 440), rendered.size)


if __name__ == "__main__":
    unittest.main()
