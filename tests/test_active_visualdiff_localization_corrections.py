import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


prepare = load(
    "prepare_active_visualdiff_localization_corrections",
    "tools/prepare_active_visualdiff_localization_corrections.py",
)
apply = load(
    "apply_active_visualdiff_finality_corrections_localization",
    "tools/apply_active_visualdiff_finality_corrections.py",
)
localization = load(
    "visualdiff_human_localization_test",
    "tools/visualdiff_human_localization.py",
)


class PrepareLocalizationCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.pair_id = "vdiff__fixture__old__to__new__0001"
        self.row = {
            "pair_id": self.pair_id,
            "change_desc_gt": localization.HUMAN_DELETION_ZH,
            "change_type": ["deletion"],
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {
                "original_human_description": localization.HUMAN_DELETION_ZH,
                "raster_semantic_confirmation": "confirmed_change",
            },
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
        }
        self.question = {
            "pair_id": self.pair_id,
            "answer_text": localization.HUMAN_DELETION_ZH,
        }

    def reasons(self, **overrides):
        values = {
            "audit_ids": set(),
            "evidence_holds": set(),
            "paper_ready_docs": {"old_doc", "new_doc"},
            "raster_confirmed_ids": {self.pair_id},
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
        }
        values.update(overrides)
        return prepare.eligibility_reasons(self.row, self.question, **values)

    def test_accepts_exact_human_localization(self):
        self.assertEqual([], self.reasons())

    def test_rejects_audit_or_machine_hold(self):
        self.assertIn("active_audit_flag", self.reasons(audit_ids={self.pair_id}))
        self.assertIn("machine_evidence_hold", self.reasons(evidence_holds={self.pair_id}))

    def test_rejects_unaligned_boxes(self):
        self.row["bbox_new"] = [11, 10, 40, 30]
        self.assertIn("unaligned_localization_boxes", self.reasons())

    def test_rejects_textlayer_only_semantics(self):
        self.assertIn(
            "raster_semantic_confirmation_missing",
            self.reasons(raster_confirmed_ids=set()),
        )

    def test_translation_is_exact_and_fail_closed(self):
        self.assertEqual(
            localization.HUMAN_DELETION_EN,
            localization.localized_description(localization.HUMAN_DELETION_ZH),
        )
        with self.assertRaisesRegex(ValueError, "unsupported_human_localization_text"):
            localization.localized_description("不同的句子。")


class ApplyLocalizationCorrectionTests(unittest.TestCase):
    def test_updates_only_localized_description_and_provenance(self):
        pair_id = "vdiff__fixture__old__to__new__0001"
        original = localization.HUMAN_DELETION_ZH
        pairs = [{
            "pair_id": pair_id,
            "change_desc_gt": original,
            "desc_source": "human",
            "review_confidence": "high",
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
            "review_evidence": {"original_human_description": original},
        }]
        questions = [{"pair_id": pair_id, "answer_text": original, "desc_source": "human"}]
        correction = {
            "policy_version": localization.POLICY_VERSION,
            "safe_to_apply": True,
            "pair_id": pair_id,
            "kind": "human_reviewed_deletion_localization",
            "target": "",
            "original_description": original,
            "final_description": localization.HUMAN_DELETION_EN,
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
        }
        new_pairs, new_questions = apply.apply_corrections_to_rows(
            pairs,
            questions,
            [correction],
            report_path="derived/quality/preview/report.json",
            report_sha256="a" * 64,
            corrections_path="derived/quality/preview/corrections.jsonl",
            corrections_sha256="b" * 64,
        )
        apply.validate_exact_mutations(pairs, new_pairs, questions, new_questions, {pair_id})
        self.assertEqual(localization.HUMAN_DELETION_EN, new_pairs[0]["change_desc_gt"])
        self.assertEqual(localization.DESC_SOURCE, new_pairs[0]["desc_source"])
        self.assertEqual(localization.HUMAN_DELETION_EN, new_questions[0]["answer_text"])

    def test_rejects_unapproved_translation(self):
        correction = {
            "policy_version": localization.POLICY_VERSION,
            "original_description": localization.HUMAN_DELETION_ZH,
            "final_description": "Different text.",
        }
        self.assertNotEqual(
            correction["final_description"],
            apply.deterministic_final_description(correction, None),
        )


if __name__ == "__main__":
    unittest.main()
