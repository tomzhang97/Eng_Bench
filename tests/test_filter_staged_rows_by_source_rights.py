import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from filter_staged_rows_by_source_rights import partition_rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FilterStagedRowsBySourceRightsTest(unittest.TestCase):
    def test_partitions_microtext_and_visualdiff_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = []
            inventory = []
            for doc_id, status in (
                ("public_doc", "public_domain_candidate"),
                ("vendor_doc", "public_vendor_docs_candidate"),
                ("noncommercial_doc", "cc_by_nc_sa_reference_candidate"),
                ("old_rev", "cc_by_sa_4_0_candidate"),
                ("new_rev", "cc_by_nd_4_0_vendor_docs"),
            ):
                payload = doc_id.encode("ascii")
                relative = Path("sources") / f"{doc_id}.pdf"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                manifest = {
                    "type": "doc",
                    "doc_id": doc_id,
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "source_url": f"https://example.test/{doc_id}",
                    "public_status": status,
                }
                manifests.append(manifest)
                inventory.append(
                    {
                        "doc_id": doc_id,
                        "path": relative.as_posix(),
                        "source_url": manifest["source_url"],
                        "public_status": status,
                    }
                )
            manifests.append(
                {
                    "type": "pair",
                    "pair_id": "vdiff__widget__v1__to__v2",
                    "from_doc_id": "old_rev",
                    "to_doc_id": "new_rev",
                }
            )
            write_jsonl(root / "manifest.jsonl", manifests)
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["doc_id", "path", "source_url", "public_status"]
                )
                writer.writeheader()
                writer.writerows(inventory)
            input_path = root / "rows.jsonl"
            write_jsonl(
                input_path,
                [
                    {"candidate_id": "good", "doc_id": "public_doc"},
                    {"candidate_id": "held", "doc_id": "vendor_doc"},
                    {"candidate_id": "held_nc", "doc_id": "noncommercial_doc"},
                    {
                        "pair_id": "vdiff__widget__v1__to__v2__0001",
                        "project_id": "vdiff__widget__v1__to__v2",
                    },
                ],
            )

            passing, held, report = partition_rows(root, [input_path])

            self.assertEqual([row["candidate_id"] for row in passing], ["good"])
            self.assertEqual(len(held), 3)
            self.assertEqual(report["passing_rows"], 1)
            self.assertEqual(report["held_rows"], 3)
            self.assertTrue(
                any("license_evidence_missing" in reason for reason in held[0]["source_rights_hold_reasons"])
            )
            self.assertTrue(
                any("cc_by_nc" in reason for reason in held[1]["source_rights_hold_reasons"])
            )
            self.assertTrue(
                any("cc_by_nd" in reason for reason in held[2]["source_rights_hold_reasons"])
            )
            self.assertTrue(report["valid"])


if __name__ == "__main__":
    unittest.main()
