from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_loc_habs_item_rights as audit


def sample_payload(item_id: str) -> dict:
    return {
        "unrestricted": True,
        "item": {
            "id": item_id,
            "link": f"https://www.loc.gov/pictures/item/{item_id}/",
            "title": "Plan and Section",
            "call_number": "HAER MD-123 (sheet 1 of 1)",
            "service_medium": (
                "https://cdn.loc.gov/service/pnp/habshaer/md/md0600/md0624/sheet/00001r.jpg"
            ),
            "rights_information": (
                "No known restrictions on images made by the U.S. Government; "
                "images copied from other sources may be restricted."
            ),
            "collections": [{"code": "hh", "title": "HABS/HAER/HALS"}],
        },
        "resources": [
            {
                "larger": (
                    "https://cdn.loc.gov/master/pnp/habshaer/md/md0600/"
                    "md0624/sheet/00001a.tif"
                )
            }
        ],
    }


def queue_row(item_id: str, rank: int = 1) -> dict[str, str]:
    return {
        "queue_rank": str(rank),
        "candidate_id": f"fixture_{rank}",
        "domain": "civil_architectural",
        "proposed_doc_id": f"fixture_{rank}",
        "asset_title": "Plan and Section",
        "source_url": "https://www.loc.gov/pictures/search/?q=section&co=hh",
        "page_url": f"https://www.loc.gov/pictures/collection/hh/item/{item_id}/",
        "direct_asset_url": (
            "https://tile.loc.gov/storage-services/service/pnp/habshaer/"
            "md/md0600/md0624/sheet/00001r.jpg"
        ),
    }


def write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


class LocRightsCacheTest(unittest.TestCase):
    def test_network_fetch_writes_verified_cache_then_cache_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item_id = "md0624.sheet.00001a"
            queue = root / "queue.csv"
            write_queue(queue, [queue_row(item_id)])
            calls: list[str] = []

            report = audit.build_report(
                root,
                queue_csv=queue,
                cache_dir="cache",
                fetcher=lambda url: calls.append(url) or sample_payload(item_id),
            )
            self.assertEqual(report["totals"]["ready_for_intake"], 1)
            self.assertEqual(report["rows"][0]["metadata_source"], "network")
            self.assertEqual(len(calls), 1)

            cache_path = root / report["rows"][0]["metadata_cache_path"]
            envelope = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["cache_schema"], audit.CACHE_SCHEMA)
            self.assertEqual(
                envelope["payload_sha256"],
                audit.payload_sha256(envelope["payload"]),
            )

            report = audit.build_report(
                root,
                queue_csv=queue,
                cache_dir="cache",
                fetcher=lambda _url: self.fail("valid cache should avoid a network request"),
            )
            self.assertEqual(report["rows"][0]["metadata_source"], "cache")
            self.assertTrue(report["rows"][0]["ready_for_intake"])

    def test_refresh_failure_uses_only_a_verified_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item_id = "md0624.sheet.00001a"
            queue = root / "queue.csv"
            write_queue(queue, [queue_row(item_id)])
            audit.build_report(
                root,
                queue_csv=queue,
                cache_dir="cache",
                fetcher=lambda _url: sample_payload(item_id),
            )

            def fail(_url: str) -> dict:
                raise RuntimeError("HTTP Error 429: Too Many Requests")

            report = audit.build_report(
                root,
                queue_csv=queue,
                cache_dir="cache",
                refresh_cache=True,
                fetcher=fail,
            )
            row = report["rows"][0]
            self.assertEqual(row["metadata_source"], "cache_fallback")
            self.assertIn("429", row["metadata_warning"])
            self.assertEqual(row["error"], "")
            self.assertTrue(row["ready_for_intake"])

    def test_corrupt_cache_does_not_bypass_a_failed_network_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item_id = "md0624.sheet.00001a"
            queue = root / "queue.csv"
            write_queue(queue, [queue_row(item_id)])
            cache_path = audit.cache_file_for_item(root / "cache", item_id)
            audit.write_metadata_cache(cache_path, audit.metadata_url(item_id), sample_payload(item_id))
            envelope = json.loads(cache_path.read_text(encoding="utf-8"))
            envelope["payload"]["unrestricted"] = False
            cache_path.write_text(json.dumps(envelope), encoding="utf-8")

            report = audit.build_report(
                root,
                queue_csv=queue,
                cache_dir="cache",
                fetcher=lambda _url: (_ for _ in ()).throw(RuntimeError("429")),
            )
            row = report["rows"][0]
            self.assertFalse(row["ready_for_intake"])
            self.assertEqual(row["metadata_source"], "fetch_failed")
            self.assertIn("cache_invalid", row["error"])

    def test_request_delay_applies_between_unique_network_fetches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = root / "queue.csv"
            first = "md0624.sheet.00001a"
            second = "md0624.sheet.00002a"
            write_queue(queue, [queue_row(first, 1), queue_row(second, 2)])
            sleeps: list[float] = []

            def fetch(url: str) -> dict:
                item_id = url.split("/item/", 1)[1].split("/", 1)[0]
                payload = sample_payload(item_id)
                if item_id == second:
                    payload["item"]["service_medium"] = payload["item"][
                        "service_medium"
                    ].replace("00001r", "00002r")
                return payload

            audit.build_report(
                root,
                queue_csv=queue,
                fetcher=fetch,
                request_delay_seconds=2.5,
                sleeper=sleeps.append,
            )
            self.assertEqual(sleeps, [2.5])


if __name__ == "__main__":
    unittest.main()
