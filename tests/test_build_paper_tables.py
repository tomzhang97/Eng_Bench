import json
import tempfile
import unittest
from pathlib import Path

from tools import build_paper_tables as paper


class BuildPaperTablesTests(unittest.TestCase):
    def test_report_separates_active_gold_from_staged_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results" / "baselines").mkdir(parents=True)
            dataset = root / "eng_bench.jsonl"
            rows = [
                {"id": "m1", "task": "microtext", "split": "train", "metadata": {"category": "dimension_value"}},
                {"id": "v1", "task": "visualdiff", "split": "test", "metadata": {"change_type": ["text"]}},
            ]
            dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            baseline_path = root / "results" / "baselines" / "demo_report.json"
            baseline_path.write_text(json.dumps({
                "task": "all", "split": "test", "rows_scored": 2,
                "microtext": {"total": 1, "normalized_exact_match": 0.5},
                "visualdiff": {"total": 1, "normalized_description_f1": 0.25, "evidence_recall_iou_0_5": 0.125},
            }), encoding="utf-8")
            gate_path = root / "gate.json"
            gate_path.write_text(json.dumps({
                "gates": {
                    "total_rows": {"current": 2, "target": "25000-50000", "passes": False},
                    "leaderboard_infrastructure": {"current": 7, "target": 7, "passes": True},
                },
                "baseline_registry": {"counted": [{
                    "name": "demo", "report_path": "results/baselines/demo_report.json",
                    "prediction_hash": "a" * 64,
                }]},
            }), encoding="utf-8")
            capacity_path = root / "capacity.json"
            capacity_path.write_text(json.dumps({
                "capacity_input_clean": True,
                "current_gold": {"rows": 2},
                "targets": {
                    "rows": {"current": 2, "all_staged_upper_bound": 30000, "target": 25000, "can_close_from_staged_capacity": True},
                    "unique_source_payloads": {"current": 1, "all_staged_upper_bound": 200, "target": 150, "can_close_from_staged_capacity": True},
                    "visualdiff_families": {"current": 1, "all_staged_upper_bound": 35, "target": 30, "can_close_from_staged_capacity": True},
                    "test_rows": {"current": 1, "explicit_all_staged_upper_bound": 6000, "target": 5000, "can_close_from_explicit_staged_test_capacity": True},
                },
                "human_work": {"all_unique_expansion_rows": 29998},
                "microtext_balance_capacity": {
                    "staged": {"canonical_non_pin_rows": 10},
                    "category_floor_shortfalls_after_all_canonical_staged_rows": {"dimension_value": 0},
                    "row_target_projection": {"additional_canonical_non_pin_rows_needed": 5},
                },
            }), encoding="utf-8")
            provenance_path = root / "provenance.json"
            provenance_path.write_text(json.dumps({
                "totals": {"active_source_docs": 1, "paper_ready_docs": 1},
                "documents": [{"domain": "pid", "paper_ready": True, "active_row_references": 2}],
            }), encoding="utf-8")
            comparison_path = root / "comparison.json"
            comparison_path.write_text(json.dumps({
                "schema_version": "1.0",
                "retrieved_on": "2026-08-17",
                "datasets": [
                    {
                        "dataset": name,
                        "release_context": "paper",
                        "modality": "text" if name != "DocVQA" else "document images",
                        "primary_task": "question answering",
                        "reported_scale": scale,
                        "source_units": "reported contexts",
                        "supervision": "answers and evidence",
                        "citation_title": f"{name} paper",
                        "citation_url": f"https://example.org/{name.lower()}",
                        "citation_year": 2020,
                        "source_type": "primary_paper",
                        "source_evidence": "Fixture evidence.",
                    }
                    for name, scale in (("DocVQA", "50,000"), ("HotpotQA", "113k"), ("MuSiQue", "25K"))
                ],
            }), encoding="utf-8")

            report = paper.build_report(
                root, dataset, gate_path, capacity_path, provenance_path, comparison_path,
                "2026-08-17-fixture"
            )
            self.assertEqual(report["active_gold"]["rows"], 2)
            self.assertEqual(report["active_gold"]["microtext_category_counts"], {"dimension_value": 1})
            self.assertEqual(report["active_gold"]["visualdiff_change_type_mentions"], {"text": 1})
            self.assertEqual(report["formal_gate_status"]["passed"], 1)
            self.assertEqual(report["baselines"][0]["visualdiff_description_f1"], 0.25)
            self.assertEqual(report["staged_capacity_projection"]["targets"]["rows"]["all_staged_upper_bound"], 30000)
            self.assertEqual(report["release_claim_status"], "NOT RELEASE CLAIM READY")
            self.assertEqual(report["external_benchmark_comparison"]["status"], "citation_backed_primary_sources")
            self.assertEqual(len(report["external_benchmark_comparison"]["rows"]), 4)
            self.assertIn("Staged Capacity Projection (Not Gold)", paper.markdown(report))
            self.assertIn("HotpotQA", paper.markdown(report))

    def test_comparison_requires_all_plan_datasets(self):
        with self.assertRaisesRegex(ValueError, "required comparison datasets missing"):
            paper.comparison_statistics(
                {"datasets": [{
                    "dataset": "DocVQA", "release_context": "paper", "modality": "images",
                    "primary_task": "qa", "reported_scale": "50K", "source_units": "images",
                    "supervision": "answers", "citation_title": "paper",
                    "citation_url": "https://example.org/paper", "citation_year": 2020,
                    "source_type": "primary_paper", "source_evidence": "evidence",
                }]},
                {"rows": 1}, {"totals": {}}, {"gates": {}}, "2026-fixture"
            )


if __name__ == "__main__":
    unittest.main()
