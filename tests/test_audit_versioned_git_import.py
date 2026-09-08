import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_versioned_git_import import build_report


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class AuditVersionedGitImportTest(unittest.TestCase):
    def test_accepts_complete_distinct_pair_and_rejects_identical_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            license_path = root / "license.txt"
            license_path.write_text("license evidence", encoding="utf-8")
            docs = []
            for index, payload in enumerate((b"old", b"new")):
                doc_id = f"doc_{index}"
                source = root / f"{doc_id}.sch"
                source.write_bytes(payload)
                pages = root / "derived" / "pages_300dpi" / doc_id
                pages.mkdir(parents=True)
                image = Image.new("L", (600, 400), 255)
                ImageDraw.Draw(image).rectangle((50, 50, 300, 200), outline=0, width=8)
                image.save(pages / "page_000.png")
                textlayer = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
                textlayer.parent.mkdir(parents=True, exist_ok=True)
                textlayer.write_text('{"text":"R1"}\n', encoding="utf-8")
                docs.append(
                    {
                        "type": "doc",
                        "doc_id": doc_id,
                        "task": "visualdiff",
                        "source_candidate_id": "candidate",
                        "same_model_id": "model",
                        "path": source.name,
                        "sha256": sha256(source),
                        "pages": 1,
                        "public_status": "cc_by_sa_4_0_open_hardware_candidate",
                        "license_evidence": {"path": license_path.name, "sha256": sha256(license_path)},
                    }
                )
            pair = {
                "type": "pair",
                "pair_id": "vdiff__model__v1__to__v2",
                "source_candidate_id": "candidate",
                "from_doc_id": "doc_0",
                "to_doc_id": "doc_1",
            }
            additions = root / "additions.jsonl"
            additions.write_text("".join(json.dumps(row) + "\n" for row in [*docs, pair]), encoding="utf-8")

            report = build_report(root, additions)
            self.assertTrue(report["valid"])
            self.assertEqual(report["totals"]["rendered_pages"], 2)
            self.assertEqual(report["totals"]["text_spans"], 2)

            docs[1]["sha256"] = docs[0]["sha256"]
            additions.write_text("".join(json.dumps(row) + "\n" for row in [*docs, pair]), encoding="utf-8")
            report = build_report(root, additions)
            self.assertFalse(report["valid"])
            self.assertIn("identical_source_payloads", {issue["type"] for issue in report["issues"]})

    def test_visualdiff_mesh_does_not_require_text_spans(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            license_path = root / "license.txt"
            license_path.write_text("license evidence", encoding="utf-8")
            docs = []
            for index, payload in enumerate((b"old mesh", b"new mesh")):
                doc_id = f"mesh_{index}"
                source = root / f"{doc_id}.stl"
                source.write_bytes(payload)
                pages = root / "derived" / "pages_300dpi" / doc_id
                pages.mkdir(parents=True)
                image = Image.new("L", (600, 400), 255)
                ImageDraw.Draw(image).rectangle((50, 50, 300, 200), fill=120)
                image.save(pages / "page_000.png")
                textlayer = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
                textlayer.parent.mkdir(parents=True, exist_ok=True)
                textlayer.write_text("", encoding="utf-8")
                docs.append(
                    {
                        "type": "doc",
                        "doc_id": doc_id,
                        "task": "visualdiff",
                        "doc_type": "stl_mechanical_model",
                        "source_candidate_id": "candidate",
                        "same_model_id": "mesh_model",
                        "path": source.name,
                        "sha256": sha256(source),
                        "pages": 1,
                        "public_status": "cern_ohl_1_2_open_hardware_candidate",
                        "license_evidence": {
                            "path": license_path.name,
                            "sha256": sha256(license_path),
                        },
                    }
                )
            pair = {
                "type": "pair",
                "pair_id": "vdiff__mesh__v1__to__v2",
                "source_candidate_id": "candidate",
                "from_doc_id": "mesh_0",
                "to_doc_id": "mesh_1",
            }
            additions = root / "additions.jsonl"
            additions.write_text(
                "".join(json.dumps(row) + "\n" for row in [*docs, pair]),
                encoding="utf-8",
            )

            report = build_report(root, additions)

            self.assertTrue(report["valid"])
            self.assertEqual(0, report["totals"]["text_spans"])
            self.assertTrue(all(not row["textlayer_required"] for row in report["docs"]))


if __name__ == "__main__":
    unittest.main()
