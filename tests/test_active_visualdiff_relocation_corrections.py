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


relocation = load(
    "visualdiff_relocation_finality",
    "tools/visualdiff_relocation_finality.py",
)
prepare = load(
    "prepare_active_visualdiff_relocation_corrections",
    "tools/prepare_active_visualdiff_relocation_corrections.py",
)
apply = load(
    "apply_active_visualdiff_finality_corrections_for_relocation",
    "tools/apply_active_visualdiff_finality_corrections.py",
)


def probe(bbox, distance):
    return {
        "status": "observed",
        "page_matches": 1,
        "nearby_matches": 1,
        "best": {
            "bbox_px": bbox,
            "distance_px": distance,
            "font": "Times-Roman",
            "size": 5.0,
            "flags": 4,
        },
    }


class RelocationDescriptionTests(unittest.TestCase):
    def test_describes_axis_and_diagonal_movements(self):
        left = relocation.movement_from_probes(
            probe([100, 100, 140, 120], 2),
            probe([10, 100, 50, 120], 88),
        )
        diagonal = relocation.movement_from_probes(
            probe([100, 100, 140, 120], 2),
            probe([40, 40, 80, 60], 82),
        )
        self.assertEqual("left", left["direction"])
        self.assertEqual("up and left", diagonal["direction"])
        self.assertEqual(
            'The engineering text "FB1" moved left.',
            relocation.relocation_description("FB1", left),
        )


class PrepareRelocationTests(unittest.TestCase):
    def setUp(self):
        self.original = "Localized text may have been removed: 'FB1'."
        self.row = {
            "pair_id": "vdiff__fixture__old__to__new__gap_1",
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_confidence": "high",
            "source": {"type": "human_reviewed_source_expansion"},
            "review_evidence": {"original_human_description": self.original},
            "bbox_old": [100, 100, 140, 120],
            "bbox_new": [100, 100, 140, 120],
            "page_index_old": 0,
            "page_index_new": 0,
        }
        self.details = {"kind": "text_removed", "target": "FB1"}
        self.old_probe = probe([100, 100, 140, 120], 2)
        self.new_probe = probe([10, 100, 50, 120], 88)

    def reasons(self, **overrides):
        values = {
            "revision_target_count": 1,
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

    def test_accepts_unique_bound_same_font_relocation(self):
        reasons, movement = self.reasons()
        self.assertEqual([], reasons)
        self.assertEqual("left", movement["direction"])

    def test_rejects_repeated_target(self):
        reasons, _movement = self.reasons(revision_target_count=2)
        self.assertIn("repeated_target_in_revision_pair", reasons)

    def test_rejects_active_audit_flag(self):
        reasons, _movement = self.reasons(audit_ids={self.row["pair_id"]})
        self.assertIn("active_audit_flag", reasons)

    def test_rejects_unbound_matches(self):
        self.old_probe["best"]["distance_px"] = 30
        self.new_probe["best"]["distance_px"] = 90
        reasons, _movement = self.reasons()
        self.assertIn("neither_side_is_bound_to_gap_box", reasons)

    def test_rejects_font_change(self):
        self.new_probe["best"]["font"] = "Helvetica"
        reasons, _movement = self.reasons()
        self.assertIn("font_changed", reasons)


class ApplyRelocationTests(unittest.TestCase):
    def setUp(self):
        self.pair_id = "vdiff__fixture__old__to__new__gap_1"
        self.original = "Localized text may have been removed: 'FB1'."
        self.old_probe = probe([100, 100, 140, 120], 2)
        self.new_probe = probe([10, 100, 50, 120], 88)
        self.movement = relocation.movement_from_probes(
            self.old_probe, self.new_probe
        )
        self.final = relocation.relocation_description("FB1", self.movement)
        self.pairs = [{
            "pair_id": self.pair_id,
            "change_desc_gt": self.original,
            "desc_source": "human",
            "review_confidence": "high",
            "bbox_old": [100, 100, 140, 120],
            "bbox_new": [100, 100, 140, 120],
            "review_evidence": {"original_human_description": self.original},
        }]
        self.questions = [{
            "pair_id": self.pair_id,
            "answer_text": self.original,
            "desc_source": "human",
        }]
        self.correction = {
            "policy_version": relocation.POLICY_VERSION,
            "safe_to_apply": True,
            "pair_id": self.pair_id,
            "kind": "text_removed",
            "target": "FB1",
            "original_description": self.original,
            "final_description": self.final,
            "old_doc_id": "old_doc",
            "new_doc_id": "new_doc",
            "bbox_old": [100, 100, 140, 120],
            "bbox_new": [100, 100, 140, 120],
            "old_probe": self.old_probe,
            "new_probe": self.new_probe,
            "movement": self.movement,
            "revision_target_count": 1,
        }

    def run_correction(self, correction=None):
        return apply.apply_corrections_to_rows(
            self.pairs,
            self.questions,
            [correction or self.correction],
            report_path="derived/quality/relocation/report.json",
            report_sha256="a" * 64,
            corrections_path="derived/quality/relocation/corrections.jsonl",
            corrections_sha256="b" * 64,
        )

    def test_updates_relocation_with_machine_owned_provenance(self):
        pairs, questions = self.run_correction()
        self.assertEqual(self.final, pairs[0]["change_desc_gt"])
        self.assertEqual(
            apply.DESC_SOURCE_BY_POLICY[relocation.POLICY_VERSION],
            pairs[0]["desc_source"],
        )
        certification = pairs[0]["review_evidence"]["machine_finality_certification"]
        self.assertEqual(self.movement, certification["movement"])
        self.assertEqual(self.final, questions[0]["answer_text"])

    def test_rejects_tampered_movement(self):
        correction = dict(self.correction)
        correction["movement"] = dict(self.movement, direction="right")
        with self.assertRaisesRegex(ValueError, "relocation_movement_mismatch"):
            self.run_correction(correction)


if __name__ == "__main__":
    unittest.main()
