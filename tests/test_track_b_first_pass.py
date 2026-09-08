import importlib.util
import csv
import hashlib
import json
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TrackBFirstPassTests(unittest.TestCase):
    def test_apply_split_policy_updates_visualdiff_and_microtext_splits(self):
        mod = load_module("apply_split_policy", ROOT / "tools" / "apply_split_policy.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "splits").mkdir()
            (tmp_path / "splits" / "visualdiff_train.txt").write_text("vdiff__bbb__C__to__C3\n")
            (tmp_path / "splits" / "visualdiff_dev.txt").write_text(
                "vdiff__viola__pcbV1.0__to__pcbV1.1\n"
            )
            (tmp_path / "splits" / "visualdiff_test.txt").write_text(
                "vdiff__viola__pcbV1.1__to__pcbV1.2\n"
            )
            (tmp_path / "splits" / "microtext_train.txt").write_text("# provisional empty\n")
            (tmp_path / "splits" / "microtext_dev.txt").write_text("tolerances_table_iso\n")
            (tmp_path / "splits" / "microtext_test.txt").write_text("# provisional empty\n")

            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "vdiff__bbb__C__to__C3__0000",
                        "project_id": "vdiff__bbb__C__to__C3",
                        "split": "test",
                    },
                    {
                        "pair_id": "vdiff__viola__pcbV1.0__to__pcbV1.1__0000",
                        "project_id": "vdiff__viola__pcbV1.0__to__pcbV1.1",
                        "split": "test",
                    },
                    {
                        "pair_id": "vdiff__viola__pcbV1.1__to__pcbV1.2__0000",
                        "project_id": "vdiff__viola__pcbV1.1__to__pcbV1.2",
                        "split": "train",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_questions.jsonl",
                [
                    {
                        "question_id": "q0",
                        "pair_id": "vdiff__bbb__C__to__C3__0000",
                        "split": "test",
                    },
                    {
                        "question_id": "q1",
                        "pair_id": "vdiff__viola__pcbV1.0__to__pcbV1.1__0000",
                        "split": "test",
                    },
                    {
                        "question_id": "q2",
                        "pair_id": "vdiff__viola__pcbV1.1__to__pcbV1.2__0000",
                        "split": "train",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "mt0", "doc_id": "tolerances_table_iso", "split": "test"}],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {
                        "question_id": "qm0",
                        "item_ids": ["mt0"],
                        "doc_id": "tolerances_table_iso",
                        "split": "test",
                    }
                ],
            )

            stats = mod.apply_split_policy(tmp_path)

            self.assertEqual(stats["visualdiff_pairs_by_split"], {"train": 1, "dev": 1, "test": 1})
            self.assertEqual(stats["microtext_items_by_split"], {"dev": 1})
            self.assertEqual(
                [
                    r["split"]
                    for r in read_jsonl(tmp_path / "visualdiff/annotations/visualdiff_pairs.jsonl")
                ],
                ["train", "dev", "test"],
            )
            self.assertEqual(
                read_jsonl(tmp_path / "microtext/annotations/microtext_items.jsonl")[0]["split"],
                "dev",
            )
            self.assertEqual(
                read_jsonl(tmp_path / "microtext/annotations/microtext_questions.jsonl")[0]["split"],
                "dev",
            )

    def test_leakage_check_detects_duplicate_families(self):
        mod = load_module("leakage_check", ROOT / "splits" / "leakage_check.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "splits").mkdir()
            (tmp_path / "splits" / "visualdiff_train.txt").write_text("family_a\n")
            (tmp_path / "splits" / "visualdiff_dev.txt").write_text("family_a\n")
            (tmp_path / "splits" / "visualdiff_test.txt").write_text("family_b\n")
            (tmp_path / "splits" / "microtext_train.txt").write_text("doc_a\n")
            (tmp_path / "splits" / "microtext_dev.txt").write_text("doc_b\n")
            (tmp_path / "splits" / "microtext_test.txt").write_text("doc_a\n")

            errors = mod.check_leakage(tmp_path)

            self.assertTrue(any("visualdiff" in err and "family_a" in err for err in errors))
            self.assertTrue(any("microtext" in err and "doc_a" in err for err in errors))

    def test_v2_validator_accepts_string_answers_and_uses_metadata_pair_id(self):
        mod = load_module("validate_engbench_v2", ROOT / "tools" / "validate_engbench_v2.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            items = [
                {
                    "id": "q_string_answer",
                    "task": "visualdiff",
                    "question": "What changed?",
                    "answer": "Changed the visible label.",
                    "images": ["images/a.png", "images/b.png"],
                    "evidence": [{"bbox": [1, 2, 3, 4], "image_index": 0}],
                    "split": "dev",
                    "metadata": {"pair_id": "missing_pair_family"},
                }
            ]

            report, bad_indices = mod.validate_all(items, {}, str(tmp_path), skip_textlayer=True)

            self.assertEqual(report.stats["split_dev_yes"], 0)
            self.assertTrue(
                any("[Check 10]" in err and "missing_pair_family" in err for err in report.errors)
            )
            self.assertEqual(bad_indices, {0})

    def test_unify_dataset_uses_microtext_item_ids_for_split_and_metadata(self):
        mod = load_module("unify_dataset", ROOT / "tools" / "unify_dataset.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_questions.jsonl", [])
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "tolerances_table_iso",
                        "version_id": "iso",
                        "page_index": 2,
                        "bbox": [1, 2, 3, 4],
                        "text_gt": "G7/h6",
                        "category": "tolerance_value",
                        "split": "dev",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {
                        "question_id": "qm0",
                        "item_ids": ["mt0"],
                        "doc_id": "tolerances_table_iso",
                        "query_text": "Read the tolerance.",
                        "answer_text": "G7/h6",
                        "split": "test",
                    }
                ],
            )

            rows = mod.process_microtext(tmp_path)

            self.assertEqual(rows[0]["split"], "dev")
            self.assertEqual(rows[0]["metadata"]["item_id"], "mt0")

    def test_finalize_all_images_copies_derived_microtext_pages_to_canonical_unknown_dir(self):
        mod = load_module("finalize_all_images", ROOT / "tools" / "finalize_all_images.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_page = tmp_path / "derived" / "pages_300dpi" / "example_doc" / "page_0002.png"
            source_page.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 8), "white").save(source_page)

            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "example_doc",
                        "version_id": "unknown",
                        "page_index": 2,
                        "bbox": [1, 1, 2, 2],
                        "text_gt": "A12",
                        "category": "pin_label",
                        "split": "train",
                    }
                ],
            )

            stats = mod.finalize_images(tmp_path)

            self.assertTrue((tmp_path / "images" / "example_doc__unknown" / "page_0002.png").exists())
            self.assertEqual(stats["required"], 1)
            self.assertEqual(stats["copied"], 1)
            self.assertEqual(stats["missing"], 0)

    def test_unify_dataset_resolves_microtext_image_from_derived_page_when_canonical_missing(self):
        mod = load_module("unify_dataset", ROOT / "tools" / "unify_dataset.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_page = tmp_path / "derived" / "pages_300dpi" / "example_doc" / "page_0002.png"
            source_page.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 8), "white").save(source_page)
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_questions.jsonl", [])
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "example_doc",
                        "version_id": "unknown",
                        "page_index": 2,
                        "bbox": [1, 1, 2, 2],
                        "text_gt": "A12",
                        "category": "pin_label",
                        "split": "train",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {
                        "question_id": "qm0",
                        "item_ids": ["mt0"],
                        "query_text": "Read the label.",
                        "answer_text": "A12",
                    }
                ],
            )

            rows = mod.process_microtext(tmp_path)

            self.assertEqual(
                rows[0]["images"],
                ["derived/pages_300dpi/example_doc/page_0002.png"],
            )

    def test_v2_validator_reports_missing_unified_images(self):
        mod = load_module("validate_engbench_v2", ROOT / "tools" / "validate_engbench_v2.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            items = [
                {
                    "id": "q_missing_image",
                    "task": "microtext",
                    "question": "Read the label.",
                    "answer": "A12",
                    "images": ["images/example_doc__unknown/page_0002.png"],
                    "evidence": [],
                    "split": "dev",
                    "metadata": {"doc_id": "example_doc"},
                }
            ]
            manifest = {"example_doc": {"type": "doc", "doc_id": "example_doc"}}

            report, bad_indices = mod.validate_all(items, manifest, str(tmp_path), skip_textlayer=True)

            self.assertEqual(report.stats["missing_images"], 1)
            self.assertEqual(report.stats["rows_with_missing_images"], 1)
            self.assertTrue(
                any("[Check 12]" in err and "page_0002.png" in err for err in report.errors)
            )
            self.assertEqual(bad_indices, {0})

    def test_benchmark_health_report_summarizes_packaging_blockers(self):
        mod = load_module("benchmark_health_report", ROOT / "tools" / "benchmark_health_report.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            present_image = tmp_path / "images" / "doc_a__v1" / "page_0000.png"
            present_image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 8), "white").save(present_image)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "visualdiff",
                        "split": "train",
                        "images": ["images/doc_a__v1/page_0000.png"],
                        "metadata": {"pair_id": "family_a"},
                    },
                    {
                        "id": "q1",
                        "task": "microtext",
                        "split": "dev",
                        "images": ["images/doc_b__unknown/page_0003.png"],
                        "metadata": {"doc_id": "doc_b", "category": "dimension_value"},
                    },
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "family_a__0000",
                        "project_id": "family_a",
                        "split": "train",
                        "change_desc_gt": "CHANGE_DESC_GT_TODO",
                        "review_confidence": "low",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc_b",
                        "split": "dev",
                        "category": "dimension_value",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [{"type": "doc", "doc_id": "doc_b", "public_status": "public_domain_candidate"}],
            )
            (tmp_path / "splits").mkdir()
            (tmp_path / "splits" / "visualdiff_train.txt").write_text("family_a\n", encoding="utf-8")
            (tmp_path / "splits" / "visualdiff_dev.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "visualdiff_test.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_train.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_dev.txt").write_text("doc_b\n", encoding="utf-8")
            (tmp_path / "splits" / "microtext_test.txt").write_text("", encoding="utf-8")

            report = mod.compute_health(tmp_path, release_target="v0.95")

            self.assertEqual(report["total_rows"], 2)
            self.assertEqual(report["rows_by_task"], {"microtext": 1, "visualdiff": 1})
            self.assertEqual(report["missing_image_rows"], 1)
            self.assertEqual(report["visualdiff_todo_total"], 1)
            self.assertEqual(report["visualdiff_low_confidence_total"], 1)
            self.assertEqual(
                report["microtext_categories_by_split"],
                {"dev": {"dimension_value": 1}},
            )
            self.assertFalse(report["release_gate_status"]["passed"])
            self.assertIn("missing_image_rows", report["release_gate_status"]["blockers"][0])

    def test_benchmark_health_report_v1_gate_blocks_scale_and_devtest_label_debt(self):
        mod = load_module("benchmark_health_report", ROOT / "tools" / "benchmark_health_report.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for image_path in [
                tmp_path / "images" / "doc_a__v1" / "page_0000.png",
                tmp_path / "images" / "doc_b__unknown" / "page_0000.png",
            ]:
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (12, 8), "white").save(image_path)

            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "visualdiff",
                        "split": "dev",
                        "images": ["images/doc_a__v1/page_0000.png"],
                        "metadata": {"pair_id": "family_a"},
                    },
                    {
                        "id": "q1",
                        "task": "microtext",
                        "split": "test",
                        "images": ["images/doc_b__unknown/page_0000.png"],
                        "metadata": {"doc_id": "doc_b", "category": "dimension_value"},
                    },
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "family_a__0000",
                        "project_id": "family_a",
                        "split": "dev",
                        "change_desc_gt": "Highlighted symbol changed.",
                        "review_confidence": "low",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc_b",
                        "split": "test",
                        "category": "dimension_value",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [{"type": "doc", "doc_id": "doc_b", "public_status": "public_domain_candidate"}],
            )
            (tmp_path / "splits").mkdir()
            (tmp_path / "splits" / "visualdiff_train.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "visualdiff_dev.txt").write_text("family_a\n", encoding="utf-8")
            (tmp_path / "splits" / "visualdiff_test.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_train.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_dev.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_test.txt").write_text("doc_b\n", encoding="utf-8")

            report = mod.compute_health(tmp_path, release_target="v1.0")
            blockers = report["release_gate_status"]["blockers"]

            self.assertEqual(report["visualdiff_low_confidence_by_split"], {"dev": 1})
            self.assertEqual(report["visualdiff_low_confidence_dev_test"], 1)
            self.assertEqual(report["test_rows_by_task"], {"microtext": 1})
            self.assertFalse(report["release_gate_status"]["passed"])
            self.assertTrue(any("total_rows<5000" in blocker for blocker in blockers))
            self.assertTrue(any("source_count<35" in blocker for blocker in blockers))
            self.assertTrue(
                any("visualdiff_low_confidence_dev_test=1" in blocker for blocker in blockers)
            )
            self.assertFalse(report["active_gold_provenance"]["passes"])
            self.assertIn("active_gold_provenance_incomplete", blockers)
            self.assertFalse(report["release_gate_status"]["checks"]["v1_provenance_ready"])

    def test_benchmark_runner_scores_protocol_metrics_for_unified_rows(self):
        mod = load_module("benchmark_runner", ROOT / "tools" / "benchmark_runner.py")

        gold = [
            {
                "id": "mt0",
                "task": "microtext",
                "split": "test",
                "answer": "Pump P-101",
                "evidence": [{"image_index": 0, "bbox": [10, 10, 30, 30]}],
                "metadata": {"category": "equipment_tag", "doc_id": "pid_doc"},
            },
            {
                "id": "vd0",
                "task": "visualdiff",
                "split": "test",
                "answer": "Old label A changed to new label B.",
                "evidence": [
                    {"image_index": 0, "bbox": [100, 100, 140, 140]},
                    {"image_index": 1, "bbox": [200, 200, 240, 240]},
                ],
                "metadata": {"change_type": ["text"], "doc_id": "pcb_doc"},
            },
        ]
        predictions = [
            {
                "id": "mt0",
                "answer": "pump p101",
                "evidence": [{"image_index": 0, "bbox": [10, 10, 30, 30]}],
                "metadata": {"model": "smoke"},
            },
            {
                "id": "vd0",
                "answer": "label a changed to label b",
                "evidence": [
                    {"image_index": 0, "bbox": [102, 102, 138, 138]},
                    {"image_index": 1, "bbox": [200, 200, 240, 240]},
                ],
                "metadata": {"model": "smoke", "change_type": "text"},
            },
        ]

        report = mod.score_predictions(
            gold,
            predictions,
            model_name="smoke",
            bootstrap_samples=20,
            bootstrap_seed=7,
        )

        self.assertEqual(report["model_name"], "smoke")
        self.assertEqual(report["rows_scored"], 2)
        self.assertEqual(report["missing_predictions"], 0)
        self.assertEqual(report["rows_by_task"], {"microtext": 1, "visualdiff": 1})
        self.assertAlmostEqual(report["microtext"]["exact_match"], 0.0)
        self.assertAlmostEqual(report["microtext"]["normalized_exact_match"], 1.0)
        self.assertAlmostEqual(report["microtext"]["character_error_rate"], 0.0)
        self.assertAlmostEqual(report["microtext"]["evidence_iou_0_5"], 1.0)
        self.assertAlmostEqual(
            report["microtext"]["per_category_accuracy"]["equipment_tag"]["normalized_exact_match"],
            1.0,
        )
        self.assertAlmostEqual(report["visualdiff"]["evidence_recall_iou_0_3"], 1.0)
        self.assertAlmostEqual(report["visualdiff"]["evidence_recall_iou_0_5"], 1.0)
        self.assertAlmostEqual(report["visualdiff"]["old_new_side_hit_rate"], 1.0)
        self.assertAlmostEqual(report["visualdiff"]["change_type_accuracy"], 1.0)
        self.assertGreater(report["visualdiff"]["normalized_description_f1"], 0.5)
        self.assertEqual(
            report["confidence_intervals"]["microtext.normalized_exact_match"]["samples"],
            20,
        )
        self.assertAlmostEqual(
            report["confidence_intervals"]["visualdiff.evidence_recall_iou_0_5"]["point"],
            1.0,
        )
        self.assertEqual(report["per_domain"]["pid_doc"]["rows"], 1)
        self.assertAlmostEqual(report["per_domain"]["pid_doc"]["microtext_normalized_exact_match"], 1.0)
        self.assertEqual(report["per_domain"]["pcb_doc"]["rows"], 1)
        self.assertAlmostEqual(report["per_domain"]["pcb_doc"]["visualdiff_evidence_recall_iou_0_5"], 1.0)

    def test_benchmark_runner_cli_writes_json_and_markdown_report(self):
        mod = load_module("benchmark_runner", ROOT / "tools" / "benchmark_runner.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            gold_path = tmp_path / "gt.jsonl"
            pred_path = tmp_path / "pred.jsonl"
            report_json = tmp_path / "report.json"
            report_md = tmp_path / "report.md"
            write_jsonl(
                gold_path,
                [
                    {
                        "id": "mt0",
                        "task": "microtext",
                        "split": "test",
                        "answer": "A12",
                        "evidence": [{"image_index": 0, "bbox": [1, 1, 10, 10]}],
                        "metadata": {"category": "pin_label"},
                    },
                    {
                        "id": "vd0",
                        "task": "visualdiff",
                        "split": "dev",
                        "answer": "Part number changed.",
                        "evidence": [{"image_index": 1, "bbox": [10, 10, 20, 20]}],
                    },
                ],
            )
            write_jsonl(
                pred_path,
                [
                    {
                        "id": "mt0",
                        "answer": "A12",
                        "evidence": [{"image_index": 0, "bbox": [1, 1, 10, 10]}],
                    },
                    {
                        "id": "vd0",
                        "answer": "Part changed.",
                        "evidence": [{"image_index": 1, "bbox": [10, 10, 20, 20]}],
                    },
                ],
            )

            rc = mod.main(
                [
                    "--gt",
                    str(gold_path),
                    "--pred",
                    str(pred_path),
                    "--split",
                    "test",
                    "--model-name",
                    "smoke_cli",
                    "--report-json",
                    str(report_json),
                    "--report-md",
                    str(report_md),
                    "--bootstrap-samples",
                    "10",
                ]
            )

            self.assertEqual(rc, 0)
            report = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(report["model_name"], "smoke_cli")
            self.assertEqual(report["rows_scored"], 1)
            self.assertEqual(report["rows_by_task"], {"microtext": 1})
            self.assertIn("microtext.normalized_exact_match", report["confidence_intervals"])
            self.assertIn("Eng_Bench Baseline Report", report_md.read_text(encoding="utf-8"))

    def test_simple_diff_baseline_emits_unified_visualdiff_predictions(self):
        mod = load_module("simple_diff_baseline", ROOT / "baselines" / "simple_diff_baseline.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            old_path = tmp_path / "images" / "doc__old" / "page_0000.png"
            new_path = tmp_path / "images" / "doc__new" / "page_0000.png"
            old_path.parent.mkdir(parents=True)
            new_path.parent.mkdir(parents=True)
            old = Image.new("RGB", (80, 60), "white")
            new = Image.new("RGB", (80, 60), "white")
            for x in range(30, 45):
                for y in range(20, 35):
                    new.putpixel((x, y), (0, 0, 0))
            old.save(old_path)
            new.save(new_path)
            rows = [
                {
                    "id": "q_vd0",
                    "task": "visualdiff",
                    "split": "test",
                    "question": "What changed?",
                    "images": [
                        "images/doc__old/page_0000.png",
                        "images/doc__new/page_0000.png",
                    ],
                },
                {
                    "id": "q_mt0",
                    "task": "microtext",
                    "images": ["images/doc__new/page_0000.png"],
                },
            ]

            predictions, stats = mod.predict_rows(tmp_path, rows, max_dim=80)

            self.assertEqual(stats["visualdiff_rows"], 1)
            self.assertEqual(stats["predictions"], 1)
            self.assertEqual(predictions[0]["id"], "q_vd0")
            self.assertEqual(len(predictions[0]["evidence"]), 2)
            self.assertEqual({ev["image_index"] for ev in predictions[0]["evidence"]}, {0, 1})
            bbox = predictions[0]["evidence"][0]["bbox"]
            self.assertLessEqual(bbox[0], 31)
            self.assertLessEqual(bbox[1], 21)
            self.assertGreaterEqual(bbox[2], 44)
            self.assertGreaterEqual(bbox[3], 34)

    def test_edge_diff_baseline_emits_structural_visualdiff_predictions(self):
        mod = load_module("simple_diff_baseline", ROOT / "baselines" / "simple_diff_baseline.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            old_path = tmp_path / "images" / "doc__old" / "page_0000.png"
            new_path = tmp_path / "images" / "doc__new" / "page_0000.png"
            old_path.parent.mkdir(parents=True)
            new_path.parent.mkdir(parents=True)
            old = Image.new("RGB", (80, 60), "white")
            new = Image.new("RGB", (80, 60), "white")
            for x in range(30, 45):
                for y in range(20, 35):
                    new.putpixel((x, y), (0, 0, 0))
            old.save(old_path)
            new.save(new_path)
            rows = [
                {
                    "id": "q_vd_edge",
                    "task": "visualdiff",
                    "split": "test",
                    "question": "What changed?",
                    "images": [
                        "images/doc__old/page_0000.png",
                        "images/doc__new/page_0000.png",
                    ],
                }
            ]

            predictions, stats = mod.predict_rows(tmp_path, rows, max_dim=80, mode="edge")

            self.assertEqual(stats["predictions"], 1)
            self.assertEqual(predictions[0]["metadata"]["model"], "edge_diff")
            self.assertEqual(predictions[0]["metadata"]["method"], "canny_edge_map_difference")
            self.assertNotEqual(predictions[0]["answer"], mod.DEFAULT_ANSWER)
            bbox = predictions[0]["evidence"][0]["bbox"]
            self.assertLessEqual(bbox[0], 30)
            self.assertLessEqual(bbox[1], 20)
            self.assertGreaterEqual(bbox[2], 45)
            self.assertGreaterEqual(bbox[3], 35)

    def test_textlayer_microtext_baseline_uses_category_without_gold_evidence(self):
        mod = load_module(
            "textlayer_microtext_baseline",
            ROOT / "baselines" / "textlayer_microtext_baseline.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "derived" / "textlayer" / "floor_plan.jsonl",
                [
                    {"page": 0, "text": "general note", "bbox_px": [1, 1, 10, 10]},
                    {"page": 0, "text": "KITCHEN", "bbox_px": [20, 20, 60, 40]},
                ],
            )
            rows = [
                {
                    "id": "q_mt0",
                    "task": "microtext",
                    "question": "What room label is shown in this region?",
                    "images": ["images/floor_plan__unknown/page_0000.png"],
                    "metadata": {"doc_id": "floor_plan", "category": "room_label"},
                },
                {
                    "id": "q_vd0",
                    "task": "visualdiff",
                    "images": [],
                },
            ]

            predictions, stats = mod.predict_rows(tmp_path, rows)

            self.assertEqual(stats["microtext_rows"], 1)
            self.assertEqual(stats["predictions"], 1)
            self.assertEqual(predictions[0]["id"], "q_mt0")
            self.assertEqual(predictions[0]["answer"], "KITCHEN")
            self.assertEqual(predictions[0]["evidence"], [{"image_index": 0, "bbox": [20, 20, 60, 40]}])

    def test_tesseract_microtext_baseline_parses_and_scores_ocr_tokens(self):
        mod = load_module(
            "tesseract_microtext_baseline",
            ROOT / "baselines" / "tesseract_microtext_baseline.py",
        )

        tsv = "\t".join(["level", "left", "top", "width", "height", "conf", "text"]) + "\n"
        tsv += "\t".join(["5", "10", "20", "30", "12", "88", "TIC-101"]) + "\n"
        tsv += "\t".join(["5", "5", "6", "10", "9", "-1", ""]) + "\n"

        tokens = mod.parse_tesseract_tsv(tsv)
        best = mod.pick_token(tokens, "instrument_tag")

        self.assertEqual(tokens[0]["bbox"], [10, 20, 40, 32])
        self.assertEqual(best["text"], "TIC-101")

    def test_benchmark_health_report_counts_non_smoke_baseline_reports(self):
        mod = load_module("benchmark_health_report", ROOT / "tools" / "benchmark_health_report.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            baselines_dir = tmp_path / "results" / "baselines"
            baselines_dir.mkdir(parents=True)
            for name in ("alpha", "beta", "gamma", "orphan"):
                (baselines_dir / f"{name}_report.json").write_text("{}\n", encoding="utf-8")
            write_jsonl(
                baselines_dir / "alpha_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "alpha"}}],
            )
            write_jsonl(
                baselines_dir / "beta_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "beta"}}],
            )
            write_jsonl(
                baselines_dir / "gamma_predictions.jsonl",
                [{"id": "q0", "answer": "B", "metadata": {"model": "gamma"}}],
            )
            (baselines_dir / "oracle_smoke_test_report.json").write_text("{}", encoding="utf-8")
            (baselines_dir / "README.md").write_text("notes\n", encoding="utf-8")

            self.assertEqual(mod.baseline_count(tmp_path), 2)

    def test_benchmark_health_report_uses_baseline_coverage_gate(self):
        mod = load_module("benchmark_health_report", ROOT / "tools" / "benchmark_health_report.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            image = tmp_path / "images" / "doc__v1" / "page_0000.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 8), "white").save(image)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "microtext",
                        "split": "test",
                        "images": ["images/doc__v1/page_0000.png"],
                        "metadata": {"doc_id": "doc", "category": "dimension_value"},
                    }
                ],
            )
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "m0", "doc_id": "doc", "split": "test", "category": "dimension_value"}],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [{"type": "doc", "doc_id": "doc", "public_status": "public_domain_candidate"}],
            )
            (tmp_path / "splits").mkdir()
            for name in [
                "visualdiff_train",
                "visualdiff_dev",
                "visualdiff_test",
                "microtext_train",
                "microtext_dev",
            ]:
                (tmp_path / "splits" / f"{name}.txt").write_text("", encoding="utf-8")
            (tmp_path / "splits" / "microtext_test.txt").write_text("doc\n", encoding="utf-8")
            baselines_dir = tmp_path / "results" / "baselines"
            baselines_dir.mkdir(parents=True)
            for idx in range(5):
                (baselines_dir / f"b{idx}_report.json").write_text("{}", encoding="utf-8")
                write_jsonl(
                    baselines_dir / f"b{idx}_predictions.jsonl",
                    [{"id": "q0", "answer": f"answer_{idx}"}],
                )
            (baselines_dir / "baseline_coverage_audit.json").write_text(
                json.dumps({"passed": False, "failing_rows": 1}),
                encoding="utf-8",
            )

            failing_report = mod.compute_health(tmp_path, release_target="v1.0")
            self.assertIn(
                "baseline_coverage_failing_rows=1",
                failing_report["release_gate_status"]["blockers"],
            )

            (baselines_dir / "baseline_coverage_audit.json").write_text(
                json.dumps({"passed": True, "failing_rows": 0}),
                encoding="utf-8",
            )
            passing_report = mod.compute_health(tmp_path, release_target="v1.0")
            self.assertNotIn(
                "baseline_coverage_failing_rows=1",
                passing_report["release_gate_status"]["blockers"],
            )
            self.assertTrue(passing_report["baseline_coverage"]["passed"])

    def test_benchmark_health_report_includes_loader_and_question_diversity(self):
        mod = load_module("benchmark_health_report", ROOT / "tools" / "benchmark_health_report.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "derived" / "quality").mkdir(parents=True)
            (tmp_path / "results" / "health").mkdir(parents=True)
            write_jsonl(tmp_path / "eng_bench.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            (tmp_path / "derived" / "quality" / "question_diversity_report.json").write_text(
                json.dumps({"passes": True, "by_task": {"microtext": {"unique_templates": 4}}}),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / "question_leakage_audit.json").write_text(
                json.dumps({"critical_failures": 0, "leak_count": 0}),
                encoding="utf-8",
            )
            (tmp_path / "results" / "health" / "loader_smoke.json").write_text(
                json.dumps({"total": 0, "by_task": {}}),
                encoding="utf-8",
            )
            (tmp_path / "results" / "health" / "release_file_manifest.json").write_text(
                json.dumps({"missing_count": 0, "file_count": 1}),
                encoding="utf-8",
            )
            write_jsonl(
                tmp_path / "release" / "public_inputs" / "eng_bench_dev_inputs.jsonl",
                [{"id": "dev0", "question": "Read it.", "images": []}],
            )
            write_jsonl(
                tmp_path / "release" / "public_inputs" / "eng_bench_test_inputs.jsonl",
                [{"id": "test0", "question": "Read it.", "images": []}],
            )

            report = mod.compute_health(tmp_path, release_target="v0.95")

            self.assertTrue(report["question_diversity"]["passes"])
            self.assertEqual(report["question_leakage"]["critical_failures"], 0)
            self.assertEqual(report["loader_smoke"]["total"], 0)
            self.assertEqual(report["release_manifest"]["missing_count"], 0)
            self.assertEqual(report["public_inputs"]["label_field_hit_count"], 0)
            mod.write_report(tmp_path, report)
            self.assertTrue((tmp_path / "results" / "health" / "benchmark_health_report_v0.95.json").exists())

    def test_build_baseline_table_summarizes_reports(self):
        mod = load_module("build_baseline_table", ROOT / "tools" / "build_baseline_table.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            reports_dir = tmp_path / "results" / "baselines"
            reports_dir.mkdir(parents=True)
            (reports_dir / "simple_diff_visualdiff_all_report.json").write_text(
                json.dumps(
                    {
                        "model_name": "simple_diff",
                        "task": "visualdiff",
                        "split": "all",
                        "rows_scored": 10,
                        "missing_predictions": 0,
                        "microtext": {"normalized_exact_match": 0, "character_error_rate": 0},
                        "visualdiff": {
                            "evidence_recall_iou_0_3": 0.2,
                            "evidence_recall_iou_0_5": 0.1,
                            "old_new_side_hit_rate": 0.15,
                            "normalized_description_f1": 0.05,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (reports_dir / "textlayer_microtext_test_report.json").write_text(
                json.dumps(
                    {
                        "model_name": "textlayer",
                        "task": "microtext",
                        "split": "test",
                        "rows_scored": 5,
                        "missing_predictions": 1,
                        "microtext": {
                            "normalized_exact_match": 0.4,
                            "character_error_rate": 0.6,
                            "evidence_iou_0_5": 0.2,
                        },
                        "visualdiff": {},
                    }
                ),
                encoding="utf-8",
            )
            (reports_dir / "renamed_duplicate_report.json").write_text(
                json.dumps({"model_name": "renamed_duplicate"}),
                encoding="utf-8",
            )
            write_jsonl(
                reports_dir / "simple_diff_visualdiff_all_predictions.jsonl",
                [{"id": "q0", "answer": "A"}],
            )
            write_jsonl(
                reports_dir / "renamed_duplicate_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "renamed"}}],
            )
            write_jsonl(
                reports_dir / "textlayer_microtext_test_predictions.jsonl",
                [{"id": "q0", "answer": "B"}],
            )

            rows = mod.collect_reports(tmp_path)
            table = mod.render_table(rows)

            self.assertEqual(len(rows), 2)
            self.assertIn("| simple_diff | visualdiff | all | 10 | 0 |", table)
            self.assertIn("| textlayer | microtext | test | 5 | 1 |", table)
            self.assertIn("Norm EM", table)

    def test_build_baseline_error_analysis_samples_failures(self):
        mod = load_module(
            "build_baseline_error_analysis",
            ROOT / "tools" / "build_baseline_error_analysis.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            reports_dir = tmp_path / "results" / "baselines"
            reports_dir.mkdir(parents=True)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "mt0",
                        "task": "microtext",
                        "split": "test",
                        "answer": "A12",
                        "evidence": [{"image_index": 0, "bbox": [0, 0, 10, 10]}],
                        "metadata": {"category": "pin_label"},
                    },
                    {
                        "id": "vd0",
                        "task": "visualdiff",
                        "split": "test",
                        "answer": "Changed.",
                        "evidence": [{"image_index": 1, "bbox": [0, 0, 10, 10]}],
                    },
                ],
            )
            (reports_dir / "toy_microtext_test_report.json").write_text(
                json.dumps({"model_name": "toy_text", "task": "microtext"}),
                encoding="utf-8",
            )
            write_jsonl(
                reports_dir / "toy_microtext_test_predictions.jsonl",
                [{"id": "mt0", "answer": "B34", "evidence": [{"image_index": 0, "bbox": [20, 20, 30, 30]}]}],
            )
            (reports_dir / "toy_visualdiff_test_report.json").write_text(
                json.dumps({"model_name": "toy_diff", "task": "visualdiff"}),
                encoding="utf-8",
            )
            write_jsonl(
                reports_dir / "toy_visualdiff_test_predictions.jsonl",
                [{"id": "vd0", "answer": "Different.", "evidence": [{"image_index": 1, "bbox": [20, 20, 30, 30]}]}],
            )

            rows = mod.collect_failure_rows(tmp_path, max_examples=5)
            report = mod.render_markdown(rows)

            self.assertTrue(any(row["id"] == "mt0" for row in rows))
            self.assertTrue(any(row["id"] == "vd0" for row in rows))
            self.assertIn("toy_text", report)
            self.assertIn("toy_diff", report)
            self.assertIn("mt0", report)

    def test_build_source_quotas_reports_domain_shortfalls(self):
        mod = load_module("build_source_quotas", ROOT / "tools" / "build_source_quotas.py")

        inventory = [
            {"doc_id": "pcb_a", "domain": "pcb_schematic", "task": "microtext", "public_status": "public_candidate"},
            {"doc_id": "pid_a", "domain": "pid", "task": "reference", "public_status": "unknown"},
            {"doc_id": "pid_b", "domain": "pid", "task": "microtext", "public_status": "restricted_public_utility_pdf_candidate"},
        ]
        candidates = [
            {"candidate_id": "pid_1", "domain": "pid", "rights_tier": "public_candidate"},
            {"candidate_id": "pid_2", "domain": "pid", "rights_tier": "rights_uncertain"},
        ]
        validations = [
            {"candidate_id": "pid_1", "next_action": "intake_first"},
            {"candidate_id": "pid_2", "next_action": "prototype_only"},
        ]

        rows = mod.summarize_quotas(inventory, candidates, validations)
        pid = next(row for row in rows if row["domain"] == "pid")
        table = mod.render_markdown(rows)

        self.assertEqual(pid["inventory_total"], 2)
        self.assertEqual(pid["release_safe_active"], 0)
        self.assertEqual(pid["candidate_total"], 2)
        self.assertEqual(pid["intake_first"], 1)
        self.assertIn("Source Quotas", table)
        self.assertIn("remaining_to_v1_0", table)

    def test_gold_expansion_plan_connects_gate_gaps_review_queues_and_imports(self):
        mod = load_module("build_gold_expansion_plan", ROOT / "tools" / "build_gold_expansion_plan.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "mt0",
                        "task": "microtext",
                        "split": "test",
                        "metadata": {"doc_id": "mech_doc", "category": "dimension_value"},
                    },
                    {
                        "id": "vd0",
                        "task": "visualdiff",
                        "split": "test",
                        "metadata": {
                            "doc_id": "pcb_doc",
                            "pair_id": "vdiff__pcb_doc__a__to__b__0000",
                        },
                    },
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "vdiff__pcb_doc__a__to__b__0000", "doc_id": "pcb_doc"}],
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "path",
                        "task",
                        "doc_id",
                        "domain",
                        "source_url",
                        "public_status",
                        "textlayer_status",
                        "render_status",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/mech_doc.pdf",
                        "task": "microtext",
                        "doc_id": "mech_doc",
                        "domain": "mechanical_cad",
                        "public_status": "public_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "visualdiff/docs/pcb_doc.pdf",
                        "task": "visualdiff",
                        "doc_id": "pcb_doc",
                        "domain": "pcb_schematic",
                        "public_status": "public_candidate",
                    }
                )
            with (tmp_path / "SOURCE_CANDIDATES.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "domain",
                        "task_fit",
                        "rights_tier",
                        "source_url",
                        "revision_family_potential",
                        "annotation_yield",
                        "priority_score",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "visualdiff,microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.com/pid.pdf",
                        "revision_family_potential": "high",
                        "annotation_yield": "high",
                        "priority_score": "8",
                    }
                )
            with (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "browser_result",
                        "source_validity",
                        "release_posture",
                        "next_action",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "browser_result": "loaded",
                        "source_validity": "validated_public_pdf_candidate",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                    }
                )
            (tmp_path / "SOURCE_INTAKE_LOG.md").write_text("# no imports yet\n", encoding="utf-8")
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_pid.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "pid_doc",
                        "category": "instrument_tag",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "pid_doc",
                        "category": "equipment_tag",
                        "review_status": "needs_review",
                    },
                ],
            )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            write_jsonl(
                packet_root / "review_packs" / "pack" / "manifest.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "pid_doc",
                        "category": "instrument_tag",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "pid_doc",
                        "category": "equipment_tag",
                    },
                ],
            )
            packet_index_path = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-03.json"
            packet_index_path.parent.mkdir(parents=True, exist_ok=True)
            packet_index_path.write_text(
                json.dumps(
                    {
                        "date_label": "2026-06-03",
                        "totals": {"review_rows": 7, "ready_to_send": 1},
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "kind": "standalone_review_batch",
                                "ready_to_send": True,
                                "human_status": "unfilled",
                                "review_rows": 7,
                                "primary_rows": 7,
                                "extra_review_rows": 0,
                                "missing_evidence_refs": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                                "zip_path": "derived/human_adjudication/packet_a.zip",
                                "notes": "test packet",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            plan = mod.build_plan(tmp_path, limit=3, date_label="2026-06-03")
            markdown = mod.render_markdown(plan)

            self.assertEqual(plan["date_label"], "2026-06-03")
            self.assertEqual(plan["current"]["total_rows"], 2)
            self.assertEqual(plan["gate_gaps"]["v1_0"]["total_rows"]["remaining"], 4998)
            forecast = plan["human_packet_forecast"]
            self.assertTrue(forecast["available"])
            self.assertEqual(forecast["review_rows_upper_bound"], 7)
            self.assertEqual(forecast["manifest_rows_inspectable"], 2)
            self.assertEqual(
                forecast["release_projection"]["v1_0"]["remaining_after_all_packet_rows_accepted"],
                4991,
            )
            self.assertEqual(plan["machine_import_priority"][0]["candidate_id"], "pid_001")
            self.assertEqual(plan["review_queues"]["human_priority_files"], [])
            self.assertEqual(plan["review_queues"]["resolution_totals"]["packeted_open_rows"], 2)
            self.assertEqual(plan["review_queues"]["resolution_totals"]["actionable_fresh_open_rows"], 0)
            pid_domain = next(row for row in plan["domain_status"] if row["domain"] == "pid")
            self.assertEqual(pid_domain["review_open_rows"], 2)
            self.assertIn("Gold Expansion Plan", markdown)
            self.assertIn("Current Human Packet Forecast", markdown)
            self.assertIn("Actionable release-safe fresh rows", markdown)
            self.assertIn("verified packet index", plan["interpretation"]["primary_human_bottleneck"])

    def test_gold_expansion_plan_uses_strict_source_provenance_and_rights_counts(self):
        mod = load_module(
            "build_gold_expansion_plan_strict_counts",
            ROOT / "tools" / "build_gold_expansion_plan.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [{"id": "vd0", "task": "visualdiff", "split": "test"}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "vdiff__widget__a__to__b__0000",
                        "project_id": "vdiff__widget__a__to__b",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {"type": "doc", "doc_id": "widget_a", "task": "visualdiff"},
                    {"type": "doc", "doc_id": "widget_b", "task": "visualdiff"},
                    {
                        "type": "pair",
                        "pair_id": "vdiff__widget__a__to__b",
                        "from_doc_id": "widget_a",
                        "to_doc_id": "widget_b",
                    },
                ],
            )
            inventory = [
                {
                    "task": "visualdiff",
                    "doc_id": "widget_a",
                    "domain": "pcb_schematic",
                    "public_status": "public_domain_candidate",
                },
                {
                    "task": "visualdiff",
                    "doc_id": "widget_b",
                    "domain": "pcb_schematic",
                    "public_status": "public_domain_candidate",
                },
                {
                    "task": "visualdiff",
                    "doc_id": "held",
                    "domain": "pcb_schematic",
                    "public_status": "rights_uncertain_open_hardware_candidate",
                },
            ]
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["task", "doc_id", "domain", "public_status"],
                )
                writer.writeheader()
                writer.writerows(inventory)

            status = mod.current_status(tmp_path, inventory)

            self.assertEqual(status["gold_source_docs"], 2)
            self.assertEqual(status["release_safe_inventory_docs"], 2)
            self.assertFalse(mod.is_release_safe(inventory[2]))

    def test_unmerged_reviewed_rows_audit_separates_active_duplicates_from_new_rows(self):
        mod = load_module(
            "audit_unmerged_reviewed_rows",
            ROOT / "tools" / "audit_unmerged_reviewed_rows.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "source_candidate_id": "cand_active",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a_reviewed.jsonl",
                [
                    {
                        "candidate_id": "cand_active",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "review_status": "accepted",
                    },
                    {
                        "candidate_id": "cand_new",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 20, 30, 40],
                        "category": "dimension_value",
                        "review_status": "accepted",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "pair_active", "project_id": "proj", "bbox_old": [1, 1, 2, 2], "bbox_new": [1, 1, 2, 2]}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_review_doc_a.jsonl",
                [
                    {"pair_id": "pair_active", "project_id": "proj", "human_status": "valid"},
                    {
                        "pair_id": "pair_new",
                        "project_id": "proj",
                        "human_status": "edit",
                        "bbox_old": [5, 5, 9, 9],
                        "bbox_new": [5, 5, 9, 9],
                    },
                ],
            )

            report = mod.audit(tmp_path)
            markdown = mod.render_markdown(report)

            self.assertEqual(report["totals"]["mergeable_review_rows"], 4)
            self.assertEqual(report["totals"]["already_active_by_candidate_id"], 1)
            self.assertEqual(report["totals"]["already_active_by_pair_id"], 1)
            self.assertEqual(report["totals"]["unmerged_mergeable"], 2)
            self.assertEqual(len(report["priority_unmerged_files"]), 2)
            self.assertIn("Unmerged Reviewed Rows Audit", markdown)

    def test_unmerged_reviewed_rows_audit_reports_explicit_zero_pending_count(self):
        mod = load_module(
            "audit_unmerged_reviewed_rows_zero",
            ROOT / "tools" / "audit_unmerged_reviewed_rows.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "source_candidate_id": "cand_active",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a_reviewed.jsonl",
                [
                    {
                        "candidate_id": "cand_active",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "review_status": "accepted",
                    }
                ],
            )

            report = mod.audit(tmp_path)

            self.assertEqual(report["totals"]["mergeable_review_rows"], 1)
            self.assertEqual(report["totals"]["unmerged_mergeable"], 0)
            self.assertEqual(report["totals"]["blocked_missing_evidence"], 0)

    def test_parallel_handoff_builder_writes_verified_zip(self):
        mod = load_module(
            "build_parallel_human_handoff",
            ROOT / "tools" / "build_parallel_human_handoff.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_packet = tmp_path / "derived" / "human_adjudication" / "2026-05-25_v1_5_intern_delivery"
            packet_review_root = (
                source_packet
                / "derived"
                / "human_adjudication"
                / "2026-05-25_v1_5_review_handoff"
            )
            packet_review_root.mkdir(parents=True)
            (source_packet / "README.md").write_text("packet\n", encoding="utf-8")
            (packet_review_root / "microtext_v1_5_first20_validation_checklist.csv").write_text(
                "candidate_id,review_status\n",
                encoding="utf-8",
            )
            write_jsonl(tmp_path / "eng_bench.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            (tmp_path / "SOURCE_INVENTORY.csv").write_text(
                "path,task,doc_id,domain,source_url,public_status,textlayer_status,render_status,notes\n",
                encoding="utf-8",
            )
            (tmp_path / "SOURCE_CANDIDATES.csv").write_text(
                "candidate_id,domain,task_fit,rights_tier,source_url,revision_family_potential,annotation_yield,priority_score,notes\n",
                encoding="utf-8",
            )
            (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").write_text(
                "candidate_id,browser_result,source_validity,release_posture,next_action\n",
                encoding="utf-8",
            )
            (tmp_path / "SOURCE_INTAKE_LOG.md").write_text("# none\n", encoding="utf-8")
            (tmp_path / "docs").mkdir()
            (tmp_path / "docs" / "HUMAN_REVIEW_CATEGORY_GUIDE.md").write_text(
                "# Guide\n",
                encoding="utf-8",
            )
            processed = tmp_path / "derived" / "human_adjudication" / "processed_returns" / "status"
            processed.mkdir(parents=True)
            (processed / "processing_summary.md").write_text("# Summary\n", encoding="utf-8")
            (processed / "processing_summary.json").write_text("{}\n", encoding="utf-8")
            extra_pack = tmp_path / "derived" / "review_packs" / "new_pack"
            extra_pack.mkdir(parents=True)
            (extra_pack / "index.html").write_text("<html></html>\n", encoding="utf-8")

            report = mod.build_handoff_package(
                root=tmp_path,
                source_packet=source_packet,
                handoff_dir=tmp_path / "derived" / "human_adjudication" / "handoff",
                zip_output=tmp_path / "derived" / "human_adjudication" / "handoff.zip",
                processed_status_dir=processed,
                date_label="2026-06-02",
                limit=3,
                extra_review_packs=[extra_pack],
            )

            self.assertTrue(report["valid"])
            self.assertGreater(report["copied_packet_files"], 0)
            self.assertEqual(report["extra_review_packs"]["copied_packs"], ["new_pack"])
            self.assertTrue(report["zip_status"]["has_5_25_packet"])
            self.assertEqual(report["zip_status"]["missing_required_entries"], [])
            with zipfile.ZipFile(tmp_path / "derived" / "human_adjudication" / "handoff.zip") as zf:
                names = set(zf.namelist())
            self.assertIn("handoff/03_new_machine_review_packs/new_pack/index.html", names)

    def test_parallel_handoff_zip_verifier_reports_missing_entries(self):
        mod = load_module(
            "build_parallel_human_handoff",
            ROOT / "tools" / "build_parallel_human_handoff.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            zip_path = tmp_path / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("handoff/README.md", "partial")

            status = mod.verify_zip(zip_path, "handoff", "2026-06-02")

            self.assertTrue(status["exists"])
            self.assertFalse(status["valid"])
            self.assertIn("handoff/HUMAN_REVIEW_ORDER.md", status["missing_required_entries"])

    def test_verify_handoff_package_checks_extra_pack_evidence(self):
        mod = load_module(
            "verify_handoff_package",
            ROOT / "tools" / "verify_handoff_package.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            handoff = tmp_path / "handoff"
            status_dir = handoff / "02_status_and_queues"
            status_dir.mkdir(parents=True)
            (handoff / "README.md").write_text("readme\n", encoding="utf-8")
            (handoff / "HUMAN_REVIEW_ORDER.md").write_text("order\n", encoding="utf-8")
            (handoff / "01_current_5_25_packet").mkdir()
            for name in (
                "GOLD_EXPANSION_HUMAN_QUEUE.csv",
                "GOLD_EXPANSION_MACHINE_QUEUE.csv",
                "SOURCE_CONVERSION_LOCAL_QUEUE.csv",
                "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv",
            ):
                (status_dir / name).write_text("id\n", encoding="utf-8")
            pack = handoff / "03_new_machine_review_packs" / "pack_a"
            (pack / "crops").mkdir(parents=True)
            (pack / "pages").mkdir()
            Image.new("RGB", (10, 10), "white").save(pack / "crops" / "c0.png")
            Image.new("RGB", (10, 10), "white").save(pack / "pages" / "doc__p0000.png")
            write_jsonl(
                pack / "manifest.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "crop_path": "derived/review_packs/pack_a/crops/c0.png",
                        "page_path": "derived/review_packs/pack_a/pages/doc__p0000.png",
                    }
                ],
            )
            with (pack / "pack_checklist.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "crop_path", "page_path", "review_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "c0",
                        "crop_path": "crops/c0.png",
                        "page_path": "pages/doc__p0000.png",
                        "review_status": "",
                    }
                )

            report = mod.verify_handoff_dir(handoff)
            self.assertTrue(report["valid"])
            self.assertEqual(report["totals"]["checklist_rows"], 1)
            self.assertEqual(report["totals"]["page_files"], 1)

            (pack / "pages" / "doc__p0000.png").unlink()
            broken = mod.verify_handoff_dir(handoff)
            self.assertFalse(broken["valid"])
            self.assertTrue(any("checklist local evidence paths missing" in issue for issue in broken["issues"]))

    def test_verify_review_batch_package_checks_folder_and_zip(self):
        builder = load_module(
            "build_next_review_batch",
            ROOT / "tools" / "build_next_review_batch.py",
        )
        verifier = load_module(
            "verify_review_batch_package",
            ROOT / "tools" / "verify_review_batch_package.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "target_text": "J5",
                        "proposed_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    }
                ],
            )

            builder.build_next_batch(
                root=tmp_path,
                queues=[
                    builder.QueueSpec(
                        "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_doc_a",
                        "Doc A",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-03",
                pad_px=4,
            )

            folder_report = verifier.verify_batch_dir(tmp_path / "derived/human_adjudication/batch")
            zip_report = verifier.verify_batch_zip(tmp_path / "derived/human_adjudication/batch.zip")

            self.assertTrue(folder_report["valid"])
            self.assertTrue(zip_report["valid"])
            self.assertEqual(folder_report["totals"]["checklist_rows"], 1)
            self.assertEqual(zip_report["totals"]["page_files"], 1)

            crop = next((tmp_path / "derived/human_adjudication/batch/review_packs/pack_doc_a/crops").glob("*.png"))
            crop.unlink()
            broken = verifier.verify_batch_dir(tmp_path / "derived/human_adjudication/batch")
            self.assertFalse(broken["valid"])
            self.assertTrue(any("checklist local evidence paths missing" in issue for issue in broken["issues"]))

    def test_build_next_review_batch_exports_packs_and_zip(self):
        mod = load_module(
            "build_next_review_batch",
            ROOT / "tools" / "build_next_review_batch.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "target_text": "J5",
                        "proposed_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    }
                ],
            )

            report = mod.build_next_batch(
                root=tmp_path,
                queues=[
                    mod.QueueSpec(
                        "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_doc_a",
                        "Doc A",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-03",
                pad_px=4,
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["rows"], 1)
            self.assertTrue((tmp_path / "derived/human_adjudication/batch.zip").exists())
            self.assertTrue(
                (tmp_path / "derived/human_adjudication/batch/next_review_batch_build_report.md").exists()
            )
            checklist = read_jsonl(
                tmp_path / "derived/human_adjudication/batch/review_packs/pack_doc_a/manifest.jsonl"
            )
            self.assertEqual(checklist[0]["page_path"], "derived/review_packs/pack_doc_a/pages/doc_a__p0000.png")
            checklist_path = (
                tmp_path
                / "derived/human_adjudication/batch/review_packs/pack_doc_a/pack_doc_a_validation_checklist.csv"
            )
            with checklist_path.open(encoding="utf-8") as f:
                csv_rows = list(csv.DictReader(f))
            self.assertEqual(csv_rows[0]["crop_path"], "crops/c0.png")
            self.assertEqual(csv_rows[0]["page_path"], "pages/doc_a__p0000.png")

    def test_build_next_review_batch_blocks_prior_packet_overlap(self):
        mod = load_module(
            "build_next_review_batch",
            ROOT / "tools" / "build_next_review_batch.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "target_text": "J5",
                        "proposed_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "prior_packet" / "review_packs" / "old_pack" / "manifest.jsonl",
                [{"candidate_id": "c0"}],
            )

            report = mod.build_next_batch(
                root=tmp_path,
                queues=[
                    mod.QueueSpec(
                        "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_doc_a",
                        "Doc A",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-03",
                pad_px=4,
                exclude_roots=[Path("prior_packet")],
            )

            self.assertFalse(report["valid"])
            self.assertEqual(report["overlap_count"], 1)
            self.assertEqual(report["overlap_examples"][0]["candidate_id"], "c0")
            self.assertFalse((tmp_path / "derived/human_adjudication/batch.zip").exists())
            self.assertTrue(
                (tmp_path / "derived/human_adjudication/batch/next_review_batch_build_report.md").exists()
            )

    def test_build_next_review_batch_filters_active_packet_index_rows(self):
        mod = load_module(
            "build_next_review_batch_filter_packet",
            ROOT / "tools" / "build_next_review_batch.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "target_text": "J5",
                        "proposed_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [40, 40, 60, 60],
                        "target_text": "J6",
                        "proposed_text": "J6",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                ],
            )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            write_jsonl(
                packet_root / "review_packs" / "pack_doc_a" / "manifest.jsonl",
                [{"candidate_id": "c0", "doc_id": "doc_a"}],
            )
            index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-03.json"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.build_next_batch(
                root=tmp_path,
                queues=[
                    mod.QueueSpec(
                        "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_doc_a",
                        "Doc A",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-03",
                pad_px=4,
                exclude_active_packet_date_label="2026-06-03",
            )
            manifest = read_jsonl(
                tmp_path / "derived/human_adjudication/batch/review_packs/pack_doc_a/manifest.jsonl"
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["rows"], 1)
            self.assertEqual(report["excluded_packet_rows"], 1)
            self.assertEqual(report["active_packet_candidate_count"], 1)
            self.assertEqual(manifest[0]["candidate_id"], "c1")

    def test_build_next_review_batch_excludes_reviewed_sibling_rows(self):
        mod = load_module(
            "build_next_review_batch",
            ROOT / "tools" / "build_next_review_batch.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            source = tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "target_text": "J5",
                        "proposed_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [40, 40, 60, 60],
                        "target_text": "J6",
                        "proposed_text": "J6",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                ],
            )
            write_jsonl(
                source.with_name("microtext_review_doc_a_reviewed.jsonl"),
                [{"candidate_id": "c0", "review_status": "accepted"}],
            )

            report = mod.build_next_batch(
                root=tmp_path,
                queues=[
                    mod.QueueSpec(
                        "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_doc_a",
                        "Doc A",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-03",
                pad_px=4,
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["source_rows"], 2)
            self.assertEqual(report["excluded_reviewed_rows"], 1)
            self.assertEqual(report["rows"], 1)
            manifest = read_jsonl(
                tmp_path / "derived/human_adjudication/batch/review_packs/pack_doc_a/manifest.jsonl"
            )
            self.assertEqual([row["candidate_id"] for row in manifest], ["c1"])

    def test_process_next_review_batch_return_stages_reviewed_rows(self):
        mod = load_module(
            "process_next_review_batch_return",
            ROOT / "tools" / "process_next_review_batch_return.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(page)
            source = tmp_path / "microtext" / "annotations" / "microtext_review_doc_a.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_a",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "proposed_text": "J5",
                        "target_text": "J5",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "doc_a",
                        "page_index": 0,
                        "bbox": [5, 6, 7, 8],
                        "proposed_text": "bad",
                        "target_text": "bad",
                        "category": "pin_label",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "needs_review",
                    },
                ],
            )
            batch = tmp_path / "derived" / "human_adjudication" / "batch"
            pack = batch / "review_packs" / "pack_a"
            (pack / "crops").mkdir(parents=True)
            (pack / "pages").mkdir()
            Image.new("RGB", (10, 10), "white").save(pack / "crops" / "c0.png")
            Image.new("RGB", (10, 10), "white").save(pack / "crops" / "c1.png")
            Image.new("RGB", (100, 100), "white").save(pack / "pages" / "doc_a__p0000.png")
            with (batch / "NEXT_REVIEW_BATCH_MANIFEST.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["source_jsonl", "pack_name", "checklist", "rows"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source_jsonl": "microtext/annotations/microtext_review_doc_a.jsonl",
                        "pack_name": "pack_a",
                        "checklist": "pack_a_validation_checklist.csv",
                        "rows": "2",
                    }
                )
            with (pack / "pack_a_validation_checklist.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "proposed_text",
                        "crop_path",
                        "page_path",
                        "image_path",
                        "review_status",
                        "corrected_text",
                        "corrected_category",
                        "review_notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "c0",
                        "proposed_text": "J5",
                        "crop_path": "crops/c0.png",
                        "page_path": "pages/doc_a__p0000.png",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "accepted",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "c1",
                        "proposed_text": "bad",
                        "crop_path": "crops/c1.png",
                        "page_path": "pages/doc_a__p0000.png",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                        "review_status": "edited",
                        "corrected_text": "TP7",
                        "corrected_category": "pin_label",
                        "review_notes": "clear crop",
                    }
                )

            report, errors = mod.process_batch(
                tmp_path,
                batch,
                tmp_path / "derived" / "human_adjudication" / "processed",
            )
            staged = read_jsonl(
                tmp_path / "derived/human_adjudication/processed/pack_a_reviewed.jsonl"
            )

            self.assertEqual(errors, [])
            self.assertTrue(report["complete"])
            self.assertEqual(report["totals"]["mergeable_rows"], 2)
            self.assertEqual(staged[0]["review_status"], "accepted")
            self.assertEqual(staged[1]["review_status"], "edited")
            self.assertEqual(staged[1]["corrected_text"], "TP7")

    def test_build_human_packet_index_summarizes_current_handoffs(self):
        mod = load_module(
            "build_human_packet_index",
            ROOT / "tools" / "build_human_packet_index.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            date_label = "2026-06-03"
            parallel = tmp_path / "derived" / "human_adjudication" / f"{date_label}_v2_0_parallel_handoff"
            next_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{date_label}_next_review_batch_status"
            )
            diversity_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{date_label}_diversity_review_batch_status"
            )
            for path in (
                parallel / "02_status_and_queues",
                next_status,
                diversity_status,
                tmp_path / "derived" / "quality",
                tmp_path / "derived" / "human_adjudication",
            ):
                path.mkdir(parents=True, exist_ok=True)
            for name in (
                f"Eng_Bench_v2_0_parallel_human_handoff_{date_label}.zip",
                f"Eng_Bench_v2_0_next_review_batch_{date_label}.zip",
                f"Eng_Bench_v2_0_diversity_review_batch_{date_label}.zip",
            ):
                (tmp_path / "derived" / "human_adjudication" / name).write_text("zip\n", encoding="utf-8")

            (parallel / "handoff_build_report.json").write_text(
                json.dumps({"valid": True, "zipped_entries": 10, "issues": []}),
                encoding="utf-8",
            )
            (parallel / "02_status_and_queues" / f"processing_summary_{date_label}.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {"rows": 5, "mergeable_rows": 0, "missing_evidence_refs": 0},
                        "tasks": [{"apply_stats": {"blank": 5}}],
                    }
                ),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"handoff_verification_{date_label}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 12,
                        "issues": [],
                        "totals": {"checklist_rows": 2, "crop_files": 2, "page_files": 1},
                    }
                ),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"next_review_batch_{date_label}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "rows": 3,
                        "source_rows": 12,
                        "excluded_reviewed_rows": 7,
                        "excluded_active_gold_rows": 2,
                        "overlap_count": 0,
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"next_review_batch_zip_verification_{date_label}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 9,
                        "issues": [],
                        "totals": {"checklist_rows": 3, "crop_files": 3, "page_files": 2},
                    }
                ),
                encoding="utf-8",
            )
            (next_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 3,
                            "blank_rows": 3,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"diversity_review_batch_{date_label}.json").write_text(
                json.dumps({"valid": True, "rows": 4, "source_rows": 4, "overlap_count": 0, "issues": []}),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"diversity_review_batch_zip_verification_{date_label}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 11,
                        "issues": [],
                        "totals": {"checklist_rows": 4, "crop_files": 4, "page_files": 1},
                    }
                ),
                encoding="utf-8",
            )
            (diversity_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 4,
                            "blank_rows": 4,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            index = mod.build_index(tmp_path, date_label)
            markdown = mod.render_markdown(index)

            self.assertEqual(index["totals"]["packets"], 3)
            self.assertEqual(index["totals"]["ready_to_send"], 3)
            self.assertEqual(index["totals"]["review_rows"], 14)
            self.assertEqual(index["totals"]["extra_review_rows"], 2)
            self.assertEqual(index["totals"]["excluded_reviewed_rows"], 7)
            self.assertEqual(index["totals"]["excluded_active_gold_rows"], 2)
            self.assertEqual(index["packets"][0]["human_status"], "unfilled_primary_plus_extra_pack")
            self.assertIn("not the obsolete broad 948-row", markdown)

            stored_base = json.loads(json.dumps(index))
            prior_optional = dict(stored_base["packets"][0])
            prior_optional.update(
                {
                    "priority": 4,
                    "packet_id": "2026-06-02_prior_optional_review",
                    "kind": "standalone_review_batch",
                    "intended_use": "Previously indexed optional packet.",
                    "review_rows": 1,
                    "primary_rows": 1,
                    "extra_review_rows": 0,
                    "source_rows": 1,
                    "zip_entries": 1,
                    "crop_files": 1,
                    "page_files": 1,
                }
            )
            stored_base["packets"].append(prior_optional)
            (tmp_path / "derived" / "quality" / f"human_packet_index_{date_label}.json").write_text(
                json.dumps(stored_base),
                encoding="utf-8",
            )

            new_date = "2026-06-04"
            unpacketed_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{new_date}_unpacketed_review_batch_status"
            )
            unpacketed_status.mkdir(parents=True, exist_ok=True)
            (tmp_path / "derived" / "human_adjudication" / f"Eng_Bench_v2_0_unpacketed_review_batch_{new_date}.zip").write_text(
                "zip\n",
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"unpacketed_review_batch_{new_date}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "rows": 2,
                        "source_rows": 3,
                        "excluded_packet_rows": 1,
                        "excluded_reviewed_rows": 0,
                        "excluded_active_gold_rows": 0,
                        "overlap_count": 0,
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"unpacketed_review_batch_zip_verification_{new_date}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 8,
                        "issues": [],
                        "totals": {"checklist_rows": 2, "crop_files": 2, "page_files": 1},
                    }
                ),
                encoding="utf-8",
            )
            (unpacketed_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 2,
                            "blank_rows": 2,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            carried = mod.build_index(tmp_path, new_date, base_date_label=date_label)
            self.assertEqual(carried["base_date_label"], date_label)
            self.assertEqual(carried["totals"]["packets"], 5)
            self.assertEqual(carried["totals"]["review_rows"], 17)
            self.assertEqual(carried["totals"]["excluded_packet_rows"], 1)
            self.assertEqual(carried["packets"][-1]["packet_id"], f"{new_date}_unpacketed_review")
            self.assertIn("2026-06-02_prior_optional_review", [row["packet_id"] for row in carried["packets"]])

            latest_date = "2026-06-05"
            latest_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{latest_date}_unpacketed_review_batch_status"
            )
            latest_status.mkdir(parents=True, exist_ok=True)
            (tmp_path / "derived" / "human_adjudication" / f"Eng_Bench_v2_0_unpacketed_review_batch_{latest_date}.zip").write_text(
                "zip\n",
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"unpacketed_review_batch_{latest_date}.json").write_text(
                json.dumps({"valid": True, "rows": 1, "source_rows": 3, "issues": []}),
                encoding="utf-8",
            )
            (tmp_path / "derived" / "quality" / f"unpacketed_review_batch_zip_verification_{latest_date}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 9,
                        "issues": [],
                        "totals": {"checklist_rows": 3, "crop_files": 3, "page_files": 1},
                    }
                ),
                encoding="utf-8",
            )
            (latest_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 3,
                            "blank_rows": 3,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            multi_carried = mod.build_index(
                tmp_path,
                latest_date,
                base_date_label=date_label,
                carry_forward_date_labels=[new_date],
            )
            self.assertEqual(multi_carried["carry_forward_date_labels"], [new_date])
            self.assertEqual(multi_carried["totals"]["packets"], 6)
            self.assertEqual(multi_carried["totals"]["review_rows"], 20)
            self.assertEqual(multi_carried["packets"][-1]["review_rows"], 3)
            self.assertEqual(multi_carried["packets"][-1]["primary_rows"], 3)
            self.assertEqual(
                [row["packet_id"] for row in multi_carried["packets"][-2:]],
                [f"{new_date}_unpacketed_review", f"{latest_date}_unpacketed_review"],
            )
            self.assertIn(
                "inspect NEXT_REVIEW_BATCH_MANIFEST.csv",
                multi_carried["packets"][-1]["intended_use"],
            )

            source_family_date = "2026-06-06"
            source_family_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{source_family_date}_source_family_review_batch_status"
            )
            source_family_status.mkdir(parents=True, exist_ok=True)
            (
                tmp_path
                / "derived"
                / "human_adjudication"
                / f"Eng_Bench_v2_0_source_family_review_batch_{source_family_date}.zip"
            ).write_text("zip\n", encoding="utf-8")
            (tmp_path / "derived" / "quality" / f"source_family_review_batch_{source_family_date}.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "rows": 6,
                        "source_rows": 8,
                        "excluded_packet_rows": 2,
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            (
                tmp_path
                / "derived"
                / "quality"
                / f"source_family_review_batch_zip_verification_{source_family_date}.json"
            ).write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 20,
                        "issues": [],
                        "totals": {"checklist_rows": 6, "crop_files": 6, "page_files": 4},
                    }
                ),
                encoding="utf-8",
            )
            (source_family_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 6,
                            "blank_rows": 6,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            source_family_index = mod.build_index(tmp_path, source_family_date, base_date_label=date_label)
            self.assertEqual(source_family_index["totals"]["packets"], 5)
            self.assertEqual(source_family_index["totals"]["review_rows"], 21)
            self.assertEqual(source_family_index["totals"]["excluded_packet_rows"], 2)
            self.assertEqual(
                source_family_index["packets"][-1]["packet_id"],
                f"{source_family_date}_source_family_review",
            )
            self.assertIn("VisualDiff revision families", source_family_index["packets"][-1]["intended_use"])

            carried_next_date = "2026-06-07"
            carried_next_status = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "processed_returns"
                / f"{carried_next_date}_next_review_batch_status"
            )
            carried_next_status.mkdir(parents=True, exist_ok=True)
            carried_next_batch = (
                tmp_path
                / "derived"
                / "human_adjudication"
                / f"{carried_next_date}_v2_0_next_review_batch"
            )
            carried_next_batch.mkdir(parents=True, exist_ok=True)
            (
                tmp_path
                / "derived"
                / "human_adjudication"
                / f"Eng_Bench_v2_0_next_review_batch_{carried_next_date}.zip"
            ).write_text("zip\n", encoding="utf-8")
            (carried_next_batch / "next_review_batch_build_report.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "rows": 7,
                        "source_rows": 9,
                        "excluded_packet_rows": 2,
                        "issues": [],
                    }
                ),
                encoding="utf-8",
            )
            (
                tmp_path
                / "derived"
                / "quality"
                / f"next_review_batch_zip_verification_{carried_next_date}.json"
            ).write_text(
                json.dumps(
                    {
                        "valid": True,
                        "entry_count": 24,
                        "issues": [],
                        "totals": {"checklist_rows": 7, "crop_files": 7, "page_files": 3},
                    }
                ),
                encoding="utf-8",
            )
            (carried_next_status / "processing_summary.json").write_text(
                json.dumps(
                    {
                        "complete": False,
                        "totals": {
                            "checklist_rows": 7,
                            "blank_rows": 7,
                            "mergeable_rows": 0,
                            "missing_evidence_refs": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            next_carried_index = mod.build_index(
                tmp_path,
                carried_next_date,
                base_date_label=date_label,
                carry_forward_date_labels=[new_date, latest_date, source_family_date],
            )
            self.assertEqual(next_carried_index["totals"]["packets"], 8)
            self.assertEqual(next_carried_index["totals"]["review_rows"], 33)
            self.assertEqual(next_carried_index["totals"]["excluded_packet_rows"], 5)
            self.assertEqual(
                next_carried_index["packets"][-1]["packet_id"],
                f"{carried_next_date}_fresh_follow_on",
            )
            self.assertIn("Fresh mixed microtext", next_carried_index["packets"][-1]["intended_use"])

            exit_code = mod.main(["--root", str(tmp_path), "--date-label", date_label])
            self.assertEqual(exit_code, 0)
            self.assertTrue((tmp_path / "derived" / "quality" / f"human_packet_index_{date_label}.json").exists())
            self.assertTrue(
                (tmp_path / "derived" / "human_adjudication" / f"HUMAN_PACKET_INDEX_{date_label}.csv").exists()
            )

    def test_source_conversion_readiness_writes_header_for_empty_repair_queue(self):
        mod = load_module(
            "audit_source_conversion_readiness_empty_queue",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "repair_queue.csv"
            mod.write_csv(output, [], fieldnames=mod.SOURCE_INVENTORY_REPAIR_FIELDS)
            with output.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(rows, [])
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines()[0],
                ",".join(mod.SOURCE_INVENTORY_REPAIR_FIELDS),
            )

    def test_source_conversion_readiness_uses_canonical_textlayer_without_double_counting_pages(self):
        mod = load_module(
            "audit_source_conversion_readiness_canonical_textlayer",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "derived" / "textlayer" / "doc_ready.jsonl",
                [
                    {"page": 0, "text": "A"},
                    {"page": 0, "text": "B"},
                    {"page": 1, "text": "C"},
                ],
            )
            page_path = tmp_path / "derived" / "textlayer" / "doc_ready" / "page_000.json"
            page_path.parent.mkdir(parents=True)
            page_path.write_text(
                json.dumps({"page": 0, "spans": [{"text": "A"}, {"text": "B"}]}),
                encoding="utf-8",
            )

            self.assertEqual(mod.textlayer_span_count(tmp_path, "doc_ready"), 3)

    def test_source_conversion_readiness_counts_spans_in_page_json_fallback(self):
        mod = load_module(
            "audit_source_conversion_readiness_page_json_fallback",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "textlayer" / "doc_ready" / "page_000.json"
            page_path.parent.mkdir(parents=True)
            page_path.write_text(
                json.dumps({"page": 0, "spans": [{"text": "A"}, {"text": "B"}]}),
                encoding="utf-8",
            )

            self.assertEqual(mod.textlayer_span_count(tmp_path, "doc_ready"), 2)

    def test_source_conversion_readiness_splits_local_and_candidate_next_steps(self):
        mod = load_module(
            "audit_source_conversion_readiness",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_review" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(page_path)
            ready_page_path = tmp_path / "derived" / "pages_300dpi" / "doc_ready" / "page_000.png"
            ready_page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(ready_page_path)
            unmineable_page_path = (
                tmp_path / "derived" / "pages_300dpi" / "doc_unmineable" / "page_000.png"
            )
            unmineable_page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(unmineable_page_path)
            textlayer = tmp_path / "derived" / "textlayer" / "doc_ready.jsonl"
            textlayer.parent.mkdir(parents=True)
            textlayer.write_text(
                '{"page": 0, "text": "TIC-101", "bbox_px": [1, 1, 5, 5]}\n',
                encoding="utf-8",
            )
            (tmp_path / "derived" / "textlayer" / "doc_unmineable.jsonl").write_text(
                '{"page": 0, "text": "general note without useful labels", "bbox_px": [1, 1, 5, 5]}\n',
                encoding="utf-8",
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "path",
                        "task",
                        "doc_id",
                        "domain",
                        "source_url",
                        "public_status",
                        "textlayer_status",
                        "render_status",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_review.pdf",
                        "task": "microtext",
                        "doc_id": "doc_review",
                        "domain": "pid",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_ready.pdf",
                        "task": "microtext",
                        "doc_id": "doc_ready",
                        "domain": "mechanical_cad",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_unmineable.pdf",
                        "task": "microtext",
                        "doc_id": "doc_unmineable",
                        "domain": "mechanical_cad",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_rights.pdf",
                        "task": "microtext",
                        "doc_id": "doc_rights",
                        "domain": "pid",
                        "public_status": "restricted_public_utility_pdf_candidate",
                    }
                )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_review.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "source_candidate_id": "pid_001",
                        "doc_id": "doc_review",
                        "review_status": "needs_review",
                        "image_path": "derived/pages_300dpi/doc_review/page_000.png",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_rights.jsonl",
                [
                    {
                        "candidate_id": "c1",
                        "source_candidate_id": "pid_002",
                        "doc_id": "doc_rights",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "mt0", "doc_id": "doc_gold", "category": "pin_label"}],
            )
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "domain",
                        "task_fit",
                        "rights_tier",
                        "source_url",
                        "priority_score",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "visualdiff,microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/pid.pdf",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "mech_001",
                        "domain": "mechanical_cad",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/mech.pdf",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "pid_002",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "rights_uncertain",
                        "source_url": "https://example.com/restricted.pdf",
                    }
                )
            with (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "release_posture",
                        "next_action",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "mech_001",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                    }
                )
            (tmp_path / "SOURCE_INTAKE_LOG.md").write_text("Candidate: `pid_001`\n", encoding="utf-8")

            report = mod.build_report(tmp_path)
            markdown = mod.render_markdown(report)

            local_by_doc = {row["doc_id"]: row for row in report["local_sources"]}
            candidate_by_id = {row["candidate_id"]: row for row in report["candidate_sources"]}
            self.assertEqual(local_by_doc["doc_review"]["next_step"], "human_review")
            self.assertEqual(local_by_doc["doc_ready"]["next_step"], "mine_candidates_and_export_review")
            self.assertEqual(local_by_doc["doc_ready"]["mineable_candidates"], 1)
            self.assertEqual(
                local_by_doc["doc_unmineable"]["next_step"],
                "ocr_or_manual_region_proposal",
            )
            self.assertEqual(local_by_doc["doc_unmineable"]["mineable_candidates"], 0)
            self.assertEqual(local_by_doc["doc_rights"]["next_step"], "rights_review_or_hold")
            self.assertEqual(local_by_doc["doc_rights"]["fresh_open_review_rows"], 0)
            self.assertEqual(local_by_doc["doc_rights"]["rights_blocked_open_review_rows"], 1)
            self.assertEqual(candidate_by_id["pid_001"]["next_step"], "human_review")
            self.assertEqual(candidate_by_id["mech_001"]["next_step"], "import_render_extract")
            self.assertEqual(candidate_by_id["pid_002"]["next_step"], "rights_review_or_hold")
            self.assertEqual(candidate_by_id["pid_002"]["fresh_open_review_rows"], 0)
            self.assertEqual(candidate_by_id["pid_002"]["rights_blocked_open_review_rows"], 1)
            self.assertEqual(report["totals"]["rights_blocked_open_review_rows_by_local_source"], 1)
            self.assertEqual(report["totals"]["rights_blocked_open_review_rows_by_candidate"], 1)
            self.assertIn("Source Conversion Readiness", markdown)

    def test_source_conversion_readiness_marks_active_packet_rows(self):
        mod = load_module(
            "audit_source_conversion_readiness_packeted",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_review" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(page_path)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_review.pdf",
                        "task": "microtext",
                        "doc_id": "doc_review",
                        "domain": "pid",
                        "public_status": "public_domain_candidate",
                    }
                )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_review.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "source_candidate_id": "pid_001",
                        "doc_id": "doc_review",
                        "review_status": "needs_review",
                        "image_path": "derived/pages_300dpi/doc_review/page_000.png",
                    }
                ],
            )
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "domain", "task_fit", "rights_tier", "source_url"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/pid.pdf",
                    }
                )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            write_jsonl(
                packet_root / "review_packs" / "microtext_doc_review" / "manifest.jsonl",
                [{"candidate_id": "c0", "doc_id": "doc_review", "category": "instrument_tag"}],
            )
            packet_index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-03.json"
            packet_index.parent.mkdir(parents=True, exist_ok=True)
            packet_index.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.build_report(tmp_path, date_label="2026-06-03")
            markdown = mod.render_markdown(report)

            local_row = report["local_sources"][0]
            candidate_row = report["candidate_sources"][0]
            self.assertEqual(local_row["next_step"], "await_human_return")
            self.assertEqual(local_row["packeted_open_review_rows"], 1)
            self.assertEqual(local_row["unpacketed_open_review_rows"], 0)
            self.assertEqual(candidate_row["next_step"], "await_human_return")
            self.assertEqual(report["totals"]["active_packet_row_keys"], 1)
            self.assertIn("already in active human packets", markdown)

    def test_source_conversion_readiness_uses_latest_packet_index_when_requested_label_is_missing(self):
        mod = load_module(
            "audit_source_conversion_readiness_packeted_latest",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_review" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(page_path)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_review.pdf",
                        "task": "microtext",
                        "doc_id": "doc_review",
                        "domain": "pid",
                        "public_status": "public_domain_candidate",
                    }
                )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_review.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "source_candidate_id": "pid_001",
                        "doc_id": "doc_review",
                        "review_status": "needs_review",
                        "image_path": "derived/pages_300dpi/doc_review/page_000.png",
                    }
                ],
            )
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "domain", "task_fit", "rights_tier", "source_url"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/pid.pdf",
                    }
                )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            write_jsonl(
                packet_root / "review_packs" / "microtext_doc_review" / "manifest.jsonl",
                [{"candidate_id": "c0", "doc_id": "doc_review", "category": "instrument_tag"}],
            )
            packet_index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-02.json"
            packet_index.parent.mkdir(parents=True, exist_ok=True)
            packet_index.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.build_report(tmp_path, date_label="2026-06-03")

            local_row = report["local_sources"][0]
            self.assertEqual(local_row["next_step"], "await_human_return")
            self.assertEqual(report["totals"]["active_packet_row_keys"], 1)
            self.assertEqual(report["totals"]["active_packet_index_label"], "2026-06-02")

    def test_source_conversion_readiness_reads_packet_checklist_csv_without_jsonl_manifest(self):
        mod = load_module(
            "audit_source_conversion_readiness_packeted_csv",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_review" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(page_path)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_review.pdf",
                        "task": "microtext",
                        "doc_id": "doc_review",
                        "domain": "pid",
                        "public_status": "public_domain_candidate",
                    }
                )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_review.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "source_candidate_id": "pid_001",
                        "doc_id": "doc_review",
                        "review_status": "needs_review",
                        "image_path": "derived/pages_300dpi/doc_review/page_000.png",
                    }
                ],
            )
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "domain", "task_fit", "rights_tier", "source_url"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/pid.pdf",
                    }
                )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            checklist_path = (
                packet_root
                / "review_packs"
                / "microtext_doc_review"
                / "microtext_doc_review_validation_checklist.csv"
            )
            checklist_path.parent.mkdir(parents=True, exist_ok=True)
            with checklist_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["candidate_id", "doc_id", "category"])
                writer.writeheader()
                writer.writerow({"candidate_id": "c0", "doc_id": "doc_review", "category": "instrument_tag"})
            packet_index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-03.json"
            packet_index.parent.mkdir(parents=True, exist_ok=True)
            packet_index.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.build_report(tmp_path, date_label="2026-06-03")

            local_row = report["local_sources"][0]
            self.assertEqual(local_row["next_step"], "await_human_return")
            self.assertEqual(local_row["packeted_open_review_rows"], 1)
            self.assertEqual(local_row["unpacketed_open_review_rows"], 0)
            self.assertEqual(report["totals"]["active_packet_row_keys"], 1)

    def test_source_conversion_readiness_marks_reviewed_sibling_open_rows_stale(self):
        mod = load_module(
            "audit_source_conversion_readiness_stale",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_review" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(page_path)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_review.pdf",
                        "task": "microtext",
                        "doc_id": "doc_review",
                        "domain": "mechanical_cad",
                        "public_status": "public_domain_candidate",
                    }
                )
            review_path = tmp_path / "microtext" / "annotations" / "microtext_review_doc_review.jsonl"
            write_jsonl(
                review_path,
                [
                    {
                        "candidate_id": "c0",
                        "source_candidate_id": "mech_001",
                        "doc_id": "doc_review",
                        "review_status": "needs_review",
                        "image_path": "derived/pages_300dpi/doc_review/page_000.png",
                    }
                ],
            )
            write_jsonl(
                review_path.with_name("microtext_review_doc_review_reviewed.jsonl"),
                [{"candidate_id": "c0", "doc_id": "doc_review", "review_status": "accepted"}],
            )
            write_jsonl(tmp_path / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "domain", "task_fit", "rights_tier", "source_url"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "mech_001",
                        "domain": "mechanical_cad",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.com/mech.pdf",
                    }
                )

            report = mod.build_report(tmp_path, date_label="2026-06-03")
            local_row = report["local_sources"][0]
            candidate_row = report["candidate_sources"][0]

            self.assertEqual(local_row["next_step"], "reviewed_sibling_or_active_gold")
            self.assertEqual(local_row["fresh_open_review_rows"], 0)
            self.assertEqual(local_row["stale_open_review_rows"], 1)
            self.assertEqual(candidate_row["next_step"], "reviewed_sibling_or_active_gold")
            self.assertEqual(report["totals"]["fresh_open_review_rows_by_local_source"], 0)
            self.assertEqual(report["totals"]["stale_open_review_rows_by_local_source"], 1)

    def test_source_conversion_readiness_uses_candidate_local_lineage_evidence(self):
        mod = load_module(
            "audit_source_conversion_readiness_lineage",
            ROOT / "tools" / "audit_source_conversion_readiness.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            renderer_hold_page = (
                tmp_path
                / "derived"
                / "pages_300dpi"
                / "doc_renderer_hold"
                / "page_000.png"
            )
            renderer_hold_page.parent.mkdir(parents=True)
            renderer_hold_page.write_bytes(b"renderer hold")
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "path",
                        "task",
                        "doc_id",
                        "domain",
                        "source_url",
                        "public_status",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_packet.pdf",
                        "task": "microtext",
                        "doc_id": "doc_packet",
                        "domain": "architectural",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_gold.pdf",
                        "task": "microtext",
                        "doc_id": "doc_gold",
                        "domain": "civil",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_note.pdf",
                        "task": "microtext",
                        "doc_id": "doc_note",
                        "domain": "mechanical_cad",
                        "public_status": "public_domain_candidate",
                        "notes": "Imported from browser-validated mech_010.",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_ambiguous.pdf",
                        "task": "microtext",
                        "doc_id": "doc_ambiguous",
                        "domain": "architectural",
                        "public_status": "public_domain_candidate",
                        "notes": "Possible relationship to arch_012 and arch_013.",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_rights.pdf",
                        "task": "microtext",
                        "doc_id": "doc_rights",
                        "domain": "mechanical_cad",
                        "public_status": "release_review_needed",
                        "notes": "Imported from browser-validated mech_011.",
                    }
                )
                writer.writerow(
                    {
                        "path": "visualdiff/docs/doc_hold.pdf",
                        "task": "visualdiff",
                        "doc_id": "doc_hold",
                        "domain": "pcb_schematic",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "microtext/docs/doc_path.pdf",
                        "task": "microtext",
                        "doc_id": "doc_path",
                        "domain": "pid",
                        "public_status": "public_domain_candidate",
                    }
                )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_packet.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc_packet",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_orphan.jsonl",
                [
                    {
                        "candidate_id": "c_orphan",
                        "doc_id": "doc_orphan",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_rights.jsonl",
                [
                    {
                        "candidate_id": "c_rights",
                        "doc_id": "doc_rights",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_doc_path.jsonl",
                [
                    {
                        "candidate_id": "c_path",
                        "doc_id": "doc_path",
                        "review_status": "needs_review",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "mt0", "doc_id": "doc_gold", "category": "room_label"}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "pair0",
                        "project_id": "vdiff__arch_011__old__to__new",
                        "source_candidate_id": "arch_011",
                    }
                ],
            )
            with (tmp_path / "SOURCE_CANDIDATES_RANKED.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "domain", "task_fit", "rights_tier", "source_url"],
                )
                writer.writeheader()
                for candidate_id, domain in (
                    ("arch_011", "architectural"),
                    ("civil_007", "civil"),
                    ("mech_010", "mechanical_cad"),
                    ("arch_012", "architectural"),
                    ("arch_013", "architectural"),
                    ("civil_009", "civil"),
                    ("mech_011", "mechanical_cad"),
                    ("pcb_013", "pcb_schematic"),
                    ("pcb_066", "pcb_schematic"),
                    ("pid_014", "pid"),
                    ("pid_052", "pid"),
                ):
                    writer.writerow(
                        {
                            "candidate_id": candidate_id,
                            "domain": domain,
                            "task_fit": "microtext",
                            "rights_tier": "public_domain_candidate",
                            "source_url": f"https://example.com/{candidate_id}",
                        }
                    )
            (tmp_path / "SOURCE_INTAKE_LOG.md").write_text(
                "\n".join(
                    [
                        "## Explicit intake",
                        "- Candidate: `arch_011`",
                        "- Manifest doc id: `doc_packet`",
                        "",
                        "## Candidate without a manifest doc",
                        "- Candidate: `arch_012`",
                        "- Notes: may later use doc_ambiguous",
                        "",
                        "## Explicit local doc missing from inventory",
                        "- Candidate: `civil_009`",
                        "- Manifest doc id: `doc_orphan`",
                        "",
                        "## Explicit nested local path",
                        "- `pid_014` GOV source:",
                        "  - Local PDF: `microtext/docs/doc_path.pdf`",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            import_dir = tmp_path / "derived" / "source_imports"
            import_dir.mkdir(parents=True)
            (import_dir / "civil_import.json").write_text(
                json.dumps(
                    {
                        "candidate_id": "civil_007",
                        "documents": [{"doc_id": "doc_gold"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (import_dir / "hold_import.json").write_text(
                json.dumps({"candidate_id": "pcb_013", "doc_id": "doc_hold"}) + "\n",
                encoding="utf-8",
            )
            (import_dir / "renderer_hold.json").write_text(
                json.dumps(
                    {
                        "candidate_id": "pid_052",
                        "doc_id": "doc_renderer_hold",
                        "release_posture": "renderer_blocked_hold",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (import_dir / "browser_validated_100_sources_2026-05-24.md").write_text(
                "Validated candidate: `pcb_066`\n",
                encoding="utf-8",
            )
            with (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["candidate_id", "release_posture", "next_action"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pcb_013",
                        "release_posture": "research_candidate",
                        "next_action": "hold_identical_pdf_pair",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "pcb_066",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                    }
                )
                writer.writerow(
                    {
                        "candidate_id": "pid_052",
                        "release_posture": "hold",
                        "next_action": "use_compatible_svg_renderer_or_replace_source",
                    }
                )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet_a"
            write_jsonl(
                packet_root / "review_packs" / "microtext_doc_packet" / "manifest.jsonl",
                [
                    {"candidate_id": "c0", "doc_id": "doc_packet", "category": "room_label"},
                    {"candidate_id": "c_orphan", "doc_id": "doc_orphan", "category": "dimension_value"},
                    {"candidate_id": "c_path", "doc_id": "doc_path", "category": "instrument_tag"},
                ],
            )
            packet_index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-05.json"
            packet_index.parent.mkdir(parents=True, exist_ok=True)
            packet_index.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet_a",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.build_report(tmp_path, date_label="2026-06-05")
            markdown = mod.render_markdown(report)
            candidate_by_id = {row["candidate_id"]: row for row in report["candidate_sources"]}

            self.assertEqual(candidate_by_id["arch_011"]["linked_local_doc_ids"], "doc_packet")
            self.assertEqual(candidate_by_id["arch_011"]["next_step"], "await_human_return")
            self.assertEqual(candidate_by_id["civil_007"]["linked_local_doc_ids"], "doc_gold")
            self.assertEqual(
                candidate_by_id["civil_007"]["next_step"],
                "linked_local_active_gold_or_reviewed",
            )
            self.assertEqual(candidate_by_id["mech_010"]["linked_local_doc_ids"], "doc_note")
            self.assertEqual(candidate_by_id["civil_009"]["linked_local_doc_ids"], "doc_orphan")
            self.assertEqual(candidate_by_id["civil_009"]["linked_inventory_source_count"], 0)
            self.assertEqual(candidate_by_id["civil_009"]["linked_local_orphan_doc_ids"], "doc_orphan")
            self.assertEqual(candidate_by_id["civil_009"]["next_step"], "await_human_return")
            self.assertEqual(candidate_by_id["mech_011"]["next_step"], "rights_review_or_hold")
            self.assertEqual(candidate_by_id["pcb_013"]["next_step"], "select_or_deprioritize")
            self.assertEqual(candidate_by_id["pcb_066"]["next_step"], "import_render_extract")
            self.assertEqual(candidate_by_id["pid_052"]["linked_local_doc_ids"], "doc_renderer_hold")
            self.assertEqual(candidate_by_id["pid_052"]["linked_inventory_source_count"], 0)
            self.assertEqual(candidate_by_id["pid_052"]["next_step"], "select_or_deprioritize")
            self.assertEqual(candidate_by_id["pid_014"]["linked_local_doc_ids"], "doc_path")
            self.assertEqual(candidate_by_id["pid_014"]["next_step"], "await_human_return")
            self.assertIn("source_intake_local_path", candidate_by_id["pid_014"]["lineage_evidence"])
            self.assertEqual(candidate_by_id["arch_012"]["linked_local_source_count"], 0)
            self.assertEqual(candidate_by_id["arch_013"]["linked_local_source_count"], 0)
            self.assertEqual(report["totals"]["candidate_sources_with_explicit_local_lineage"], 8)
            self.assertEqual(report["totals"]["explicit_candidate_local_links"], 8)
            self.assertEqual(report["totals"]["explicit_lineage_docs_missing_inventory"], 1)
            self.assertEqual(len(report["candidate_lineage"]), 8)
            self.assertEqual(len(report["source_inventory_repair_queue"]), 1)
            self.assertEqual(report["source_inventory_repair_queue"][0]["doc_id"], "doc_orphan")
            self.assertEqual(report["source_inventory_repair_queue"][0]["candidate_id"], "civil_009")
            self.assertIn("source_intake_log", candidate_by_id["arch_011"]["lineage_evidence"])
            self.assertIn("source_import_json", candidate_by_id["civil_007"]["lineage_evidence"])
            self.assertIn("inventory_note", candidate_by_id["mech_010"]["lineage_evidence"])
            self.assertIn("Candidate-To-Local Lineage", markdown)

    def test_build_agreement_audit_packet_stratifies_and_localizes_two_reviewer_sheets(self):
        mod = load_module(
            "build_agreement_audit_packet",
            ROOT / "tools" / "build_agreement_audit_packet.py",
        )
        verifier = load_module(
            "verify_agreement_audit_packet",
            ROOT / "tools" / "verify_agreement_audit_packet.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            image = tmp_path / "images" / "doc__v1" / "page_0000.png"
            image.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(image)
            rows = []
            specs = [
                ("dev", "microtext", "pin_label", 3),
                ("test", "microtext", "room_label", 2),
                ("dev", "visualdiff", "symbol", 3),
                ("test", "visualdiff", "text", 2),
            ]
            row_index = 0
            for split, task, category, count in specs:
                for _ in range(count):
                    row = {
                        "id": f"q{row_index}",
                        "task": task,
                        "question": "What is shown?",
                        "answer": f"A{row_index}",
                        "images": ["images/doc__v1/page_0000.png"],
                        "evidence": [{"image_index": 0, "bbox": [10, 10, 30, 30]}],
                        "split": split,
                        "metadata": {"doc_id": "doc"},
                    }
                    if task == "microtext":
                        row["metadata"]["category"] = category
                    else:
                        row["images"].append("images/doc__v1/page_0000.png")
                        row["evidence"].append({"image_index": 1, "bbox": [10, 10, 30, 30]})
                        row["metadata"]["change_type"] = [category]
                    rows.append(row)
                    row_index += 1
            write_jsonl(tmp_path / "eng_bench.jsonl", rows)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["doc_id", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc",
                        "source_url": "https://example.com/doc",
                        "public_status": "public_candidate",
                    }
                )

            report = mod.build_packet(
                root=tmp_path,
                input_path=Path("eng_bench.jsonl"),
                output_dir=Path("derived/human_adjudication/agreement"),
                zip_output=Path("derived/human_adjudication/agreement.zip"),
                sample_fraction=0.5,
                min_per_stratum=1,
                seed="test-seed",
                pad_px=4,
            )

            packet = tmp_path / "derived" / "human_adjudication" / "agreement"
            self.assertTrue(report["valid"])
            self.assertEqual(report["sample_rows"], 6)
            self.assertEqual(
                report["sample_by_stratum"],
                {
                    "dev|microtext|pin_label": 2,
                    "dev|visualdiff|symbol": 2,
                    "test|microtext|room_label": 1,
                    "test|visualdiff|text": 1,
                },
            )
            with (packet / "reviewer_a_checklist.csv").open(encoding="utf-8") as f:
                reviewer_a = list(csv.DictReader(f))
            with (packet / "reviewer_b_checklist.csv").open(encoding="utf-8") as f:
                reviewer_b = list(csv.DictReader(f))
            self.assertEqual(reviewer_a, reviewer_b)
            self.assertTrue(all(not row["answer_correct"] for row in reviewer_a))
            self.assertTrue(all(not row["accept_reject"] for row in reviewer_a))
            self.assertTrue(all(row["doc_id"] == "doc" for row in reviewer_a))
            self.assertTrue(all(row["source_status"] == "public_candidate" for row in reviewer_a))
            self.assertTrue(all(row["source_url"] == "https://example.com/doc" for row in reviewer_a))
            self.assertTrue(all((packet / row["primary_evidence_path"]).exists() for row in reviewer_a))
            self.assertTrue((packet / "index.html").exists())
            self.assertTrue((tmp_path / "derived/human_adjudication/agreement.zip").exists())
            folder_verification = verifier.verify_dir(packet)
            self.assertTrue(folder_verification["valid"])
            self.assertEqual(folder_verification["missing_provenance_values"], 0)
            self.assertTrue(
                verifier.verify_zip(tmp_path / "derived/human_adjudication/agreement.zip")["valid"]
            )
            (packet / reviewer_a[0]["primary_evidence_path"]).unlink()
            broken = verifier.verify_dir(packet)
            self.assertFalse(broken["valid"])
            self.assertEqual(broken["missing_evidence_refs"], 1)

    def test_build_agreement_audit_packet_preserves_distinct_microtext_pages(self):
        mod = load_module(
            "build_agreement_audit_packet_pages",
            ROOT / "tools" / "build_agreement_audit_packet.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for page_index in (2, 3):
                image = tmp_path / "images" / "doc__v1" / f"page_{page_index:04d}.png"
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 100), "white").save(image)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": f"q{page_index}",
                        "task": "microtext",
                        "question": "Read it.",
                        "answer": f"J{page_index}",
                        "images": [f"images/doc__v1/page_{page_index:04d}.png"],
                        "evidence": [{"image_index": 0, "bbox": [10, 10, 30, 30]}],
                        "split": "dev",
                        "metadata": {"doc_id": "doc", "category": "pin_label"},
                    }
                    for page_index in (2, 3)
                ],
            )

            mod.build_packet(
                root=tmp_path,
                input_path=Path("eng_bench.jsonl"),
                output_dir=Path("derived/human_adjudication/agreement"),
                zip_output=Path("derived/human_adjudication/agreement.zip"),
                sample_fraction=1.0,
                min_per_stratum=1,
                seed="test-seed",
                pad_px=4,
            )
            with (
                tmp_path / "derived/human_adjudication/agreement/reviewer_a_checklist.csv"
            ).open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(len({row["page_path"] for row in rows}), 2)
            self.assertTrue(
                all(
                    (tmp_path / "derived/human_adjudication/agreement" / row["page_path"]).exists()
                    for row in rows
                )
            )

    def test_build_agreement_audit_packet_resolves_visualdiff_revision_sources(self):
        mod = load_module(
            "build_agreement_audit_packet_visualdiff_sources",
            ROOT / "tools" / "build_agreement_audit_packet.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for version in ("R1", "R2"):
                image = tmp_path / "images" / f"widget__{version}" / "page_0000.png"
                image.parent.mkdir(parents=True)
                Image.new("RGB", (100, 100), "white").save(image)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q_pair0",
                        "task": "visualdiff",
                        "question": "What changed?",
                        "answer": "The label changed.",
                        "images": [
                            "images/widget__R1/page_0000.png",
                            "images/widget__R2/page_0000.png",
                        ],
                        "evidence": [
                            {"image_index": 0, "bbox": [10, 10, 30, 30]},
                            {"image_index": 1, "bbox": [10, 10, 30, 30]},
                        ],
                        "split": "test",
                        "metadata": {
                            "pair_id": "pair0",
                            "doc_id": "widget",
                            "change_type": ["text"],
                        },
                    }
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "pair0",
                        "doc_id": "widget",
                        "version_id_old": "R1",
                        "version_id_new": "R2",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "task": "visualdiff",
                        "doc_id": "widget_r1",
                        "version": {"rev": "R1"},
                        "path": "visualdiff/docs/widget_r1.pdf",
                    },
                    {
                        "type": "doc",
                        "task": "visualdiff",
                        "doc_id": "widget_r2",
                        "version": {"rev": "R2"},
                        "path": "visualdiff/docs/widget_r2.pdf",
                    },
                ],
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["task", "doc_id", "source_url", "public_status"],
                )
                writer.writeheader()
                for doc_id in ("widget_r1", "widget_r2"):
                    writer.writerow(
                        {
                            "task": "visualdiff",
                            "doc_id": doc_id,
                            "source_url": "https://example.com/widget",
                            "public_status": "public_candidate",
                        }
                    )

            mod.build_packet(
                root=tmp_path,
                input_path=Path("eng_bench.jsonl"),
                output_dir=Path("derived/human_adjudication/agreement"),
                zip_output=Path("derived/human_adjudication/agreement.zip"),
                sample_fraction=1,
                min_per_stratum=1,
                seed="test-seed",
                pad_px=4,
            )

            with (
                tmp_path
                / "derived"
                / "human_adjudication"
                / "agreement"
                / "reviewer_a_checklist.csv"
            ).open(encoding="utf-8") as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row["source_doc_ids"], "widget_r1; widget_r2")
            self.assertEqual(row["source_url"], "https://example.com/widget")
            self.assertEqual(row["source_status"], "public_candidate")

    def test_agreement_audit_scores_answers_bboxes_and_categorical_kappa(self):
        mod = load_module("agreement_audit", ROOT / "tools" / "agreement_audit.py")
        reference = [
            {
                "id": "m0",
                "task": "microtext",
                "stratum": "dev|microtext|pin_label",
                "reference_answer": "J5",
                "reference_evidence_json": json.dumps(
                    [{"image_index": 0, "bbox": [0, 0, 10, 10]}]
                ),
            },
            {
                "id": "m1",
                "task": "microtext",
                "stratum": "test|microtext|room_label",
                "reference_answer": "BEDROOM",
                "reference_evidence_json": json.dumps(
                    [{"image_index": 0, "bbox": [0, 0, 10, 10]}]
                ),
            },
            {
                "id": "v0",
                "task": "visualdiff",
                "stratum": "dev|visualdiff|text",
                "reference_answer": "R1 changed to R2",
                "reference_evidence_json": json.dumps(
                    [
                        {"image_index": 0, "bbox": [0, 0, 10, 10]},
                        {"image_index": 1, "bbox": [0, 0, 10, 10]},
                    ]
                ),
            },
        ]
        reviewer_a = [
            {
                "id": "m0",
                "answer_correct": "yes",
                "corrected_answer": "",
                "bbox_correct": "yes",
                "corrected_evidence_json": "",
                "accept_reject": "accept",
                "ambiguity": "no",
                "rights_concern": "no",
            },
            {
                "id": "m1",
                "answer_correct": "no",
                "corrected_answer": "ROOM A",
                "bbox_correct": "no",
                "corrected_evidence_json": json.dumps(
                    [{"image_index": 0, "bbox": [0, 0, 10, 10]}]
                ),
                "accept_reject": "accept",
                "ambiguity": "no",
                "rights_concern": "no",
            },
            {
                "id": "v0",
                "answer_correct": "yes",
                "corrected_answer": "",
                "bbox_correct": "yes",
                "corrected_evidence_json": "",
                "accept_reject": "reject",
                "ambiguity": "yes",
                "rights_concern": "no",
            },
        ]
        reviewer_b = [
            {
                "id": "m0",
                "answer_correct": "yes",
                "corrected_answer": "",
                "bbox_correct": "yes",
                "corrected_evidence_json": "",
                "accept_reject": "accept",
                "ambiguity": "no",
                "rights_concern": "no",
            },
            {
                "id": "m1",
                "answer_correct": "no",
                "corrected_answer": "ROOM B",
                "bbox_correct": "no",
                "corrected_evidence_json": json.dumps(
                    [{"image_index": 0, "bbox": [1, 1, 9, 9]}]
                ),
                "accept_reject": "reject",
                "ambiguity": "no",
                "rights_concern": "no",
            },
            {
                "id": "v0",
                "answer_correct": "yes",
                "corrected_answer": "",
                "bbox_correct": "no",
                "corrected_evidence_json": json.dumps(
                    [
                        {"image_index": 0, "bbox": [1, 1, 9, 9]},
                        {"image_index": 1, "bbox": [1, 1, 9, 9]},
                    ]
                ),
                "accept_reject": "reject",
                "ambiguity": "no",
                "rights_concern": "no",
            },
        ]

        reference_by_id = {row["id"]: row for row in reference}
        response_defaults = {field: "" for field in mod.REVIEW_RESPONSE_FIELDS}
        reviewer_a = [
            {**reference_by_id[row["id"]], **response_defaults, **row}
            for row in reviewer_a
        ]
        reviewer_b = [
            {**reference_by_id[row["id"]], **response_defaults, **row}
            for row in reviewer_b
        ]

        report = mod.compute_report(reference, reviewer_a, reviewer_b)
        markdown = mod.render_markdown(report)

        self.assertEqual(report["complete_rows"], 3)
        self.assertAlmostEqual(report["overall"]["exact_answer_agreement"], 2 / 3)
        self.assertAlmostEqual(report["overall"]["accept_reject_agreement"], 2 / 3)
        self.assertAlmostEqual(report["overall"]["accept_reject_kappa"], 0.4)
        self.assertAlmostEqual(report["by_task"]["microtext"]["exact_answer_agreement"], 0.5)
        self.assertAlmostEqual(report["by_task"]["visualdiff"]["accept_reject_agreement"], 1.0)
        self.assertFalse(report["gates"]["microtext_answer_agreement_0_95"]["passes"])
        self.assertTrue(report["gates"]["visualdiff_accept_reject_agreement_0_90"]["passes"])
        self.assertIn("Human Agreement Audit", markdown)

    def test_agreement_audit_requires_complete_reference_sample_for_release_gate(self):
        mod = load_module("agreement_audit_complete_sample", ROOT / "tools" / "agreement_audit.py")
        evidence = json.dumps([{"image_index": 0, "bbox": [0, 0, 10, 10]}])
        references = [
            {
                "id": "m0",
                "task": "microtext",
                "stratum": "dev|microtext|pin_label",
                "reference_answer": "J5",
                "reference_evidence_json": evidence,
            },
            {
                "id": "v0",
                "task": "visualdiff",
                "stratum": "test|visualdiff|text",
                "reference_answer": "R1 changed to R2",
                "reference_evidence_json": evidence,
            },
            {
                "id": "missing",
                "task": "microtext",
                "stratum": "test|microtext|room_label",
                "reference_answer": "ROOM",
                "reference_evidence_json": evidence,
            },
        ]
        completed = [
            {
                "id": identifier,
                "answer_correct": "yes",
                "bbox_correct": "yes",
                "accept_reject": "accept",
                "ambiguity": "no",
                "rights_concern": "no",
            }
            for identifier in ("m0", "v0")
        ]

        report = mod.compute_report(references, completed, completed)

        self.assertFalse(report["input_contract_valid"])
        self.assertEqual(report["paired_rows"], 0)
        self.assertEqual(report["complete_rows"], 0)
        self.assertEqual(report["incomplete_rows"], 3)
        self.assertIn("missing", report["incomplete_ids"])
        self.assertIn("reviewer_a:row_count_mismatch:2:3", report["input_contract_issues"])
        self.assertFalse(report["agreement_gate_complete"])

    def test_active_gold_provenance_audit_resolves_visualdiff_pair_and_flags_gaps(self):
        mod = load_module(
            "audit_active_gold_provenance",
            ROOT / "tools" / "audit_active_gold_provenance.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            sources = {
                "micro_doc": b"micro",
                "widget_r1": b"old",
                "widget_r2": b"new",
            }
            for doc_id, content in sources.items():
                path = tmp_path / "docs" / f"{doc_id}.pdf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "m0", "doc_id": "micro_doc"}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "pair0__0000",
                        "project_id": "pair0",
                        "doc_id": "widget",
                        "version_id_old": "R1",
                        "version_id_new": "R2",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "micro_doc",
                        "task": "microtext",
                        "path": "docs/micro_doc.pdf",
                        "source_url": "https://example.com/micro",
                        "public_status": "public_domain_candidate",
                        "sha256": hashlib.sha256(sources["micro_doc"]).hexdigest(),
                    },
                    {
                        "type": "doc",
                        "doc_id": "widget_r1",
                        "task": "visualdiff",
                        "path": "docs/widget_r1.pdf",
                        "source_url": "https://example.com/widget",
                        "public_status": "public_candidate",
                    },
                    {
                        "type": "doc",
                        "doc_id": "widget_r2",
                        "task": "visualdiff",
                        "path": "docs/widget_r2.pdf",
                        "source_url": "https://example.com/widget",
                        "public_status": "rights_uncertain",
                        "sha256": "0" * 64,
                    },
                    {
                        "type": "pair",
                        "pair_id": "pair0",
                        "from_doc_id": "widget_r1",
                        "to_doc_id": "widget_r2",
                    },
                ],
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "docs/micro_doc.pdf",
                        "task": "microtext",
                        "doc_id": "micro_doc",
                        "domain": "mechanical_cad",
                        "source_url": "https://example.com/micro",
                        "public_status": "public_domain_candidate",
                    }
                )
                writer.writerow(
                    {
                        "path": "docs/widget_r1.pdf",
                        "task": "visualdiff",
                        "doc_id": "widget_r1",
                        "domain": "pcb_schematic",
                        "source_url": "https://example.com/widget",
                        "public_status": "public_candidate",
                    }
                )

            report = mod.build_report(tmp_path, date_label="2026-06-05")
            by_doc = {row["doc_id"]: row for row in report["documents"]}

            self.assertEqual(report["totals"]["active_gold_rows"], 2)
            self.assertEqual(report["totals"]["active_source_docs"], 3)
            self.assertEqual(report["totals"]["unresolved_active_rows"], 0)
            self.assertEqual(report["totals"]["missing_inventory_docs"], 1)
            self.assertEqual(report["totals"]["missing_recorded_sha256_docs"], 1)
            self.assertEqual(report["totals"]["sha256_mismatch_docs"], 1)
            self.assertEqual(report["totals"]["rights_blocked_docs"], 2)
            self.assertTrue(by_doc["micro_doc"]["sha256_matches"])
            self.assertEqual(by_doc["widget_r1"]["blocker"], "license_evidence_missing")
            self.assertEqual(by_doc["widget_r2"]["blocker"], "rights_uncertain")
            self.assertFalse(report["release_provenance_complete"])
            self.assertFalse(report["paper_ready_provenance_complete"])

    def test_active_gold_provenance_holds_inventory_manifest_rights_disagreement(self):
        mod = load_module(
            "audit_active_gold_provenance_rights_disagreement",
            ROOT / "tools" / "audit_active_gold_provenance.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            content = b"licensed-source"
            source_path = tmp_path / "docs" / "source.pdf"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(content)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "m0", "doc_id": "source"}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "source",
                        "task": "microtext",
                        "path": "docs/source.pdf",
                        "source_url": "https://example.com/source",
                        "public_status": "rights_uncertain",
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "docs/source.pdf",
                        "task": "microtext",
                        "doc_id": "source",
                        "source_url": "https://example.com/source",
                        "public_status": "public_domain_candidate",
                    }
                )

            report = mod.build_report(tmp_path, date_label="2026-08-09")
            document = report["documents"][0]

            self.assertTrue(document["rights_status_disagreement"])
            self.assertEqual(document["blocker"], "rights_status_disagreement")
            self.assertEqual(report["totals"]["rights_status_disagreement_docs"], 1)
            self.assertFalse(document["release_ready"])

    def test_public_and_global_gates_count_resolved_visualdiff_revision_documents(self):
        v1 = load_module(
            "audit_v1_5_gate_resolved_sources",
            ROOT / "tools" / "audit_v1_5_gate.py",
        )
        v2 = load_module(
            "audit_v2_0_gate_resolved_sources",
            ROOT / "tools" / "audit_v2_0_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for doc_id in ("widget_r1", "widget_r2"):
                path = tmp_path / "docs" / f"{doc_id}.pdf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(doc_id.encode("ascii"))
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q_pair0",
                        "task": "visualdiff",
                        "split": "test",
                        "metadata": {"doc_id": "widget", "pair_id": "pair0__0000"},
                    }
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "pair0__0000",
                        "project_id": "pair0",
                        "doc_id": "widget",
                        "version_id_old": "R1",
                        "version_id_new": "R2",
                    }
                ],
            )
            manifest = []
            for doc_id in ("widget_r1", "widget_r2"):
                content = doc_id.encode("ascii")
                manifest.append(
                    {
                        "type": "doc",
                        "doc_id": doc_id,
                        "task": "visualdiff",
                        "path": f"docs/{doc_id}.pdf",
                        "source_url": "https://example.com/widget",
                        "public_status": "public_domain_candidate",
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            manifest.append(
                {
                    "type": "pair",
                    "pair_id": "pair0",
                    "from_doc_id": "widget_r1",
                    "to_doc_id": "widget_r2",
                }
            )
            write_jsonl(tmp_path / "manifest.jsonl", manifest)
            with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["path", "task", "doc_id", "domain", "source_url", "public_status"],
                )
                writer.writeheader()
                for doc_id in ("widget_r1", "widget_r2"):
                    writer.writerow(
                        {
                            "path": f"docs/{doc_id}.pdf",
                            "task": "visualdiff",
                            "doc_id": doc_id,
                            "domain": "pcb_schematic",
                            "source_url": "https://example.com/widget",
                            "public_status": "public_domain_candidate",
                        }
                    )

            self.assertEqual(v1.collect_status(tmp_path)["gates"]["gold_source_docs"]["current"], 2)
            self.assertEqual(v2.collect_status(tmp_path)["gates"]["gold_source_docs"]["current"], 2)

    def test_public_and_global_gates_exclude_unresolved_inventory_rights(self):
        v1 = load_module(
            "audit_v1_5_gate_inventory_rights",
            ROOT / "tools" / "audit_v1_5_gate.py",
        )
        v2 = load_module(
            "audit_v2_0_gate_inventory_rights",
            ROOT / "tools" / "audit_v2_0_gate.py",
        )

        blocked_statuses = (
            "rights_uncertain_open_hardware_candidate",
            "release_review_needed",
            "internal_only_candidate",
            "not_promoted_candidate",
            "reference_only_cc_by_sa_candidate",
        )
        for status in blocked_statuses:
            row = {"task": "visualdiff", "public_status": status}
            self.assertFalse(v1.is_release_safe_active(row), status)
            self.assertFalse(v2.is_release_safe_active(row), status)

        safe = {"task": "microtext", "public_status": "public_domain_us_federal_candidate"}
        self.assertTrue(v1.is_release_safe_active(safe))
        self.assertTrue(v2.is_release_safe_active(safe))

    def test_question_diversity_report_flags_dominant_templates(self):
        mod = load_module(
            "question_diversity_report",
            ROOT / "tools" / "question_diversity_report.py",
        )

        rows = [
            {"id": "q0", "task": "microtext", "question": "Read the text."},
            {"id": "q1", "task": "microtext", "question": "Read the text."},
            {"id": "q2", "task": "microtext", "question": "What label is shown?"},
            {"id": "q3", "task": "visualdiff", "question": "What changed?"},
        ]

        report = mod.compute_diversity(rows, max_template_share=0.5)
        markdown = mod.render_markdown(report)

        self.assertEqual(report["by_task"]["microtext"]["unique_templates"], 2)
        self.assertAlmostEqual(report["by_task"]["microtext"]["max_template_share"], 2 / 3)
        self.assertFalse(report["by_task"]["microtext"]["passes_max_share"])
        self.assertIn("Question Diversity", markdown)
        self.assertIn("microtext", markdown)

    def test_apply_question_templates_spreads_repeated_categories(self):
        mod = load_module("apply_question_templates", ROOT / "tools" / "apply_question_templates.py")

        micro_questions = [
            {
                "question_id": f"q_mt_{i:02d}",
                "item_ids": [f"mt_{i:02d}"],
                "query_text": "What pin or component label is shown in this region?",
            }
            for i in range(12)
        ]
        micro_items = {
            f"mt_{i:02d}": {"item_id": f"mt_{i:02d}", "category": "pin_label"}
            for i in range(12)
        }
        visual_questions = [
            {
                "question_id": f"q_vd_{i:02d}",
                "pair_id": f"pair_{i:02d}",
                "query_text": "What changed in this region between old and new?",
                "version_id_old": "old",
                "version_id_new": "new",
            }
            for i in range(12)
        ]
        visual_pairs = {
            f"pair_{i:02d}": {
                "pair_id": f"pair_{i:02d}",
                "version_id_old": "old",
                "version_id_new": "new",
                "change_type": ["symbol"],
            }
            for i in range(12)
        }

        updated_micro, micro_stats = mod.apply_microtext_templates(micro_questions, micro_items)
        updated_visual, visual_stats = mod.apply_visualdiff_templates(visual_questions, visual_pairs)

        self.assertEqual(micro_stats["updated"], 12)
        self.assertGreaterEqual(len({row["query_text"] for row in updated_micro}), 6)
        self.assertEqual(visual_stats["updated"], 12)
        self.assertGreaterEqual(len({row["query_text"] for row in updated_visual}), 6)

    def test_apply_question_templates_supports_component_values(self):
        mod = load_module("apply_question_templates", ROOT / "tools" / "apply_question_templates.py")
        questions = [{"question_id": "q_value", "item_ids": ["value"]}]
        items = {"value": {"item_id": "value", "category": "component_value"}}

        updated, stats = mod.apply_microtext_templates(questions, items)

        self.assertEqual(stats["updated"], 1)
        self.assertEqual(updated[0]["template_family"], "component_value")
        self.assertIn("component", updated[0]["query_text"].lower())

    def test_load_eng_bench_filters_and_verifies_images(self):
        mod = load_module("engbench_loader", ROOT / "engbench" / "loader.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            image_path = tmp_path / "images" / "doc__v1" / "page_0000.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), "white").save(image_path)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "microtext",
                        "split": "test",
                        "images": ["images/doc__v1/page_0000.png"],
                    },
                    {
                        "id": "q1",
                        "task": "visualdiff",
                        "split": "train",
                        "images": ["images/doc__v1/missing.png"],
                    },
                ],
            )

            rows = mod.load_eng_bench(tmp_path, task="microtext", split="test", verify_images=True)

            self.assertEqual([row["id"] for row in rows], ["q0"])
            self.assertEqual(rows[0]["resolved_images"], [str(image_path)])
            with self.assertRaises(FileNotFoundError):
                mod.load_eng_bench(tmp_path, verify_images=True)

    def test_loader_smoke_counts_all_task_split_pairs(self):
        mod = load_module("loader_smoke", ROOT / "tools" / "loader_smoke.py")

        rows = [
            {"id": "a", "task": "microtext", "split": "train"},
            {"id": "b", "task": "microtext", "split": "test"},
            {"id": "c", "task": "visualdiff", "split": "test"},
        ]

        summary = mod.summarize_rows(rows)

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_task"], {"microtext": 2, "visualdiff": 1})
        self.assertEqual(summary["by_split"], {"test": 2, "train": 1})

    def test_build_dataset_infos_summarizes_unified_rows(self):
        mod = load_module("build_dataset_infos", ROOT / "tools" / "build_dataset_infos.py")

        rows = [
            {"id": "a", "task": "microtext", "split": "train", "metadata": {"doc_id": "doc_a"}},
            {
                "id": "b",
                "task": "visualdiff",
                "split": "test",
                "metadata": {"doc_id": "doc_b", "pair_id": "vdiff__doc_b__v1__to__v2__0000"},
            },
            {
                "id": "c",
                "task": "visualdiff",
                "split": "test",
                "metadata": {"doc_id": "doc_b", "pair_id": "vdiff__doc_b__v1__to__v2__0001"},
            },
            {
                "id": "d",
                "task": "visualdiff",
                "split": "test",
                "metadata": {"doc_id": "doc_b", "pair_id": "vdiff__doc_b__v1__to__v2__0002"},
            },
        ]

        info = mod.build_dataset_info(rows)

        self.assertEqual(info["eng_bench"]["splits"]["train"]["num_examples"], 1)
        self.assertEqual(info["eng_bench"]["splits"]["test"]["num_examples"], 3)
        self.assertEqual(info["eng_bench"]["task_counts"], {"microtext": 1, "visualdiff": 3})
        self.assertEqual(info["eng_bench"]["source_count"], 2)

    def test_pyproject_exposes_local_engbench_package(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(pyproject["project"]["name"], "engbench")
        self.assertIn("Pillow>=9.0.0", pyproject["project"]["dependencies"])
        self.assertEqual(pyproject["tool"]["setuptools"]["packages"]["find"]["include"], ["engbench*"])

    def test_build_release_manifest_hashes_present_files_and_reports_missing(self):
        mod = load_module("build_release_manifest", ROOT / "tools" / "build_release_manifest.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
            manifest = mod.build_manifest(tmp_path, ["README.md", "missing.jsonl"])

            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(manifest["missing_count"], 1)
            self.assertEqual(manifest["missing_paths"], ["missing.jsonl"])
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertIn("missing.jsonl", mod.render_markdown(manifest))
            self.assertIn("docs/ACTIVE_GOLD_PROVENANCE.csv", mod.DEFAULT_PATHS)
            self.assertIn("tools/audit_active_gold_provenance.py", mod.DEFAULT_PATHS)

    def test_question_leakage_audit_flags_answer_in_microtext_prompt(self):
        mod = load_module("audit_question_leakage", ROOT / "tools" / "audit_question_leakage.py")

        rows = [
            {
                "id": "q_bad",
                "task": "microtext",
                "split": "dev",
                "question": "Read label J5 in the highlighted region.",
                "answer": "J5",
            },
            {
                "id": "q_ok",
                "task": "microtext",
                "split": "dev",
                "question": "Read the highlighted label.",
                "answer": "J6",
            },
        ]

        report = mod.audit(rows)

        self.assertEqual(report["critical_failures"], 1)
        self.assertEqual(report["samples"][0]["id"], "q_bad")

    def test_export_public_inputs_strips_labels(self):
        mod = load_module("export_public_inputs", ROOT / "tools" / "export_public_inputs.py")

        rows = [
            {
                "id": "q0",
                "task": "microtext",
                "split": "test",
                "question": "Read the label.",
                "answer": "J5",
                "images": ["images/page.png"],
                "evidence": [{"bbox": [1, 2, 3, 4], "image_index": 0}],
                "metadata": {"doc_id": "doc", "category": "pin_label", "text_gt": "J5"},
            },
            {"id": "q1", "task": "visualdiff", "split": "train", "answer": "Changed."},
        ]

        public_rows, stats = mod.export_inputs(rows, split="test")

        self.assertEqual(stats["output_rows"], 1)
        self.assertNotIn("answer", public_rows[0])
        self.assertNotIn("evidence", public_rows[0])
        self.assertNotIn("text_gt", public_rows[0]["metadata"])
        self.assertEqual(public_rows[0]["question"], "Read the label.")

    def test_v1_expansion_queue_prefers_needed_validated_sources(self):
        mod = load_module("build_v1_expansion_queue", ROOT / "tools" / "build_v1_expansion_queue.py")

        candidates = [
            {
                "candidate_id": "pid_001",
                "domain": "pid",
                "task_fit": "visualdiff,microtext",
                "rights_tier": "public_candidate",
                "priority_score": "8",
                "source_url": "https://example.com/pid.pdf",
            },
            {
                "candidate_id": "pcb_001",
                "domain": "pcb_schematic",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "priority_score": "9",
            },
        ]
        validations = [
            {
                "candidate_id": "pid_001",
                "next_action": "intake_first",
                "release_posture": "release_candidate",
            }
        ]
        quotas = [
            {"domain": "pid", "remaining_to_v1_0": 4},
            {"domain": "pcb_schematic", "remaining_to_v1_0": 0},
        ]

        queue = mod.build_queue(candidates, validations, quotas, imported_ids=set(), limit=2)

        self.assertEqual(queue[0]["candidate_id"], "pid_001")
        self.assertIn("browser_validated_intake_first", queue[0]["reason"])

    def test_apply_visualdiff_polish_updates_valid_edits_and_quarantines_rejects(self):
        mod = load_module("apply_visualdiff_polish", ROOT / "tools" / "apply_visualdiff_polish.py")

        pairs = [
            {
                "pair_id": "p_valid",
                "split": "dev",
                "change_desc_gt": "Generic symbol changed.",
                "desc_source": "codex_assisted_visual_review",
                "review_confidence": "low",
            },
            {
                "pair_id": "p_edit",
                "split": "test",
                "change_desc_gt": "Generic symbol changed.",
                "desc_source": "codex_assisted_visual_review",
                "review_confidence": "low",
            },
            {
                "pair_id": "p_reject",
                "split": "dev",
                "change_desc_gt": "Generic symbol changed.",
                "desc_source": "codex_assisted_visual_review",
                "review_confidence": "low",
            },
            {
                "pair_id": "p_full",
                "split": "test",
                "change_desc_gt": "Generic symbol changed.",
                "desc_source": "codex_assisted_visual_review",
                "review_confidence": "low",
            },
            {
                "pair_id": "p_unreviewed",
                "split": "test",
                "change_desc_gt": "Generic symbol changed.",
                "desc_source": "codex_assisted_visual_review",
                "review_confidence": "low",
            },
        ]
        checklist = [
            {"pair_id": "p_valid", "human_status": "valid", "human_notes": "looks correct"},
            {
                "pair_id": "p_edit",
                "human_status": "edit",
                "human_description": "The highlighted connector graphic was redrawn.",
                "human_notes": "specific enough",
            },
            {
                "pair_id": "p_reject",
                "human_status": "reject_no_change",
                "human_notes": "no visible delta in crop",
            },
            {
                "pair_id": "p_full",
                "human_status": "needs_full_page",
                "human_notes": "crop is too small",
            },
        ]

        active, quarantine, stats = mod.apply_polish(pairs, checklist)

        self.assertEqual(stats["valid"], 1)
        self.assertEqual(stats["edit"], 1)
        self.assertEqual(stats["reject_no_change"], 1)
        self.assertEqual(stats["needs_full_page"], 1)
        self.assertEqual([row["pair_id"] for row in active], ["p_valid", "p_edit", "p_full", "p_unreviewed"])
        self.assertEqual([row["pair_id"] for row in quarantine], ["p_reject"])
        by_id = {row["pair_id"]: row for row in active}
        self.assertEqual(by_id["p_valid"]["review_confidence"], "human_validated")
        self.assertEqual(by_id["p_edit"]["change_desc_gt"], "The highlighted connector graphic was redrawn.")
        self.assertEqual(by_id["p_edit"]["desc_source"], "human")
        self.assertEqual(by_id["p_full"]["human_review_status"], "needs_full_page")
        self.assertEqual(quarantine[0]["quarantine_reason"], "reject_no_change")

    def test_normalize_visualdiff_polish_checklist_converts_outside_box_edits(self):
        mod = load_module(
            "normalize_visualdiff_polish_checklist",
            ROOT / "tools" / "normalize_visualdiff_polish_checklist.py",
        )

        rows = [
            {"pair_id": "p0", "human_status": "reject_unclear", "human_notes": ""},
            {"pair_id": "p1", "human_status": "edit", "human_description": "line appears", "human_notes": "outside box"},
            {"pair_id": "p2", "human_status": "valid", "human_notes": ""},
        ]

        normalized, stats = mod.normalize_rows(rows, edit_with_notes_status="reject_bbox_mismatch")

        self.assertEqual([row["human_status"] for row in normalized], ["reject_no_change", "reject_bbox_mismatch", "valid"])
        self.assertEqual(stats["reject_no_change"], 1)
        self.assertEqual(stats["reject_bbox_mismatch"], 1)
        self.assertEqual(stats["valid"], 1)

    def test_build_visualdiff_review_queue_prioritizes_release_todo_rows(self):
        mod = load_module(
            "build_visualdiff_review_queue", ROOT / "tools" / "build_visualdiff_review_queue.py"
        )

        rows = [
            {
                "pair_id": "train_title",
                "doc_id": "bbb",
                "version_id_old": "C",
                "version_id_new": "C3",
                "page_index_old": 0,
                "page_index_new": 0,
                "change_desc_gt": "CHANGE_DESC_GT_TODO",
                "split": "train",
                "is_titleblock": True,
            },
            {
                "pair_id": "test_title",
                "doc_id": "viola",
                "version_id_old": "pcbV1.1",
                "version_id_new": "pcbV1.2",
                "page_index_old": 0,
                "page_index_new": 0,
                "change_desc_gt": "CHANGE_DESC_GT_TODO",
                "split": "test",
                "is_titleblock": True,
            },
            {
                "pair_id": "dev_symbol",
                "doc_id": "viola",
                "version_id_old": "pcbV1.0",
                "version_id_new": "pcbV1.1",
                "page_index_old": 2,
                "page_index_new": 2,
                "change_desc_gt": "CHANGE_DESC_GT_TODO",
                "split": "dev",
                "is_titleblock": False,
            },
        ]

        queue = mod.build_queue(rows)

        self.assertEqual([row["pair_id"] for row in queue], ["dev_symbol", "test_title", "train_title"])
        self.assertEqual(queue[0]["review_bucket"], "release_non_titleblock")
        self.assertEqual(queue[1]["review_bucket"], "release_titleblock")
        self.assertEqual(queue[0]["image_old"], "images/viola__pcbV1.0/page_0002.png")

    def test_rebuild_visualdiff_questions_syncs_answer_split_and_desc_source(self):
        mod = load_module(
            "rebuild_visualdiff_questions", ROOT / "tools" / "rebuild_visualdiff_questions.py"
        )

        pairs = [
            {
                "pair_id": "p0",
                "change_desc_gt": "Changed connector J5 from 4-pin to 6-pin.",
                "split": "dev",
                "desc_source": "human",
            }
        ]
        questions = [
            {"question_id": "q0", "pair_id": "p0", "answer_text": "old", "split": "test"},
            {"question_id": "q_missing", "pair_id": "p_missing", "answer_text": "stale"},
        ]

        rebuilt, stats = mod.rebuild_questions(pairs, questions)

        self.assertEqual(len(rebuilt), 1)
        self.assertEqual(rebuilt[0]["answer_text"], "Changed connector J5 from 4-pin to 6-pin.")
        self.assertEqual(rebuilt[0]["split"], "dev")
        self.assertEqual(rebuilt[0]["desc_source"], "human")
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["dropped_missing_pair"], 1)

    def test_maap_review_filters_by_split_bucket_and_limit(self):
        mod = load_module("maap_review", ROOT / "tools" / "maap_review.py")

        drafts = [
            {"pair_id": "p0", "split": "train", "review_bucket": "train_non_titleblock"},
            {"pair_id": "p1", "split": "dev", "review_bucket": "release_non_titleblock"},
            {"pair_id": "p2", "split": "test", "review_bucket": "release_titleblock"},
        ]
        reviewed = [{"pair_id": "p2"}]

        selected = mod.select_review_items(
            drafts,
            reviewed,
            split="dev",
            bucket="release_non_titleblock",
            limit=1,
        )

        self.assertEqual([row["pair_id"] for row in selected], ["p1"])

    def test_maap_merge_preserves_unreviewed_rows_and_updates_reviewed_only(self):
        mod = load_module("maap_merge", ROOT / "tools" / "maap_merge.py")

        existing = [
            {"pair_id": "p0", "change_desc_gt": "CHANGE_DESC_GT_TODO", "split": "dev"},
            {"pair_id": "p1", "change_desc_gt": "CHANGE_DESC_GT_TODO", "split": "test"},
        ]
        reviewed = [
            {
                "pair_id": "p0",
                "change_desc_gt": "Changed connector J5 from 4-pin to 6-pin.",
                "split": "dev",
                "annotation_status": "edited",
                "reviewed_at": "2026-05-12T00:00:00",
            }
        ]

        merged, stats = mod.merge_reviewed_pairs(existing, reviewed)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["change_desc_gt"], "Changed connector J5 from 4-pin to 6-pin.")
        self.assertEqual(merged[0]["desc_source"], "human")
        self.assertEqual(merged[1]["change_desc_gt"], "CHANGE_DESC_GT_TODO")
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["preserved"], 1)

    def test_source_inventory_discovers_pdfs_and_infers_domain(self):
        mod = load_module("build_source_inventory", ROOT / "tools" / "build_source_inventory.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pdf_path = tmp_path / "microtext" / "docs" / "Kimray_PID_Sample.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n")
            textlayer = tmp_path / "derived" / "textlayer" / "kimray_pid_sample.jsonl"
            textlayer.parent.mkdir(parents=True)
            textlayer.write_text('{"page": 0, "text": "TIC-101", "bbox_px": [0, 0, 1, 1]}\n')

            rows = mod.build_inventory(tmp_path)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], "microtext/docs/Kimray_PID_Sample.pdf")
            self.assertEqual(rows[0]["domain"], "pid")
            self.assertEqual(rows[0]["doc_id"], "kimray_pid_sample")
            self.assertEqual(rows[0]["textlayer_status"], "present")
            self.assertEqual(rows[0]["public_status"], "unknown")

    def test_stronger_complete_status_reports_remaining_gaps(self):
        mod = load_module("stronger_complete_status", ROOT / "tools" / "stronger_complete_status.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            inv_path = tmp_path / "SOURCE_INVENTORY.csv"
            with inv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "path",
                        "task",
                        "doc_id",
                        "domain",
                        "source_url",
                        "public_status",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "path": "microtext/docs/Kimray_PID_Sample.pdf",
                        "task": "microtext",
                        "doc_id": "kimray_pid_sample",
                        "domain": "pid",
                        "source_url": "",
                        "public_status": "unknown",
                        "notes": "",
                    }
                )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "p0", "split": "test", "change_desc_gt": "CHANGE_DESC_GT_TODO"}],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "m0", "category": "instrument_tag", "doc_id": "kimray_pid_sample"}],
            )
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "kimray_pid_sample",
                        "task": "microtext",
                        "path": "microtext/docs/Kimray_PID_Sample.pdf",
                    }
                ],
            )
            with (tmp_path / "SOURCE_CANDIDATES.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "domain",
                        "task_fit",
                        "rights_tier",
                        "source_url",
                        "revision_family_potential",
                        "annotation_yield",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "domain": "pid",
                        "task_fit": "visualdiff,microtext",
                        "rights_tier": "rights_uncertain",
                        "source_url": "https://example.com/pid.pdf",
                        "revision_family_potential": "high",
                        "annotation_yield": "high",
                    }
                )
            with (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "source_validity",
                        "next_action",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "pid_001",
                        "source_validity": "validated_public_pdf_candidate",
                        "next_action": "intake_first",
                    }
                )
            packet_index = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-05.json"
            packet_index.parent.mkdir(parents=True, exist_ok=True)
            packet_index.write_text(
                json.dumps(
                    {
                        "date_label": "2026-06-05",
                        "totals": {
                            "packets": 2,
                            "ready_to_send": 2,
                            "review_rows": 12,
                            "missing_evidence_refs": 0,
                            "overlap_count": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            status = mod.compute_status(tmp_path)

            self.assertEqual(status["visualdiff"]["todo_dev_test"], 1)
            self.assertEqual(status["microtext"]["items_remaining_to_1000"], 999)
            self.assertEqual(status["microtext"]["split_counts"], {"unknown": 1})
            self.assertIn("pid", status["domains"]["present"])
            self.assertEqual(status["sources"]["candidate_rows"], 1)
            self.assertEqual(status["sources"]["candidate_rights_tiers"]["rights_uncertain"], 1)
            self.assertEqual(status["sources"]["browser_validation_rows"], 1)
            self.assertEqual(
                status["sources"]["browser_validation_next_actions"]["intake_first"], 1
            )
            self.assertEqual(status["sources"]["active_source_blocker_count"], 1)
            self.assertEqual(
                status["sources"]["active_source_blockers"][0]["reason"],
                "missing_or_unknown_public_status",
            )
            self.assertFalse(status["stronger_complete"])
            self.assertEqual(status["handoff"]["active_packet_date_label"], "2026-06-05")
            self.assertEqual(status["handoff"]["active_packet_review_rows"], 12)
            self.assertEqual(status["handoff"]["active_packets_ready"], 2)
            self.assertFalse(status["release_gates"]["packaging_clean_v0_95"]["passes"])
            self.assertFalse(status["release_gates"]["gold_v1_0"]["passes"])
            self.assertFalse(status["release_gates"]["public_v1_5"]["passes"])
            self.assertFalse(status["release_gates"]["global_v2_0"]["passes"])
            rendered = mod.markdown_report(status)
            self.assertIn("Legacy stronger slice complete", rendered)
            self.assertIn("Active human packet review rows: `12`", rendered)
            self.assertNotIn("current handoff CSVs", rendered)
            self.assertIn("Gold v1.0", rendered)
            self.assertIn("Global v2.0", rendered)
            self.assertNotIn("- Stronger complete:", rendered)

    def test_mine_microtext_candidates_extracts_multiple_categories(self):
        mod = load_module("mine_microtext_candidates", ROOT / "tools" / "mine_microtext_candidates.py")

        rows = [
            {"page": 0, "text": "Mississauga, ON, L4V 1X1", "bbox_px": [1, 0, 20, 8]},
            {"page": 0, "text": "WP-1", "bbox_px": [1, 1, 20, 9]},
            {"page": 0, "text": "TIC-101", "bbox_px": [1, 2, 20, 10]},
            {"page": 0, "text": "WIRE-204", "bbox_px": [1, 12, 20, 20]},
            {"page": 0, "text": "BEDROOM", "bbox_px": [1, 17, 20, 25]},
            {"page": 0, "text": "J5", "bbox_px": [1, 22, 20, 30]},
            {"page": 0, "text": "12.5 mm", "bbox_px": [1, 32, 20, 40]},
            {"page": 0, "text": "1 1/2\"", "bbox_px": [1, 37, 20, 45]},
            {"page": 0, "text": "3 in.", "bbox_px": [1, 39, 20, 47]},
            {"page": 0, "text": "2 - #6 x 8'-0\",", "bbox_px": [1, 40, 80, 48]},
            {"page": 0, "text": "+0.10/-0.05", "bbox_px": [1, 42, 20, 50]},
            {"page": 0, "text": "PIT", "bbox_px": [1, 52, 20, 60]},
            {"page": 0, "text": "GENERATOR", "bbox_px": [1, 62, 20, 70]},
            {"page": 0, "text": "SPS-301", "bbox_px": [1, 72, 20, 80]},
        ]

        candidates = mod.mine_rows(rows, doc_id="sample_doc", version_id="v1")
        categories = {row["category"] for row in candidates}

        self.assertIn("instrument_tag", categories)
        self.assertIn("equipment_tag", categories)
        self.assertIn("wire_number", categories)
        self.assertIn("room_label", categories)
        self.assertIn("pin_label", categories)
        self.assertIn("dimension_value", categories)
        self.assertIn("tolerance_value", categories)
        self.assertTrue(all(row["review_status"] == "candidate" for row in candidates))
        self.assertFalse(any("Mississauga" in row["target_text"] for row in candidates))
        self.assertTrue(any(row["target_text"] == "PIT" for row in candidates))
        self.assertTrue(any(row["target_text"] == "GENERATOR" for row in candidates))
        self.assertTrue(any(row["target_text"] == "8'-0\"" for row in candidates))
        self.assertFalse(any(row["target_text"] == "SPS-301" for row in candidates))

    def test_mine_microtext_candidates_filters_by_doc_id_and_category(self):
        mod = load_module("mine_microtext_candidates", ROOT / "tools" / "mine_microtext_candidates.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            textlayer_dir = tmp_path / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            write_jsonl(
                textlayer_dir / "doc_a.jsonl",
                [
                    {"page": 0, "text": "TIC-101", "bbox_px": [1, 2, 20, 10]},
                    {"page": 0, "text": "J5", "bbox_px": [1, 22, 20, 30]},
                ],
            )
            write_jsonl(
                textlayer_dir / "doc_b.jsonl",
                [{"page": 0, "text": "TIC-202", "bbox_px": [1, 2, 20, 10]}],
            )

            candidates = mod.mine_textlayers(
                tmp_path,
                max_per_doc_category=10,
                doc_ids={"doc_a"},
                categories={"instrument_tag"},
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["doc_id"], "doc_a")
            self.assertEqual(candidates[0]["category"], "instrument_tag")

    def test_mine_microtext_candidates_emits_review_pack_evidence_fields(self):
        mod = load_module("mine_microtext_candidates_evidence", ROOT / "tools" / "mine_microtext_candidates.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            textlayer_dir = tmp_path / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            write_jsonl(
                textlayer_dir / "doc_a.jsonl",
                [{"page": 2, "text": "3'-6\" or", "bbox_px": [10, 20, 80, 40]}],
            )

            candidates = mod.mine_textlayers(
                tmp_path,
                max_per_doc_category=10,
                doc_ids={"doc_a"},
                categories={"dimension_value"},
            )

            self.assertEqual(len(candidates), 1)
            row = candidates[0]
            self.assertEqual(row["proposed_text"], "3'-6\"")
            self.assertEqual(row["image_path"], "derived/pages_300dpi/doc_a/page_002.png")
            self.assertEqual(row["question_text"], "What dimension value is shown in this region?")
            self.assertEqual(row["text_context"], "3'-6\" or")
            self.assertEqual(row["review_status"], "needs_review")
            self.assertIn("source_candidate_id", row)

    def test_mine_microtext_candidates_extracts_board_pin_aliases(self):
        mod = load_module("mine_microtext_candidates", ROOT / "tools" / "mine_microtext_candidates.py")

        rows = [
            {"page": 0, "text": "GPIO22", "bbox_px": [1, 1, 20, 9]},
            {"page": 0, "text": "ADC2_CH1", "bbox_px": [1, 11, 20, 19]},
            {"page": 0, "text": "SCK", "bbox_px": [1, 21, 20, 29]},
            {"page": 0, "text": "A0", "bbox_px": [1, 31, 20, 39]},
            {"page": 0, "text": "3V", "bbox_px": [1, 41, 20, 49]},
            {"page": 0, "text": "V_NEOI2C", "bbox_px": [1, 51, 20, 59]},
            {"page": 0, "text": "Input Only", "bbox_px": [1, 61, 20, 69]},
            {"page": 0, "text": "Adafruit ESP32 Feather V2", "bbox_px": [1, 71, 20, 79]},
        ]

        candidates = mod.mine_rows(rows, doc_id="pinout_doc", version_id="v2")
        target_texts = {row["target_text"] for row in candidates}

        self.assertEqual(
            target_texts,
            {"GPIO22", "ADC2_CH1", "SCK", "A0", "3V", "V_NEOI2C"},
        )
        self.assertTrue(all(row["category"] == "pin_label" for row in candidates))

    def test_mine_microtext_candidates_extracts_complete_schematic_signal_labels(self):
        mod = load_module("mine_microtext_candidates", ROOT / "tools" / "mine_microtext_candidates.py")

        rows = [
            {"page": 0, "text": "MIPI_CSI1_CLK_N", "bbox_px": [1, 1, 40, 9]},
            {"page": 0, "text": "USB1_D_P", "bbox_px": [1, 11, 40, 19]},
            {"page": 0, "text": "SYS_SCL", "bbox_px": [1, 21, 40, 29]},
            {"page": 0, "text": "+3V3", "bbox_px": [1, 31, 40, 39]},
            {"page": 0, "text": "PWR_FLAG", "bbox_px": [1, 41, 40, 49]},
            {"page": 0, "text": "PJE138K_R1_00001", "bbox_px": [1, 51, 40, 59]},
            {"page": 0, "text": "signal label note", "bbox_px": [1, 61, 40, 69]},
        ]

        candidates = mod.mine_rows(rows, doc_id="schematic_doc", version_id="v1")
        target_texts = {row["target_text"] for row in candidates}

        self.assertEqual(target_texts, {"MIPI_CSI1_CLK_N", "USB1_D_P", "SYS_SCL", "+3V3"})
        self.assertTrue(all(row["category"] == "pin_label" for row in candidates))

    def test_mine_microtext_candidates_extracts_process_unit_equipment_labels(self):
        mod = load_module("mine_microtext_candidates_process_units", ROOT / "tools" / "mine_microtext_candidates.py")

        rows = [
            {"page": 0, "text": "Hydrotreater", "bbox_px": [1, 1, 40, 9]},
            {"page": 0, "text": "Hydrocracker", "bbox_px": [1, 11, 40, 19]},
            {"page": 0, "text": "Alkylation", "bbox_px": [1, 21, 40, 29]},
            {"page": 0, "text": "Atmospheric Distillation", "bbox_px": [1, 31, 80, 39]},
            {"page": 0, "text": "Acid Gas Removal", "bbox_px": [1, 41, 80, 49]},
            {"page": 0, "text": "Sulfur Unit", "bbox_px": [1, 51, 80, 59]},
            {"page": 0, "text": "Tail Gas Treating", "bbox_px": [1, 61, 80, 69]},
            {"page": 0, "text": "Dehydration", "bbox_px": [1, 71, 80, 79]},
            {"page": 0, "text": "Nitrogen Rejection", "bbox_px": [1, 81, 80, 89]},
            {"page": 0, "text": "Mercury Removal", "bbox_px": [1, 91, 80, 99]},
            {"page": 0, "text": "NGL Recovery", "bbox_px": [1, 101, 80, 109]},
            {"page": 0, "text": "Fractionation Train", "bbox_px": [1, 111, 80, 119]},
            {"page": 0, "text": "Sweetening Units", "bbox_px": [1, 121, 80, 129]},
            {"page": 0, "text": "Absorber", "bbox_px": [1, 131, 80, 139]},
            {"page": 0, "text": "Regenerator", "bbox_px": [1, 141, 80, 149]},
            {"page": 0, "text": "Condenser", "bbox_px": [1, 151, 80, 159]},
            {"page": 0, "text": "Reboiler", "bbox_px": [1, 161, 80, 169]},
            {"page": 0, "text": "Finished products are shown in blue", "bbox_px": [1, 171, 100, 179]},
            {"page": 0, "text": "Condensate to an oil refinery", "bbox_px": [1, 181, 100, 189]},
        ]

        candidates = mod.mine_rows(rows, doc_id="refinery_flow", version_id="v1")
        target_texts = {row["target_text"] for row in candidates}

        self.assertEqual(
            target_texts,
            {
                "Hydrotreater",
                "Hydrocracker",
                "Alkylation",
                "Atmospheric Distillation",
                "Acid Gas Removal",
                "Sulfur Unit",
                "Tail Gas Treating",
                "Dehydration",
                "Nitrogen Rejection",
                "Mercury Removal",
                "NGL Recovery",
                "Fractionation Train",
                "Sweetening Units",
                "Absorber",
                "Regenerator",
                "Condenser",
                "Reboiler",
            },
        )
        self.assertTrue(all(row["category"] == "equipment_tag" for row in candidates))

    def test_microtext_review_builds_prioritized_context_batch(self):
        mod = load_module("microtext_review", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_dir = tmp_path / "derived" / "pages_300dpi" / "doc_a"
            page_dir.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_dir / "page_000.png")
            write_jsonl(
                tmp_path / "derived" / "textlayer" / "doc_a.jsonl",
                [
                    {"page": 0, "text": "near left", "bbox_px": [0, 0, 1, 1]},
                    {"page": 0, "text": "TIC-101", "bbox_px": [1, 2, 20, 10]},
                    {"page": 0, "text": "near right", "bbox_px": [21, 2, 40, 10]},
                ],
            )
            candidates = [
                {
                    "candidate_id": "c_pin",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [4, 5, 6, 7],
                    "target_text": "J5",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
                {
                    "candidate_id": "c_tag",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [1, 2, 20, 10],
                    "target_text": "TIC-101",
                    "category": "instrument_tag",
                    "review_status": "candidate",
                },
            ]

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=2,
                max_per_category=10,
                source_candidate_id="pcb_019",
            )

            self.assertEqual([row["candidate_id"] for row in batch], ["c_tag", "c_pin"])
            self.assertEqual(batch[0]["review_status"], "needs_review")
            self.assertEqual(batch[0]["image_path"], "derived/pages_300dpi/doc_a/page_000.png")
            self.assertIn("near left", batch[0]["text_context"])
            self.assertIn("near right", batch[0]["text_context"])
            self.assertEqual(batch[0]["question_text"], "What instrument tag is shown in this region?")
            self.assertTrue(all(row["source_candidate_id"] == "pcb_019" for row in batch))

    def test_microtext_review_limits_repeated_proposed_text(self):
        mod = load_module("microtext_review_text_diversity", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_dir = tmp_path / "derived" / "pages_300dpi" / "doc_a"
            page_dir.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_dir / "page_000.png")
            candidates = []
            for idx, text in enumerate(("GND", "GND", "GND", "J1")):
                candidates.append(
                    {
                        "candidate_id": f"c{idx}",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [idx * 10, 5, idx * 10 + 8, 15],
                        "target_text": text,
                        "category": "pin_label",
                        "review_status": "candidate",
                    }
                )

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
                max_per_text=1,
            )

            self.assertEqual([row["target_text"] for row in batch], ["GND", "J1"])

    def test_microtext_review_limits_rows_per_document(self):
        mod = load_module("microtext_review_doc_diversity", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            candidates = []
            for doc_id in ("doc_a", "doc_b"):
                page_dir = tmp_path / "derived" / "pages_300dpi" / doc_id
                page_dir.mkdir(parents=True)
                Image.new("RGB", (100, 100), color="white").save(page_dir / "page_000.png")
                for idx in range(3):
                    candidates.append(
                        {
                            "candidate_id": f"{doc_id}_{idx}",
                            "doc_id": doc_id,
                            "version_id": "v1",
                            "page_index": 0,
                            "bbox": [idx * 10, 5, idx * 10 + 8, 15],
                            "target_text": f"J{idx}",
                            "category": "pin_label",
                            "review_status": "candidate",
                        }
                    )

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
                max_per_doc=1,
            )

            self.assertEqual([row["doc_id"] for row in batch], ["doc_a", "doc_b"])

    def test_microtext_review_limits_rows_per_document_page(self):
        mod = load_module("microtext_review_page_diversity", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_dir = tmp_path / "derived" / "pages_300dpi" / "doc_a"
            page_dir.mkdir(parents=True)
            for page in range(2):
                Image.new("RGB", (100, 100), color="white").save(page_dir / f"page_{page:03d}.png")
            candidates = [
                {
                    "candidate_id": f"c{page}_{idx}",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": page,
                    "bbox": [idx * 10, 5, idx * 10 + 8, 15],
                    "target_text": f"SIG_{page}_{idx}",
                    "category": "pin_label",
                    "review_status": "candidate",
                }
                for page in range(2)
                for idx in range(3)
            ]

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
                max_per_page=1,
            )

            self.assertEqual([row["page_index"] for row in batch], [0, 1])

    def test_microtext_review_can_exclude_component_reference_designators(self):
        mod = load_module("microtext_review_reference_filter", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_dir = tmp_path / "derived" / "pages_300dpi" / "doc_a"
            page_dir.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_dir / "page_000.png")
            candidates = [
                {
                    "candidate_id": "component_ref",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [1, 2, 10, 12],
                    "target_text": "R287",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
                {
                    "candidate_id": "signal_label",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [20, 2, 40, 12],
                    "target_text": "USB1_D_P",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
            ]

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
                exclude_reference_designators=True,
            )

            self.assertEqual([row["candidate_id"] for row in batch], ["signal_label"])

    def test_microtext_review_can_require_full_source_text_match(self):
        mod = load_module("microtext_review_exact_text", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_dir = tmp_path / "derived" / "pages_300dpi" / "doc_a"
            page_dir.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_dir / "page_000.png")
            candidates = [
                {
                    "candidate_id": "partial",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [1, 2, 10, 12],
                    "target_text": "J3",
                    "raw_text": "J3-17",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
                {
                    "candidate_id": "exact",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [20, 2, 30, 12],
                    "target_text": "C1",
                    "raw_text": "C1",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
            ]

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
                exact_text_only=True,
            )

            self.assertEqual([row["candidate_id"] for row in batch], ["exact"])

    def test_microtext_miner_reads_pixhawk_eagle_revision_from_doc_id(self):
        mod = load_module(
            "mine_microtext_candidates_eagle_version",
            ROOT / "tools" / "mine_microtext_candidates.py",
        )

        self.assertEqual(mod.version_from_doc_id("pixhawk_fmuv1_1_7_1_eagle"), "1.7.1")
        self.assertEqual(mod.version_from_doc_id("pixhawk_fmuv2_4_6_eagle"), "4.6")

    def test_microtext_review_skips_existing_items(self):
        mod = load_module("microtext_review", ROOT / "tools" / "microtext_review.py")

        candidates = [
            {
                "candidate_id": "c0",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "J5",
                "category": "pin_label",
                "review_status": "candidate",
            }
        ]
        existing_items = [
            {
                "item_id": "m0",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "text_gt": "J5",
            }
        ]

        batch = mod.build_review_batch(
            root=ROOT,
            candidates=candidates,
            existing_items=existing_items,
            limit=10,
            max_per_category=10,
        )

        self.assertEqual(batch, [])

    def test_microtext_review_skips_previously_reviewed_rows(self):
        mod = load_module("microtext_review", ROOT / "tools" / "microtext_review.py")

        candidates = [
            {
                "candidate_id": "repeat_rejected",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "L4V",
                "category": "pin_label",
                "review_status": "candidate",
            },
            {
                "candidate_id": "repeat_by_region",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [5, 6, 7, 8],
                "target_text": "REV",
                "category": "pin_label",
                "review_status": "candidate",
            },
        ]
        reviewed_rows = [
            {
                "candidate_id": "repeat_rejected",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "L4V",
                "review_status": "rejected",
            },
            {
                "candidate_id": "different_id",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [5, 6, 7, 8],
                "target_text": "REV",
                "review_status": "rejected",
            },
        ]

        batch = mod.build_review_batch(
            root=ROOT,
            candidates=candidates,
            existing_items=[],
            reviewed_rows=reviewed_rows,
            limit=10,
            max_per_category=10,
        )

        self.assertEqual(batch, [])

    def test_microtext_review_skips_missing_or_out_of_frame_images(self):
        mod = load_module("microtext_review", ROOT / "tools" / "microtext_review.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_path)

            candidates = [
                {
                    "candidate_id": "valid",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [10, 10, 20, 20],
                    "target_text": "J5",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
                {
                    "candidate_id": "offpage",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [10, 140, 20, 150],
                    "target_text": "J6",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
                {
                    "candidate_id": "missing",
                    "doc_id": "doc_missing",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [10, 10, 20, 20],
                    "target_text": "J7",
                    "category": "pin_label",
                    "review_status": "candidate",
                },
            ]

            batch = mod.build_review_batch(
                root=tmp_path,
                candidates=candidates,
                existing_items=[],
                limit=10,
                max_per_category=10,
            )

            self.assertEqual([row["candidate_id"] for row in batch], ["valid"])

    def test_microtext_merge_promotes_only_accepted_review_rows(self):
        mod = load_module("microtext_merge", ROOT / "tools" / "microtext_merge.py")

        reviewed = [
            {
                "candidate_id": "mtcand__doc_a__v1__p0000__000001",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "TIC-101",
                "proposed_text": "TIC-101",
                "corrected_text": "",
                "category": "instrument_tag",
                "review_status": "accepted",
            },
            {
                "candidate_id": "mtcand__doc_a__v1__p0000__000002",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [4, 5, 6, 7],
                "target_text": "bad",
                "category": "pin_label",
                "review_status": "rejected",
            },
        ]

        items, questions, stats = mod.merge_review_rows(
            existing_items=[],
            existing_questions=[],
            reviewed_rows=reviewed,
            split="dev",
            reviewed_at="2026-05-12T00:00:00",
        )

        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(items[0]["item_id"], "mt__doc_a__v1__p0000__000001")
        self.assertEqual(items[0]["text_gt"], "TIC-101")
        self.assertEqual(items[0]["review_source"], "human")
        self.assertEqual(questions[0]["answer_text"], "TIC-101")
        self.assertEqual(questions[0]["split"], "dev")

    def test_microtext_merge_skips_quantized_bbox_duplicates(self):
        mod = load_module("microtext_merge", ROOT / "tools" / "microtext_merge.py")

        existing_items = [
            {
                "item_id": "m0",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [10, 10, 20, 20],
                "text_gt": "45°",
            }
        ]
        reviewed = [
            {
                "candidate_id": "mtcand__doc_a__v1__p0000__000001",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [9, 10, 19, 20],
                "target_text": "45°",
                "category": "dimension_value",
                "review_status": "accepted",
            }
        ]

        items, questions, stats = mod.merge_review_rows(
            existing_items=existing_items,
            existing_questions=[],
            reviewed_rows=reviewed,
            split="dev",
            reviewed_at="2026-05-12T00:00:00",
        )

        self.assertEqual(items, existing_items)
        self.assertEqual(questions, [])
        self.assertEqual(stats["duplicate"], 1)

    def test_microtext_merge_skips_same_region_even_if_ocr_text_differs(self):
        mod = load_module("microtext_merge", ROOT / "tools" / "microtext_merge.py")

        existing_items = [
            {
                "item_id": "m0",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [10, 10, 20, 20],
                "text_gt": "45°",
            }
        ]
        reviewed = [
            {
                "candidate_id": "mtcand__doc_a__v1__p0000__000001",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [10, 10, 20, 20],
                "target_text": "45\"",
                "category": "dimension_value",
                "review_status": "accepted",
            }
        ]

        items, questions, stats = mod.merge_review_rows(
            existing_items=existing_items,
            existing_questions=[],
            reviewed_rows=reviewed,
            split="dev",
            reviewed_at="2026-05-12T00:00:00",
        )

        self.assertEqual(items, existing_items)
        self.assertEqual(questions, [])
        self.assertEqual(stats["duplicate"], 1)

    def test_microtext_quality_audit_reports_out_of_frame_bbox(self):
        mod = load_module("audit_microtext_quality", ROOT / "tools" / "audit_microtext_quality.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_path)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "mt0",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [10, 10, 120, 20],
                        "text_gt": "J5",
                        "category": "pin_label",
                        "source_candidate_id": "c0",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_questions.jsonl",
                [{"question_id": "q0", "item_ids": ["mt0"], "answer_text": "J5"}],
            )

            report = mod.audit(tmp_path)

            self.assertEqual(report["critical_failures"], 1)
            self.assertEqual(report["out_of_frame"][0]["item_id"], "mt0")

    def test_microtext_split_audit_detects_family_leakage(self):
        mod = load_module("audit_microtext_splits", ROOT / "tools" / "audit_microtext_splits.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "splits").mkdir()
            (tmp_path / "splits" / "microtext_train.txt").write_text("doc_a\n")
            (tmp_path / "splits" / "microtext_dev.txt").write_text("doc_b\n")
            (tmp_path / "splits" / "microtext_test.txt").write_text("doc_c\n")
            write_jsonl(
                tmp_path / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "task": "microtext",
                        "doc_id": "doc_a",
                        "same_model_id": "family_1",
                    },
                    {
                        "type": "doc",
                        "task": "microtext",
                        "doc_id": "doc_b",
                        "same_model_id": "family_1",
                    },
                    {
                        "type": "doc",
                        "task": "microtext",
                        "doc_id": "doc_c",
                        "same_model_id": "family_2",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "m0",
                        "doc_id": "doc_a",
                        "category": "pin_label",
                        "split": "train",
                    },
                    {
                        "item_id": "m1",
                        "doc_id": "doc_b",
                        "category": "pin_label",
                        "split": "dev",
                    },
                    {
                        "item_id": "m2",
                        "doc_id": "doc_c",
                        "category": "dimension_value",
                        "split": "test",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_questions.jsonl",
                [
                    {"question_id": "q0", "item_ids": ["m0"], "split": "train"},
                    {"question_id": "q1", "item_ids": ["m1"], "split": "dev"},
                    {"question_id": "q2", "item_ids": ["m2"], "split": "test"},
                ],
            )

            report = mod.audit(tmp_path)

            self.assertIn("family_1", report["family_leakage"])
            self.assertGreater(report["critical_failures"], 0)

    def test_visualdiff_quality_audit_reports_missing_images_and_todos(self):
        mod = load_module("audit_visualdiff_quality", ROOT / "tools" / "audit_visualdiff_quality.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "p0",
                        "project_id": "project0",
                        "doc_id": "doc",
                        "version_id_old": "old",
                        "version_id_new": "new",
                        "page_index_old": 0,
                        "page_index_new": 0,
                        "bbox_old": [1, 2, 3, 4],
                        "bbox_new": [1, 2, 3, 4],
                        "change_desc_gt": "CHANGE_DESC_GT_TODO",
                        "change_type": ["symbol"],
                        "split": "dev",
                    }
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_questions.jsonl",
                [{"question_id": "q0", "pair_id": "p0", "answer_text": "CHANGE_DESC_GT_TODO"}],
            )

            report = mod.audit(tmp_path)

            self.assertEqual(report["todo_by_split"]["dev"], 1)
            self.assertEqual(report["critical_failures"], 2)
            self.assertEqual({row["reason"] for row in report["bbox_failures"]}, {"missing_image"})

    def test_visualdiff_assisted_describe_prefers_textlayer_diff(self):
        mod = load_module(
            "visualdiff_assisted_describe",
            ROOT / "tools" / "visualdiff_assisted_describe.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            old_image = tmp_path / "images" / "doc__old" / "page_0000.png"
            new_image = tmp_path / "images" / "doc__new" / "page_0000.png"
            old_image.parent.mkdir(parents=True)
            new_image.parent.mkdir(parents=True)
            Image.new("RGB", (80, 80), color="white").save(old_image)
            Image.new("RGB", (80, 80), color="gray").save(new_image)
            write_jsonl(
                tmp_path / "derived" / "textlayer" / "doc_old.jsonl",
                [{"page": 0, "text": "Viola V1.0", "bbox_px": [10, 10, 40, 25]}],
            )
            write_jsonl(
                tmp_path / "derived" / "textlayer" / "doc_new.jsonl",
                [{"page": 0, "text": "Viola V1.1", "bbox_px": [10, 10, 40, 25]}],
            )
            rows = [
                {
                    "pair_id": "p0",
                    "doc_id": "doc",
                    "version_id_old": "old",
                    "version_id_new": "new",
                    "page_index_old": 0,
                    "page_index_new": 0,
                    "bbox_old": [10, 10, 40, 25],
                    "bbox_new": [10, 10, 40, 25],
                    "change_desc_gt": "CHANGE_DESC_GT_TODO",
                    "split": "dev",
                    "change_type": ["text"],
                }
            ]

            reviewed, stats = mod.describe_rows(
                tmp_path,
                rows,
                {"dev"},
                "2026-05-14T00:00:00",
            )

            self.assertEqual(stats["rows"], 1)
            self.assertEqual(reviewed[0]["change_desc_gt"], "- Viola V1.0 + Viola V1.1")
            self.assertEqual(reviewed[0]["desc_source"], "codex_assisted_textlayer_diff")
            self.assertEqual(reviewed[0]["annotation_status"], "edited")

    def test_export_microtext_review_pack_writes_crop_and_manifest(self):
        mod = load_module("export_review_packs", ROOT / "tools" / "export_review_packs.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_path)

            rows = [
                {
                    "candidate_id": "c0",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [20, 30, 40, 50],
                    "target_text": "TIC-101",
                    "category": "instrument_tag",
                    "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                    "text_context": "A | TIC-101 | B",
                }
            ]

            stats = mod.export_microtext_pack(tmp_path, rows, Path("derived/review_packs/microtext_001"))
            manifest = read_jsonl(tmp_path / "derived/review_packs/microtext_001/manifest.jsonl")

            self.assertEqual(stats["rows"], 1)
            self.assertEqual(stats["source_pages"], 1)
            self.assertEqual(manifest[0]["crop_path"], "derived/review_packs/microtext_001/crops/c0.png")
            self.assertEqual(
                manifest[0]["page_path"],
                "derived/review_packs/microtext_001/pages/doc_a__p0000.png",
            )
            self.assertTrue((tmp_path / manifest[0]["crop_path"]).exists())
            self.assertTrue((tmp_path / manifest[0]["page_path"]).exists())
            self.assertTrue((tmp_path / "derived/review_packs/microtext_001/index.html").exists())

    def test_export_visualdiff_review_pack_writes_pair_panel(self):
        mod = load_module("export_review_packs", ROOT / "tools" / "export_review_packs.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            old_path = tmp_path / "images" / "doc__old" / "page_0000.png"
            new_path = tmp_path / "images" / "doc__new" / "page_0000.png"
            old_path.parent.mkdir(parents=True)
            new_path.parent.mkdir(parents=True)
            Image.new("RGB", (120, 100), color="white").save(old_path)
            Image.new("RGB", (120, 100), color="gray").save(new_path)

            rows = [
                {
                    "pair_id": "p0",
                    "doc_id": "doc",
                    "version_id_old": "old",
                    "version_id_new": "new",
                    "page_index_old": 0,
                    "page_index_new": 0,
                    "bbox_old": [10, 20, 30, 40],
                    "bbox_new": [12, 22, 32, 42],
                    "image_old": "images/doc__old/page_0000.png",
                    "image_new": "images/doc__new/page_0000.png",
                    "split": "dev",
                    "review_bucket": "release_non_titleblock",
                }
            ]

            stats = mod.export_visualdiff_pack(tmp_path, rows, Path("derived/review_packs/vdiff_001"))
            manifest = read_jsonl(tmp_path / "derived/review_packs/vdiff_001/manifest.jsonl")

            self.assertEqual(stats["rows"], 1)
            self.assertEqual(manifest[0]["panel_path"], "derived/review_packs/vdiff_001/panels/p0.png")
            panel_path = tmp_path / manifest[0]["panel_path"]
            self.assertTrue(panel_path.exists())
            with Image.open(panel_path) as panel:
                self.assertGreater(panel.height, 88)
                self.assertEqual(panel.getpixel((4, 4)), (153, 27, 27))
                self.assertEqual(panel.getpixel((panel.width - 5, 4)), (15, 107, 111))

    def test_export_microtext_review_pack_removes_stale_crops(self):
        mod = load_module("export_review_packs", ROOT / "tools" / "export_review_packs.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_path)
            output_dir = Path("derived/review_packs/microtext_clean")
            stale = tmp_path / output_dir / "crops" / "stale.png"
            stale.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10), color="red").save(stale)

            rows = [
                {
                    "candidate_id": "fresh",
                    "bbox": [10, 10, 30, 30],
                    "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                }
            ]

            stats = mod.export_microtext_pack(tmp_path, rows, output_dir)

            self.assertEqual(stats["rows"], 1)
            self.assertFalse(stale.exists())
            self.assertTrue((tmp_path / output_dir / "crops" / "fresh.png").exists())

    def test_export_microtext_review_pack_skips_out_of_frame_bbox(self):
        mod = load_module("export_review_packs", ROOT / "tools" / "export_review_packs.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page_path = tmp_path / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            page_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page_path)

            rows = [
                {
                    "candidate_id": "c_bad",
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": [20, 140, 40, 160],
                    "target_text": "off-page",
                    "category": "tolerance_value",
                    "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                }
            ]

            stats = mod.export_microtext_pack(tmp_path, rows, Path("derived/review_packs/microtext_bad"))
            manifest = read_jsonl(tmp_path / "derived/review_packs/microtext_bad/manifest.jsonl")

            self.assertEqual(stats["rows"], 0)
            self.assertEqual(stats["out_of_frame"], 1)
            self.assertEqual(manifest, [])

    def test_microtext_review_checklist_exports_and_applies_human_statuses(self):
        mod = load_module(
            "microtext_review_checklist", ROOT / "tools" / "microtext_review_checklist.py"
        )

        review_rows = [
            {
                "candidate_id": "c0",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "category": "instrument_tag",
                "proposed_text": "bad",
                "crop_path": "derived/review_packs/x/crops/c0.png",
                "page_path": "derived/review_packs/x/pages/doc_a__p0000.png",
                "image_path": "images/doc_a__v1/page_0000.png",
                "text_context": "bad | TIC-101",
                "question_text": "What instrument tag is shown?",
                "review_status": "needs_review",
            }
        ]

        checklist = mod.export_rows(review_rows)
        self.assertEqual(checklist[0]["crop_path"], "crops/c0.png")
        self.assertEqual(checklist[0]["page_path"], "pages/doc_a__p0000.png")
        checklist[0]["review_status"] = "edited"
        checklist[0]["corrected_text"] = "TIC-101"
        checklist[0]["corrected_category"] = "equipment_tag"
        checklist[0]["review_notes"] = "crop is clear"
        updated, stats, errors = mod.apply_checklist(review_rows, checklist)

        self.assertEqual(errors, [])
        self.assertEqual(stats["edited"], 1)
        self.assertEqual(stats["mergeable"], 1)
        self.assertEqual(updated[0]["review_status"], "edited")
        self.assertEqual(updated[0]["corrected_text"], "TIC-101")
        self.assertEqual(updated[0]["category"], "equipment_tag")

    def test_microtext_review_checklist_requires_at_least_one_correction_for_edits(self):
        mod = load_module(
            "microtext_review_checklist", ROOT / "tools" / "microtext_review_checklist.py"
        )

        review_rows = [{"candidate_id": "c0", "review_status": "needs_review"}]
        checklist = [{"candidate_id": "c0", "review_status": "edited", "corrected_text": ""}]
        updated, stats, errors = mod.apply_checklist(review_rows, checklist)

        self.assertEqual(updated[0]["review_status"], "needs_review")
        self.assertEqual(stats["edited_missing_correction"], 1)
        self.assertTrue(errors)

    def test_microtext_review_checklist_allows_category_only_edits(self):
        mod = load_module(
            "microtext_review_checklist", ROOT / "tools" / "microtext_review_checklist.py"
        )

        review_rows = [
            {
                "candidate_id": "c0",
                "category": "equipment_tag",
                "proposed_text": "PT-10",
                "review_status": "needs_review",
            }
        ]
        checklist = [
            {
                "candidate_id": "c0",
                "review_status": "edited",
                "corrected_text": "",
                "corrected_category": "instrument_tag",
            }
        ]
        updated, stats, errors = mod.apply_checklist(review_rows, checklist)

        self.assertEqual(errors, [])
        self.assertEqual(stats["edited"], 1)
        self.assertEqual(stats["mergeable"], 1)
        self.assertEqual(updated[0]["proposed_text"], "PT-10")
        self.assertEqual(updated[0]["corrected_text"], "")
        self.assertEqual(updated[0]["category"], "instrument_tag")

    def test_microtext_review_checklist_rejects_accepted_blank_proposals(self):
        mod = load_module(
            "microtext_review_checklist", ROOT / "tools" / "microtext_review_checklist.py"
        )

        review_rows = [{"candidate_id": "c0", "proposed_text": "", "target_text": ""}]
        checklist = [{"candidate_id": "c0", "review_status": "accepted", "proposed_text": ""}]
        updated, stats, errors = mod.apply_checklist(review_rows, checklist)

        self.assertEqual(updated[0].get("review_status"), None)
        self.assertEqual(stats["accepted_missing_proposed_text"], 1)
        self.assertTrue(errors)

    def test_review_packet_status_summarizes_microtext_visualdiff_and_source_csvs(self):
        mod = load_module("review_packet_status", ROOT / "tools" / "review_packet_status.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            packet = Path(temp_dir)
            with (packet / "micro.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "candidate_id",
                        "review_status",
                        "corrected_text",
                        "corrected_category",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "c0",
                        "review_status": "edited",
                        "corrected_text": "TIC-101",
                        "corrected_category": "",
                    }
                )
            with (packet / "visual.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["pair_id", "human_status", "human_description"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "pair_id": "p0",
                        "human_status": "edit",
                        "human_description": "",
                    }
                )
            with (packet / "source.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "selected_for_next_import",
                        "candidate_id",
                        "human_rights_note",
                        "downloaded_file_or_exact_url",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "selected_for_next_import": "yes",
                        "candidate_id": "civil_009",
                        "human_rights_note": "public",
                        "downloaded_file_or_exact_url": "https://example.com/file.pdf",
                    }
                )

            report = mod.summarize_packet(packet)

            self.assertEqual(report["complete_files"], 1)
            self.assertEqual(report["incomplete_files"], 2)
            visual = next(file for file in report["files"] if file["kind"] == "visualdiff")
            self.assertEqual(visual["status_counts"]["edit_missing_human_description"], 1)

    def test_source_candidate_backlog_summarizes_domains_rights_and_rank(self):
        mod = load_module("source_candidate_backlog", ROOT / "tools" / "source_candidate_backlog.py")

        rows = [
            {
                "candidate_id": "arch_001",
                "domain": "architectural",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.com/arch.pdf",
                "revision_family_potential": "low",
                "annotation_yield": "high",
                "notes": "",
            },
            {
                "candidate_id": "pid_001",
                "domain": "pid",
                "task_fit": "visualdiff,microtext",
                "rights_tier": "internal_only_candidate",
                "source_url": "https://example.com/pid.pdf",
                "revision_family_potential": "high",
                "annotation_yield": "high",
                "notes": "",
            },
        ]

        summary = mod.summarize_candidates(rows)
        ranked = mod.rank_candidates(rows)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_domain"]["architectural"], 1)
        self.assertEqual(summary["by_rights"]["internal_only_candidate"], 1)
        self.assertEqual(ranked[0]["candidate_id"], "pid_001")
        self.assertGreater(ranked[0]["priority_score"], ranked[1]["priority_score"])

    def test_source_candidate_backlog_writes_markdown_report(self):
        mod = load_module("source_candidate_backlog", ROOT / "tools" / "source_candidate_backlog.py")

        rows = [
            {
                "candidate_id": "civil_001",
                "domain": "civil",
                "task_fit": "visualdiff",
                "rights_tier": "rights_uncertain",
                "source_url": "https://example.com/civil.pdf",
                "revision_family_potential": "medium",
                "annotation_yield": "medium",
                "notes": "",
            }
        ]
        report = mod.markdown_report(rows)

        self.assertIn("Source Candidate Backlog", report)
        self.assertIn("rights_uncertain", report)
        self.assertIn("civil", report)

    def test_source_candidate_backlog_includes_browser_validation_summary(self):
        mod = load_module("source_candidate_backlog", ROOT / "tools" / "source_candidate_backlog.py")

        rows = [
            {
                "candidate_id": "arch_001",
                "domain": "architectural",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.com/arch.pdf",
                "revision_family_potential": "medium",
                "annotation_yield": "high",
                "notes": "",
            }
        ]
        validation_rows = [
            {
                "candidate_id": "arch_001",
                "next_action": "intake_first",
            },
            {
                "candidate_id": "pid_007",
                "next_action": "prototype_only",
            },
        ]

        report = mod.markdown_report(rows, validation_rows=validation_rows)

        self.assertIn("Browser-Validated First Tranche", report)
        self.assertIn("intake_first", report)
        self.assertIn("prototype_only", report)
        self.assertIn("arch_001", report)

    def test_page_range_parser_uses_one_based_ranges_and_original_indexes(self):
        mod = load_module("page_ranges", ROOT / "tools" / "page_ranges.py")

        self.assertEqual(mod.parse_page_selection("1,3-5,3", 8), [0, 2, 3, 4])
        self.assertEqual(mod.parse_page_selection("all", 3), [0, 1, 2])
        with self.assertRaises(ValueError):
            mod.parse_page_selection("0", 8)
        with self.assertRaises(ValueError):
            mod.parse_page_selection("4-3", 8)
        with self.assertRaises(ValueError):
            mod.parse_page_selection("9", 8)

    def test_import_image_pages_normalizes_raster_sources_and_writes_manifest(self):
        mod = load_module("import_image_pages", ROOT / "tools" / "import_image_pages.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "microtext" / "docs" / "source_sheet.jpg"
            source.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (10, 8), "white").save(source)

            manifest = mod.import_image_pages(
                root=tmp_path,
                doc_id="source_sheet",
                image_paths=[Path("microtext/docs/source_sheet.jpg")],
                manifest_path=Path("derived/source_imports/source_sheet.json"),
            )

            out_page = tmp_path / "derived" / "pages_300dpi" / "source_sheet" / "page_000.png"
            self.assertTrue(out_page.exists())
            self.assertEqual(manifest["page_count"], 1)
            self.assertEqual(manifest["pages"][0]["width"], 10)
            self.assertEqual(manifest["pages"][0]["height"], 8)
            self.assertTrue((tmp_path / "derived/source_imports/source_sheet.json").exists())

    def test_propose_microtext_regions_builds_unlabeled_review_rows(self):
        mod = load_module(
            "propose_microtext_regions", ROOT / "tools" / "propose_microtext_regions.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            page = tmp_path / "derived" / "pages_300dpi" / "raster_doc" / "page_000.png"
            page.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (480, 220), "white")
            # Dense black bars approximate a compact engineering label for the
            # proposal algorithm without requiring OCR in the test environment.
            for x in range(80, 240, 20):
                for y in range(90, 130, 8):
                    for dx in range(10):
                        for dy in range(3):
                            image.putpixel((x + dx, y + dy), (0, 0, 0))
            image.save(page)

            rows = mod.build_review_rows(
                root=tmp_path,
                doc_id="raster_doc",
                version_id="unknown",
                page_indexes=[0],
                total_limit=5,
            )

            self.assertTrue(rows)
            self.assertEqual(rows[0]["source"], "image_region_proposal")
            self.assertEqual(rows[0]["review_status"], "needs_review")
            self.assertEqual(rows[0]["proposed_text"], "")
            self.assertIn("derived/pages_300dpi/raster_doc/page_000.png", rows[0]["image_path"])
            self.assertEqual(mod.parse_csv_filter("doc_a, doc_b"), ["doc_a", "doc_b"])

    def test_make_public_private_split_strips_labels_from_public_inputs(self):
        mod = load_module(
            "make_public_private_split", ROOT / "tools" / "make_public_private_split.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "mt0",
                        "task": "microtext",
                        "split": "test",
                        "answer": "A12",
                        "evidence": [{"bbox": [1, 2, 3, 4]}],
                        "metadata": {"category": "pin_label", "text_gt": "A12"},
                    },
                    {
                        "id": "mt1",
                        "task": "microtext",
                        "split": "test",
                        "answer": "B13",
                        "evidence": [{"bbox": [1, 2, 3, 4]}],
                        "metadata": {"category": "pin_label"},
                    },
                    {
                        "id": "vd0",
                        "task": "visualdiff",
                        "split": "test",
                        "answer": "The label changed.",
                        "evidence": [{"bbox": [1, 2, 3, 4], "image_index": 0}],
                        "metadata": {"change_type": "text"},
                    },
                    {
                        "id": "vd1",
                        "task": "visualdiff",
                        "split": "test",
                        "answer": "The resistor moved.",
                        "evidence": [{"bbox": [1, 2, 3, 4], "image_index": 1}],
                        "metadata": {"change_type": "text"},
                    },
                ],
            )

            # This fixture isolates allocation/label stripping; guard behavior has dedicated tests.
            with patch.object(mod, "assert_release_eligible", return_value={"status": "fixture"}):
                stats = mod.make_split_files(tmp_path, public_ratio=0.5, seed="unit")
            public_inputs = read_jsonl(
                tmp_path / "release/public_inputs/eng_bench_public_test_inputs.jsonl"
            )
            hidden_inputs = read_jsonl(
                tmp_path / "release/public_inputs/eng_bench_hidden_test_inputs.jsonl"
            )
            hidden_labels = read_jsonl(
                tmp_path / "release/private_labels/eng_bench_hidden_test_labels.jsonl"
            )

            self.assertEqual(stats["source_test_rows"], 4)
            self.assertEqual(stats["public_test_rows"], 2)
            self.assertEqual(stats["hidden_test_rows"], 2)
            self.assertEqual(len(public_inputs), 2)
            self.assertEqual(len(hidden_inputs), 2)
            self.assertEqual(len(hidden_labels), 2)
            for row in public_inputs + hidden_inputs:
                self.assertNotIn("answer", row)
                self.assertNotIn("evidence", row)
                self.assertNotIn("text_gt", row.get("metadata", {}))
            self.assertTrue(all("answer" in row and "evidence" in row for row in hidden_labels))

    def test_validate_submission_format_checks_ids_answers_and_evidence(self):
        mod = load_module(
            "validate_submission_format", ROOT / "tools" / "validate_submission_format.py"
        )

        inputs = [{"id": "q0"}, {"id": "q1"}]
        predictions = [
            {
                "id": "q0",
                "answer": "A12",
                "evidence": [{"bbox": [1, 2, 3, 4], "image_index": 0}],
            },
            {"id": "q0", "answer": "duplicate", "evidence": []},
            {"id": "extra", "answer": "unknown", "evidence": [{"bbox": [4, 3, 2, 1]}]},
        ]

        report = mod.validate_submission(inputs, predictions)

        self.assertFalse(report["passed"])
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["duplicate_count"], 1)
        self.assertEqual(report["unknown_count"], 1)
        self.assertTrue(any("positive width" in error for error in report["errors"]))

    def test_audit_v2_0_gate_reports_infrastructure_status(self):
        mod = load_module("audit_v2_0_gate", ROOT / "tools" / "audit_v2_0_gate.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "microtext",
                        "split": "test",
                        "metadata": {"doc_id": "doc_a"},
                    }
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "item0", "doc_id": "doc_a"}],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "family__a__to__b__0000"}],
            )
            (tmp_path / "SOURCE_INVENTORY.csv").write_text(
                "path,task,doc_id,domain,source_url,public_status,textlayer_status,render_status,notes\n"
                "doc.pdf,microtext,doc_a,civil,https://example.com,public_domain_candidate,present,present,\n",
                encoding="utf-8",
            )
            for path in [
                "tools/make_public_private_split.py",
                "tools/validate_submission_format.py",
                "tools/register_leaderboard_submission.py",
                "docs/LEADERBOARD_PROTOCOL.md",
                "leaderboard/README.md",
                "release/public_inputs/eng_bench_hidden_test_inputs.jsonl",
                "release/private_labels/eng_bench_hidden_test_labels.jsonl",
            ]:
                full_path = tmp_path / path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text("{}\n", encoding="utf-8")
            baseline_path = tmp_path / "results" / "baselines" / "demo_report.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text("{}\n", encoding="utf-8")
            write_jsonl(
                baseline_path.with_name("demo_predictions.jsonl"),
                [{"id": "q0", "answer": "demo"}],
            )
            (baseline_path.parent / "oracle_smoke_report.json").write_text(
                "{}\n", encoding="utf-8"
            )
            submission_path = tmp_path / "leaderboard" / "submissions" / "team_a.json"
            submission_path.parent.mkdir(parents=True, exist_ok=True)
            submission_path.write_text(
                json.dumps(
                    {
                        "dataset_version": "v2.0-dev",
                        "split_manifest_hash": "a" * 64,
                        "model_name": "team_a_model",
                        "prediction_hash": "b" * 64,
                        "scorer_command": "python tools/benchmark_runner.py --gt hidden --pred team_a.jsonl",
                        "metrics": {"microtext.normalized_exact_match": 0.25},
                        "confidence_intervals": {
                            "microtext.normalized_exact_match": {"low": 0.2, "high": 0.3}
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (submission_path.parent / "smoke_submission.json").write_text(
                "{}\n", encoding="utf-8"
            )
            agreement_path = tmp_path / "results" / "annotation" / "agreement_report.json"
            agreement_path.parent.mkdir(parents=True, exist_ok=True)
            agreement_path.write_text(
                json.dumps(
                    {
                        "reference_rows": 10,
                        "complete_rows": 10,
                        "incomplete_rows": 0,
                        "agreement_gate_complete": True,
                        "inputs": {"reference_sha256": "a" * 64},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            readiness_path = (
                tmp_path / "derived" / "quality" / "agreement_sample_readiness.json"
            )
            readiness_path.parent.mkdir(parents=True, exist_ok=True)
            readiness_path.write_text(
                json.dumps(
                    {
                        "reference_rows": 10,
                        "release_ready_rows": 10,
                        "non_release_ready_rows": 0,
                        "fully_ready_rows": 10,
                        "release_sample_ready": True,
                        "inputs": {"reference_sha256": "a" * 64},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            status = mod.collect_status(tmp_path)

            self.assertFalse(status["v2_0_global_complete"])
            self.assertTrue(status["gates"]["human_agreement_audit"]["passes"])
            self.assertFalse(status["gates"]["paper_ready_provenance"]["passes"])
            self.assertFalse(
                status["release_constraints"]["microtext_category_balance"]["passes"]
            )
            self.assertTrue(status["gates"]["leaderboard_infrastructure"]["passes"])
            self.assertEqual(status["gates"]["gold_source_docs"]["current"], 0)
            self.assertEqual(status["gates"]["baselines_or_external_submissions"]["current"], 2)
            self.assertEqual(
                status["baseline_or_submission_names"],
                ["baseline:demo", "submission:team_a"],
            )

            exit_code = mod.main(["--root", str(tmp_path), "--date-label", "2026-06-03"])

            self.assertEqual(exit_code, 0)
            json_path = tmp_path / "results" / "health" / "v2_0_gate_audit_2026-06-03.json"
            md_path = tmp_path / "results" / "health" / "v2_0_gate_audit_2026-06-03.md"
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["date_label"], "2026-06-03")
            self.assertFalse(report["v2_0_global_complete"])
            self.assertTrue(report["gates"]["leaderboard_infrastructure"]["passes"])
            self.assertIn("audit date label", md_path.read_text(encoding="utf-8"))

    def test_audit_v2_0_gate_reads_bom_prefixed_inventory(self):
        mod = load_module(
            "audit_v2_0_gate_bom_inventory",
            ROOT / "tools" / "audit_v2_0_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "SOURCE_INVENTORY.csv"
            inventory_path.write_text(
                "doc_id,task,public_status\n"
                "doc_a,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )

            inventory = mod.read_csv(inventory_path)
            status = mod.unique_release_safe_inventory_status(
                inventory,
                {"alias_doc_ids": []},
            )

            self.assertEqual(inventory[0]["doc_id"], "doc_a")
            self.assertEqual(status["current"], 1)

    def test_audit_v1_5_gate_writes_dated_json_and_markdown(self):
        mod = load_module("audit_v1_5_gate", ROOT / "tools" / "audit_v1_5_gate.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "microtext",
                        "split": "test",
                        "metadata": {"doc_id": "doc_a"},
                    }
                ],
            )
            write_jsonl(
                tmp_path / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "family__a__to__b__0000"}],
            )
            (tmp_path / "SOURCE_INVENTORY.csv").write_text(
                "path,task,doc_id,domain,source_url,public_status,textlayer_status,render_status,notes\n"
                "doc.pdf,microtext,doc_a,civil,https://example.com,public_domain_candidate,present,present,\n",
                encoding="utf-8",
            )
            (tmp_path / "SOURCE_CANDIDATES.csv").write_text(
                "candidate_id,domain,source_url,public_status,notes\n"
                "civil_001,civil,https://example.com,public_domain_candidate,\n",
                encoding="utf-8",
            )
            baseline_path = tmp_path / "results" / "baselines" / "demo_report.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text("{}\n", encoding="utf-8")
            write_jsonl(
                baseline_path.with_name("demo_predictions.jsonl"),
                [{"id": "q0", "answer": "demo"}],
            )
            (baseline_path.parent / "oracle_smoke_test_report.json").write_text(
                "{}\n", encoding="utf-8"
            )
            agreement_path = tmp_path / "results" / "annotation" / "agreement_report.json"
            agreement_path.parent.mkdir(parents=True, exist_ok=True)
            agreement_path.write_text(
                json.dumps(
                    {
                        "reference_rows": 10,
                        "complete_rows": 10,
                        "incomplete_rows": 0,
                        "agreement_gate_complete": True,
                        "inputs": {"reference_sha256": "a" * 64},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            readiness_path = (
                tmp_path / "derived" / "quality" / "agreement_sample_readiness.json"
            )
            readiness_path.parent.mkdir(parents=True, exist_ok=True)
            readiness_path.write_text(
                json.dumps(
                    {
                        "reference_rows": 10,
                        "release_ready_rows": 10,
                        "non_release_ready_rows": 0,
                        "fully_ready_rows": 10,
                        "release_sample_ready": True,
                        "inputs": {"reference_sha256": "a" * 64},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            exit_code = mod.main(["--root", str(tmp_path), "--date-label", "2026-06-03"])

            self.assertEqual(exit_code, 0)
            json_path = tmp_path / "results" / "health" / "v1_5_gate_audit_2026-06-03.json"
            md_path = tmp_path / "results" / "health" / "v1_5_gate_audit_2026-06-03.md"
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(report["date_label"], "2026-06-03")
            self.assertFalse(report["v1_5_public_complete"])
            self.assertTrue(report["gates"]["human_agreement_audit"]["passes"])
            self.assertFalse(report["gates"]["active_gold_provenance"]["passes"])
            self.assertEqual(report["gates"]["baseline_reports"]["current"], 1)
            self.assertEqual(
                report["baseline_report_files"],
                ["results/baselines/demo_report.json"],
            )
            self.assertIn("audit date label", md_path.read_text(encoding="utf-8"))

    def test_audit_v1_5_gate_reads_bom_prefixed_inventory(self):
        mod = load_module(
            "audit_v1_5_gate_bom_inventory",
            ROOT / "tools" / "audit_v1_5_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "SOURCE_INVENTORY.csv"
            inventory_path.write_text(
                "doc_id,task,public_status\n"
                "doc_a,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )

            inventory = mod.read_csv(inventory_path)

            self.assertEqual(inventory[0]["doc_id"], "doc_a")
            self.assertEqual(sum(mod.is_release_safe_active(row) for row in inventory), 1)

    def test_v1_5_gate_counts_only_paired_content_distinct_baselines(self):
        mod = load_module(
            "audit_v1_5_gate_distinct_baselines",
            ROOT / "tools" / "audit_v1_5_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            baselines = tmp_path / "results" / "baselines"
            baselines.mkdir(parents=True)
            for name in ("alpha", "beta", "gamma", "orphan"):
                (baselines / f"{name}_report.json").write_text(
                    json.dumps({"model_name": name}),
                    encoding="utf-8",
                )
            write_jsonl(
                baselines / "alpha_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "alpha"}}],
            )
            write_jsonl(
                baselines / "beta_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "beta"}}],
            )
            write_jsonl(
                baselines / "gamma_predictions.jsonl",
                [{"id": "q0", "answer": "B", "metadata": {"model": "gamma"}}],
            )

            status = mod.collect_status(tmp_path)

            self.assertEqual(status["gates"]["baseline_reports"]["current"], 2)
            self.assertEqual(
                status["baseline_report_files"],
                [
                    "results/baselines/alpha_report.json",
                    "results/baselines/gamma_report.json",
                ],
            )

    def test_v2_gate_counts_only_paired_content_distinct_baselines(self):
        mod = load_module(
            "audit_v2_0_gate_distinct_baselines",
            ROOT / "tools" / "audit_v2_0_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            baselines = tmp_path / "results" / "baselines"
            baselines.mkdir(parents=True)
            for name in ("alpha", "beta", "gamma", "orphan"):
                (baselines / f"{name}_report.json").write_text(
                    json.dumps({"model_name": name}),
                    encoding="utf-8",
                )
            write_jsonl(
                baselines / "alpha_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "alpha"}}],
            )
            write_jsonl(
                baselines / "beta_predictions.jsonl",
                [{"id": "q0", "answer": "A", "metadata": {"model": "beta"}}],
            )
            write_jsonl(
                baselines / "gamma_predictions.jsonl",
                [{"id": "q0", "answer": "B", "metadata": {"model": "gamma"}}],
            )

            status = mod.collect_status(tmp_path)

            self.assertEqual(
                status["baseline_or_submission_names"],
                ["baseline:alpha", "baseline:gamma"],
            )
            self.assertEqual(status["gates"]["baselines_or_external_submissions"]["current"], 2)

    def test_v2_gate_counts_only_valid_content_distinct_external_submissions(self):
        mod = load_module(
            "audit_v2_0_gate_distinct_submissions",
            ROOT / "tools" / "audit_v2_0_gate.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            submissions = tmp_path / "leaderboard" / "submissions"
            submissions.mkdir(parents=True)
            valid_submission = {
                "dataset_version": "v2.0-dev",
                "split_manifest_hash": "a" * 64,
                "model_name": "team_a_model",
                "prediction_hash": "b" * 64,
                "scorer_command": "python tools/benchmark_runner.py --gt hidden --pred team_a.jsonl",
                "metrics": {"microtext.normalized_exact_match": 0.25},
                "confidence_intervals": {
                    "microtext.normalized_exact_match": {"low": 0.2, "high": 0.3}
                },
            }
            (submissions / "team_a.json").write_text(
                json.dumps(valid_submission) + "\n",
                encoding="utf-8",
            )
            duplicate = dict(valid_submission)
            duplicate["model_name"] = "renamed_team_a_model"
            (submissions / "duplicate.json").write_text(
                json.dumps(duplicate) + "\n",
                encoding="utf-8",
            )
            (submissions / "empty.json").write_text("{}\n", encoding="utf-8")
            (submissions / "smoke_submission.json").write_text("{}\n", encoding="utf-8")

            status = mod.collect_status(tmp_path)

            self.assertEqual(
                status["baseline_or_submission_names"],
                ["submission:duplicate"],
            )
            self.assertEqual(status["gates"]["baselines_or_external_submissions"]["current"], 1)
            excluded = {
                entry["name"]: entry["reason"]
                for entry in status["submission_registry"]["excluded"]
            }
            self.assertEqual(excluded["team_a"], "duplicate_prediction_hash")
            self.assertEqual(excluded["empty"], "missing_required_fields")
            self.assertEqual(excluded["smoke_submission"], "ignored_name")

    def test_register_leaderboard_submission_builds_countable_entry(self):
        mod = load_module(
            "register_leaderboard_submission",
            ROOT / "tools" / "register_leaderboard_submission.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            manifest = tmp_path / "release" / "private_labels" / "challenge_split_manifest.json"
            predictions = tmp_path / "submitted_predictions.jsonl"
            report = tmp_path / "submitted_report.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"seed":13}\n', encoding="utf-8")
            predictions.write_text('{"id":"q0","answer":"A"}\n', encoding="utf-8")
            report.write_text(
                json.dumps(
                    {
                        "model_name": "Submitted Model",
                        "microtext": {"normalized_exact_match": 0.25},
                        "confidence_intervals": {
                            "microtext.normalized_exact_match": {
                                "point": 0.25,
                                "low": 0.2,
                                "high": 0.3,
                                "samples": 1000,
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            exit_code = mod.main(
                [
                    "--root",
                    str(tmp_path),
                    "--dataset-version",
                    "v2.0-dev",
                    "--manifest",
                    str(manifest.relative_to(tmp_path)),
                    "--predictions",
                    str(predictions.relative_to(tmp_path)),
                    "--report",
                    str(report.relative_to(tmp_path)),
                    "--scorer-command",
                    "python tools/benchmark_runner.py --bootstrap-samples 1000",
                ]
            )

            self.assertEqual(exit_code, 0)
            output = tmp_path / "leaderboard" / "submissions" / "submitted_model.json"
            entry = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(entry["model_name"], "Submitted Model")
            self.assertEqual(entry["metrics"], {"microtext.normalized_exact_match": 0.25})
            self.assertEqual(
                entry["confidence_intervals"],
                {"microtext.normalized_exact_match": {"low": 0.2, "high": 0.3}},
            )
            self.assertEqual(len(entry["prediction_hash"]), 64)
            registry = mod.submission_registry(tmp_path)
            self.assertEqual([row["name"] for row in registry["counted"]], ["submitted_model"])
            self.assertEqual(registry["excluded"], [])

    def test_review_queue_inventory_counts_open_rows_and_missing_evidence(self):
        mod = load_module("review_queue_inventory", ROOT / "tools" / "review_queue_inventory.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            image = tmp_path / "images" / "doc__v1" / "page_0000.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 8), "white").save(image)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo.jsonl",
                [
                    {
                        "candidate_id": "c0",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                        "image_path": "images/doc__v1/page_0000.png",
                    },
                    {
                        "candidate_id": "c1",
                        "doc_id": "doc",
                        "review_status": "edited",
                        "image_path": "images/missing/page_0000.png",
                    },
                    {
                        "candidate_id": "c2",
                        "doc_id": "doc2",
                        "review_status": "rejected",
                    },
                ],
            )

            report = mod.inventory(tmp_path)
            item = report["files"][0]

            self.assertEqual(report["totals"]["files"], 1)
            self.assertEqual(report["totals"]["rows"], 3)
            self.assertEqual(report["totals"]["open_rows"], 1)
            self.assertEqual(report["totals"]["mergeable_status_rows"], 1)
            self.assertEqual(report["totals"]["terminal_reject_rows"], 1)
            self.assertEqual(item["missing_evidence_rows"], 1)
            self.assertEqual(item["source_count"], 2)

    def test_review_queue_inventory_does_not_recycle_hold_ledgers(self):
        mod = load_module(
            "review_queue_inventory_holds",
            ROOT / "tools" / "review_queue_inventory.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo.jsonl",
                [
                    {
                        "candidate_id": "selected",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "held",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                        "reservoir_disposition": "held",
                        "reservoir_hold_reason": "target_cap",
                    },
                ],
            )

            report = mod.inventory(tmp_path)
            item = report["files"][0]

            self.assertEqual(item["open_rows"], 1)
            self.assertEqual(item["fresh_open_rows"], 1)
            self.assertEqual(item["nonactionable_hold_rows"], 1)
            self.assertEqual(report["totals"]["actionable_fresh_open_rows"], 1)

    def test_review_queue_inventory_excludes_resolved_rows_from_next_human_candidates(self):
        mod = load_module(
            "review_queue_inventory_resolved",
            ROOT / "tools" / "review_queue_inventory.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo.jsonl",
                [
                    {
                        "candidate_id": "already_reviewed",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "fresh",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                    },
                ],
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo_reviewed.jsonl",
                [
                    {
                        "candidate_id": "already_reviewed",
                        "doc_id": "doc",
                        "review_status": "accepted",
                    }
                ],
            )

            report = mod.inventory(tmp_path)
            item = next(
                row
                for row in report["files"]
                if row["path"].endswith("microtext_review_demo.jsonl")
            )

            self.assertEqual(item["open_rows"], 2)
            self.assertEqual(item["fresh_open_rows"], 1)
            self.assertEqual(item["stale_open_rows"], 1)
            self.assertEqual(report["totals"]["fresh_open_rows"], 1)
            self.assertEqual(
                [row["path"] for row in report["next_human_candidates"]],
                ["microtext/annotations/microtext_review_demo.jsonl"],
            )

    def test_review_queue_inventory_excludes_active_packet_rows_from_next_human_candidates(self):
        mod = load_module(
            "review_queue_inventory_packeted",
            ROOT / "tools" / "review_queue_inventory.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo.jsonl",
                [
                    {
                        "candidate_id": "already_packeted",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                    }
                ],
            )
            packet_root = tmp_path / "derived" / "human_adjudication" / "packet"
            write_jsonl(
                packet_root / "review_packs" / "demo" / "manifest.jsonl",
                [{"candidate_id": "already_packeted", "doc_id": "doc"}],
            )
            index_path = tmp_path / "derived" / "quality" / "human_packet_index_2026-06-05.json"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = mod.inventory(tmp_path, packet_date_label="2026-06-05")
            item = report["files"][0]

            self.assertEqual(item["open_rows"], 1)
            self.assertEqual(item["packeted_open_rows"], 1)
            self.assertEqual(item["fresh_open_rows"], 0)
            self.assertEqual(report["next_human_candidates"], [])

    def test_review_queue_inventory_excludes_rights_blocked_fresh_rows_from_human_candidates(self):
        mod = load_module(
            "review_queue_inventory_rights",
            ROOT / "tools" / "review_queue_inventory.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "SOURCE_INVENTORY.csv").write_text(
                "path,task,doc_id,domain,source_url,public_status,textlayer_status,render_status,notes\n"
                "docs/held.pdf,microtext,held_doc,mechanical_cad,,unknown,present,present,rights pending\n",
                encoding="utf-8",
            )
            write_jsonl(
                tmp_path / "microtext" / "annotations" / "microtext_review_demo.jsonl",
                [
                    {
                        "candidate_id": "held",
                        "doc_id": "held_doc",
                        "review_status": "needs_review",
                    }
                ],
            )

            report = mod.inventory(tmp_path)
            item = report["files"][0]

            self.assertEqual(item["fresh_open_rows"], 1)
            self.assertEqual(item["rights_blocked_fresh_open_rows"], 1)
            self.assertEqual(item["actionable_fresh_open_rows"], 0)
            self.assertEqual(report["totals"]["rights_blocked_fresh_open_rows"], 1)
            self.assertEqual(report["next_human_candidates"], [])

    def test_weak_center_baseline_emits_center_evidence_for_both_tasks(self):
        mod = load_module(
            "weak_heuristic_baselines", ROOT / "baselines" / "weak_heuristic_baselines.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for rel in ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"]:
                image = tmp_path / rel
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 80), "white").save(image)
            rows = [
                {
                    "id": "mt0",
                    "task": "microtext",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png"],
                },
                {
                    "id": "vd0",
                    "task": "visualdiff",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"],
                },
            ]

            predictions, stats = mod.predict_rows(tmp_path, rows, mode="center", split="test")

            self.assertEqual(stats["predictions"], 2)
            self.assertEqual(predictions[0]["evidence"][0]["bbox"], [37, 30, 62, 50])
            self.assertEqual(len(predictions[1]["evidence"]), 2)

    def test_weak_full_page_baseline_emits_page_sized_evidence(self):
        mod = load_module(
            "weak_heuristic_baselines", ROOT / "baselines" / "weak_heuristic_baselines.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for rel in ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"]:
                image = tmp_path / rel
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 80), "white").save(image)
            rows = [
                {
                    "id": "mt0",
                    "task": "microtext",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png"],
                },
                {
                    "id": "vd0",
                    "task": "visualdiff",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"],
                },
            ]

            predictions, stats = mod.predict_rows(tmp_path, rows, mode="full_page", split="test")

            self.assertEqual(stats["predictions"], 2)
            self.assertEqual(predictions[0]["evidence"], [{"image_index": 0, "bbox": [0, 0, 100, 80]}])
            self.assertEqual(
                predictions[1]["evidence"],
                [
                    {"image_index": 0, "bbox": [0, 0, 100, 80]},
                    {"image_index": 1, "bbox": [0, 0, 100, 80]},
                ],
            )

    def test_weak_grid_baseline_emits_distinct_cell_evidence(self):
        mod = load_module(
            "weak_heuristic_baselines_grid", ROOT / "baselines" / "weak_heuristic_baselines.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            for rel in ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"]:
                image = tmp_path / rel
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (90, 60), "white").save(image)
            rows = [
                {
                    "id": "mt0",
                    "task": "microtext",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png"],
                },
                {
                    "id": "vd0",
                    "task": "visualdiff",
                    "split": "test",
                    "images": ["images/doc__v1/page_0000.png", "images/doc__v2/page_0000.png"],
                },
            ]

            top_left, top_left_stats = mod.predict_rows(
                tmp_path,
                rows,
                mode="grid",
                split="test",
                grid_cell=0,
                grid_rows=3,
                grid_cols=3,
            )
            bottom_right, bottom_right_stats = mod.predict_rows(
                tmp_path,
                rows,
                mode="grid",
                split="test",
                grid_cell=8,
                grid_rows=3,
                grid_cols=3,
            )

            self.assertEqual(top_left_stats["predictions"], 2)
            self.assertEqual(bottom_right_stats["predictions"], 2)
            self.assertEqual(top_left[0]["evidence"], [{"image_index": 0, "bbox": [0, 0, 30, 20]}])
            self.assertEqual(bottom_right[0]["evidence"], [{"image_index": 0, "bbox": [60, 40, 90, 60]}])
            self.assertEqual(
                bottom_right[1]["evidence"],
                [
                    {"image_index": 0, "bbox": [60, 40, 90, 60]},
                    {"image_index": 1, "bbox": [60, 40, 90, 60]},
                ],
            )
            self.assertNotEqual(top_left[0]["evidence"], bottom_right[0]["evidence"])

    def test_weak_label_prior_uses_only_calibration_split(self):
        mod = load_module(
            "weak_heuristic_baselines", ROOT / "baselines" / "weak_heuristic_baselines.py"
        )

        rows = [
            {
                "id": "train0",
                "task": "microtext",
                "split": "train",
                "answer": "TRAIN_PRIOR",
                "metadata": {"category": "dimension_value"},
            },
            {
                "id": "test0",
                "task": "microtext",
                "split": "test",
                "answer": "TEST_LABEL_MUST_NOT_BE_USED",
                "metadata": {"category": "dimension_value"},
            },
        ]

        predictions, stats = mod.predict_rows(
            Path("."),
            rows,
            mode="label_prior",
            split="test",
            task="microtext",
            calibration_splits={"train"},
        )

        self.assertEqual(stats["prior_buckets"], 2)
        self.assertEqual(predictions[0]["answer"], "TRAIN_PRIOR")
        self.assertNotEqual(predictions[0]["answer"], "TEST_LABEL_MUST_NOT_BE_USED")

    def test_audit_baseline_coverage_counts_prediction_files_per_row(self):
        mod = load_module("audit_baseline_coverage", ROOT / "tools" / "audit_baseline_coverage.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {"id": "q0", "task": "microtext", "split": "test"},
                    {"id": "q1", "task": "visualdiff", "split": "test"},
                ],
            )
            for name, ids in {
                "a_predictions.jsonl": ["q0", "q1"],
                "b_predictions.jsonl": ["q0"],
                "c_predictions.jsonl": ["q0"],
                "oracle_predictions.jsonl": ["q1"],
            }.items():
                write_jsonl(
                    tmp_path / "results" / "baselines" / name,
                    [{"id": identifier, "answer": "x"} for identifier in ids],
                )
            for name in ("a", "b", "c"):
                (tmp_path / "results" / "baselines" / f"{name}_report.json").write_text(
                    "{}\n",
                    encoding="utf-8",
                )

            report = mod.audit_coverage(tmp_path, min_baselines=2)

            self.assertFalse(report["passed"])
            self.assertEqual(report["baseline_prediction_file_count"], 2)
            self.assertEqual(report["rows_by_coverage"], {"1": 1, "2": 1})
            self.assertEqual(report["failing_rows"], 1)
            self.assertEqual(report["failing_examples"][0]["id"], "q1")

    def test_process_v1_5_human_return_stages_completed_packet(self):
        mod = load_module(
            "process_v1_5_human_return", ROOT / "tools" / "process_v1_5_human_return.py"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            packet = tmp_path / "packet"
            packet.mkdir()
            for task in mod.TASKS:
                source_path = tmp_path / task.source_jsonl
                if task.kind == "microtext":
                    write_jsonl(
                        source_path,
                        [
                            {
                                "candidate_id": f"{task.output_stem}__c0",
                                "doc_id": "doc",
                                "version_id": "v1",
                                "page_index": 0,
                                "bbox": [1, 2, 3, 4],
                                "proposed_text": "A12",
                                "category": "pin_label",
                                "review_status": "needs_review",
                            }
                        ],
                    )
                    with (packet / task.checklist).open("w", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(
                            f,
                            fieldnames=[
                                "candidate_id",
                                "review_status",
                                "proposed_text",
                                "corrected_text",
                                "corrected_category",
                                "review_notes",
                            ],
                        )
                        writer.writeheader()
                        writer.writerow(
                            {
                                "candidate_id": f"{task.output_stem}__c0",
                                "review_status": "accepted",
                                "proposed_text": "A12",
                                "corrected_text": "",
                                "corrected_category": "",
                                "review_notes": "clear",
                            }
                        )
                else:
                    write_jsonl(
                        source_path,
                        [
                            {
                                "pair_id": f"{task.output_stem}__p0",
                                "project_id": "family",
                                "description": "CHANGE_DESC_GT_TODO",
                                "change_desc_gt": "CHANGE_DESC_GT_TODO",
                            }
                        ],
                    )
                    with (packet / task.checklist).open("w", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(
                            f,
                            fieldnames=[
                                "pair_id",
                                "human_status",
                                "human_description",
                                "human_notes",
                            ],
                        )
                        writer.writeheader()
                        writer.writerow(
                            {
                                "pair_id": f"{task.output_stem}__p0",
                                "human_status": "edit",
                                "human_description": "The visible label changes from A to B.",
                                "human_notes": "inside crop",
                            }
                        )

            report, errors = mod.process_packet(tmp_path, packet, tmp_path / "processed")

            self.assertEqual(errors, [])
            self.assertTrue(report["complete"])
            self.assertEqual(report["totals"]["checklists"], 6)
            self.assertEqual(report["totals"]["mergeable_rows"], 6)
            self.assertTrue(
                (tmp_path / "processed" / "visualdiff_train_description_rewrite_reviewed.jsonl").exists()
            )

    def test_process_v1_5_human_return_rejects_visualdiff_valid_on_todo(self):
        mod = load_module(
            "process_v1_5_human_return", ROOT / "tools" / "process_v1_5_human_return.py"
        )

        reviewed, quarantine, stats, errors = mod.apply_visualdiff_checklist(
            [{"pair_id": "p0", "change_desc_gt": "CHANGE_DESC_GT_TODO"}],
            [{"pair_id": "p0", "human_status": "valid", "human_description": ""}],
        )

        self.assertEqual(reviewed, [])
        self.assertEqual(quarantine, [])
        self.assertEqual(stats["valid_on_todo_description"], 1)
        self.assertTrue(any("valid cannot be used" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
