import unittest

from tools.validate_engbench_v2 import pair_manifest_candidates


class ValidateEngbenchV2ManifestCandidatesTest(unittest.TestCase):
    def test_page_and_row_suffix_resolves_to_revision_family(self) -> None:
        pair_id = "vdiff__family_a__v1__to__v2__p0000__003"

        self.assertEqual(
            [
                pair_id,
                "vdiff__family_a__v1__to__v2__p0000",
                "vdiff__family_a__v1__to__v2",
            ],
            pair_manifest_candidates(pair_id),
        )

    def test_single_numeric_suffix_behavior_is_preserved(self) -> None:
        self.assertEqual(
            ["vdiff__family_a__v1__to__v2__003", "vdiff__family_a__v1__to__v2"],
            pair_manifest_candidates("vdiff__family_a__v1__to__v2__003"),
        )

    def test_textlayer_suffix_resolves_to_revision_family(self) -> None:
        pair_id = "vdiff__family_a__v1__to__v2__p0000__txt003"

        self.assertEqual(
            [
                pair_id,
                "vdiff__family_a__v1__to__v2__p0000",
                "vdiff__family_a__v1__to__v2",
            ],
            pair_manifest_candidates(pair_id),
        )

    def test_gap_hash_suffix_resolves_to_revision_family(self) -> None:
        pair_id = "vdiff__family_a__v1__to__v2__gap_03bdd0c32a1fc818"

        self.assertEqual(
            [pair_id, "vdiff__family_a__v1__to__v2"],
            pair_manifest_candidates(pair_id),
        )

    def test_gap_hash_suffix_is_case_insensitive(self) -> None:
        pair_id = "vdiff__family_a__v1__to__v2__GAP_A1B2C3"

        self.assertEqual(
            [pair_id, "vdiff__family_a__v1__to__v2"],
            pair_manifest_candidates(pair_id),
        )


if __name__ == "__main__":
    unittest.main()
