from __future__ import annotations

import csv
from pathlib import Path

from tools import audit_loc_habs_item_rights


def sample_payload(item_id: str = "md0624.sheet.00001a") -> dict:
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
            "modified": "2024-01-01T00:00:00Z",
            "collections": [
                {
                    "code": "hh",
                    "title": (
                        "Historic American Buildings Survey/Historic American "
                        "Engineering Record/Historic American Landscapes Survey"
                    ),
                }
            ],
        },
        "resources": [
            {
                "larger": (
                    "https://cdn.loc.gov/master/pnp/habshaer/md/md0600/md0624/sheet/00001a.tif"
                )
            }
        ],
    }


def queue_row() -> dict[str, str]:
    return {
        "queue_rank": "1",
        "candidate_id": "arch_014",
        "domain": "civil_architectural",
        "proposed_doc_id": "arch_014_plan_section",
        "asset_title": "Plan and Section",
        "source_url": "https://www.loc.gov/pictures/search/?q=section&co=hh",
        "page_url": (
            "https://www.loc.gov/pictures/collection/hh/item/md0624.sheet.00001a/"
        ),
        "direct_asset_url": (
            "https://tile.loc.gov/storage-services/service/pnp/habshaer/"
            "md/md0600/md0624/sheet/00001r.jpg"
        ),
    }


def test_audit_accepts_matching_habs_item_metadata() -> None:
    audited = audit_loc_habs_item_rights.audit_row(queue_row(), sample_payload())

    assert audited["audit_status"] == "ready_for_intake"
    assert audited["ready_for_intake"] is True
    assert audited["loc_item_id"] == "md0624.sheet.00001a"
    assert audited["resolved_master_url"].endswith("00001a.tif")


def test_master_intake_uses_audited_tiff_and_item_provenance() -> None:
    audited = audit_loc_habs_item_rights.audit_row(queue_row(), sample_payload())
    report = {"rows": [audited]}

    rows = audit_loc_habs_item_rights.build_master_intake_rows(
        report,
        "microtext/docs/source_intake_2026_07_31_loc_master",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["download_url"].endswith("00001a.tif")
    assert row["local_path"].endswith(".tif")
    assert row["source_url"].endswith("/md0624.sheet.00001a/")
    assert row["rights_capture"] == "loc_item_json_rights_call_number_asset_sha256"


def test_audit_holds_rights_or_asset_mismatch() -> None:
    payload = sample_payload()
    payload["item"]["rights_information"] = "Rights status not evaluated."
    payload["item"]["service_medium"] = "https://cdn.loc.gov/service/pnp/other.jpg"

    audited = audit_loc_habs_item_rights.audit_row(queue_row(), payload)

    assert audited["audit_status"] == "hold"
    assert "rights_advisory_not_release_safe" in audited["error"]
    assert "queued_asset_mismatch" in audited["error"]


def test_report_fetches_each_loc_item_once(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.csv"
    rows = [queue_row(), {**queue_row(), "candidate_id": "arch_016"}]
    with queue_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    calls: list[str] = []

    def fetcher(url: str) -> dict:
        calls.append(url)
        return sample_payload()

    report = audit_loc_habs_item_rights.build_report(
        tmp_path,
        queue_csv=queue_path,
        fetcher=fetcher,
    )

    assert report["totals"] == {
        "queue_rows": 2,
        "unique_loc_items": 1,
        "ready_for_intake": 2,
        "held": 0,
    }
    assert len(calls) == 1
