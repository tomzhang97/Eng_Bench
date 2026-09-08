import unittest

from tools.expand_primary_intern_to_3000 import (
    SPECIALIST_ENGLISH_DRAFTS,
    TARGET_NEW_PRIMARY_ROWS,
    choose_primary_extension,
    deduplicate_prior_records,
    primary_engineering_reason,
)


def micro(identity: str, category: str = "component_value") -> dict:
    return {"candidate_id": identity, "doc_id": identity.split("-")[0], "category": category}


def visual(identity: str) -> dict:
    return {"pair_id": identity, "project_id": identity.split("-")[0], "change_type": "text"}


class ExpandPrimaryInternTo3000Tests(unittest.TestCase):
    def test_extension_excludes_specialist_visuals_and_fills_to_target(self) -> None:
        specialist = set(SPECIALIST_ENGLISH_DRAFTS)
        post = [visual(value) for value in list(specialist)[:2]]
        post.extend(visual(f"visual-{index}") for index in range(384))
        components = [micro(f"doc{index % 20}-component-{index}") for index in range(780)]
        exact = [micro(f"exact-{index}") for index in range(56)]
        substring = [micro(f"substring-{index}", "dimension_value") for index in range(3)]

        rows, component_count = choose_primary_extension(
            post, components, exact, substring, specialist
        )

        self.assertEqual(len(rows), TARGET_NEW_PRIMARY_ROWS)
        self.assertEqual(component_count, 557)
        self.assertFalse({row.get("pair_id") for row in rows} & specialist)

    def test_engineering_reason_marks_visual_and_exact_microtext(self) -> None:
        self.assertTrue(primary_engineering_reason(visual("visual-1"), set(), set()))
        self.assertTrue(
            primary_engineering_reason(
                micro("exact-1", "dimension_value"), {"exact-1"}, set()
            )
        )
        self.assertEqual(primary_engineering_reason(micro("plain-1"), set(), set()), "")

    def test_prior_evidence_dedup_keeps_first_record(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"same")
            second.write_bytes(b"same")
            kept, held, hashes = deduplicate_prior_records(
                [
                    {"record_id": "first", "task": "microtext", "evidence_path": str(first)},
                    {"record_id": "second", "task": "microtext", "evidence_path": str(second)},
                ]
            )

        self.assertEqual([row["record_id"] for row in kept], ["first"])
        self.assertEqual([row["record_id"] for row in held], ["second"])
        self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
