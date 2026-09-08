import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "filter_microtext_review_against_cohorts.py"
SPEC = importlib.util.spec_from_file_location("filter_microtext_review_against_cohorts", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FilterMicrotextReviewAgainstCohortsTests(unittest.TestCase):
    def test_resolves_repeatable_exclude_globs_and_deduplicates_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "assignments" / "a.jsonl"
            second = root / "assignments" / "nested" / "b.jsonl"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            write_jsonl(first, [])
            write_jsonl(second, [])

            paths = MODULE.resolve_exclude_paths(
                root,
                [Path("assignments/a.jsonl")],
                ["assignments/**/*.jsonl"],
            )

            self.assertEqual(paths, [first.resolve(), second.resolve()])

    def test_exclude_glob_fails_closed_when_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                MODULE.resolve_exclude_paths(
                    Path(temporary),
                    [],
                    ["missing/**/*.jsonl"],
                )

    def test_blocks_different_candidate_id_on_same_physical_region(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [{"candidate_id": "new", "doc_id": "doc", "page_index": 2, "bbox": [1, 2, 3, 4]}],
            )
            write_jsonl(
                exclude,
                [{"candidate_id": "old", "doc_id": "doc", "page_index": 2, "bbox": [1, 2, 3, 4]}],
            )

            kept, held, report = MODULE.build_report(source, [exclude])

            self.assertEqual(kept, [])
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0]["machine_hold_reason"], "excluded_physical_region")
            self.assertEqual(report["held_reasons"], {"excluded_physical_region": 1})

    def test_blocks_candidate_id_even_when_region_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [{"candidate_id": "same", "doc_id": "doc", "page_index": 0, "bbox": [0, 0, 2, 2]}],
            )
            write_jsonl(
                exclude,
                [{"candidate_id": "same", "doc_id": "doc", "page_index": 1, "bbox": [4, 4, 6, 6]}],
            )

            kept, held, _report = MODULE.build_report(source, [exclude])

            self.assertEqual(kept, [])
            self.assertEqual(held[0]["machine_hold_reason"], "excluded_candidate_id")

    def test_blocks_pre_padding_alias_used_by_active_gold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "legacy-candidate",
                        "doc_id": "new-doc",
                        "page_index": 0,
                        "bbox": [0, 0, 2, 2],
                    }
                ],
            )
            write_jsonl(
                exclude,
                [
                    {
                        "item_id": "active-item",
                        "pre_padding_candidate_id": "legacy-candidate",
                        "doc_id": "active-doc",
                        "page_index": 1,
                        "bbox": [4, 4, 6, 6],
                    }
                ],
            )

            kept, held, report = MODULE.build_report(source, [exclude])

            self.assertEqual(kept, [])
            self.assertEqual(held[0]["machine_hold_reason"], "excluded_candidate_id")
            self.assertEqual(report["held_reasons"], {"excluded_candidate_id": 1})

    def test_blocks_mtcand_source_candidate_alias_used_by_active_gold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "mtcand__source-alias",
                        "doc_id": "new-doc",
                        "page_index": 0,
                        "bbox": [0, 0, 2, 2],
                    }
                ],
            )
            write_jsonl(
                exclude,
                [
                    {
                        "item_id": "active-item",
                        "source_candidate_id": "mtcand__source-alias",
                        "doc_id": "active-doc",
                        "page_index": 1,
                        "bbox": [4, 4, 6, 6],
                    }
                ],
            )

            kept, held, _report = MODULE.build_report(source, [exclude])

            self.assertEqual(kept, [])
            self.assertEqual(held[0]["machine_hold_reason"], "excluded_candidate_id")

    def test_near_region_filter_is_opt_in_and_matches_capacity_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "shifted",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [8, 0, 108, 40],
                    },
                    {
                        "candidate_id": "distinct",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [140, 0, 240, 40],
                    },
                ],
            )
            write_jsonl(
                exclude,
                [
                    {
                        "candidate_id": "old",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [0, 0, 100, 40],
                    }
                ],
            )

            default_kept, default_held, _default_report = MODULE.build_report(
                source, [exclude]
            )
            self.assertEqual(
                [row["candidate_id"] for row in default_kept], ["shifted", "distinct"]
            )
            self.assertEqual(default_held, [])

            kept, held, report = MODULE.build_report(
                source,
                [exclude],
                exclude_near_regions=True,
            )
            self.assertEqual([row["candidate_id"] for row in kept], ["distinct"])
            self.assertEqual(
                held[0]["machine_hold_reason"], "excluded_near_physical_region"
            )
            self.assertTrue(report["exclude_near_regions"])

    def test_near_region_filter_blocks_shifted_duplicates_within_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "first",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [0, 0, 100, 40],
                    },
                    {
                        "candidate_id": "shifted",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [8, 0, 108, 40],
                    },
                ],
            )

            kept, held, _report = MODULE.build_report(
                source,
                [],
                exclude_near_regions=True,
            )
            self.assertEqual([row["candidate_id"] for row in kept], ["first"])
            self.assertEqual(
                held[0]["machine_hold_reason"],
                "duplicate_input_near_physical_region",
            )

    def test_holds_duplicate_input_region_and_keeps_first_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            write_jsonl(
                source,
                [
                    {"candidate_id": "a", "doc_id": "doc", "page_index": 0, "bbox": [0, 0, 2, 2]},
                    {"candidate_id": "b", "doc_id": "doc", "page_index": 0, "bbox": [0, 0, 2, 2]},
                ],
            )

            kept, held, report = MODULE.build_report(source, [])

            self.assertEqual([row["candidate_id"] for row in kept], ["a"])
            self.assertEqual(kept[0]["cross_packet_filter_status"], "passing")
            self.assertEqual(held[0]["machine_hold_reason"], "duplicate_input_physical_region")
            self.assertEqual(report["totals"]["unique_kept_physical_regions"], 1)

    def test_holds_rows_without_stable_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            write_jsonl(source, [{"candidate_id": "", "doc_id": "doc"}, {"candidate_id": "a"}])

            kept, held, report = MODULE.build_report(source, [])

            self.assertEqual(kept, [])
            self.assertEqual(len(held), 2)
            self.assertEqual(report["held_reasons"]["missing_candidate_id"], 1)
            self.assertEqual(report["held_reasons"]["missing_physical_region"], 1)

    def test_reads_utf8_bom_jsonl(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "candidate_id": "a",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [0, 0, 2, 2],
                    }
                )
                + "\n",
                encoding="utf-8-sig",
            )

            kept, held, _report = MODULE.build_report(source, [])

            self.assertEqual([row["candidate_id"] for row in kept], ["a"])
            self.assertEqual(held, [])

    def test_reactivated_row_drops_stale_supersession_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "a",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [0, 0, 2, 2],
                        "review_status": "machine_superseded",
                        "superseded_by": "old.jsonl",
                        "machine_qa_status": "superseded",
                        "machine_qa_notes": "Do not review.",
                    }
                ],
            )

            kept, held, _report = MODULE.build_report(source, [])

            self.assertEqual(held, [])
            self.assertEqual(kept[0]["review_status"], "needs_review")
            self.assertNotIn("superseded_by", kept[0])
            self.assertNotIn("machine_qa_status", kept[0])
            self.assertNotIn("machine_qa_notes", kept[0])

    def test_payload_alias_filter_blocks_scaled_region_from_same_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            alias_report = root / "aliases.json"
            source_image = root / "derived" / "pages_300dpi" / "canonical" / "page_000.png"
            exclude_image = root / "derived" / "pages_300dpi" / "alias" / "page_000.png"
            source_image.parent.mkdir(parents=True)
            exclude_image.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(source_image)
            Image.new("RGB", (200, 100), "white").save(exclude_image)
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "new",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [100, 40, 200, 80],
                    }
                ],
            )
            write_jsonl(
                exclude,
                [
                    {
                        "candidate_id": "old",
                        "doc_id": "alias",
                        "page_index": 0,
                        "bbox": [50, 20, 100, 40],
                    }
                ],
            )
            alias_report.write_text(
                json.dumps(
                    {
                        "duplicate_groups": [
                            {
                                "canonical_doc_id": "canonical",
                                "doc_ids": ["canonical", "alias"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report = MODULE.build_report(
                source,
                [exclude],
                root=root,
                payload_alias_report=alias_report,
                exclude_payload_alias_regions=True,
            )

            self.assertEqual(kept, [])
            self.assertEqual(held[0]["machine_hold_reason"], "excluded_payload_alias_region")
            self.assertEqual(report["payload_alias_groups"], 1)
            self.assertEqual(report["totals"]["excluded_payload_alias_regions"], 1)

    def test_payload_alias_filter_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            exclude = root / "exclude.jsonl"
            write_jsonl(
                source,
                [{"candidate_id": "new", "doc_id": "canonical", "page_index": 0, "bbox": [2, 2, 4, 4]}],
            )
            write_jsonl(
                exclude,
                [{"candidate_id": "old", "doc_id": "alias", "page_index": 0, "bbox": [2, 2, 4, 4]}],
            )

            kept, held, report = MODULE.build_report(source, [exclude])

            self.assertEqual([row["candidate_id"] for row in kept], ["new"])
            self.assertEqual(held, [])
            self.assertFalse(report["exclude_payload_alias_regions"])


if __name__ == "__main__":
    unittest.main()
