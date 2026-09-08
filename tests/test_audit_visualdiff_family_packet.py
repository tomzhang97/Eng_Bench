import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_visualdiff_family_packet import build_report


class VisualDiffFamilyPacketAuditTest(unittest.TestCase):
    def _fixture(
        self,
        identical_crops: bool = False,
        blocked_rights: bool = False,
        different_crop_sizes: bool = False,
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        packet = root / "packet"
        for directory in ("old", "new", "panels", "pages_old", "pages_new"):
            (packet / directory).mkdir(parents=True, exist_ok=True)
        (root / "visualdiff" / "annotations").mkdir(parents=True)
        (root / "visualdiff" / "docs").mkdir(parents=True)
        for doc_id in ("revision_old", "revision_new"):
            (root / "derived" / "pages_300dpi" / doc_id).mkdir(parents=True, exist_ok=True)

        old_payload = b"old source"
        new_payload = b"new source"
        (root / "visualdiff" / "docs" / "old.bin").write_bytes(old_payload)
        (root / "visualdiff" / "docs" / "new.bin").write_bytes(new_payload)
        old_sha = hashlib.sha256(old_payload).hexdigest()
        new_sha = hashlib.sha256(new_payload).hexdigest()
        docs = [
            {
                "type": "doc",
                "doc_id": "revision_old",
                "task": "visualdiff",
                "path": "visualdiff/docs/old.bin",
                "sha256": old_sha,
                "source_url": "https://example.test/old",
                "public_status": "public_domain",
                "derived": {"pages_dir": "derived/pages_300dpi/revision_old"},
            },
            {
                "type": "doc",
                "doc_id": "revision_new",
                "task": "visualdiff",
                "path": "visualdiff/docs/new.bin",
                "sha256": new_sha,
                "source_url": "https://example.test/new",
                "public_status": "public_domain",
                "derived": {"pages_dir": "derived/pages_300dpi/revision_new"},
            },
            {
                "type": "pair",
                "pair_id": "vdiff__fixture__old__to__new",
                "from_doc_id": "revision_old",
                "to_doc_id": "revision_new",
            },
        ]
        (root / "manifest.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in docs), encoding="utf-8"
        )
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["doc_id", "public_status", "source_url"])
            writer.writeheader()
            writer.writerow(
                {
                    "doc_id": "revision_old",
                    "public_status": "rights_uncertain" if blocked_rights else "public_domain",
                    "source_url": "https://example.test/old",
                }
            )
            writer.writerow(
                {
                    "doc_id": "revision_new",
                    "public_status": "public_domain",
                    "source_url": "https://example.test/new",
                }
            )

        old_page = root / "derived" / "pages_300dpi" / "revision_old" / "page_000.png"
        new_page = root / "derived" / "pages_300dpi" / "revision_new" / "page_000.png"
        Image.new("RGB", (20, 20), "white").save(old_page)
        Image.new("RGB", (20, 20), "black").save(new_page)
        Image.new("RGB", (10, 10), "white").save(packet / "old" / "pair.png")
        new_crop_size = (12, 10) if different_crop_sizes else (10, 10)
        Image.new("RGB", new_crop_size, "white" if identical_crops else "black").save(packet / "new" / "pair.png")
        Image.new("RGB", (20, 10), "gray").save(packet / "panels" / "pair.png")
        Image.new("RGB", (20, 20), "white").save(packet / "pages_old" / "old.png")
        Image.new("RGB", (20, 20), "black").save(packet / "pages_new" / "new.png")
        packet_row = {
            "pair_id": "vdiff__fixture__old__to__new__000",
            "project_id": "vdiff__fixture__old__to__new",
            "page_old": 0,
            "page_new": 0,
            "bbox_old": [0, 0, 10, 10],
            "bbox_new": [0, 0, 10, 10],
            "image_old": "derived/pages_300dpi/revision_old/page_000.png",
            "image_new": "derived/pages_300dpi/revision_new/page_000.png",
            "old_crop_path": "packet/old/pair.png",
            "new_crop_path": "packet/new/pair.png",
            "panel_path": "packet/panels/pair.png",
            "old_page_path": "packet/pages_old/old.png",
            "new_page_path": "packet/pages_new/new.png",
        }
        (packet / "manifest.jsonl").write_text(json.dumps(packet_row) + "\n", encoding="utf-8")
        (root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl").write_text("", encoding="utf-8")
        return temp, root, packet

    def test_valid_packet_passes_complete_audit(self) -> None:
        temp, root, packet = self._fixture()
        self.addCleanup(temp.cleanup)

        report = build_report(root, packet, "2026-07-31")

        self.assertTrue(report["passes"])
        self.assertEqual(report["totals"]["candidate_revision_families"], 1)
        self.assertEqual(report["totals"]["paper_ready_source_docs"], 2)
        self.assertGreater(report["totals"]["minimum_changed_pixel_ratio"], 0)

    def test_identical_crops_and_blocked_rights_block_packet(self) -> None:
        temp, root, packet = self._fixture(identical_crops=True, blocked_rights=True)
        self.addCleanup(temp.cleanup)

        report = build_report(root, packet, "2026-07-31")

        self.assertFalse(report["passes"])
        self.assertEqual(report["totals"]["blocked_rows"], 1)
        self.assertEqual(report["issue_counts"]["crop_pair_pixel_identical"], 1)
        self.assertIn("rights_blocked:rights_uncertain", report["document_issue_counts"])

    def test_different_crop_dimensions_are_a_non_blocking_warning(self) -> None:
        temp, root, packet = self._fixture(different_crop_sizes=True)
        self.addCleanup(temp.cleanup)

        report = build_report(root, packet, "2026-07-31")

        self.assertTrue(report["passes"])
        self.assertEqual(report["warning_counts"]["crop_dimensions_differ"], 1)

    def test_external_input_resolves_review_pack_evidence(self) -> None:
        temp, root, packet = self._fixture()
        self.addCleanup(temp.cleanup)
        review_pack = root / "derived" / "review_packs" / "fixture_pack"
        review_pack.parent.mkdir(parents=True, exist_ok=True)
        packet.rename(review_pack)
        rows = [json.loads((review_pack / "manifest.jsonl").read_text(encoding="utf-8"))]
        rows[0]["_review_pack"] = "fixture_pack"
        for field in ("old_crop_path", "new_crop_path", "panel_path", "old_page_path", "new_page_path"):
            parts = Path(str(rows[0][field])).parts
            rows[0][field] = Path(*parts[1:]).as_posix()
        input_path = root / "review_rows.jsonl"
        input_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

        report = build_report(root, root / "derived" / "review_packs", "2026-07-31", input_path)

        self.assertTrue(report["passes"])
        self.assertEqual(report["totals"]["packet_rows"], 1)


if __name__ == "__main__":
    unittest.main()
