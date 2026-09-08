from __future__ import annotations

import unittest

from tools import register_manifest_source_candidate as register


class RegisterManifestSourceCandidateTests(unittest.TestCase):
    def test_upsert_unique_is_idempotent(self) -> None:
        rows, added = register.upsert_unique(
            [{"candidate_id": "a", "value": "old"}],
            "candidate_id",
            "a",
            {"candidate_id": "a", "value": "new"},
        )
        self.assertFalse(added)
        self.assertEqual(rows, [{"candidate_id": "a", "value": "new"}])
        rows, added = register.upsert_unique(
            rows,
            "candidate_id",
            "b",
            {"candidate_id": "b", "value": "next"},
        )
        self.assertTrue(added)
        self.assertEqual(len(rows), 2)

    def test_manifest_linkage_rejects_conflicts(self) -> None:
        rows = [
            {"type": "doc", "doc_id": "v1"},
            {"type": "doc", "doc_id": "v2", "source_candidate_id": "candidate"},
        ]
        linked, changed = register.link_manifest_docs(rows, ["v1", "v2"], "candidate")
        self.assertEqual(changed, 1)
        self.assertTrue(all(row.get("source_candidate_id") == "candidate" for row in linked))
        with self.assertRaises(ValueError):
            register.link_manifest_docs(linked, ["v1"], "different")

    def test_validate_spec_requires_release_posture_and_unique_docs(self) -> None:
        spec = {
            "candidate": {"candidate_id": "pcb_999"},
            "doc_ids": ["v1", "v2"],
            "priority_score": 9,
            "validation": {"release_posture": "release_candidate"},
        }
        candidate, docs, score, validation = register.validate_spec(spec)
        self.assertEqual(candidate["candidate_id"], "pcb_999")
        self.assertEqual(docs, ["v1", "v2"])
        self.assertEqual(score, 9)
        self.assertEqual(validation["release_posture"], "release_candidate")
        with self.assertRaises(ValueError):
            register.validate_spec({**spec, "doc_ids": ["v1", "v1"]})


if __name__ == "__main__":
    unittest.main()
