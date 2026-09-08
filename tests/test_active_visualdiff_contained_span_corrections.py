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


contained = load(
    "visualdiff_contained_span_finality",
    "tools/visualdiff_contained_span_finality.py",
)
prepare = load(
    "prepare_active_visualdiff_contained_span_corrections",
    "tools/prepare_active_visualdiff_contained_span_corrections.py",
)


def probe(*, bbox=(0, 0, 100, 40), distance=20, coverage=0.4, matches=1):
    return {
        "status": "observed",
        "page_matches": matches,
        "nearby_matches": matches,
        "best": {
            "bbox_px": list(bbox),
            "distance_px": distance,
            "original_crop_span_coverage": coverage,
            "font": "Helvetica",
            "size": 5.0,
            "flags": 0,
        } if matches else None,
    }


class ContainedSpanTests(unittest.TestCase):
    def test_accepts_substantial_crop_fully_inside_unique_span(self):
        self.assertTrue(contained.qualifying_contained_span(
            probe(distance=26, coverage=0.3), [20, 10, 80, 30]
        ))

    def test_rejects_partial_crop_intersection(self):
        self.assertFalse(contained.qualifying_contained_span(
            probe(bbox=(20, 0, 100, 40)), [0, 10, 80, 30]
        ))

    def test_rejects_tiny_crop_inside_wide_span(self):
        self.assertFalse(contained.qualifying_contained_span(
            probe(coverage=0.02), [45, 15, 55, 25]
        ))

    def test_rejects_repeated_page_match(self):
        self.assertFalse(contained.qualifying_contained_span(
            probe(matches=2), [20, 10, 80, 30]
        ))


class PrepareEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.original = "Localized text may have been added: 'PORT_AUX_P'."
        self.row = {
            "pair_id": "vdiff__fixture__old__to__new__gap_1",
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {"original_human_description": self.original},
            "bbox_old": [20, 10, 80, 30],
            "bbox_new": [20, 10, 80, 30],
            "page_index_old": 0,
            "page_index_new": 0,
        }
        self.details = {"kind": "text_added", "target": "PORT_AUX_P"}
        self.expected = probe(distance=26, coverage=0.3)
        self.opposite = probe(matches=0)

    def reasons(self, alternatives=None, **overrides):
        values = {
            "audit_ids": set(),
            "evidence_holds": set(),
            "paper_ready_docs": {"old_doc", "new_doc"},
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
        }
        values.update(overrides)
        return prepare.eligibility_reasons(
            self.row, self.details, self.expected, self.opposite,
            alternatives or {"localized": [], "related": []}, **values
        )

    def test_accepts_clean_contained_span_addition(self):
        self.assertEqual([], self.reasons())

    def test_rejects_related_opposite_text(self):
        reasons = self.reasons({
            "localized": [],
            "related": [{"text": "PORT_AUX_N", "distance_px": 40}],
        })
        self.assertIn("opposite_side_has_related_text", reasons)

    def test_rejects_exact_target_elsewhere_on_opposite_page(self):
        self.opposite = probe(bbox=(200, 200, 300, 240), distance=180, coverage=0.0)
        self.opposite["nearby_matches"] = 0
        reasons = self.reasons()
        self.assertIn("opposite_side_has_exact_page_match", reasons)

    def test_rejects_active_audit_flag(self):
        reasons = self.reasons(audit_ids={self.row["pair_id"]})
        self.assertIn("active_audit_flag", reasons)


if __name__ == "__main__":
    unittest.main()
