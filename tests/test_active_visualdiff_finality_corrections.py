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
    "prepare_active_visualdiff_finality_corrections",
    "tools/prepare_active_visualdiff_finality_corrections.py",
)
apply = load(
    "apply_active_visualdiff_finality_corrections",
    "tools/apply_active_visualdiff_finality_corrections.py",
)


class PrepareFinalityCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "pair_id": "vdiff__fixture__old__to__new__0001",
            "change_desc_gt": "Localized text may have been added: 'P101'.",
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {
                "original_human_description": "Localized text may have been added: 'P101'."
            },
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
        }
        self.details = {"kind": "text_added", "target": "P101"}
        self.old_probe = {"status": "observed", "nearby_matches": 0, "best": None}
        self.new_probe = {
            "status": "observed",
            "nearby_matches": 1,
            "best": {"distance_px": 2.0},
        }

    def reasons(self, **overrides):
        values = {
            "audit_ids": set(),
            "evidence_holds": set(),
            "paper_ready_docs": {"old_doc", "new_doc"},
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
        }
        values.update(overrides)
        return prepare.eligibility_reasons(
            self.row,
            self.details,
            self.old_probe,
            self.new_probe,
            **values,
        )

    def test_accepts_one_sided_exact_human_reviewed_text(self):
        self.assertEqual([], self.reasons())

    def test_rejects_active_audit_flag(self):
        self.assertIn("active_audit_flag", self.reasons(audit_ids={self.row["pair_id"]}))

    def test_rejects_text_present_on_both_sides(self):
        old_probe = {"status": "observed", "nearby_matches": 1, "best": {"distance_px": 1}}
        reasons = prepare.eligibility_reasons(
            self.row,
            self.details,
            old_probe,
            self.new_probe,
            audit_ids=set(),
            evidence_holds=set(),
            paper_ready_docs={"old_doc", "new_doc"},
            old_doc_id="old_doc",
            new_doc_id="new_doc",
        )
        self.assertIn("opposite_side_has_nearby_exact_match", reasons)

    def test_rejects_distant_match(self):
        self.new_probe["best"]["distance_px"] = prepare.MAX_MATCH_DISTANCE_PX + 0.1
        self.assertIn("expected_match_too_far_from_box", self.reasons())

    def test_rejects_opposite_side_replacement_text(self):
        reasons = self.reasons(opposite_alternatives={
            "localized": [{"text": "P10", "distance_px": 2.0}],
            "related": [{"text": "P10", "distance_px": 2.0}],
        })
        self.assertIn("opposite_side_has_localized_alternative_text", reasons)
        self.assertIn("opposite_side_has_related_text", reasons)

    def test_detects_related_text_near_the_same_region(self):
        findings = prepare.nearby_alternative_text(
            [{"page": 0, "text": "SPI_SCK", "bbox_px": [50, 10, 100, 30]}],
            "SPI_SCK/QSPI_SCK",
            0,
            [95, 10, 120, 30],
        )
        self.assertEqual("SPI_SCK", findings["related"][0]["text"])

    def test_detects_fuzzy_label_replacement(self):
        findings = prepare.nearby_alternative_text(
            [{"page": 0, "text": "33-G6/RTC", "bbox_px": [55, 10, 115, 30]}],
            "33-G7/RTC",
            0,
            [50, 10, 110, 30],
        )
        self.assertEqual("33-G6/RTC", findings["related"][0]["text"])


class ApplyFinalityCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.pair_id = "vdiff__fixture__old__to__new__0001"
        self.original = "Localized text may have been added: 'P101'."
        self.final = 'The engineering text "P101" was added.'
        self.pairs = [{
            "pair_id": self.pair_id,
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_confidence": "high",
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
            "review_evidence": {"original_human_description": self.original},
        }]
        self.questions = [{
            "pair_id": self.pair_id,
            "answer_text": self.original,
            "desc_source": "human",
        }]
        self.correction = {
            "policy_version": apply.POLICY_VERSION,
            "safe_to_apply": True,
            "pair_id": self.pair_id,
            "kind": "text_added",
            "target": "P101",
            "expected_side": "new",
            "original_description": self.original,
            "final_description": self.final,
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
            "bbox_old": [10, 10, 40, 30],
            "bbox_new": [10, 10, 40, 30],
            "expected_probe": {"status": "observed", "nearby_matches": 1},
            "opposite_probe": {"status": "observed", "nearby_matches": 0},
        }

    def run_correction(self, correction=None):
        return apply.apply_corrections_to_rows(
            self.pairs,
            self.questions,
            [correction or self.correction],
            report_path="derived/quality/preview/report.json",
            report_sha256="a" * 64,
            corrections_path="derived/quality/preview/corrections.jsonl",
            corrections_sha256="b" * 64,
        )

    def test_updates_only_answer_and_auditable_provenance_fields(self):
        pairs, questions = self.run_correction()
        apply.validate_exact_mutations(
            self.pairs, pairs, self.questions, questions, {self.pair_id}
        )
        self.assertEqual(self.final, pairs[0]["change_desc_gt"])
        self.assertEqual(apply.DESC_SOURCE, pairs[0]["desc_source"])
        certification = pairs[0]["review_evidence"]["machine_finality_certification"]
        self.assertEqual(self.original, certification["original_human_description"])
        self.assertEqual(self.final, questions[0]["answer_text"])

    def test_rejects_nondeterministic_final_description(self):
        correction = dict(self.correction)
        correction["final_description"] = "Different answer."
        with self.assertRaisesRegex(ValueError, "final_description_not_deterministic"):
            self.run_correction(correction)

    def test_rejects_stale_active_description(self):
        self.pairs[0]["change_desc_gt"] = "Changed after preview."
        with self.assertRaisesRegex(ValueError, "active_description_mismatch"):
            self.run_correction()


if __name__ == "__main__":
    unittest.main()
