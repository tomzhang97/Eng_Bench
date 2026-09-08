#!/usr/bin/env python3
"""Resolve LOC HABS/HAER/HALS search pages into exact image asset rows."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = "Eng-Bench-loc-source-resolver/1.0 (engineering-document benchmark)"
OUTPUT_FIELDS = [
    "candidate_id",
    "rank",
    "domain",
    "task_card_type",
    "source_url",
    "page_url",
    "discovery_status",
    "http_status",
    "retry_count",
    "error",
    "link_type",
    "asset_kind",
    "score",
    "url",
    "text",
    "loc_pk",
    "loc_call_number",
    "loc_medium",
    "loc_collection",
    "search_page",
]
SHEET_HINTS = (
    ".sheet.",
    "/sheet/",
)
TITLE_HINTS = re.compile(
    r"\b(plan|elevation|section|detail|drawing|sheet|diagram|schedule)\b",
    re.IGNORECASE,
)
PHOTO_HINTS = (
    ".photos.",
    "/photos/",
)


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


def loc_json_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["fo"] = "json"
    return urlunparse(parsed._replace(query=urlencode(query)))


def loc_search_page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["sp"] = str(max(1, page))
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_url(url: str, timeout: int) -> str:
    request = Request(loc_json_url(url), headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def is_loc_search_row(row: dict[str, str]) -> bool:
    url = str(row.get("final_url") or row.get("source_url") or "")
    task_type = str(row.get("task_card_type") or "")
    strategy = str(row.get("import_strategy") or "")
    parsed = urlparse(url)
    return (
        parsed.netloc.endswith("loc.gov")
        and "/pictures/search/" in parsed.path
        and (task_type in {"loc_item_selection", ""} or strategy in {"loc_search_page", ""})
    )


def is_sheet_like_item(item: dict[str, Any]) -> bool:
    pk = str(item.get("pk") or "").lower()
    image_url = str((item.get("image") or {}).get("full") or "").lower()
    title = str(item.get("title") or "")
    if any(hint in pk or hint in image_url for hint in SHEET_HINTS):
        return True
    if any(hint in pk or hint in image_url for hint in PHOTO_HINTS):
        return False
    return bool(TITLE_HINTS.search(title))


def score_item(row: dict[str, str], item: dict[str, Any]) -> int:
    rank = int(str(row.get("rank") or "99")) if str(row.get("rank") or "").isdigit() else 99
    title = str(item.get("title") or "")
    score = 100 + max(0, 30 - rank)
    if TITLE_HINTS.search(title):
        score += 20
    if "hh" in item.get("collection", []):
        score += 10
    return score


def asset_row_from_item(row: dict[str, str], item: dict[str, Any]) -> dict[str, Any] | None:
    image = item.get("image") or {}
    links = item.get("links") or {}
    full_url = str(image.get("full") or "").strip()
    page_url = str(links.get("item") or "").strip()
    if not full_url or not page_url:
        return None
    collection = item.get("collection") or []
    return {
        "candidate_id": row.get("candidate_id", ""),
        "rank": row.get("rank", ""),
        "domain": row.get("domain", ""),
        "task_card_type": "loc_item_selection",
        "source_url": row.get("source_url", ""),
        "page_url": page_url,
        "discovery_status": "links_found",
        "http_status": 200,
        "retry_count": 0,
        "error": "",
        "link_type": "asset",
        "asset_kind": "image",
        "score": score_item(row, item),
        "url": full_url,
        "text": str(item.get("title") or "").strip(),
        "loc_pk": str(item.get("pk") or ""),
        "loc_call_number": str(item.get("call_number") or ""),
        "loc_medium": str(item.get("medium") or ""),
        "loc_collection": ",".join(str(value) for value in collection),
    }


def explicit_sheet_asset(row: dict[str, Any]) -> bool:
    return ".sheet." in str(row.get("loc_pk") or "").lower() or ".sheet." in str(
        row.get("page_url") or ""
    ).lower()


def rows_from_loc_payload(
    row: dict[str, str],
    payload: str,
    *,
    max_per_source: int,
    sheet_only: bool = True,
) -> tuple[list[dict[str, Any]], int, int]:
    data = json.loads(payload)
    asset_rows: list[dict[str, Any]] = []
    skipped_non_sheet = 0
    skipped_missing_asset = 0
    for item in data.get("results", []):
        if sheet_only and not is_sheet_like_item(item):
            skipped_non_sheet += 1
            continue
        asset = asset_row_from_item(row, item)
        if asset is None:
            skipped_missing_asset += 1
            continue
        asset_rows.append(asset)
        if len(asset_rows) >= max_per_source:
            break
    return asset_rows, skipped_non_sheet, skipped_missing_asset


def build_report(
    root: str | Path,
    *,
    task_cards_csv: str | Path,
    date_label: str | None = None,
    fetcher: Callable[[str, int], str] = fetch_url,
    timeout: int = 20,
    max_per_source: int = 5,
    pages_per_source: int = 1,
    fixture_json: str | Path | None = None,
    include_photos: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(task_cards_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    asset_rows: list[dict[str, Any]] = []
    checked_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    skipped_non_sheet = 0
    skipped_missing_asset = 0
    duplicate_assets_skipped = 0
    pages_fetched = 0
    fixture_payload = ""
    if fixture_json:
        fixture_path = Path(fixture_json)
        if not fixture_path.is_absolute():
            fixture_path = root / fixture_path
        fixture_payload = fixture_path.read_text(encoding="utf-8")

    for row in rows:
        if not is_loc_search_row(row):
            skipped_rows.append(
                {
                    "candidate_id": row.get("candidate_id", ""),
                    "source_url": row.get("source_url", ""),
                    "reason": "not_loc_search_row",
                }
            )
            continue
        try:
            base_url = row.get("final_url") or row.get("source_url") or ""
            resolved: list[dict[str, Any]] = []
            seen_assets: dict[str, int] = {}
            row_non_sheet = 0
            row_missing = 0
            row_duplicates = 0
            row_pages_fetched = 0
            for page in range(1, max(1, pages_per_source) + 1):
                page_url = loc_search_page_url(base_url, page)
                payload = fixture_payload or fetcher(page_url, timeout)
                row_pages_fetched += 1
                page_rows, non_sheet, missing = rows_from_loc_payload(
                    row,
                    payload,
                    max_per_source=max_per_source,
                    sheet_only=not include_photos,
                )
                row_non_sheet += non_sheet
                row_missing += missing
                for asset in page_rows:
                    key = str(asset.get("url") or asset.get("page_url") or "")
                    if key in seen_assets:
                        row_duplicates += 1
                        previous_index = seen_assets[key]
                        if explicit_sheet_asset(asset) and not explicit_sheet_asset(
                            resolved[previous_index]
                        ):
                            asset["search_page"] = page
                            resolved[previous_index] = asset
                        continue
                    seen_assets[key] = len(resolved)
                    asset["search_page"] = page
                    resolved.append(asset)
                    if len(resolved) >= max_per_source:
                        break
                if len(resolved) >= max_per_source:
                    break
            pages_fetched += row_pages_fetched
            skipped_non_sheet += row_non_sheet
            skipped_missing_asset += row_missing
            duplicate_assets_skipped += row_duplicates
            status = "links_found" if resolved else "no_links_found"
            status_counts[status] += 1
            checked_rows.append(
                {
                    "candidate_id": row.get("candidate_id", ""),
                    "source_url": row.get("source_url", ""),
                    "discovery_status": status,
                    "asset_rows": len(resolved),
                    "pages_fetched": row_pages_fetched,
                    "skipped_non_sheet_items": row_non_sheet,
                    "skipped_missing_asset_items": row_missing,
                    "duplicate_assets_skipped": row_duplicates,
                }
            )
            asset_rows.extend(resolved)
        except Exception as exc:  # noqa: BLE001 - capture per-row resolver failures.
            status_counts["fetch_failed"] += 1
            checked_rows.append(
                {
                    "candidate_id": row.get("candidate_id", ""),
                    "source_url": row.get("source_url", ""),
                    "discovery_status": "fetch_failed",
                    "asset_rows": 0,
                    "error": exc.__class__.__name__,
                }
            )

    totals = {
        "date_label": date_label or date.today().isoformat(),
        "rows": len(rows),
        "loc_rows": len(checked_rows),
        "skipped_rows": len(skipped_rows),
        "asset_rows": len(asset_rows),
        "skipped_non_sheet_items": skipped_non_sheet,
        "skipped_missing_asset_items": skipped_missing_asset,
        "max_per_source": max_per_source,
        "pages_per_source": max(1, pages_per_source),
        "pages_fetched": pages_fetched,
        "include_photos": include_photos,
        "duplicate_assets_skipped": duplicate_assets_skipped,
    }
    return {
        "date_label": totals["date_label"],
        "task_cards_csv": input_path.as_posix(),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "checked_rows": checked_rows,
        "skipped_rows": skipped_rows,
        "asset_rows": asset_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# LOC HABS/HAER/HALS Asset Resolution",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Task cards CSV: `{report['task_cards_csv']}`",
        f"- Rows: `{totals['rows']}`",
        f"- LOC rows checked: `{totals['loc_rows']}`",
        f"- Asset rows: `{totals['asset_rows']}`",
        f"- Skipped non-sheet items: `{totals['skipped_non_sheet_items']}`",
        f"- Skipped missing-asset items: `{totals['skipped_missing_asset_items']}`",
        f"- Search pages fetched: `{totals['pages_fetched']}`",
        f"- Duplicate assets skipped: `{totals['duplicate_assets_skipped']}`",
        "- Resolution only: do not merge unreviewed rows into gold.",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in report["status_counts"].items():
        lines.append(f"- {status}: `{count}`")
    lines.extend(
        [
            "",
            "## Asset Rows",
            "",
            "| Candidate | Score | Call Number | Title | Image URL | Item URL |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in report["asset_rows"]:
        title = str(row["text"]).replace("|", "\\|")[:160]
        lines.append(
            f"| `{row['candidate_id']}` | {row['score']} | `{row['loc_call_number']}` | "
            f"{title} | {row['url']} | {row['page_url']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- These rows are source-asset candidates only.",
            "- Each asset still needs duplicate checks, download/hash receipts, rendering, candidate mining, and human review packets.",
            "- HABS/HAER/HALS rows should retain LOC item URL, image URL, call number, and collection metadata in provenance.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve LOC HABS/HAER/HALS search pages into image assets.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--task-cards-csv", default="derived/quality/source_import_task_cards_2026-06-16.csv")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-per-source", type=int, default=5)
    parser.add_argument("--pages-per-source", type=int, default=1)
    parser.add_argument("--include-photos", action="store_true")
    parser.add_argument("--fixture-json", default="")
    parser.add_argument("--output-json", default="derived/quality/loc_habs_assets_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/loc_habs_assets_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/loc_habs_assets_2026-06-16.csv")
    args = parser.parse_args(argv)

    report = build_report(
        args.root,
        task_cards_csv=args.task_cards_csv,
        date_label=args.date_label,
        timeout=args.timeout,
        max_per_source=args.max_per_source,
        pages_per_source=args.pages_per_source,
        fixture_json=args.fixture_json or None,
        include_photos=args.include_photos,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = Path(args.root) / output_json
    if not output_md.is_absolute():
        output_md = Path(args.root) / output_md
    if not output_csv.is_absolute():
        output_csv = Path(args.root) / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["asset_rows"])
    print(f"[OK] Wrote {output_json.relative_to(Path(args.root)) if not Path(args.root).is_absolute() else output_json}")
    print(f"[OK] Wrote {output_md.relative_to(Path(args.root)) if not Path(args.root).is_absolute() else output_md}")
    print(f"[OK] Wrote {output_csv.relative_to(Path(args.root)) if not Path(args.root).is_absolute() else output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
