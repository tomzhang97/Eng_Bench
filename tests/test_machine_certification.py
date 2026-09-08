import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.audit_machine_certification_eligibility import (
    POLICY_VERSION,
    build_audit,
    category_shape_safe,
    evidence_record_sha256,
    normalized_revision_tokens,
    pin_label_matches_source_revision,
    source_text_fields_machine_safe,
    source_text_contract,
    stratified_sample,
)
from tools.build_machine_certification_delta_audit import (
    candidate_from_additions,
    eligible_delta,
)
from tools.build_machine_ocr_rescue_delta import newly_ocr_only_rows
from tools.finalize_machine_certification import build_report as finalize_report
from tools.microtext_merge import merge_review_rows
from tools.preview_reviewed_gold_promotion import machine_certification_reasons
from tools.validate_engbench_v2 import validate_all


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class MachineCertificationEligibilityTest(unittest.TestCase):

    def test_pin_label_equal_to_manifest_revision_is_human_required(self) -> None:
        manifest = {"version": {"sch_rev": "C3", "edition": "Rev. 4"}}
        self.assertEqual({"c3", "4"}, normalized_revision_tokens(manifest))
        self.assertTrue(pin_label_matches_source_revision("pin_label", "C3", manifest))
        self.assertFalse(pin_label_matches_source_revision("pin_label", "C37", manifest))
        self.assertFalse(pin_label_matches_source_revision("dimension_value", "C3", manifest))

    def test_eligible_delta_is_additive_and_rejects_removed_rows(self) -> None:
        baseline = [{"candidate_id": "a"}, {"candidate_id": "b"}]
        candidate = [{"candidate_id": "a"}, {"candidate_id": "b"}, {"candidate_id": "c"}]
        self.assertEqual(["c"], [row["candidate_id"] for row in eligible_delta(baseline, candidate)])

        with self.assertRaisesRegex(ValueError, "removed 1 baseline rows"):
            eligible_delta(baseline, [{"candidate_id": "a"}])

    def test_candidate_from_additions_preserves_baseline_and_addition_order(self) -> None:
        baseline = [{"candidate_id": "a"}, {"candidate_id": "b"}]
        additions = [[{"candidate_id": "c"}], [{"candidate_id": "d"}]]

        candidate = candidate_from_additions(baseline, additions)

        self.assertEqual(["a", "b", "c", "d"], [row["candidate_id"] for row in candidate])
        self.assertEqual(["a", "b"], [row["candidate_id"] for row in baseline])

    def test_newly_ocr_only_rows_excludes_preexisting_ocr_failures(self) -> None:
        baseline = [
            {"candidate_id": "new", "machine_certification_reasons": ["category_text_pattern_not_deterministic"]},
            {"candidate_id": "old", "machine_certification_reasons": ["independent_ocr_text_mismatch"]},
        ]
        candidate = [
            {"candidate_id": "new", "machine_certification_reasons": ["independent_ocr_text_mismatch"]},
            {"candidate_id": "old", "machine_certification_reasons": ["independent_ocr_text_mismatch"]},
        ]
        self.assertEqual(
            ["new"],
            [row["candidate_id"] for row in newly_ocr_only_rows(baseline, candidate)],
        )

    def build_root(self) -> tuple[Path, tempfile.TemporaryDirectory, dict, Path, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        payload = root / "sources/doc.pdf"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"source")
        source_sha = hashlib.sha256(b"source").hexdigest()
        image = root / "pages/doc.png"
        image.parent.mkdir(parents=True)
        page = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(page).text((10, 10), "P1", fill="black")
        page.save(image)
        manifest = {
            "type": "doc",
            "doc_id": "doc",
            "path": "sources/doc.pdf",
            "sha256": source_sha,
            "source_url": "https://example.test/doc.pdf",
            "public_status": "public_domain",
            "version": {"revision": "v1"},
        }
        write_jsonl(root / "manifest.jsonl", [manifest])
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("doc_id", "path", "source_url", "public_status"),
            )
            writer.writeheader()
            writer.writerow({
                "doc_id": "doc",
                "path": "sources/doc.pdf",
                "source_url": "https://example.test/doc.pdf",
                "public_status": "public_domain",
            })
        write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [])
        write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])
        row = {
            "candidate_id": "candidate_1",
            "doc_id": "doc",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [8, 8, 35, 30],
            "image_path": "pages/doc.png",
            "category": "pin_label",
            "raw_text": "P1",
            "target_text": "P1",
            "proposed_text": "P1",
            "source": "textlayer_regex_candidate",
            "review_status": "needs_review",
            "reserved_split": "train",
            "split_reservation_id": "reservation-1",
            "safe_to_merge_gold": False,
        }
        cohort = root / "cohort.jsonl"
        write_jsonl(cohort, [row])
        plan = root / "plan.json"
        plan.write_text(json.dumps({
            "valid": True,
            "reservations": [{
                "task": "microtext",
                "unit_id": "doc",
                "split": "train",
                "reservation_id": "reservation-1",
            }],
        }), encoding="utf-8")
        return root, temp, row, cohort, plan

    def test_exact_cached_ocr_train_row_is_auto_eligible(self) -> None:
        root, temp, row, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        evidence = {
            "candidate_id": "candidate_1",
            "source_payload_sha256": hashlib.sha256(b"source").hexdigest(),
            "page_image_sha256": hashlib.sha256((root / "pages/doc.png").read_bytes()).hexdigest(),
            "doc_id": "doc",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [8, 8, 35, 30],
            "category": "pin_label",
            "answer": "P1",
            "source": "textlayer_regex_candidate",
            "reserved_split": "train",
            "split_reservation_id": "reservation-1",
            "policy_version": POLICY_VERSION,
        }
        evidence_sha = evidence_record_sha256(evidence)
        cache = root / "ocr-cache.jsonl"
        write_jsonl(cache, [{
            "evidence_sha256": evidence_sha,
            "candidate_id": "candidate_1",
            "ocr_engine": "fixture",
            "ocr_text": "P1",
            "ocr_confidence": 0.999,
        }])

        auto, human, held, report = build_audit(
            root,
            [("fixture", cohort)],
            plan,
            date_label="fixture",
            ocr_mode="none",
            ocr_min_confidence=0.98,
            ocr_scale=3,
            ocr_cache_path=cache,
            max_ocr_rows=0,
        )

        self.assertEqual(1, len(auto))
        self.assertFalse(human)
        self.assertFalse(held)
        self.assertEqual(1, report["counts"]["auto_eligible_pending_calibration"])
        self.assertFalse(report["active_gold_modified"])
        self.assertEqual("fixture", auto[0]["machine_certification_ocr_evidence"]["ocr_engine"])
        self.assertEqual(
            64,
            len(auto[0]["machine_certification_ocr_evidence_sha256"]),
        )

    def test_locked_split_is_backfilled_only_from_matching_authoritative_plan(self) -> None:
        root, temp, row, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        row.pop("reserved_split")
        row.pop("split_reservation_id")
        row["split"] = "train"
        row["split_locked"] = True
        write_jsonl(cohort, [row])
        evidence = {
            "candidate_id": "candidate_1",
            "source_payload_sha256": hashlib.sha256(b"source").hexdigest(),
            "page_image_sha256": hashlib.sha256((root / "pages/doc.png").read_bytes()).hexdigest(),
            "doc_id": "doc",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [8, 8, 35, 30],
            "category": "pin_label",
            "answer": "P1",
            "source": "textlayer_regex_candidate",
            "reserved_split": "train",
            "split_reservation_id": "reservation-1",
            "policy_version": POLICY_VERSION,
        }
        cache = root / "ocr-cache.jsonl"
        write_jsonl(cache, [{
            "evidence_sha256": evidence_record_sha256(evidence),
            "candidate_id": "candidate_1",
            "ocr_engine": "fixture",
            "ocr_text": "P1",
            "ocr_confidence": 0.999,
        }])

        auto, human, held, _ = build_audit(
            root, [("fixture", cohort)], plan, date_label="fixture", ocr_mode="none",
            ocr_min_confidence=0.98, ocr_scale=3, ocr_cache_path=cache, max_ocr_rows=0,
        )

        self.assertEqual(1, len(auto))
        self.assertFalse(human)
        self.assertFalse(held)
        self.assertEqual("train", auto[0]["reserved_split"])
        self.assertEqual("reservation-1", auto[0]["split_reservation_id"])
        self.assertEqual(
            "authoritative_plan_backfill",
            auto[0]["machine_certification_split_resolution"],
        )

        row["split_locked"] = False
        write_jsonl(cohort, [row])
        auto, human, held, _ = build_audit(
            root, [("fixture", cohort)], plan, date_label="fixture", ocr_mode="none",
            ocr_min_confidence=0.98, ocr_scale=3, ocr_cache_path=cache, max_ocr_rows=0,
        )
        self.assertFalse(auto)
        self.assertEqual(
            ["evaluation_split_requires_human_review"],
            human[0]["machine_certification_reasons"],
        )
        self.assertFalse(held)

    def test_dev_row_stays_human_required_even_with_exact_text(self) -> None:
        root, temp, row, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        row["reserved_split"] = "dev"
        row["split_reservation_id"] = "reservation-dev"
        write_jsonl(cohort, [row])
        plan.write_text(json.dumps({
            "valid": True,
            "reservations": [{
                "task": "microtext", "unit_id": "doc", "split": "dev",
                "reservation_id": "reservation-dev",
            }],
        }), encoding="utf-8")

        auto, human, held, _ = build_audit(
            root, [("fixture", cohort)], plan, date_label="fixture", ocr_mode="none",
            ocr_min_confidence=0.98, ocr_scale=3, ocr_cache_path=None, max_ocr_rows=0,
        )

        self.assertFalse(auto)
        self.assertEqual("evaluation_split_requires_human_review", human[0]["machine_certification_reasons"][0])
        self.assertFalse(held)

    def test_engineering_tag_categories_stay_human_required(self) -> None:
        for category, text in (
            ("instrument_tag", "FIT-101"),
            ("equipment_tag", "P-101"),
            ("pipe_line_tag", "L-204"),
        ):
            with self.subTest(category=category):
                root, temp, row, cohort, plan = self.build_root()
                self.addCleanup(temp.cleanup)
                row.update(
                    {
                        "category": category,
                        "raw_text": text,
                        "target_text": text,
                        "proposed_text": text,
                    }
                )
                write_jsonl(cohort, [row])

                auto, human, held, _ = build_audit(
                    root,
                    [("fixture", cohort)],
                    plan,
                    date_label="fixture",
                    ocr_mode="none",
                    ocr_min_confidence=0.98,
                    ocr_scale=3,
                    ocr_cache_path=None,
                    max_ocr_rows=0,
                )

                self.assertFalse(auto)
                self.assertIn("semantic_category_requires_human_review", human[0]["machine_certification_reasons"])
                self.assertFalse(held)

    def test_dimension_shape_rejects_electronics_mega_value(self) -> None:
        for text in (
            "30°",
            "45 mm",
            "5MM",
            "100 ft",
            "10 foot",
            "6 feet",
            "7.75'",
            "6’",
            '12"',
            "8”",
            "6'-11\"",
            "7’-10”",
        ):
            with self.subTest(text=text):
                self.assertTrue(category_shape_safe("dimension_value", text))

        self.assertFalse(category_shape_safe("dimension_value", "2M"))
        self.assertFalse(category_shape_safe("dimension_value", "1M"))
        self.assertFalse(category_shape_safe("dimension_value", "100k"))
        self.assertFalse(category_shape_safe("dimension_value", "2'-10"))

    def test_power_rail_pin_labels_are_objective_but_malformed_signs_are_not(self) -> None:
        for text in ("+3V3", "+5V", "+24V", "+1V8", "+5V7"):
            with self.subTest(text=text):
                self.assertTrue(category_shape_safe("pin_label", text))

        for text in ("+5", "+3.3V", "+V5"):
            with self.subTest(text=text):
                self.assertFalse(category_shape_safe("pin_label", text))

    def test_source_subspan_requires_safe_boundaries(self) -> None:
        accepted = (
            ("pin_label", "PA15(JTDI)", "PA15"),
            ("pin_label", "HS2_DATA0 T2 RTC12", "T2"),
            ("dimension_value", "30°.", "30°"),
            ("dimension_value", "2 - #6 x 8'-0\",", "8'-0\""),
            ("process_value", "DIFF. PRESSURE: 2.5 bar", "2.5 bar"),
            ("tolerance_value", "B +.004/-.002", "+.004/-.002"),
        )
        for category, raw, value in accepted:
            with self.subTest(category=category, raw=raw, value=value):
                self.assertTrue(source_text_fields_machine_safe(category, raw, value, value, value))

        rejected = (
            ("dimension_value", "Mech_M2_2242_H2.5mm", "5mm"),
            ("dimension_value", "Q50MHz/25ppm/3V/4P/5x3.2mm", "2mm"),
            ("dimension_value", "approximately 120-200mm", "200mm"),
            ("pin_label", "USB.SchDoc", "USB"),
            ("pin_label", "USB Debug,", "USB"),
            ("pin_label", "GND(ADJ)", "GND"),
            ("pin_label", "D2+", "D2"),
            ("pin_label", "C2-", "C2"),
        )
        for category, raw, value in rejected:
            with self.subTest(category=category, raw=raw, value=value):
                self.assertFalse(source_text_fields_machine_safe(category, raw, value, value, value))

        self.assertFalse(
            source_text_fields_machine_safe(
                "process_value", "dp = 2,5 bar", "5 bar", "2,5 bar", "2,5 bar"
            )
        )
        self.assertEqual(
            "boundary_safe_exact_subspan",
            source_text_contract("pin_label", "PA15(JTDI)", "PA15", "PA15", "PA15"),
        )
        self.assertEqual(
            "exact_all_fields",
            source_text_contract("pin_label", "P1", "P1", "P1", "P1"),
        )
        self.assertEqual(
            "exact_raw_target_no_proposed",
            source_text_contract("pin_label", "SYS_SDA", "SYS_SDA", "", "SYS_SDA"),
        )
        self.assertTrue(category_shape_safe("pin_label", "3.3V"))
        self.assertEqual(
            "",
            source_text_contract("pin_label", "3.3V", "3.3V", "", "3.3V"),
        )

    def test_stratified_sample_is_deterministic(self) -> None:
        rows = [
            {"candidate_id": f"c{i}", "category": "pin_label" if i % 2 else "dimension_value"}
            for i in range(30)
        ]
        first = [row["candidate_id"] for row in stratified_sample(rows, 12, "seed")]
        second = [row["candidate_id"] for row in stratified_sample(rows, 12, "seed")]
        self.assertEqual(first, second)
        self.assertEqual(12, len(first))


class MachineCertificationFinalizationTest(unittest.TestCase):
    def build_inputs(self, decision: str = "correct") -> tuple[Path, tempfile.TemporaryDirectory, Path, Path, Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        eligible = root / "eligible.jsonl"
        rows = [{
            "candidate_id": f"candidate_{index:03d}",
            "reserved_split": "train",
            "machine_certification_policy_version": POLICY_VERSION,
            "machine_certification_tier": "auto_gold_train",
            "machine_certification_evidence_sha256": hashlib.sha256(str(index).encode()).hexdigest(),
        } for index in range(300)]
        write_jsonl(eligible, rows)
        sample_ids = [row["candidate_id"] for row in rows]
        eligibility_report = root / "eligibility-report.json"
        eligibility_report.write_text(json.dumps({
            "policy_version": POLICY_VERSION,
            "artifacts": {"auto_eligible": {"sha256": hashlib.sha256(eligible.read_bytes()).hexdigest()}},
            "calibration_sample": {
                "candidate_ids": sample_ids,
                "candidate_ids_sha256": hashlib.sha256("\n".join(sample_ids).encode()).hexdigest(),
            },
        }), encoding="utf-8")
        checklist = root / "checklist.csv"
        with checklist.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("candidate_id", "reviewer_decision"))
            writer.writeheader()
            for index, value in enumerate(sample_ids):
                writer.writerow({
                    "candidate_id": value,
                    "reviewer_decision": "incorrect" if index == 0 and decision == "incorrect" else decision,
                })
        report_sha = hashlib.sha256(eligibility_report.read_bytes()).hexdigest()
        return root, temp, eligible, eligibility_report, checklist, report_sha

    def test_zero_error_300_row_calibration_emits_certified_rows(self) -> None:
        root, temp, eligible, eligibility_report, checklist, report_sha = self.build_inputs()
        self.addCleanup(temp.cleanup)
        output = root / "certified.jsonl"

        report, certified = finalize_report(
            root, eligible, eligibility_report, checklist, output,
            expected_eligibility_report_sha256=report_sha,
            date_label="fixture", minimum_sample_rows=300,
            confidence=0.95, minimum_precision_bound=0.99,
        )

        self.assertTrue(report["machine_certification_release_ready"])
        self.assertEqual(300, len(certified))
        self.assertEqual("machine_verified", certified[0]["certification_method"])
        self.assertFalse(certified[0]["human_reviewed"])

    def test_one_calibration_error_fails_closed(self) -> None:
        root, temp, eligible, eligibility_report, checklist, report_sha = self.build_inputs("incorrect")
        self.addCleanup(temp.cleanup)
        output = root / "certified.jsonl"

        report, certified = finalize_report(
            root, eligible, eligibility_report, checklist, output,
            expected_eligibility_report_sha256=report_sha,
            date_label="fixture", minimum_sample_rows=300,
            confidence=0.95, minimum_precision_bound=0.99,
        )

        self.assertFalse(report["machine_certification_release_ready"])
        self.assertFalse(certified)
        self.assertEqual("", output.read_text(encoding="utf-8"))

    def test_incomplete_calibration_reports_zero_precision_bound(self) -> None:
        root, temp, eligible, eligibility_report, checklist, report_sha = self.build_inputs("")
        self.addCleanup(temp.cleanup)
        output = root / "certified.jsonl"

        report, certified = finalize_report(
            root, eligible, eligibility_report, checklist, output,
            expected_eligibility_report_sha256=report_sha,
            date_label="fixture", minimum_sample_rows=300,
            confidence=0.95, minimum_precision_bound=0.99,
        )

        self.assertFalse(report["machine_certification_release_ready"])
        self.assertEqual(0.0, report["statistics"]["one_sided_precision_lower_bound"])
        self.assertIn("calibration_incomplete_or_invalid_decisions", report["issues"])
        self.assertIn("calibration_precision_bound_below_threshold", report["issues"])
        self.assertFalse(certified)


class MachineCertificationReleaseContractTest(unittest.TestCase):
    def test_promotion_preview_verifies_machine_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            eligibility = root / "eligibility.json"
            eligibility.write_text(json.dumps({"policy_version": "1.0"}), encoding="utf-8")
            attestation = root / "attestation.json"
            attestation.write_text(json.dumps({
                "release_ready": True,
                "policy_version": "1.0",
                "incorrect": 0,
                "unclear": 0,
                "one_sided_precision_lower_bound": 0.99006,
            }), encoding="utf-8")
            eligibility_sha = hashlib.sha256(eligibility.read_bytes()).hexdigest()
            attestation_sha = hashlib.sha256(attestation.read_bytes()).hexdigest()
            row = {
                "certification_method": "machine_verified",
                "certification_tier": "auto_gold_train",
                "certification_policy_version": "1.0",
                "human_reviewed": False,
                "review_source": "machine_certification_policy",
                "machine_certification_evidence_sha256": "a" * 64,
                "certification_eligibility_report": "eligibility.json",
                "certification_eligibility_report_sha256": eligibility_sha,
                "certification_calibration_checklist_sha256": "b" * 64,
                "certification_calibration_attestation": "attestation.json",
                "certification_calibration_attestation_sha256": attestation_sha,
            }

            self.assertEqual([], machine_certification_reasons(root, row, "train", {}))
            self.assertIn(
                "machine_certification_is_train_only",
                machine_certification_reasons(root, row, "test", {}),
            )

    def test_microtext_merge_preserves_machine_certification_metadata(self) -> None:
        sha = "a" * 64
        reviewed = [{
            "candidate_id": "candidate_1",
            "doc_id": "doc",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [1, 2, 20, 30],
            "category": "pin_label",
            "proposed_text": "P1",
            "question_text": "Read the label.",
            "review_status": "accepted",
            "review_source": "machine_certification_policy",
            "human_reviewed": False,
            "certification_method": "machine_verified",
            "certification_tier": "auto_gold_train",
            "certification_policy_version": "1.0",
            "certification_eligibility_report_sha256": sha,
            "certification_calibration_attestation_sha256": sha,
            "machine_certification_evidence_sha256": sha,
        }]

        items, _, stats = merge_review_rows(
            [], [], reviewed, split="train", reviewed_at="2026-08-21T00:00:00Z",
            split_by_doc={"doc": "train"}, require_split_map=True,
        )

        self.assertEqual(1, stats["accepted"])
        self.assertEqual("machine_verified", items[0]["certification_method"])
        self.assertFalse(items[0]["human_reviewed"])
        self.assertEqual("machine_certification_policy", items[0]["review_source"])

    def test_v2_validator_rejects_machine_certification_outside_train(self) -> None:
        sha = "b" * 64
        row = {
            "id": "q1",
            "task": "microtext",
            "question": "Read the label.",
            "answer": "P1",
            "images": [],
            "evidence": [],
            "split": "test",
            "metadata": {
                "doc_id": "doc",
                "certification_method": "machine_verified",
                "certification_tier": "auto_gold_train",
                "certification_policy_version": "1.0",
                "human_reviewed": False,
                "review_source": "machine_certification_policy",
                "certification_eligibility_report_sha256": sha,
                "certification_calibration_attestation_sha256": sha,
                "machine_certification_evidence_sha256": sha,
            },
        }

        report, bad = validate_all([row], {"doc": {"type": "doc"}}, ".", strict=True, skip_textlayer=True)

        self.assertIn(0, bad)
        self.assertTrue(any("[Check 14]" in error and "limited to train" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
