from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tools import build_agreement_audit_packet, verify_agreement_audit_packet


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class AgreementAuditPacketInstructionTest(unittest.TestCase):
    def test_quality_filter_excludes_active_audits_and_tentative_visualdiff(self) -> None:
        rows = [
            {"id": "mt-ok", "task": "microtext", "answer": "12.0"},
            {
                "id": "q_mt-hold",
                "task": "microtext",
                "answer": "13.0",
                "metadata": {"item_id": "mt-hold"},
            },
            {
                "id": "vd-tentative",
                "task": "visualdiff",
                "answer": "Localized text may have been added: 'R1'.",
            },
            {
                "id": "vd-ok",
                "task": "visualdiff",
                "answer": "The resistor label R1 was added.",
            },
        ]
        eligible, contexts, exclusions = (
            build_agreement_audit_packet.filter_quality_ready_rows(
                rows,
                active_audit_ids={"mt-hold"},
            )
        )
        self.assertEqual([row["id"] for row in eligible], ["mt-ok", "vd-ok"])
        self.assertTrue(contexts["mt-ok"]["quality_ready"])
        self.assertFalse(contexts["q_mt-hold"]["quality_ready"])
        self.assertEqual(
            exclusions,
            {"active_audit_flag": 1, "tentative_visualdiff_description": 1},
        )

    def test_verifier_rejects_false_quality_ready_claim(self) -> None:
        row = {
            "id": "q1",
            "doc_id": "ready",
            "source_doc_ids": "ready",
            "source_url": "https://example.com/ready",
            "source_status": "public",
            "quality_ready": "False",
            "quality_blockers": "active_audit_flag",
        }
        report = verify_agreement_audit_packet.base_report(
            [row],
            [row],
            [row],
            [],
            0,
            {
                "quality_hold_filter_required": True,
                "quality_hold_filter_enabled": True,
                "selected_quality_ready_rows": 0,
            },
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["non_quality_ready_rows"], 1)
        self.assertEqual(report["quality_blocker_rows"], 1)

    def test_verifier_rejects_false_release_ready_claim(self) -> None:
        row = {
            "id": "q1",
            "doc_id": "blocked",
            "source_doc_ids": "blocked",
            "source_url": "https://example.com/blocked",
            "source_status": "candidate",
            "source_release_ready": "False",
            "source_blockers": "blocked:license_evidence_missing",
        }
        report = verify_agreement_audit_packet.base_report(
            [row],
            [row],
            [row],
            [],
            0,
            {
                "release_ready_filter_required": True,
                "release_ready_filter_enabled": True,
                "selected_release_ready_rows": 0,
            },
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["non_release_ready_rows"], 1)
        self.assertEqual(report["source_blocker_rows"], 1)

    def test_exact_stratified_sample_and_release_ready_filter(self) -> None:
        rows = []
        for index in range(12):
            rows.append(
                {
                    "id": f"q{index}",
                    "task": "microtext" if index < 8 else "visualdiff",
                    "split": "dev" if index % 2 else "test",
                    "metadata": {
                        "doc_id": "ready" if index != 11 else "blocked",
                        "category": "pin_label",
                    },
                }
            )
        inventory = {
            "ready": {"doc_id": "ready"},
            "blocked": {"doc_id": "blocked"},
        }
        eligible, contexts, exclusions = (
            build_agreement_audit_packet.filter_release_ready_rows(
                rows,
                source_inventory=inventory,
                visualdiff_pairs={},
                visualdiff_manifest_docs=[],
                provenance_docs={
                    "ready": {"doc_id": "ready", "release_ready": True},
                    "blocked": {
                        "doc_id": "blocked",
                        "release_ready": False,
                        "blocker": "license_evidence_missing",
                    },
                },
            )
        )
        selected = build_agreement_audit_packet.select_stratified_exact(
            eligible,
            sample_size=8,
            min_per_stratum=1,
            seed="exact-test",
        )
        self.assertEqual(len(selected), 8)
        self.assertEqual(len({row["id"] for row in selected}), 8)
        self.assertTrue(contexts["q0"]["source_release_ready"])
        self.assertFalse(contexts["q11"]["source_release_ready"])
        self.assertEqual(exclusions["blocked:license_evidence_missing"], 1)
        quota_selected = (
            build_agreement_audit_packet.select_stratified_task_quotas(
                eligible,
                {"microtext": 5, "visualdiff": 2},
                min_per_stratum=1,
                seed="quota-test",
            )
        )
        self.assertEqual(
            {task: sum(row["task"] == task for row in quota_selected) for task in ("microtext", "visualdiff")},
            {"microtext": 5, "visualdiff": 2},
        )

    def test_agreement_packet_has_stable_ascii_named_chinese_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            image = tmp_path / "images" / "doc" / "page_0000.png"
            image.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(image)
            write_jsonl(
                tmp_path / "eng_bench.jsonl",
                [
                    {
                        "id": "q0",
                        "task": "microtext",
                        "question": "Read the marked text.",
                        "answer": "A1",
                        "images": ["images/doc/page_0000.png"],
                        "evidence": [{"image_index": 0, "bbox": [10, 10, 30, 30]}],
                        "split": "dev",
                        "metadata": {"doc_id": "doc", "category": "pin_label"},
                    }
                ],
            )
            with (tmp_path / "SOURCE_INVENTORY.csv").open(
                "w", newline="", encoding="utf-8"
            ) as f:
                writer = csv.DictWriter(
                    f, fieldnames=["doc_id", "source_url", "public_status"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc",
                        "source_url": "https://example.com/doc",
                        "public_status": "public_candidate",
                    }
                )

            build_agreement_audit_packet.build_packet(
                root=tmp_path,
                input_path=Path("eng_bench.jsonl"),
                output_dir=Path("derived/human_adjudication/agreement"),
                zip_output=Path("derived/human_adjudication/agreement.zip"),
                sample_fraction=1.0,
                min_per_stratum=1,
                seed="test-seed",
                pad_px=4,
            )

            packet = tmp_path / "derived" / "human_adjudication" / "agreement"
            zh_path = packet / "INTERN_INSTRUCTIONS_ZH.md"
            self.assertTrue(zh_path.exists())
            zh_text = zh_path.read_text(encoding="utf-8")
            self.assertIn("两位 reviewer 必须独立完成", zh_text)
            self.assertIn("复制成 A、B 两份", zh_text)
            self.assertIn("Reviewer A 返回原文件名", zh_text)
            self.assertIn("不要修改", zh_text)
            self.assertIn("sample_reference.csv", zh_text)
            self.assertIn("accept_reject", zh_text)
            self.assertIn("只能填写", zh_text)
            self.assertIn("不要排序", zh_text)
            self.assertIn("重复 ID", zh_text)
            self.assertIn(
                "INTERN_INSTRUCTIONS_ZH.md",
                (packet / "HUMAN_REVIEW_STEPS.md").read_text(encoding="utf-8"),
            )

            folder_report = verify_agreement_audit_packet.verify_dir(packet)
            zip_report = verify_agreement_audit_packet.verify_zip(
                tmp_path / "derived" / "human_adjudication" / "agreement.zip"
            )
            self.assertTrue(folder_report["valid"])
            self.assertTrue(zip_report["valid"])


if __name__ == "__main__":
    unittest.main()
