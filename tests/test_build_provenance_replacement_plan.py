from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "tools" / "build_provenance_replacement_plan.py"
    spec = importlib.util.spec_from_file_location("build_provenance_replacement_plan", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class ProvenanceReplacementPlanTests(unittest.TestCase):
    def test_excluded_preferred_candidate_is_replaced_without_missing_error(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "manifest.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "active",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {"doc_id": "blocked", "category": "pin_label"},
                    }
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\n"
                "open_a,microtext,public_domain_candidate\n"
                "open_b,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )
            (root / "provenance.json").write_text(
                json.dumps({"documents": [{"doc_id": "blocked", "paper_ready": False}]}),
                encoding="utf-8",
            )
            candidates = [
                {
                    "candidate_id": "human_rejected",
                    "task": "microtext",
                    "doc_id": "open_a",
                    "category": "pin_label",
                    "reserved_split": "dev",
                    "source_public_status": "public_domain_candidate",
                    "safe_to_merge_gold": False,
                },
                {
                    "candidate_id": "fresh_alternate",
                    "task": "microtext",
                    "doc_id": "open_b",
                    "category": "pin_label",
                    "reserved_split": "dev",
                    "source_public_status": "public_domain_candidate",
                    "safe_to_merge_gold": False,
                },
            ]
            write_jsonl(root / "current.jsonl", candidates)
            write_jsonl(root / "future.jsonl", [])
            write_jsonl(root / "issued.jsonl", [candidates[0]])

            summary, _, selected, _ = mod.build_plan(
                root=root,
                provenance_report=root / "provenance.json",
                current_assignment=root / "current.jsonl",
                future_capacity=root / "future.jsonl",
                date_label="fixture",
                max_per_source=50,
                preferred_issued=[root / "issued.jsonl"],
                excluded_candidate_ids={"human_rejected"},
            )

            self.assertEqual([row["candidate_id"] for row in selected], ["fresh_alternate"])
            self.assertEqual(summary["candidate_pool_rejections"]["excluded_candidate_identity"], 1)
            self.assertEqual(summary["preferred_issued_excluded"], 1)
            self.assertEqual(summary["preferred_issued_missing"], 0)

    def test_read_identity_file_supports_csv_and_jsonl(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "holds.csv").write_text(
                "record_id,status\nfrom_csv,rejected\n",
                encoding="utf-8-sig",
            )
            write_jsonl(root / "holds.jsonl", [{"pair_id": "from_jsonl"}])

            self.assertEqual(mod.read_identity_file(root / "holds.csv"), {"from_csv"})
            self.assertEqual(mod.read_identity_file(root / "holds.jsonl"), {"from_jsonl"})

    def test_active_underlying_identity_is_excluded_and_preferred_is_retired(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "manifest.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "q_blocked",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {"doc_id": "blocked", "category": "pin_label"},
                    },
                    {
                        "id": "q_active_candidate",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {
                            "doc_id": "open",
                            "category": "pin_label",
                            "item_id": "active_candidate",
                            "item_ids": ["active_candidate"],
                        },
                    },
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\nopen,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )
            (root / "provenance.json").write_text(
                json.dumps({"documents": [{"doc_id": "blocked", "paper_ready": False}]}),
                encoding="utf-8",
            )
            candidates = [
                {
                    "candidate_id": "active_candidate",
                    "task": "microtext",
                    "doc_id": "open",
                    "category": "pin_label",
                    "reserved_split": "dev",
                    "source_public_status": "public_domain_candidate",
                    "safe_to_merge_gold": False,
                },
                {
                    "candidate_id": "fresh_candidate",
                    "task": "microtext",
                    "doc_id": "open",
                    "category": "pin_label",
                    "reserved_split": "dev",
                    "source_public_status": "public_domain_candidate",
                    "safe_to_merge_gold": False,
                },
            ]
            write_jsonl(root / "current.jsonl", candidates)
            write_jsonl(root / "future.jsonl", [])
            write_jsonl(root / "preferred.jsonl", [candidates[0]])

            summary, _, selected, _ = mod.build_plan(
                root=root,
                provenance_report=root / "provenance.json",
                current_assignment=root / "current.jsonl",
                future_capacity=root / "future.jsonl",
                date_label="fixture",
                max_per_source=50,
                preferred_issued=[root / "preferred.jsonl"],
            )

            self.assertEqual([row["candidate_id"] for row in selected], ["fresh_candidate"])
            self.assertEqual(summary["candidate_pool_rejections"]["already_active_gold_identity"], 1)
            self.assertEqual(summary["preferred_issued_retired_active_gold"], 1)
            self.assertEqual(summary["preferred_issued_missing"], 0)

    def test_builds_split_preserving_nonmergeable_replacement_plan(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "pair",
                        "pair_id": "vdiff__blocked__v1__to__v2",
                        "from_doc_id": "blocked_old",
                        "to_doc_id": "blocked_new",
                    },
                    {
                        "type": "pair",
                        "pair_id": "vdiff__open__v1__to__v2",
                        "from_doc_id": "open_old",
                        "to_doc_id": "open_new",
                    }
                ],
            )
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "mt_active",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {"doc_id": "blocked_mt", "category": "pin_label"},
                    },
                    {
                        "id": "vd_active",
                        "task": "visualdiff",
                        "split": "test",
                        "metadata": {
                            "pair_id": "vdiff__blocked__v1__to__v2__0001",
                            "change_type": ["text"],
                        },
                    },
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\n"
                "open_mt,microtext,public_domain_candidate\n"
                "open_old,visualdiff,cc_by_sa_open_hardware_candidate\n"
                "open_new,visualdiff,cc_by_sa_open_hardware_candidate\n",
                encoding="utf-8-sig",
            )
            provenance_path = root / "provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "blocked_mt", "paper_ready": False},
                            {"doc_id": "blocked_old", "paper_ready": False},
                            {"doc_id": "blocked_new", "paper_ready": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current_path = root / "current.jsonl"
            write_jsonl(
                current_path,
                [
                    {
                        "pair_id": "vdiff__open__v1__to__v2__0001",
                        "project_id": "vdiff__open__v1__to__v2",
                        "task": "visualdiff",
                        "change_type": "text",
                        "reserved_split": "test",
                        "safe_to_merge_gold": False,
                    }
                ],
            )
            future_path = root / "future.jsonl"
            write_jsonl(
                future_path,
                [
                    {
                        "candidate_id": "mt_open",
                        "task": "microtext",
                        "doc_id": "open_mt",
                        "category": "pin_label",
                        "reserved_split": "dev",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                    },
                    {
                        "pair_id": "vdiff__open_future__v1__to__v2__0001",
                        "project_id": "vdiff__open_future__v1__to__v2",
                        "task": "visualdiff",
                        "old_doc_id": "open_old",
                        "new_doc_id": "open_new",
                        "change_type": "text",
                        "reserved_split": "test",
                        "source_public_status": "cc_by_sa_open_hardware_candidate",
                        "safe_to_merge_gold": False,
                    },
                    {
                        "candidate_id": "blocked_candidate",
                        "task": "microtext",
                        "doc_id": "blocked_mt",
                        "category": "pin_label",
                        "reserved_split": "dev",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                    },
                ],
            )

            summary, affected, selected, gaps = mod.build_plan(
                root=root,
                provenance_report=provenance_path,
                current_assignment=current_path,
                future_capacity=future_path,
                date_label="fixture",
                max_per_source=50,
            )

            self.assertEqual(summary["affected_gold_rows"], 2)
            self.assertEqual(summary["selected_replacement_candidates"], 2)
            self.assertEqual(summary["remaining_replacement_gap"], 0)
            self.assertTrue(summary["all_replacement_capacity_available"])
            self.assertEqual({row["active_id"] for row in affected}, {"mt_active", "vd_active"})
            self.assertEqual(
                {row.get("candidate_id") or row.get("pair_id") for row in selected},
                {"mt_open", "vdiff__open__v1__to__v2__0001"},
            )
            self.assertTrue(all(row["safe_to_merge_gold"] is False for row in selected))
            self.assertTrue(all(row["review_status"] == "needs_review" for row in selected))
            self.assertTrue(all(row["remaining_gap"] == 0 for row in gaps))

    def test_main_writes_all_outputs(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "manifest.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "mt_active",
                        "task": "microtext",
                        "split": "test",
                        "metadata": {"doc_id": "blocked", "category": "equipment_tag"},
                    }
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\nopen,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )
            (root / "provenance.json").write_text(
                json.dumps({"documents": [{"doc_id": "blocked", "paper_ready": False}]}),
                encoding="utf-8",
            )
            write_jsonl(root / "current.jsonl", [])
            write_jsonl(
                root / "future.jsonl",
                [
                    {
                        "candidate_id": "replacement",
                        "task": "microtext",
                        "doc_id": "open",
                        "category": "room_label",
                        "reserved_split": "test",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                    }
                ],
            )

            exit_code = mod.main(
                [
                    "--root",
                    str(root),
                    "--provenance-report",
                    "provenance.json",
                    "--current-assignment",
                    "current.jsonl",
                    "--future-capacity",
                    "future.jsonl",
                    "--date-label",
                    "fixture",
                    "--output-json",
                    "out/summary.json",
                    "--affected-jsonl",
                    "out/affected.jsonl",
                    "--candidates-jsonl",
                    "out/candidates.jsonl",
                    "--new-review-candidates-jsonl",
                    "out/new_review.jsonl",
                    "--already-assigned-candidates-jsonl",
                    "out/already_assigned.jsonl",
                    "--gap-csv",
                    "out/gaps.csv",
                ]
            )

            self.assertEqual(exit_code, 0)
            summary = json.loads((root / "out/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["task_split_fallback_matches"], 1)
            self.assertEqual(len((root / "out/affected.jsonl").read_text().splitlines()), 1)
            self.assertEqual(len((root / "out/candidates.jsonl").read_text().splitlines()), 1)
            self.assertEqual(len((root / "out/new_review.jsonl").read_text().splitlines()), 1)
            self.assertEqual(
                len((root / "out/already_assigned.jsonl").read_text().splitlines()),
                0,
            )
            with (root / "out/gaps.csv").open(encoding="utf-8-sig", newline="") as handle:
                gaps = list(csv.DictReader(handle))
            self.assertEqual(gaps[0]["remaining_gap"], "0")

    def test_selection_replaces_exact_duplicate_evidence(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "manifest.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": f"active_{index}",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {"doc_id": "blocked", "category": "pin_label"},
                    }
                    for index in range(2)
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\n"
                "open_a,microtext,public_domain_candidate\n"
                "open_b,microtext,public_domain_candidate\n"
                "open_c,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )
            (root / "provenance.json").write_text(
                json.dumps({"documents": [{"doc_id": "blocked", "paper_ready": False}]}),
                encoding="utf-8",
            )
            write_jsonl(root / "current.jsonl", [])
            image_dir = root / "images"
            image_dir.mkdir()
            Image.new("RGB", (100, 80), "white").save(image_dir / "same_a.png")
            Image.new("RGB", (100, 80), "white").save(image_dir / "same_b.png")
            Image.new("RGB", (100, 80), "gray").save(image_dir / "unique.png")
            write_jsonl(
                root / "future.jsonl",
                [
                    {
                        "candidate_id": candidate_id,
                        "task": "microtext",
                        "doc_id": doc_id,
                        "category": "pin_label",
                        "reserved_split": "dev",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                        "image_path": image_path,
                        "bbox": [20, 20, 50, 50],
                    }
                    for candidate_id, doc_id, image_path in (
                        ("dup_a", "open_a", "images/same_a.png"),
                        ("dup_b", "open_b", "images/same_b.png"),
                        ("unique", "open_c", "images/unique.png"),
                    )
                ],
            )

            summary, _, selected, _ = mod.build_plan(
                root=root,
                provenance_report=root / "provenance.json",
                current_assignment=root / "current.jsonl",
                future_capacity=root / "future.jsonl",
                date_label="fixture",
                max_per_source=50,
            )

            self.assertEqual(
                {row["candidate_id"] for row in selected},
                {"dup_a", "unique"},
            )
            self.assertEqual(summary["selected_unique_evidence_fingerprints"], 2)
            self.assertEqual(summary["duplicate_evidence_candidate_skips"], 1)

    def test_preferred_issued_row_wins_over_new_exact_category_candidate(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "manifest.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "active",
                        "task": "microtext",
                        "split": "dev",
                        "metadata": {"doc_id": "blocked", "category": "pin_label"},
                    }
                ],
            )
            (root / "SOURCE_INVENTORY.csv").write_text(
                "doc_id,task,public_status\n"
                "open_exact,microtext,public_domain_candidate\n"
                "open_issued,microtext,public_domain_candidate\n",
                encoding="utf-8-sig",
            )
            (root / "provenance.json").write_text(
                json.dumps({"documents": [{"doc_id": "blocked", "paper_ready": False}]}),
                encoding="utf-8",
            )
            write_jsonl(root / "current.jsonl", [])
            write_jsonl(
                root / "future.jsonl",
                [
                    {
                        "candidate_id": "new_exact",
                        "task": "microtext",
                        "doc_id": "open_exact",
                        "category": "pin_label",
                        "reserved_split": "dev",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                    },
                    {
                        "candidate_id": "already_issued",
                        "task": "microtext",
                        "doc_id": "open_issued",
                        "category": "room_label",
                        "reserved_split": "dev",
                        "source_public_status": "public_domain_candidate",
                        "safe_to_merge_gold": False,
                    },
                ],
            )
            write_jsonl(root / "issued.jsonl", [{"candidate_id": "already_issued"}])

            summary, _, selected, _ = mod.build_plan(
                root=root,
                provenance_report=root / "provenance.json",
                current_assignment=root / "current.jsonl",
                future_capacity=root / "future.jsonl",
                date_label="fixture",
                max_per_source=50,
                preferred_issued=[root / "issued.jsonl"],
            )

            self.assertEqual([row["candidate_id"] for row in selected], ["already_issued"])
            self.assertEqual(summary["preferred_issued_requested"], 1)
            self.assertEqual(summary["preferred_issued_available"], 1)
            self.assertEqual(summary["preferred_issued_selected"], 1)
            self.assertEqual(summary["preferred_issued_missing"], 0)


if __name__ == "__main__":
    unittest.main()
