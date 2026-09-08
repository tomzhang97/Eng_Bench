from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_formal_agreement_packet_readiness import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FormalAgreementReadinessTest(unittest.TestCase):
    def test_tool_imports_with_build_report(self) -> None:
        self.assertTrue(callable(build_report))

    def test_reports_task_deficit_without_building_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "mt-1",
                        "task": "microtext",
                        "split": "test",
                        "metadata": {"doc_id": "doc-a", "category": "dimension_value"},
                    }
                ],
            )
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "source_url", "public_status"])
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc-a",
                        "source_url": "https://example.invalid/doc-a",
                        "public_status": "public_domain_candidate",
                    }
                )
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(root / "manifest.jsonl", [])
            provenance = root / "provenance.json"
            provenance.write_text(
                json.dumps({"documents": [{"doc_id": "doc-a", "release_ready": True}]}),
                encoding="utf-8",
            )
            report = build_report(
                root,
                Path("eng_bench.jsonl"),
                provenance,
                microtext_target=1,
                visualdiff_target=1,
                date_label="test",
            )
            self.assertFalse(report["packet_ready"])
            self.assertEqual(report["available_release_ready"]["microtext"], 1)
            self.assertEqual(report["deficits"], {"microtext": 0, "visualdiff": 1})
            self.assertEqual(report["issues"], ["insufficient_visualdiff_rows:0/1"])

    def test_excludes_current_audit_holds_and_tentative_visualdiff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "mt-ok",
                        "task": "microtext",
                        "split": "dev",
                        "answer": "12.0",
                        "metadata": {"doc_id": "doc-a", "category": "dimension_value"},
                    },
                    {
                        "id": "mt-hold",
                        "task": "microtext",
                        "split": "test",
                        "answer": "13.0",
                        "metadata": {"doc_id": "doc-a", "category": "dimension_value"},
                    },
                    {
                        "id": "vd-tentative",
                        "task": "visualdiff",
                        "split": "test",
                        "answer": "Localized text may have been removed: 'R1'.",
                        "metadata": {"doc_id": "doc-a", "change_type": ["text_removed"]},
                    },
                ],
            )
            with (root / "SOURCE_INVENTORY.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc-a",
                        "source_url": "https://example.invalid/doc-a",
                        "public_status": "public_domain",
                    }
                )
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(root / "manifest.jsonl", [])
            provenance = root / "provenance.json"
            provenance.write_text(
                json.dumps({"documents": [{"doc_id": "doc-a", "release_ready": True}]}),
                encoding="utf-8",
            )
            holds = root / "active_audit_rechecks.jsonl"
            write_jsonl(holds, [{"active_gold_identity": "mt-hold"}])
            report = build_report(
                root,
                Path("eng_bench.jsonl"),
                provenance,
                microtext_target=1,
                visualdiff_target=1,
                date_label="test-quality",
                audit_holds_path=holds,
                require_quality_ready=True,
            )
            self.assertFalse(report["packet_ready"])
            self.assertEqual(
                report["available_release_ready_before_quality_filter"],
                {"microtext": 2, "visualdiff": 1},
            )
            self.assertEqual(
                report["available_release_and_quality_ready"],
                {"microtext": 1},
            )
            self.assertEqual(
                report["quality_filter_exclusions"],
                {"active_audit_flag": 1, "tentative_visualdiff_description": 1},
            )


if __name__ == "__main__":
    unittest.main()
