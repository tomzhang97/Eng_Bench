#!/usr/bin/env python3
"""Audit exact LOC HABS/HAER/HALS item metadata before source intake."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


OUTPUT_FIELDS = [
    "queue_rank",
    "candidate_id",
    "domain",
    "proposed_doc_id",
    "loc_item_id",
    "item_url",
    "metadata_url",
    "metadata_source",
    "metadata_cache_path",
    "metadata_payload_sha256",
    "metadata_warning",
    "source_url",
    "queued_direct_asset_url",
    "resolved_service_url",
    "resolved_master_url",
    "title",
    "call_number",
    "collection_title",
    "rights_information",
    "unrestricted",
    "metadata_modified",
    "audit_status",
    "ready_for_intake",
    "error",
]
MASTER_INTAKE_FIELDS = [
    "intake_rank",
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "doc_id",
    "local_path",
    "download_url",
    "source_url",
    "page_url",
    "content_type",
    "content_length",
    "license_short_name",
    "license_url",
    "artist",
    "commons_sha1",
    "rights_capture",
    "review_gate",
    "intake_status",
    "notes",
]
ITEM_ID_PATTERN = re.compile(r"/item/([^/?#]+)/?", re.IGNORECASE)
EXPECTED_COLLECTION_CODE = "hh"
EXPECTED_RIGHTS_TEXT = "no known restrictions on images made by the u.s. government"
USER_AGENT = "EngBench/2.0 LOC source-rights audit"
CACHE_SCHEMA = "engbench_loc_item_metadata_v1"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_item_id(row: dict[str, str]) -> str:
    for field in ("page_url", "source_url"):
        match = ITEM_ID_PATTERN.search(str(row.get(field) or ""))
        if match:
            return match.group(1)
    return ""


def metadata_url(item_id: str) -> str:
    return f"https://www.loc.gov/pictures/item/{item_id}/?fo=json"


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def cache_file_for_item(cache_dir: Path, item_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("._")
    return cache_dir / f"{safe_id or 'unknown'}.json"


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def write_metadata_cache(path: Path, url: str, payload: dict[str, Any]) -> str:
    digest = payload_sha256(payload)
    envelope = {
        "cache_schema": CACHE_SCHEMA,
        "metadata_url": url,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload_sha256": digest,
        "payload": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return digest


def read_metadata_cache(path: Path, expected_url: str) -> tuple[dict[str, Any], str]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("cache envelope is not an object")
    if envelope.get("cache_schema") != CACHE_SCHEMA:
        raise ValueError("cache schema mismatch")
    if envelope.get("metadata_url") != expected_url:
        raise ValueError("cache metadata URL mismatch")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("cache payload is not an object")
    expected_sha = str(envelope.get("payload_sha256") or "")
    actual_sha = payload_sha256(payload)
    if not expected_sha or expected_sha != actual_sha:
        raise ValueError("cache payload SHA-256 mismatch")
    return payload, actual_sha


def canonical_asset_path(value: str | None) -> str:
    path = urlparse(str(value or "")).path.lower()
    path = path.replace("/storage-services/", "/")
    return path.rstrip("/")


def fetch_json(url: str, *, timeout: float = 30.0, retries: int = 2) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(str(last_error or "unknown LOC metadata error"))


def first_master_url(payload: dict[str, Any]) -> str:
    for resource in payload.get("resources") or []:
        if isinstance(resource, dict) and resource.get("larger"):
            return str(resource["larger"])
    return ""


def audit_row(
    row: dict[str, str],
    payload: dict[str, Any] | None,
    error: str = "",
    *,
    metadata_source: str = "",
    metadata_cache_path: str = "",
    metadata_payload_sha256: str = "",
    metadata_warning: str = "",
) -> dict[str, Any]:
    expected_id = extract_item_id(row)
    item = payload.get("item") if isinstance(payload, dict) else {}
    item = item if isinstance(item, dict) else {}
    actual_id = str(item.get("id") or "")
    collections = item.get("collections") if isinstance(item.get("collections"), list) else []
    collection_codes = {
        str(collection.get("code") or "").lower()
        for collection in collections
        if isinstance(collection, dict)
    }
    collection_titles = [
        str(collection.get("title") or "")
        for collection in collections
        if isinstance(collection, dict) and collection.get("title")
    ]
    rights_information = str(item.get("rights_information") or "")
    unrestricted = bool(payload.get("unrestricted")) if isinstance(payload, dict) else False
    resolved_service_url = str(item.get("service_medium") or "")
    queued_direct_url = str(row.get("direct_asset_url") or "")

    failures: list[str] = []
    if error:
        failures.append(f"metadata_fetch_failed:{error}")
    if not expected_id:
        failures.append("missing_item_id")
    if expected_id and actual_id != expected_id:
        failures.append("item_id_mismatch")
    if EXPECTED_COLLECTION_CODE not in collection_codes:
        failures.append("not_habs_haer_hals")
    if EXPECTED_RIGHTS_TEXT not in rights_information.lower():
        failures.append("rights_advisory_not_release_safe")
    if not unrestricted:
        failures.append("item_not_unrestricted")
    if not resolved_service_url:
        failures.append("missing_service_asset")
    if queued_direct_url and resolved_service_url:
        if canonical_asset_path(queued_direct_url) != canonical_asset_path(resolved_service_url):
            failures.append("queued_asset_mismatch")

    status = "ready_for_intake" if not failures else "hold"
    item_url = str(item.get("link") or "")
    if not item_url and expected_id:
        item_url = f"https://www.loc.gov/pictures/item/{expected_id}/"
    return {
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "proposed_doc_id": row.get("proposed_doc_id", ""),
        "loc_item_id": actual_id or expected_id,
        "item_url": item_url,
        "metadata_url": metadata_url(expected_id) if expected_id else "",
        "metadata_source": metadata_source,
        "metadata_cache_path": metadata_cache_path,
        "metadata_payload_sha256": metadata_payload_sha256,
        "metadata_warning": metadata_warning,
        "source_url": row.get("source_url", ""),
        "queued_direct_asset_url": queued_direct_url,
        "resolved_service_url": resolved_service_url,
        "resolved_master_url": first_master_url(payload or {}),
        "title": str(item.get("title") or row.get("asset_title") or ""),
        "call_number": str(item.get("call_number") or ""),
        "collection_title": " | ".join(collection_titles),
        "rights_information": rights_information,
        "unrestricted": unrestricted,
        "metadata_modified": str(item.get("modified") or ""),
        "audit_status": status,
        "ready_for_intake": not failures,
        "error": " | ".join(failures),
    }


def build_master_intake_rows(report: dict[str, Any], import_dir: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for audited in report["rows"]:
        if not audited["ready_for_intake"] or not audited["resolved_master_url"]:
            continue
        doc_id = str(audited["proposed_doc_id"])
        rows.append(
            {
                "intake_rank": len(rows) + 1,
                "queue_rank": audited["queue_rank"],
                "candidate_id": audited["candidate_id"],
                "domain": audited["domain"],
                "asset_kind": "image",
                "import_action": "download_hash_render_image",
                "doc_id": doc_id,
                "local_path": f"{import_dir.rstrip('/')}/{doc_id}.tif",
                "download_url": audited["resolved_master_url"],
                "source_url": audited["item_url"],
                "page_url": audited["item_url"],
                "content_type": "image/tiff",
                "content_length": "",
                "license_short_name": "US federal work; no known restrictions",
                "license_url": "https://www.loc.gov/rr/print/res/114_habs.html",
                "artist": "",
                "commons_sha1": "",
                "rights_capture": "loc_item_json_rights_call_number_asset_sha256",
                "review_gate": "review_packet_required_before_gold",
                "intake_status": "ready_download_hash_then_render",
                "notes": (
                    f"LOC item {audited['loc_item_id']}; call number {audited['call_number']}. "
                    "Use the master TIFF for benchmark-quality rendering. Human review remains "
                    "required before gold."
                ),
            }
        )
    return rows


def build_report(
    root: str | Path,
    *,
    queue_csv: str | Path,
    timeout: float = 30.0,
    retries: int = 2,
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    cache_dir: str | Path | None = None,
    refresh_cache: bool = False,
    request_delay_seconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    root = Path(root)
    queue_path = Path(queue_csv)
    if not queue_path.is_absolute():
        queue_path = root / queue_path
    rows = read_csv(queue_path)
    audited: list[dict[str, Any]] = []
    memory_cache: dict[str, dict[str, Any]] = {}
    resolved_cache_dir: Path | None = None
    if cache_dir:
        resolved_cache_dir = Path(cache_dir)
        if not resolved_cache_dir.is_absolute():
            resolved_cache_dir = root / resolved_cache_dir
    network_fetches = 0
    for row in rows:
        item_id = extract_item_id(row)
        payload: dict[str, Any] | None = None
        error = ""
        source = ""
        cache_path = ""
        digest = ""
        warning = ""
        if not item_id:
            error = "missing LOC item URL"
        else:
            url = metadata_url(item_id)
            if item_id not in memory_cache:
                persistent_path = (
                    cache_file_for_item(resolved_cache_dir, item_id)
                    if resolved_cache_dir
                    else None
                )
                cached_payload: dict[str, Any] | None = None
                cached_digest = ""
                cache_read_error = ""
                if persistent_path and persistent_path.is_file():
                    cache_path = display_path(persistent_path, root)
                    try:
                        cached_payload, cached_digest = read_metadata_cache(persistent_path, url)
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        cache_read_error = str(exc)

                if cached_payload is not None and not refresh_cache:
                    memory_cache[item_id] = {
                        "payload": cached_payload,
                        "error": "",
                        "source": "cache",
                        "cache_path": cache_path,
                        "digest": cached_digest,
                        "warning": "",
                    }
                else:
                    try:
                        if network_fetches and request_delay_seconds > 0:
                            sleeper(request_delay_seconds)
                        network_fetches += 1
                        fetched = (
                            fetcher(url)
                            if fetcher
                            else fetch_json(url, timeout=timeout, retries=retries)
                        )
                        if not isinstance(fetched, dict):
                            raise ValueError("LOC metadata response is not an object")
                        fetched_digest = payload_sha256(fetched)
                        if persistent_path:
                            fetched_digest = write_metadata_cache(persistent_path, url, fetched)
                            cache_path = display_path(persistent_path, root)
                        memory_cache[item_id] = {
                            "payload": fetched,
                            "error": "",
                            "source": "network",
                            "cache_path": cache_path,
                            "digest": fetched_digest,
                            "warning": (
                                f"replaced_invalid_cache:{cache_read_error}"
                                if cache_read_error
                                else ""
                            ),
                        }
                    except (
                        RuntimeError,
                        HTTPError,
                        URLError,
                        TimeoutError,
                        OSError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        if cached_payload is not None:
                            memory_cache[item_id] = {
                                "payload": cached_payload,
                                "error": "",
                                "source": "cache_fallback",
                                "cache_path": cache_path,
                                "digest": cached_digest,
                                "warning": f"network_refresh_failed:{exc}",
                            }
                        else:
                            parts = [str(exc)]
                            if cache_read_error:
                                parts.append(f"cache_invalid:{cache_read_error}")
                            memory_cache[item_id] = {
                                "payload": None,
                                "error": " | ".join(parts),
                                "source": "fetch_failed",
                                "cache_path": cache_path,
                                "digest": "",
                                "warning": "",
                            }
            cached = memory_cache[item_id]
            payload = cached["payload"]
            error = str(cached["error"])
            source = str(cached["source"])
            cache_path = str(cached["cache_path"])
            digest = str(cached["digest"])
            warning = str(cached["warning"])
        audited.append(
            audit_row(
                row,
                payload,
                error,
                metadata_source=source,
                metadata_cache_path=cache_path,
                metadata_payload_sha256=digest,
                metadata_warning=warning,
            )
        )
    status_counts = Counter(str(row["audit_status"]) for row in audited)
    return {
        "queue_csv": queue_path.as_posix(),
        "generated_on": date.today().isoformat(),
        "totals": {
            "queue_rows": len(rows),
            "unique_loc_items": len({row["loc_item_id"] for row in audited if row["loc_item_id"]}),
            "ready_for_intake": status_counts.get("ready_for_intake", 0),
            "held": status_counts.get("hold", 0),
        },
        "rows": audited,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# LOC HABS/HAER/HALS Item Rights Audit",
        "",
        f"- Queue rows: `{totals['queue_rows']}`",
        f"- Unique LOC items: `{totals['unique_loc_items']}`",
        f"- Ready for intake: `{totals['ready_for_intake']}`",
        f"- Held: `{totals['held']}`",
        "- Metadata comes from a SHA-256-verified cache or an exact LOC item JSON response.",
        "- This is source-intake evidence only. Human review remains required before gold promotion.",
        "",
        "| Rank | Item | Candidate | Status | Call number | Rights advisory |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        rights = str(row["rights_information"]).replace("|", "\\|")
        lines.append(
            f"| {row['queue_rank']} | `{row['loc_item_id']}` | `{row['candidate_id']}` | "
            f"{row['audit_status']} | {row['call_number']} | {rights} |"
        )
        if row["error"]:
            lines.append(f"|  |  |  | **Hold reason:** {row['error']} |  |  |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit exact LOC HABS/HAER/HALS item rights.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--queue-csv", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--metadata-cache-dir",
        default="derived/source_metadata/loc_habs_item_json",
        help="Verified LOC item JSON cache directory (relative to --root by default).",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Refresh cached metadata from LOC, falling back to a valid cache on failure.",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=1.5,
        help="Delay between unique LOC metadata requests.",
    )
    parser.add_argument(
        "--master-intake-csv",
        help="Optional ready intake CSV using audited LOC master TIFF URLs.",
    )
    parser.add_argument(
        "--import-dir",
        default="microtext/docs/source_intake_loc_habs_master",
        help="Local directory used by --master-intake-csv.",
    )
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        queue_csv=args.queue_csv,
        timeout=args.timeout,
        retries=args.retries,
        cache_dir=args.metadata_cache_dir,
        refresh_cache=args.refresh_cache,
        request_delay_seconds=max(args.request_delay_seconds, 0.0),
    )
    output_json = root / args.output_json
    output_md = root / args.output_md
    output_csv = root / args.output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["rows"])
    if args.master_intake_csv:
        master_intake_path = root / args.master_intake_csv
        master_rows = build_master_intake_rows(report, args.import_dir)
        master_intake_path.parent.mkdir(parents=True, exist_ok=True)
        with master_intake_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=MASTER_INTAKE_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(master_rows)
        print(f"[OK] Wrote {master_intake_path}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    return 0 if report["totals"]["held"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
