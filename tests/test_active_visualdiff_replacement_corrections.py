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


replacement = load(
    "visualdiff_replacement_finality",
    "tools/visualdiff_replacement_finality.py",
)
prepare = load(
    "prepare_active_visualdiff_replacement_corrections",
    "tools/prepare_active_visualdiff_replacement_corrections.py",
)
apply = load(
    "apply_active_visualdiff_finality_corrections_for_replacement",
    "tools/apply_active_visualdiff_finality_corrections.py",
)


def probe(text: str, bbox=None, distance=2.0):
    return {
        "status": "observed",
        "page_matches": 1,
        "nearby_matches": 1,
        "best": {
            "text": text,
            "bbox_px": bbox or [100, 100, 150, 120],
            "distance_px": distance,
            "font": "Times-Roman",
            "size": 5.0,
            "flags": 4,
        },
    }


class ReplacementPrimitiveTests(unittest.TestCase):
    def test_related_text_and_description(self):
        self.assertTrue(replacement.texts_are_related("PORT12", "PORT13"))
        self.assertFalse(replacement.texts_are_related("PORT12", "PUMP"))
        self.assertEqual(
            'The engineering text "SENSOR_A" changed to "SENSOR_B".',
            replacement.replacement_description("SENSOR_A", "SENSOR_B"),
        )

    def test_same_slot_allows_width_change_but_not_shift(self):
        old = probe("PORT12", [100, 100, 150, 120])["best"]
        new = probe("PORT13", [100, 100, 125, 120])["best"]
        self.assertTrue(replacement.same_slot_matches(old, new))
        new["bbox_px"] = [104, 100, 129, 120]
        self.assertFalse(replacement.same_slot_matches(old, new))


class PrepareReplacementTests(unittest.TestCase):
    def setUp(self):
        self.original = "Localized text may have been removed: 'PORT12'."
        self.row = {
            "pair_id": "vdiff__fixture__old__to__new__gap_1",
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {"original_human_description": self.original},
            "bbox_old": [90, 90, 160, 130],
            "bbox_new": [90, 90, 160, 130],
            "page_index_old": 0,
            "page_index_new": 0,
        }
        self.details = {"kind": "text_removed", "target": "PORT12"}
        self.old_probe = probe("PORT12", [100, 100, 150, 120])
        self.new_probe = probe("PORT13", [100, 100, 125, 120])
        self.alternatives = [{"text": "PORT13", "target_similarity": 0.5714}]

    def reasons(self, **overrides):
        values = {
            "old_text": "PORT12",
            "new_text": "PORT13",
            "audit_ids": set(),
            "evidence_holds": set(),
            "paper_ready_docs": {"old_doc", "new_doc"},
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
        }
        values.update(overrides)
        return prepare.eligibility_reasons(
            self.row, self.details, self.old_probe, self.new_probe,
            self.alternatives, **values,
        )

    def test_accepts_related_unique_same_slot_replacement(self):
        self.assertEqual([], self.reasons())

    def test_rejects_active_audit_flag(self):
        self.assertIn("active_audit_flag", self.reasons(audit_ids={self.row["pair_id"]}))

    def test_rejects_multiple_local_alternatives(self):
        self.alternatives.append({"text": "G2", "target_similarity": 0.5714})
        self.assertIn("replacement_alternative_not_unique_in_gap", self.reasons())

    def test_rejects_unrelated_or_shifted_text(self):
        self.assertIn("replacement_text_not_related", self.reasons(new_text="PUMP"))
        self.new_probe["best"]["bbox_px"] = [104, 100, 129, 120]
        self.assertIn("replacement_spans_not_same_slot", self.reasons())


class ApplyReplacementTests(unittest.TestCase):
    def setUp(self):
        self.pair_id = "vdiff__fixture__old__to__new__gap_1"
        self.original = "Localized text may have been removed: 'PORT12'."
        self.old_probe = probe("PORT12", [100, 100, 150, 120])
        self.new_probe = probe("PORT13", [100, 100, 125, 120])
        self.final = replacement.replacement_description("PORT12", "PORT13")
        self.pairs = [{
            "pair_id": self.pair_id,
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_confidence": "high",
            "bbox_old": [90, 90, 160, 130],
            "bbox_new": [90, 90, 160, 130],
            "review_evidence": {"original_human_description": self.original},
        }]
        self.questions = [{
            "pair_id": self.pair_id,
            "answer_text": self.original,
            "desc_source": "human",
        }]
        self.correction = {
            "policy_version": replacement.POLICY_VERSION,
            "safe_to_apply": True,
            "pair_id": self.pair_id,
            "kind": "text_replaced",
            "original_tentative_kind": "text_removed",
            "target": "PORT12",
            "old_text": "PORT12",
            "new_text": "PORT13",
            "original_description": self.original,
            "final_description": self.final,
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
            "bbox_old": [90, 90, 160, 130],
            "bbox_new": [90, 90, 160, 130],
            "old_probe": self.old_probe,
            "new_probe": self.new_probe,
            "localized_alternatives": [{"text": "PORT13"}],
        }

    def run_correction(self, correction=None):
        return apply.apply_corrections_to_rows(
            self.pairs, self.questions, [correction or self.correction],
            report_path="derived/quality/replacement/report.json",
            report_sha256="a" * 64,
            corrections_path="derived/quality/replacement/corrections.jsonl",
            corrections_sha256="b" * 64,
        )

    def test_updates_replacement_with_machine_owned_provenance(self):
        pairs, questions = self.run_correction()
        self.assertEqual(self.final, pairs[0]["change_desc_gt"])
        certification = pairs[0]["review_evidence"]["machine_finality_certification"]
        self.assertEqual("PORT12", certification["old_text"])
        self.assertEqual("PORT13", certification["new_text"])
        self.assertEqual(self.final, questions[0]["answer_text"])

    def test_rejects_tampered_replacement(self):
        correction = dict(self.correction, new_text="PUMP")
        correction["final_description"] = 'The engineering text "PORT12" changed to "PUMP".'
        with self.assertRaisesRegex(ValueError, "not sufficiently related"):
            self.run_correction(correction)


if __name__ == "__main__":
    unittest.main()
