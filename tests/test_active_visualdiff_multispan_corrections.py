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


multispan = load(
    "visualdiff_multispan_finality",
    "tools/visualdiff_multispan_finality.py",
)
prepare = load(
    "prepare_active_visualdiff_multispan_corrections",
    "tools/prepare_active_visualdiff_multispan_corrections.py",
)
apply = load(
    "apply_active_visualdiff_finality_corrections_for_multispan",
    "tools/apply_active_visualdiff_finality_corrections.py",
)


def span(text, bbox, *, font="Helvetica", size=2.5, flags=0, page=0):
    return {
        "text": text,
        "bbox_px": bbox,
        "font": font,
        "size": size,
        "flags": flags,
        "page": page,
    }


class ExactClusterTests(unittest.TestCase):
    def test_accepts_exact_compact_stacked_label_with_unique_anchor(self):
        spans = [
            span("NODE7", [36, 3, 59, 17]),
            span("Marker", [37, 17, 73, 31]),
            span("NODE8", [36, 63, 59, 77]),
            span("Marker", [37, 77, 73, 91]),
        ]
        result = multispan.probe_exact_cluster(
            spans, "NODE7 Marker", 0, [0, 0, 79, 33]
        )
        self.assertTrue(result["qualifies"])
        self.assertTrue(result["exact_reconstruction"])
        self.assertEqual(["NODE7"], result["unique_anchor_texts"])

    def test_rejects_extra_text_inside_reviewed_box(self):
        spans = [
            span("NODE7", [36, 3, 59, 17]),
            span("Marker", [37, 17, 73, 31]),
            span("X", [2, 2, 8, 8]),
        ]
        result = multispan.probe_exact_cluster(
            spans, "NODE7 Marker", 0, [0, 0, 79, 33]
        )
        self.assertFalse(result["qualifies"])
        self.assertFalse(result["exact_reconstruction"])

    def test_rejects_repeated_anchor(self):
        spans = [
            span("NODE7", [36, 3, 59, 17]),
            span("Marker", [37, 17, 73, 31]),
            span("NODE7", [136, 103, 159, 117]),
            span("Marker", [137, 117, 173, 131]),
        ]
        result = multispan.probe_exact_cluster(
            spans, "NODE7 Marker", 0, [0, 0, 79, 33]
        )
        self.assertFalse(result["qualifies"])
        self.assertEqual([], result["unique_anchor_texts"])

    def test_rejects_mixed_style_cluster(self):
        spans = [
            span("NODE7", [36, 3, 59, 17]),
            span("Marker", [37, 17, 73, 31], font="Times-Roman"),
        ]
        result = multispan.probe_exact_cluster(
            spans, "NODE7 Marker", 0, [0, 0, 79, 33]
        )
        self.assertFalse(result["qualifies"])
        self.assertFalse(result["uniform_style"])


class PrepareEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.original = "Localized text may have been added: 'NODE7 Marker'."
        self.row = {
            "pair_id": "vdiff__fixture__old__to__new__gap_1",
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {"original_human_description": self.original},
            "bbox_old": [0, 0, 79, 33],
            "bbox_new": [0, 0, 79, 33],
            "page_index_old": 0,
            "page_index_new": 0,
        }
        self.details = {"kind": "text_added", "target": "NODE7 Marker"}
        self.expected = multispan.probe_exact_cluster(
            [
                span("NODE7", [36, 3, 59, 17]),
                span("Marker", [37, 17, 73, 31]),
            ],
            "NODE7 Marker", 0, [0, 0, 79, 33],
        )
        self.opposite = multispan.probe_exact_cluster(
            [], "NODE7 Marker", 0, [0, 0, 79, 33]
        )

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
            self.row, self.details, self.expected, self.opposite, **values
        )

    def test_accepts_clean_exact_multispan_addition(self):
        self.assertEqual([], self.reasons())

    def test_rejects_active_audit_flag(self):
        reasons = self.reasons(audit_ids={self.row["pair_id"]})
        self.assertIn("active_audit_flag", reasons)

    def test_rejects_opposite_side_text(self):
        opposite = multispan.probe_exact_cluster(
            [span("OLD", [36, 3, 59, 17])],
            "NODE7 Marker", 0, [0, 0, 79, 33],
        )
        reasons = prepare.eligibility_reasons(
            self.row, self.details, self.expected, opposite,
            audit_ids=set(), evidence_holds=set(),
            paper_ready_docs={"old_doc", "new_doc"},
            old_doc_id="old_doc", new_doc_id="new_doc",
        )
        self.assertIn("opposite_box_contains_text", reasons)


class ApplyMultispanTests(unittest.TestCase):
    def setUp(self):
        self.pair_id = "vdiff__fixture__old__to__new__gap_1"
        self.original = "Localized text may have been added: 'NODE7 Marker'."
        self.final = 'The engineering text "NODE7 Marker" was added.'
        self.expected = multispan.probe_exact_cluster(
            [
                span("NODE7", [36, 3, 59, 17]),
                span("Marker", [37, 17, 73, 31]),
            ],
            "NODE7 Marker", 0, [0, 0, 79, 33],
        )
        self.opposite = multispan.probe_exact_cluster(
            [], "NODE7 Marker", 0, [0, 0, 79, 33]
        )
        self.pairs = [{
            "pair_id": self.pair_id,
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_confidence": "high",
            "bbox_old": [0, 0, 79, 33],
            "bbox_new": [0, 0, 79, 33],
            "review_evidence": {"original_human_description": self.original},
        }]
        self.questions = [{
            "pair_id": self.pair_id,
            "answer_text": self.original,
            "desc_source": "human",
        }]
        self.correction = {
            "policy_version": multispan.POLICY_VERSION,
            "safe_to_apply": True,
            "pair_id": self.pair_id,
            "kind": "text_added",
            "target": "NODE7 Marker",
            "expected_side": "new",
            "original_description": self.original,
            "final_description": self.final,
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
            "bbox_old": [0, 0, 79, 33],
            "bbox_new": [0, 0, 79, 33],
            "expected_cluster": self.expected,
            "opposite_cluster": self.opposite,
        }

    def run_correction(self, correction=None):
        return apply.apply_corrections_to_rows(
            self.pairs, self.questions, [correction or self.correction],
            report_path="derived/quality/multispan/report.json",
            report_sha256="a" * 64,
            corrections_path="derived/quality/multispan/corrections.jsonl",
            corrections_sha256="b" * 64,
        )

    def test_updates_multispan_with_machine_owned_provenance(self):
        pairs, questions = self.run_correction()
        self.assertEqual(self.final, pairs[0]["change_desc_gt"])
        self.assertEqual(
            apply.DESC_SOURCE_BY_POLICY[multispan.POLICY_VERSION],
            pairs[0]["desc_source"],
        )
        certification = pairs[0]["review_evidence"]["machine_finality_certification"]
        self.assertIn("NODE7", certification["expected_cluster"]["unique_anchor_texts"])
        self.assertEqual(self.final, questions[0]["answer_text"])

    def test_rejects_tampered_multispan_cluster(self):
        correction = dict(self.correction)
        correction["expected_cluster"] = dict(
            self.expected, reconstructed_normalized_text="NODE8MARKER"
        )
        correction["expected_cluster"]["spans"] = [
            dict(self.expected["spans"][0], text="NODE8"),
            self.expected["spans"][1],
        ]
        with self.assertRaisesRegex(ValueError, "multispan_cluster_reconstruction_mismatch"):
            self.run_correction(correction)


if __name__ == "__main__":
    unittest.main()
