import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from import_versioned_git_drawings import import_spec, pinned_blob_url


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


class ImportVersionedGitDrawingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "bench"
        self.repo = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.repo.mkdir()
        (self.root / "manifest.jsonl").write_text("", encoding="utf-8")
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.name", "Fixture Author")
        run_git(self.repo, "config", "user.email", "fixture@example.test")

        (self.repo / "LICENSE.txt").write_text(
            "TAPR Open Hardware License Version 1.0\n",
            encoding="utf-8",
        )
        (self.repo / "drawing.sch").write_text("<eagle>old</eagle>\n", encoding="utf-8")
        self.old_commit = commit(self.repo, "initial drawing")
        (self.repo / "drawing.sch").write_text("<eagle>new</eagle>\n", encoding="utf-8")
        self.new_commit = commit(self.repo, "updated drawing")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_spec(self, new_commit: str | None = None) -> Path:
        spec = {
            "imported_date": "2026-08-09",
            "manifest_additions": "derived/source_imports/fixture/manifest_additions.jsonl",
            "sources": [
                {
                    "candidate_id": "pcb_fixture",
                    "slug": "fixture_board",
                    "repo_url": "https://example.test/fixture-board.git",
                    "repo_path": str(self.repo),
                    "domain": "pcb_schematic",
                    "public_status": "tapr_ohl_1_0_open_hardware_candidate",
                    "license_path": "LICENSE.txt",
                    "license_markers": ["TAPR Open Hardware License"],
                    "license_note": "Fixture redistribution evidence.",
                    "artifact_path": "drawing.sch",
                    "revisions": [
                        {"label": "v1", "doc_id": "fixture_board_v1", "commit": self.old_commit},
                        {
                            "label": "v2",
                            "doc_id": "fixture_board_v2",
                            "commit": new_commit or self.new_commit,
                        },
                    ],
                    "pairs": [
                        {
                            "pair_id": "vdiff__fixture_board__v1__to__v2",
                            "from": "v1",
                            "to": "v2",
                        }
                    ],
                }
            ],
        }
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def test_imports_exact_blobs_license_and_manifest_rows(self) -> None:
        report = import_spec(self.root, self.make_spec(), apply_manifest=True)

        self.assertTrue(report["valid"])
        self.assertEqual(report["doc_count"], 2)
        self.assertEqual(report["pair_count"], 1)
        self.assertEqual(report["manifest_result"]["added"], 3)
        self.assertEqual(report["active_gold_rows_modified"], 0)
        old_path = self.root / "visualdiff/docs/fixture_board/revisions/fixture_board_v1.sch"
        new_path = self.root / "visualdiff/docs/fixture_board/revisions/fixture_board_v2.sch"
        self.assertEqual(old_path.read_text(encoding="utf-8"), "<eagle>old</eagle>\n")
        self.assertEqual(new_path.read_text(encoding="utf-8"), "<eagle>new</eagle>\n")
        self.assertTrue((self.root / "visualdiff/docs/fixture_board/UPSTREAM_LICENSE.txt").exists())

        manifest = [
            json.loads(line)
            for line in (self.root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["type"] for row in manifest], ["doc", "doc", "pair"])
        self.assertEqual(manifest[2]["from_doc_id"], "fixture_board_v1")
        self.assertEqual(manifest[2]["to_doc_id"], "fixture_board_v2")
        self.assertEqual(len(manifest[0]["provenance"]["git_commit"]), 40)

        second = import_spec(self.root, self.make_spec(), apply_manifest=True)
        self.assertEqual(second["manifest_result"], {"added": 0, "already_present": 3})

    def test_rejects_pair_with_identical_drawing_content(self) -> None:
        (self.repo / "README.md").write_text("metadata-only change\n", encoding="utf-8")
        unchanged_commit = commit(self.repo, "metadata only")
        spec_path = self.make_spec(unchanged_commit)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["sources"][0]["revisions"][0]["commit"] = self.new_commit
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identical drawing content"):
            import_spec(self.root, spec_path, apply_manifest=False)

    def test_builds_valid_gitlab_blob_url_with_encoded_path(self) -> None:
        self.assertEqual(
            pinned_blob_url(
                "https://gitlab.com/example/hardware.git",
                "abc123",
                "doc/specs/revision A.pdf",
            ),
            "https://gitlab.com/example/hardware/-/blob/abc123/doc/specs/revision%20A.pdf",
        )

    def test_imports_named_revision_paths_and_hardware_license_scope(self) -> None:
        (self.repo / "LICENSE.txt").write_text(
            "Apache License\nVersion 2.0, January 2004\n",
            encoding="utf-8",
        )
        (self.repo / "rev-a.pdf").write_bytes(b"%PDF-1.4\nrevision a\n")
        (self.repo / "rev-b.pdf").write_bytes(b"%PDF-1.4\nrevision b\n")
        (self.repo / "README.md").write_text(
            "Hardware is released under Apache 2.0 License\n",
            encoding="utf-8",
        )
        shared_commit = commit(self.repo, "add named hardware revisions")
        spec = {
            "imported_date": "2026-08-09",
            "manifest_additions": "derived/source_imports/named/manifest.jsonl",
            "sources": [
                {
                    "candidate_id": "pcb_named",
                    "slug": "named_board",
                    "repo_url": "https://example.test/named-board.git",
                    "repo_path": str(self.repo),
                    "domain": "pcb_schematic",
                    "public_status": "apache_2_0_open_hardware_candidate",
                    "license_path": "LICENSE.txt",
                    "license_markers": ["Apache License", "Version 2.0"],
                    "license_scope_path": "README.md",
                    "license_scope_markers": [
                        "Hardware is released under Apache 2.0 License"
                    ],
                    "license_note": "Fixture scope evidence.",
                    "revisions": [
                        {
                            "label": "a",
                            "doc_id": "named_board_a",
                            "commit": shared_commit,
                            "artifact_path": "rev-a.pdf",
                        },
                        {
                            "label": "b",
                            "doc_id": "named_board_b",
                            "commit": shared_commit,
                            "artifact_path": "rev-b.pdf",
                        },
                    ],
                    "pairs": [
                        {
                            "pair_id": "vdiff__named_board__a__to__b",
                            "from": "a",
                            "to": "b",
                            "page_mapping": {
                                "type": "explicit",
                                "pairs": [
                                    {"page_A": 0, "page_B": 1, "label": "power"},
                                    {"page_A": 1, "page_B": 0, "label": "adc"},
                                ],
                                "unmatched_B": [2],
                            },
                        }
                    ],
                }
            ],
        }
        spec_path = self.root / "named-spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        report = import_spec(self.root, spec_path, apply_manifest=False)

        self.assertTrue(report["valid"])
        self.assertEqual(report["doc_count"], 2)
        self.assertEqual(report["pair_count"], 1)
        self.assertTrue(
            (self.root / "visualdiff/docs/named_board/UPSTREAM_LICENSE_SCOPE.md").is_file()
        )
        additions = [
            json.loads(line)
            for line in (
                self.root / "derived/source_imports/named/manifest.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(additions[0]["provenance"]["git_path"], "rev-a.pdf")
        self.assertEqual(additions[1]["provenance"]["git_path"], "rev-b.pdf")
        self.assertEqual(
            additions[2]["provenance"]["old_git_path"], "rev-a.pdf"
        )
        self.assertEqual(
            additions[2]["provenance"]["new_git_path"], "rev-b.pdf"
        )
        self.assertEqual(
            additions[2]["page_mapping"],
            {
                "type": "explicit",
                "pairs": [
                    {"page_A": 0, "page_B": 1, "label": "power"},
                    {"page_A": 1, "page_B": 0, "label": "adc"},
                ],
                "unmatched_B": [2],
            },
        )
        self.assertIn("license_scope_evidence", additions[0])

    def test_rejects_duplicate_pages_in_explicit_mapping(self) -> None:
        spec_path = self.make_spec()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["sources"][0]["pairs"][0]["page_mapping"] = {
            "type": "explicit",
            "pairs": [
                {"page_A": 0, "page_B": 0},
                {"page_A": 0, "page_B": 1},
            ],
        }
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "map each page at most once"):
            import_spec(self.root, spec_path, apply_manifest=False)

    def test_imports_single_pinned_microtext_snapshot_without_pair(self) -> None:
        spec = {
            "imported_date": "2026-08-12",
            "manifest_additions": "derived/source_imports/snapshot/manifest.jsonl",
            "sources": [
                {
                    "candidate_id": "pcb_snapshot",
                    "slug": "snapshot_board",
                    "task": "microtext",
                    "repo_url": "https://example.test/snapshot-board.git",
                    "repo_path": str(self.repo),
                    "domain": "pcb_schematic",
                    "public_status": "tapr_ohl_1_0_open_hardware_candidate",
                    "license_path": "LICENSE.txt",
                    "license_markers": ["TAPR Open Hardware License"],
                    "license_note": "Fixture redistribution evidence.",
                    "revisions": [
                        {
                            "label": "snapshot",
                            "doc_id": "snapshot_board",
                            "commit": self.new_commit,
                            "artifact_path": "drawing.sch",
                        }
                    ],
                    "pairs": [],
                }
            ],
        }
        spec_path = self.root / "snapshot-spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        report = import_spec(self.root, spec_path, apply_manifest=True)

        self.assertTrue(report["valid"])
        self.assertEqual(report["doc_count"], 1)
        self.assertEqual(report["pair_count"], 0)
        self.assertEqual(report["manifest_result"], {"added": 1, "already_present": 0})
        drawing = self.root / "microtext/docs/snapshot_board/revisions/snapshot_board.sch"
        self.assertEqual(drawing.read_text(encoding="utf-8"), "<eagle>new</eagle>\n")
        self.assertTrue(
            (self.root / "microtext/docs/snapshot_board/UPSTREAM_LICENSE.txt").is_file()
        )
        manifest = [
            json.loads(line)
            for line in (self.root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(manifest[0]["task"], "microtext")
        self.assertEqual(manifest[0]["doc_id"], "snapshot_board")

    def test_rejects_single_revision_visualdiff_source(self) -> None:
        spec_path = self.make_spec()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["sources"][0]["revisions"] = spec["sources"][0]["revisions"][:1]
        spec["sources"][0]["pairs"] = []
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "visualdiff sources require"):
            import_spec(self.root, spec_path, apply_manifest=False)


if __name__ == "__main__":
    unittest.main()
