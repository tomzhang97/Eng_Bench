#!/usr/bin/env python3
"""Build a prioritized import queue from discovered source asset links."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


OUTPUT_FIELDS = [
    "queue_rank",
    "candidate_id",
    "source_rank",
    "domain",
    "asset_kind",
    "link_type",
    "asset_title",
    "priority_score",
    "import_action",
    "source_url",
    "page_url",
    "direct_asset_url",
    "proposed_doc_id",
    "proposed_local_path",
    "rights_capture",
    "review_gate",
    "notes",
]
IMPORT_DATE_PATH = "source_intake_2026_06_16"
IMPORT_ROOT = "microtext/docs"
RECEIPT_PAYLOAD_STATUSES = {"downloaded", "skipped_existing"}
EXISTING_MATCH_POLICIES = {"url_path_doc_id", "url_path", "url_only"}
NOISY_TEXT = re.compile(r"\b(errata|faq|frequently asked|logo|photo|thumbnail)\b", re.IGNORECASE)
GENERIC_LINK_TEXT = {
    "jump to content",
    "download",
    "file",
    "image",
    "open",
    "view",
}
POSITIVE_KEYWORDS = [
    (re.compile(r"\bschematic(s)?\b", re.IGNORECASE), 45),
    (re.compile(r"\bstandard drawing(s)?\b", re.IGNORECASE), 40),
    (re.compile(r"\bdrawing(s)?\b", re.IGNORECASE), 28),
    (re.compile(r"\bpinout\b", re.IGNORECASE), 30),
    (re.compile(r"\bblock diagram\b", re.IGNORECASE), 26),
    (re.compile(r"\bassembly\b", re.IGNORECASE), 24),
    (re.compile(r"\bdatasheet\b", re.IGNORECASE), 18),
    (re.compile(r"\bbridge\b", re.IGNORECASE), 16),
    (re.compile(r"\bpid\b|\bp&id\b", re.IGNORECASE), 35),
    (re.compile(r"\bwireframe\b|\bdimension(s)?\b|\bstackup\b", re.IGNORECASE), 18),
]
ASSET_KIND_BONUS = {
    "pdf": 25,
    "archive": 12,
    "commons_file_page": 22,
    "cad": 20,
    "image": 0,
}
RIGHTS_BLOCK_PATTERNS = (
    "rights_hold",
    "rights_uncertain",
    "hold_rights",
    "rights_review_or_hold",
    "restricted",
    "not_release_safe",
)
EXTENSION_BY_KIND = {
    "pdf": ".pdf",
    "image": ".png",
    "archive": ".zip",
    "cad": ".zip",
    "commons_file_page": "",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def as_int(value: str, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return default


def normalize_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/") if parsed.path not in {"", "/"} else parsed.path
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        fragment="",
    ).geturl()


def normalize_path(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    return raw.lower()


def normalize_doc_id(value: str | None) -> str:
    return str(value or "").strip().lower()


def asset_title(row: dict[str, str]) -> str:
    text = str(row.get("text") or "").strip()
    if text and text.lower() not in GENERIC_LINK_TEXT:
        return text
    url = str(row.get("url") or "")
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1].replace("_", " ").replace("-", " ")
    if name.lower().startswith("file:"):
        name = name[5:]
    return name.rsplit(".", 1)[0].strip() or row.get("candidate_id", "")


def slugify(value: str) -> str:
    value = unquote(value)
    value = value.lower()
    value = re.sub(r"\.[a-z0-9]{2,5}$", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:70] or "asset"


def extension_for(row: dict[str, str]) -> str:
    asset_kind = row.get("asset_kind", "")
    path = unquote(urlparse(str(row.get("url") or "")).path)
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    return EXTENSION_BY_KIND.get(asset_kind, "")


def import_date_path(date_label: str | None) -> str:
    label = str(date_label or "").strip()
    if not label:
        return IMPORT_DATE_PATH
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    return f"source_intake_{safe}" if safe else IMPORT_DATE_PATH


def eligible(row: dict[str, str]) -> bool:
    if row.get("discovery_status") != "links_found":
        return False
    if row.get("link_type") not in {"asset", "commons_file_page"}:
        return False
    if not row.get("url"):
        return False
    title = asset_title(row)
    if row.get("asset_kind") == "image" and NOISY_TEXT.search(title):
        return False
    return True


def score_row(row: dict[str, str]) -> int:
    score = as_int(row.get("score"), 0)
    rank = as_int(row.get("rank"), 99)
    title = asset_title(row)
    url = str(row.get("url") or "")
    haystack = f"{title} {url}"
    score += max(0, 30 - rank)
    score += ASSET_KIND_BONUS.get(row.get("asset_kind", ""), 0)
    for pattern, bonus in POSITIVE_KEYWORDS:
        if pattern.search(haystack):
            score += bonus
    if NOISY_TEXT.search(haystack):
        score -= 45
    return score


def import_action(row: dict[str, str]) -> str:
    asset_kind = row.get("asset_kind", "")
    if row.get("link_type") == "commons_file_page" or asset_kind == "commons_file_page":
        return "resolve_commons_license_and_payload"
    if asset_kind == "pdf":
        return "download_hash_render_textlayer"
    if asset_kind == "archive":
        return "download_hash_unpack_select_assets"
    if asset_kind == "image":
        return "download_hash_render_image"
    if asset_kind == "cad":
        return "download_hash_convert_or_render_cad"
    return "manual_import_review"


def rights_capture(row: dict[str, str]) -> str:
    url = str(row.get("url") or row.get("page_url") or "")
    host = urlparse(url).netloc.lower()
    if "commons.wikimedia.org" in host:
        return "commons_api_license_author_payload_sha256"
    if host.endswith(".gov") or "dot." in host or "penndot" in host or "scdot" in host:
        return "public_agency_source_url_terms_sha256"
    if "arduino.cc" in host:
        return "arduino_docs_license_terms_sha256"
    if "toradex.com" in host:
        return "vendor_docs_license_terms_sha256"
    if "openhardware.antmicro.com" in host:
        return "open_hardware_license_terms_sha256"
    return "source_page_terms_attribution_sha256"


def receipt_paths(root: Path, pattern: str | Path) -> list[Path]:
    pattern_path = Path(pattern)
    if pattern_path.is_absolute():
        return sorted(pattern_path.parent.glob(pattern_path.name))
    return sorted(root.glob(str(pattern).replace("\\", "/")))


def existing_asset_sets(
    root: str | Path,
    *,
    download_receipts_glob: str | Path = "derived/quality/source_asset_download_receipts_*.csv",
    source_inventory_csv: str | Path = "SOURCE_INVENTORY.csv",
) -> dict[str, Any]:
    root = Path(root)
    urls: set[str] = set()
    doc_ids: set[str] = set()
    paths: set[str] = set()
    receipt_files = receipt_paths(root, download_receipts_glob)

    for receipt_path in receipt_files:
        for row in read_csv(receipt_path):
            if str(row.get("status") or "").strip() not in RECEIPT_PAYLOAD_STATUSES:
                continue
            for field in ("download_url", "final_url", "resolved_direct_asset_url", "direct_asset_url"):
                url = normalize_url(row.get(field))
                if url:
                    urls.add(url)
            doc_id = normalize_doc_id(row.get("doc_id") or row.get("proposed_doc_id"))
            if doc_id:
                doc_ids.add(doc_id)
            local_path = normalize_path(row.get("local_path") or row.get("proposed_local_path"))
            if local_path:
                paths.add(local_path)

    inventory_path = Path(source_inventory_csv)
    if not inventory_path.is_absolute():
        inventory_path = root / inventory_path
    inventory_rows = read_csv(inventory_path)
    for row in inventory_rows:
        doc_id = normalize_doc_id(row.get("doc_id"))
        if doc_id:
            doc_ids.add(doc_id)
        local_path = normalize_path(row.get("path") or row.get("local_path"))
        if local_path:
            paths.add(local_path)

    return {
        "urls": urls,
        "doc_ids": doc_ids,
        "paths": paths,
        "receipt_files": [path.as_posix() for path in receipt_files],
        "inventory_path": inventory_path.as_posix(),
        "inventory_rows": len(inventory_rows),
    }


def candidate_validation_path(root: Path, path: str | Path) -> Path:
    validation_path = Path(path)
    if not validation_path.is_absolute():
        validation_path = root / validation_path
    return validation_path


def validation_row_is_blocked(row: dict[str, str]) -> bool:
    fields = [
        row.get("source_validity", ""),
        row.get("release_posture", ""),
        row.get("next_action", ""),
    ]
    haystack = " ".join(str(field or "").lower() for field in fields)
    return any(pattern in haystack for pattern in RIGHTS_BLOCK_PATTERNS)


def blocked_candidate_sets(
    root: str | Path,
    *,
    source_candidate_validation_csv: str | Path = "SOURCE_CANDIDATE_VALIDATION.csv",
) -> dict[str, Any]:
    root = Path(root)
    validation_path = candidate_validation_path(root, source_candidate_validation_csv)
    rows = read_csv(validation_path)
    blocked_ids: set[str] = set()
    reasons: Counter[str] = Counter()
    for row in rows:
        candidate_id = normalize_doc_id(row.get("candidate_id"))
        if not candidate_id or not validation_row_is_blocked(row):
            continue
        blocked_ids.add(candidate_id)
        reason = str(row.get("release_posture") or row.get("next_action") or row.get("source_validity") or "blocked")
        reasons[reason] += 1
    return {
        "candidate_ids": blocked_ids,
        "validation_path": validation_path.as_posix(),
        "validation_rows": len(rows),
        "blocked_candidate_ids": len(blocked_ids),
        "blocked_by_reason": dict(sorted(reasons.items())),
    }


def build_asset_row(row: dict[str, str], queue_rank: int, import_dir: str) -> dict[str, Any]:
    title = asset_title(row)
    candidate_id = row.get("candidate_id", "")
    slug = slugify(f"{candidate_id}_{title}")
    ext = extension_for(row)
    doc_id = slug
    local_path = f"{import_dir}/{slug}{ext}"
    source_url = row.get("url") if row.get("link_type") == "commons_file_page" else row.get("source_url")
    direct_asset_url = "" if row.get("link_type") == "commons_file_page" else row.get("url")
    return {
        "queue_rank": queue_rank,
        "candidate_id": candidate_id,
        "source_rank": row.get("rank", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "link_type": row.get("link_type", ""),
        "asset_title": title,
        "priority_score": score_row(row),
        "import_action": import_action(row),
        "source_url": source_url or "",
        "page_url": row.get("page_url", ""),
        "direct_asset_url": direct_asset_url,
        "proposed_doc_id": doc_id,
        "proposed_local_path": local_path,
        "rights_capture": rights_capture(row),
        "review_gate": "review_packet_required_before_gold",
        "notes": "Planning row only; download, hash, render/convert, mine candidates, then export review packets. Do not merge unreviewed rows into gold.",
    }


def existing_match_reason(
    row: dict[str, str],
    asset_row: dict[str, Any],
    existing_assets: dict[str, Any],
    *,
    match_policy: str = "url_path_doc_id",
) -> str:
    if match_policy not in EXISTING_MATCH_POLICIES:
        raise ValueError(f"Unsupported existing-match policy: {match_policy}")
    urls = existing_assets.get("urls", set())
    doc_ids = existing_assets.get("doc_ids", set())
    paths = existing_assets.get("paths", set())
    for field in ("url",):
        url = normalize_url(row.get(field))
        if url and url in urls:
            return "download_url"
    for field in ("direct_asset_url",):
        url = normalize_url(asset_row.get(field))
        if url and url in urls:
            return "download_url"
    if match_policy == "url_only":
        return ""
    doc_id = normalize_doc_id(asset_row.get("proposed_doc_id"))
    if match_policy == "url_path_doc_id" and doc_id and doc_id in doc_ids:
        return "doc_id"
    local_path = normalize_path(asset_row.get("proposed_local_path"))
    if local_path and local_path in paths:
        return "local_path"
    return ""


def uniquify_asset_row(row: dict[str, Any], seen_doc_ids: set[str], seen_paths: set[str]) -> dict[str, Any]:
    doc_id = str(row["proposed_doc_id"])
    local_path = str(row["proposed_local_path"])
    if doc_id not in seen_doc_ids and (not local_path or local_path not in seen_paths):
        seen_doc_ids.add(doc_id)
        if local_path:
            seen_paths.add(local_path)
        return row
    suffix = 2
    while True:
        candidate_doc_id = f"{doc_id}_{suffix}"
        candidate_path = local_path
        if local_path:
            path = Path(local_path)
            candidate_path = str(path.with_name(f"{path.stem}_{suffix}{path.suffix}")).replace("\\", "/")
        if candidate_doc_id not in seen_doc_ids and (not candidate_path or candidate_path not in seen_paths):
            updated = dict(row)
            updated["proposed_doc_id"] = candidate_doc_id
            updated["proposed_local_path"] = candidate_path
            seen_doc_ids.add(candidate_doc_id)
            if candidate_path:
                seen_paths.add(candidate_path)
            return updated
        suffix += 1


def select_assets(
    rows: list[dict[str, str]],
    *,
    max_assets: int,
    max_per_candidate: int,
    import_dir: str,
    existing_assets: dict[str, Any] | None = None,
    existing_match_policy: str = "url_path_doc_id",
    excluded_counter: Counter[str] | None = None,
    blocked_candidates: dict[str, Any] | None = None,
    blocked_counter: Counter[str] | None = None,
    duplicate_counter: Counter[str] | None = None,
) -> list[dict[str, Any]]:
    eligible_rows = [row for row in rows if eligible(row)]
    ranked_rows = sorted(
        eligible_rows,
        key=lambda row: (-score_row(row), as_int(row.get("rank"), 99), row.get("candidate_id", ""), row.get("url", "")),
    )
    selected: list[dict[str, Any]] = []
    per_candidate: dict[str, int] = defaultdict(int)
    seen_doc_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_urls: set[str] = set()
    for row in ranked_rows:
        candidate_id = row.get("candidate_id", "")
        normalized_candidate_id = normalize_doc_id(candidate_id)
        if blocked_candidates and normalized_candidate_id in blocked_candidates.get("candidate_ids", set()):
            if blocked_counter is not None:
                blocked_counter[candidate_id] += 1
            continue
        if per_candidate[candidate_id] >= max_per_candidate:
            continue
        normalized_asset_url = normalize_url(row.get("url"))
        if normalized_asset_url and normalized_asset_url in seen_urls:
            if duplicate_counter is not None:
                duplicate_counter["asset_url"] += 1
            continue
        asset_row = build_asset_row(row, len(selected) + 1, import_dir)
        if existing_assets:
            reason = existing_match_reason(
                row,
                asset_row,
                existing_assets,
                match_policy=existing_match_policy,
            )
            if reason:
                if excluded_counter is not None:
                    excluded_counter[reason] += 1
                continue
        selected.append(uniquify_asset_row(asset_row, seen_doc_ids, seen_paths))
        if normalized_asset_url:
            seen_urls.add(normalized_asset_url)
        per_candidate[candidate_id] += 1
        if len(selected) >= max_assets:
            break
    return selected


def build_report(
    root: str | Path,
    *,
    asset_links_csv: str | Path,
    date_label: str | None = None,
    max_assets: int = 30,
    max_per_candidate: int = 4,
    import_dir: str | None = None,
    exclude_existing: bool = False,
    existing_match_policy: str = "url_path_doc_id",
    download_receipts_glob: str | Path = "derived/quality/source_asset_download_receipts_*.csv",
    source_inventory_csv: str | Path = "SOURCE_INVENTORY.csv",
    exclude_blocked_candidates: bool = False,
    source_candidate_validation_csv: str | Path = "SOURCE_CANDIDATE_VALIDATION.csv",
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(asset_links_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    input_rows = read_csv(input_path)
    eligible_rows = [row for row in input_rows if eligible(row)]
    label = date_label or date.today().isoformat()
    resolved_import_dir = import_dir or f"{IMPORT_ROOT}/{import_date_path(label)}"
    existing_assets = (
        existing_asset_sets(
            root,
            download_receipts_glob=download_receipts_glob,
            source_inventory_csv=source_inventory_csv,
        )
        if exclude_existing
        else None
    )
    excluded_counter: Counter[str] = Counter()
    blocked_candidates = (
        blocked_candidate_sets(root, source_candidate_validation_csv=source_candidate_validation_csv)
        if exclude_blocked_candidates
        else None
    )
    blocked_counter: Counter[str] = Counter()
    duplicate_counter: Counter[str] = Counter()
    selected = select_assets(
        input_rows,
        max_assets=max_assets,
        max_per_candidate=max_per_candidate,
        import_dir=resolved_import_dir,
        existing_assets=existing_assets,
        existing_match_policy=existing_match_policy,
        excluded_counter=excluded_counter,
        blocked_candidates=blocked_candidates,
        blocked_counter=blocked_counter,
        duplicate_counter=duplicate_counter,
    )
    totals = {
        "date_label": label,
        "import_dir": resolved_import_dir,
        "input_rows": len(input_rows),
        "eligible_rows": len(eligible_rows),
        "exclude_existing": exclude_existing,
        "existing_match_policy": existing_match_policy,
        "excluded_existing_assets": sum(excluded_counter.values()),
        "excluded_existing_by_reason": dict(sorted(excluded_counter.items())),
        "exclude_blocked_candidates": exclude_blocked_candidates,
        "excluded_blocked_candidate_assets": sum(blocked_counter.values()),
        "excluded_blocked_candidate_assets_by_id": dict(sorted(blocked_counter.items())),
        "excluded_in_run_duplicate_assets": sum(duplicate_counter.values()),
        "excluded_in_run_duplicates_by_reason": dict(sorted(duplicate_counter.items())),
        "selected_assets": len(selected),
        "max_assets": max_assets,
        "max_per_candidate": max_per_candidate,
    }
    if existing_assets is not None:
        totals.update(
            {
                "existing_download_receipt_files": len(existing_assets["receipt_files"]),
                "existing_inventory_rows": existing_assets["inventory_rows"],
                "existing_url_count": len(existing_assets["urls"]),
                "existing_doc_id_count": len(existing_assets["doc_ids"]),
                "existing_path_count": len(existing_assets["paths"]),
            }
        )
    if blocked_candidates is not None:
        totals.update(
            {
                "source_candidate_validation_rows": blocked_candidates["validation_rows"],
                "blocked_candidate_ids": blocked_candidates["blocked_candidate_ids"],
                "blocked_candidate_ids_by_reason": blocked_candidates["blocked_by_reason"],
            }
        )
    return {
        "date_label": totals["date_label"],
        "asset_links_csv": input_path.as_posix(),
        "totals": totals,
        "existing_sources": {
            "download_receipts_glob": str(download_receipts_glob),
            "existing_match_policy": existing_match_policy,
            "source_inventory_csv": str(source_inventory_csv),
            "receipt_files": existing_assets["receipt_files"] if existing_assets is not None else [],
            "inventory_path": existing_assets["inventory_path"] if existing_assets is not None else "",
            "source_candidate_validation_csv": str(source_candidate_validation_csv),
            "source_candidate_validation_path": (
                blocked_candidates["validation_path"] if blocked_candidates is not None else ""
            ),
        },
        "selected_by_domain": dict(sorted(Counter(row["domain"] for row in selected).items())),
        "selected_by_action": dict(sorted(Counter(row["import_action"] for row in selected).items())),
        "selected_by_asset_kind": dict(sorted(Counter(row["asset_kind"] for row in selected).items())),
        "selected_assets": selected,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Asset Import Queue",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Asset links CSV: `{report['asset_links_csv']}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Eligible rows: `{totals['eligible_rows']}`",
        f"- Exclude existing assets: `{totals.get('exclude_existing', False)}`",
        f"- Existing assets skipped: `{totals.get('excluded_existing_assets', 0)}`",
        f"- Exclude blocked candidates: `{totals.get('exclude_blocked_candidates', False)}`",
        f"- Blocked-candidate assets skipped: `{totals.get('excluded_blocked_candidate_assets', 0)}`",
        f"- In-run duplicate assets skipped: `{totals.get('excluded_in_run_duplicate_assets', 0)}`",
        f"- Selected assets: `{totals['selected_assets']}`",
        f"- Import dir: `{totals['import_dir']}`",
        f"- Max assets: `{totals['max_assets']}`",
        f"- Max per candidate: `{totals['max_per_candidate']}`",
        "- Queue only: do not merge unreviewed rows into gold.",
        "",
        "## Selected By Domain",
        "",
    ]
    for key, value in report["selected_by_domain"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Selected By Action", ""])
    for key, value in report["selected_by_action"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Existing-Asset Exclusion", ""])
    if totals.get("exclude_existing"):
        lines.append(f"- Download receipt files scanned: `{totals.get('existing_download_receipt_files', 0)}`")
        lines.append(f"- Source inventory rows scanned: `{totals.get('existing_inventory_rows', 0)}`")
        for key, value in totals.get("excluded_existing_by_reason", {}).items():
            lines.append(f"- Skipped by {key}: `{value}`")
    else:
        lines.append("- Disabled for this queue.")
    lines.extend(["", "## Candidate Validation Exclusion", ""])
    if totals.get("exclude_blocked_candidates"):
        lines.append(f"- Source candidate validation rows scanned: `{totals.get('source_candidate_validation_rows', 0)}`")
        lines.append(f"- Blocked candidate IDs known: `{totals.get('blocked_candidate_ids', 0)}`")
        for key, value in totals.get("excluded_blocked_candidate_assets_by_id", {}).items():
            lines.append(f"- Skipped candidate {key}: `{value}` assets")
    else:
        lines.append("- Disabled for this queue.")
    lines.extend(
        [
            "",
            "## Queue",
            "",
            "| Rank | Candidate | Domain | Kind | Score | Action | Title | URL |",
            "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in report["selected_assets"]:
        url = row["source_url"] if row["import_action"] == "resolve_commons_license_and_payload" else row["direct_asset_url"]
        lines.append(
            f"| {row['queue_rank']} | `{row['candidate_id']}` | {row['domain']} | {row['asset_kind']} | "
            f"{row['priority_score']} | {row['import_action']} | {row['asset_title']} | {url} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This queue is a planning artifact, not a source import and not a gold merge.",
            "- Before import, capture source page URL, direct payload URL, local path, SHA-256, rights posture, and attribution/license notes.",
            "- Commons file-page rows must be resolved through Commons API/imageinfo before download.",
            "- Imported assets must render/convert cleanly and go through review packets before any gold promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a prioritized source asset import queue.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--asset-links-csv", default="derived/quality/source_asset_links_2026-06-16.csv")
    parser.add_argument("--max-assets", type=int, default=30)
    parser.add_argument("--max-per-candidate", type=int, default=4)
    parser.add_argument(
        "--import-dir",
        help="Override the local import directory. Defaults to microtext/docs/source_intake_<date_label>.",
    )
    parser.add_argument(
        "--exclude-existing",
        action="store_true",
        help="Skip assets whose URL, proposed doc ID, or proposed local path already appears in download receipts or SOURCE_INVENTORY.csv.",
    )
    parser.add_argument(
        "--existing-download-receipts-glob",
        default="derived/quality/source_asset_download_receipts_*.csv",
        help="Glob of previous download receipt CSVs used with --exclude-existing.",
    )
    parser.add_argument(
        "--existing-match-policy",
        choices=sorted(EXISTING_MATCH_POLICIES),
        default="url_path_doc_id",
        help=(
            "Existing-asset fields used with --exclude-existing. Use url_only for "
            "quality upgrades that retain a canonical document ID."
        ),
    )
    parser.add_argument(
        "--source-inventory-csv",
        default="SOURCE_INVENTORY.csv",
        help="Source inventory CSV used with --exclude-existing.",
    )
    parser.add_argument(
        "--exclude-blocked-candidates",
        action="store_true",
        help="Skip candidate IDs marked with rights-hold or rights-uncertain status in SOURCE_CANDIDATE_VALIDATION.csv.",
    )
    parser.add_argument(
        "--source-candidate-validation-csv",
        default="SOURCE_CANDIDATE_VALIDATION.csv",
        help="Candidate validation CSV used with --exclude-blocked-candidates.",
    )
    parser.add_argument("--output-json", default="derived/quality/source_asset_import_queue_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_asset_import_queue_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_asset_import_queue_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        asset_links_csv=args.asset_links_csv,
        date_label=args.date_label,
        max_assets=args.max_assets,
        max_per_candidate=args.max_per_candidate,
        import_dir=args.import_dir,
        exclude_existing=args.exclude_existing,
        existing_match_policy=args.existing_match_policy,
        download_receipts_glob=args.existing_download_receipts_glob,
        source_inventory_csv=args.source_inventory_csv,
        exclude_blocked_candidates=args.exclude_blocked_candidates,
        source_candidate_validation_csv=args.source_candidate_validation_csv,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["selected_assets"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
