import unittest

from tools.visualdiff_merge import merge_reviewed_rows, page_index


class VisualdiffMergeTest(unittest.TestCase):
    def test_page_index_falls_back_to_evidence_filename(self) -> None:
        self.assertEqual(
            10,
            page_index(
                {"image_old": "derived/pages_300dpi/doc/page_010.png"},
                "old",
            ),
        )

    def test_explicit_zero_page_index_wins_over_filename(self) -> None:
        self.assertEqual(
            0,
            page_index(
                {
                    "page_index_new": 0,
                    "image_new": "derived/pages_300dpi/doc/page_010.png",
                },
                "new",
            ),
        )

    def test_normalizes_reviewed_pair_and_builds_linked_question(self) -> None:
        project_id = "vdiff__family_a__v1__to__v2"
        manifest = [
            {
                "type": "doc",
                "doc_id": "family_a_v1",
                "same_model_id": "family_a",
                "version": {"revision": "v1"},
            },
            {
                "type": "doc",
                "doc_id": "family_a_v2",
                "same_model_id": "family_a",
                "version": {"revision": "v2"},
            },
            {
                "type": "pair",
                "pair_id": project_id,
                "from_doc_id": "family_a_v1",
                "to_doc_id": "family_a_v2",
            },
        ]
        reviewed = [
            {
                "pair_id": f"{project_id}__p0000__001",
                "project_id": project_id,
                "bbox_old": [1, 2, 30, 40],
                "bbox_new": [5, 6, 35, 45],
                "page_old": 0,
                "page_new": 0,
                "image_old": "derived/pages/family_a_v1/page_000.png",
                "image_new": "derived/pages/family_a_v2/page_000.png",
                "review_status": "needs_review",
                "human_review_status": "edit",
                "human_description": "The pin label changed from A to B.",
                "human_review_notes": "visible change",
                "source_candidate_id": "src_1",
            }
        ]

        pairs, questions, accepted, report = merge_reviewed_rows(
            [], [], reviewed, {project_id: "test"}, manifest
        )

        self.assertEqual(1, report["counts"]["accepted"])
        self.assertFalse(report["held_rows"])
        self.assertEqual(1, len(accepted))
        self.assertEqual("family_a", pairs[0]["doc_id"])
        self.assertEqual(["unknown"], pairs[0]["change_type"])
        self.assertEqual("v1", pairs[0]["version_id_old"])
        self.assertEqual("v2", pairs[0]["version_id_new"])
        self.assertEqual("test", pairs[0]["split"])
        self.assertEqual(pairs[0]["pair_id"], questions[0]["pair_id"])
        self.assertEqual(pairs[0]["change_desc_gt"], questions[0]["answer_text"])

    def test_missing_family_split_is_held(self) -> None:
        project_id = "vdiff__family_a__v1__to__v2"
        reviewed = [
            {
                "pair_id": f"{project_id}__001",
                "project_id": project_id,
                "bbox_old": [1, 2, 3, 4],
                "bbox_new": [1, 2, 3, 4],
                "image_old": "old.png",
                "image_new": "new.png",
                "human_review_status": "edit",
                "human_description": "Changed.",
            }
        ]

        _, _, accepted, report = merge_reviewed_rows([], [], reviewed, {}, [])

        self.assertFalse(accepted)
        self.assertEqual(1, report["counts"]["held"])
        self.assertIn("missing_family_split_assignment", report["held_rows"][0]["reasons"])

    def test_preserves_and_normalizes_source_change_type(self) -> None:
        project_id = "vdiff__family_a__v1__to__v2"
        manifest = [
            {
                "type": "doc",
                "doc_id": "family_a_v1",
                "same_model_id": "family_a",
                "version": {"revision": "v1"},
            },
            {
                "type": "doc",
                "doc_id": "family_a_v2",
                "same_model_id": "family_a",
                "version": {"revision": "v2"},
            },
            {
                "type": "pair",
                "pair_id": project_id,
                "from_doc_id": "family_a_v1",
                "to_doc_id": "family_a_v2",
            },
        ]
        reviewed = [
            {
                "pair_id": f"{project_id}__p0000__001",
                "project_id": project_id,
                "bbox_old": [1, 2, 30, 40],
                "bbox_new": [5, 6, 35, 45],
                "page_old": 0,
                "page_new": 0,
                "image_old": "old.png",
                "image_new": "new.png",
                "human_review_status": "edit",
                "human_description": "A label was added.",
                "change_type": "text_added_candidate",
            }
        ]

        pairs, _, accepted, report = merge_reviewed_rows(
            [], [], reviewed, {project_id: "test"}, manifest
        )

        self.assertEqual(1, report["counts"]["accepted"])
        self.assertEqual(1, len(accepted))
        self.assertEqual(["addition", "text"], pairs[0]["change_type"])


if __name__ == "__main__":
    unittest.main()
