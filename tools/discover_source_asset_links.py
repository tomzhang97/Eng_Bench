#!/usr/bin/env python3
"""Discover likely direct assets from source import task cards."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


USER_AGENT = "Eng-Bench-source-discovery/1.0 (engineering-document benchmark)"
CHECKABLE_TASK_TYPES = {
    "html_asset_selection",
    "commons_file_selection",
    "direct_asset_import",
    "loc_item_selection",
}
DOWNLOAD_EXTENSIONS = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".svg": "image",
    ".dwg": "cad",
    ".dxf": "cad",
    ".step": "cad",
    ".stp": "cad",
    ".stl": "cad",
    ".scad": "cad",
    ".iges": "cad",
    ".igs": "cad",
    ".zip": "archive",
}
LINK_SCORE = {
    "pdf": 100,
    "cad": 90,
    "image": 80,
    "commons_file_page": 70,
    "archive": 55,
}
NOISE_TOKENS = {
    "copyright",
    "favicon",
    "footer",
    "icon",
    "logo",
    "mediawiki",
    "poweredby",
    "pencil",
    "thumbnail",
    "wikidata",
    "wikimedia",
    "wordmark",
}
NOISE_PATH_PARTS = {
    "/etc.clientlibs/",
    "/static/",
    "/w/resources/assets/",
    "/w/skins/",
}
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
]


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    url: str
    final_url: str
    http_status: int
    content_type: str
    body: str
    error: str


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._active_anchor: dict[str, str] | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "a" and attr_map.get("href"):
            self._active_anchor = {"url": attr_map["href"], "tag": "a", "text": ""}
            self._active_text = []
        elif tag.lower() in {"img", "source"}:
            url = attr_map.get("src") or attr_map.get("srcset", "").split(" ", 1)[0]
            if url:
                self.links.append(
                    {
                        "url": url,
                        "tag": tag.lower(),
                        "text": attr_map.get("alt") or attr_map.get("title") or "",
                    }
                )

    def handle_data(self, data: str) -> None:
        if self._active_anchor is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active_anchor is not None:
            text = " ".join(" ".join(self._active_text).split())
            anchor = dict(self._active_anchor)
            anchor["text"] = text
            self.links.append(anchor)
            self._active_anchor = None
            self._active_text = []


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


def normalize_url(url: str, base_url: str) -> str:
    absolute = urljoin(base_url, url.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.scheme in {"mailto", "javascript", "tel"}:
        return ""
    return absolute.split("#", 1)[0]


def classify_link(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    lower_path = decoded_path.lower()
    if "/wiki/file:" in lower_path:
        return "commons_file_page", "commons_file_page"
    for ext, asset_kind in DOWNLOAD_EXTENSIONS.items():
        if lower_path.endswith(ext):
            return "asset", asset_kind
    if "special:redirect/file" in lower_path:
        return "asset", "direct_commons_payload"
    return "", ""


def is_noise_link(url: str, text: str, asset_kind: str) -> bool:
    parsed = urlparse(url)
    lower_path = unquote(parsed.path).lower()
    lower_text = text.lower()
    if asset_kind not in {"image", "direct_commons_payload"}:
        return False
    if parsed.netloc.endswith("wikimedia.org") and "/wikipedia/commons/thumb/" in lower_path:
        return True
    if any(part in lower_path for part in NOISE_PATH_PARTS):
        return True
    filename = lower_path.rsplit("/", 1)[-1]
    haystack = f"{filename} {lower_text}"
    return any(token in haystack for token in NOISE_TOKENS)


def extract_asset_links(html: str, base_url: str) -> list[dict[str, Any]]:
    parser = LinkExtractor()
    parser.feed(html)
    deduped: dict[str, dict[str, Any]] = {}
    for raw in parser.links:
        url = normalize_url(raw.get("url", ""), base_url)
        if not url:
            continue
        link_type, asset_kind = classify_link(url)
        if not link_type:
            continue
        text = raw.get("text", "").strip()
        if is_noise_link(url, text, asset_kind):
            continue
        score = LINK_SCORE.get(asset_kind, 50)
        candidate = {
            "url": url,
            "link_type": link_type,
            "asset_kind": asset_kind,
            "score": score,
            "text": text,
            "tag": raw.get("tag", ""),
        }
        previous = deduped.get(url)
        if previous is None or int(previous["score"]) < score:
            deduped[url] = candidate
    return sorted(deduped.values(), key=lambda row: (-int(row["score"]), row["url"]))


def fetch_url(url: str, timeout: int) -> FetchResult:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2_500_000)
            content_type = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, errors="replace")
            return FetchResult(
                ok=True,
                url=url,
                final_url=response.geturl(),
                http_status=int(response.status),
                content_type=content_type,
                body=text,
                error="",
            )
    except Exception as exc:  # pragma: no cover - network failure shapes vary
        return FetchResult(
            ok=False,
            url=url,
            final_url=url,
            http_status=0,
            content_type="",
            body="",
            error=exc.__class__.__name__,
        )


def row_url(row: dict[str, str]) -> str:
    return str(row.get("final_url") or row.get("source_url") or "").strip()


def discover_for_row(
    row: dict[str, str],
    *,
    fetcher: Callable[[str, int], FetchResult],
    timeout: int,
    no_network: bool,
    retries: int,
) -> dict[str, Any]:
    page_url = row_url(row)
    base = {
        "candidate_id": row.get("candidate_id", ""),
        "rank": row.get("rank", ""),
        "domain": row.get("domain", ""),
        "task_card_type": row.get("task_card_type", ""),
        "source_url": row.get("source_url", ""),
        "page_url": page_url,
    }
    if row.get("task_card_type") == "direct_asset_import":
        link_type, asset_kind = classify_link(page_url)
        links = []
        if link_type == "asset":
            links.append(
                {
                    "url": page_url,
                    "link_type": link_type,
                    "asset_kind": asset_kind,
                    "score": LINK_SCORE.get(asset_kind, 50),
                    "text": row.get("candidate_id", ""),
                }
            )
        return {
            **base,
            "discovery_status": "links_found" if links else "no_direct_asset_link",
            "http_status": 0,
            "retry_count": 0,
            "content_type": "",
            "error": "" if links else "direct_url_not_downloadable_asset",
            "links": links,
        }
    if no_network:
        return {
            **base,
            "discovery_status": "not_checked_no_network",
            "http_status": 0,
            "retry_count": 0,
            "error": "",
            "links": [],
        }
    if not page_url:
        return {
            **base,
            "discovery_status": "missing_url",
            "http_status": 0,
            "retry_count": 0,
            "error": "missing_url",
            "links": [],
        }
    result = FetchResult(
        ok=False,
        url=page_url,
        final_url=page_url,
        http_status=0,
        content_type="",
        body="",
        error="not_fetched",
    )
    retry_count = 0
    for attempt in range(max(0, retries) + 1):
        result = fetcher(page_url, timeout)
        retry_count = attempt
        if result.ok:
            break
    if not result.ok:
        return {
            **base,
            "discovery_status": "fetch_failed",
            "http_status": result.http_status,
            "retry_count": retry_count,
            "error": result.error,
            "links": [],
        }
    links = extract_asset_links(result.body, result.final_url or page_url)
    if row.get("task_card_type") == "commons_file_selection":
        links = [link for link in links if link.get("link_type") == "commons_file_page"]
    return {
        **base,
        "page_url": result.final_url or page_url,
        "discovery_status": "links_found" if links else "no_links_found",
        "http_status": result.http_status,
        "retry_count": retry_count,
        "content_type": result.content_type,
        "error": "",
        "links": links,
    }


def flatten_links(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        links = row.get("links") or []
        if not links:
            flattened.append(
                {
                    **{field: row.get(field, "") for field in OUTPUT_FIELDS},
                    "link_type": "",
                    "asset_kind": "",
                    "score": "",
                    "url": "",
                    "text": "",
                }
            )
            continue
        for link in links:
            flattened.append(
                {
                    **{field: row.get(field, "") for field in OUTPUT_FIELDS},
                    "link_type": link.get("link_type", ""),
                    "asset_kind": link.get("asset_kind", ""),
                    "score": link.get("score", ""),
                    "url": link.get("url", ""),
                    "text": link.get("text", ""),
                }
            )
    return flattened


def build_report(
    root: str | Path,
    *,
    task_cards_csv: str | Path,
    date_label: str | None = None,
    timeout: int = 20,
    no_network: bool = False,
    retries: int = 1,
    fetcher: Callable[[str, int], FetchResult] = fetch_url,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(task_cards_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    checkable = [row for row in rows if row.get("task_card_type") in CHECKABLE_TASK_TYPES]
    skipped = [row for row in rows if row.get("task_card_type") not in CHECKABLE_TASK_TYPES]
    checked = [
        discover_for_row(
            row,
            fetcher=fetcher,
            timeout=timeout,
            no_network=no_network,
            retries=retries,
        )
        for row in checkable
    ]
    all_links = [link for row in checked for link in row.get("links", [])]
    type_counts = Counter(link["asset_kind"] for link in all_links)
    status_counts = Counter(row["discovery_status"] for row in checked)
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "rows": len(rows),
        "checked_rows": len(checked),
        "skipped_rows": len(skipped),
        "asset_links": sum(1 for link in all_links if link.get("link_type") == "asset"),
        "commons_file_pages": sum(1 for link in all_links if link.get("link_type") == "commons_file_page"),
        "total_links": len(all_links),
    }
    return {
        "date_label": totals["date_label"],
        "task_cards_csv": input_path.as_posix(),
        "totals": totals,
        "discovery_status_counts": dict(sorted(status_counts.items())),
        "asset_kind_counts": dict(sorted(type_counts.items())),
        "checked_rows": checked,
        "skipped_rows": skipped,
        "flat_link_rows": flatten_links(checked),
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Asset Link Discovery",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Task cards CSV: `{report['task_cards_csv']}`",
        f"- Rows: `{totals['rows']}`",
        f"- Checked rows: `{totals['checked_rows']}`",
        f"- Skipped rows: `{totals['skipped_rows']}`",
        f"- Direct asset links: `{totals['asset_links']}`",
        f"- Commons file pages: `{totals['commons_file_pages']}`",
        "- Discovery only: do not merge unreviewed rows into gold.",
        "",
        "## Discovery Status",
        "",
    ]
    for status, count in report["discovery_status_counts"].items():
        lines.append(f"- {status}: `{count}`")
    lines.extend(["", "## Asset Kinds", ""])
    if report["asset_kind_counts"]:
        for kind, count in report["asset_kind_counts"].items():
            lines.append(f"- {kind}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Checked Rows",
            "",
            "| Candidate | Type | Status | Links | Top Links |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in report["checked_rows"]:
        links = row.get("links") or []
        top_links = "<br>".join(
            f"{link['asset_kind']}: {link['url']}" for link in links[:5]
        )
        lines.append(
            f"| `{row['candidate_id']}` | {row['task_card_type']} | "
            f"{row['discovery_status']} | {len(links)} | {top_links} |"
        )
    lines.extend(
        [
            "",
            "## Skipped Rows",
            "",
            "| Candidate | Type | URL |",
            "| --- | --- | --- |",
        ]
    )
    for row in report["skipped_rows"]:
        lines.append(
            f"| `{row.get('candidate_id', '')}` | {row.get('task_card_type', '')} | "
            f"{row.get('final_url') or row.get('source_url') or ''} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This report only discovers candidate URLs from already selected task cards.",
            "- Import work still must download exact payloads, hash them, capture rights/attribution metadata, render, mine, and export review packets.",
            "- Repository rows remain separate clone/asset-selection tasks.",
            "- Browser/manual fallback rows remain blocked until the URL is resolved or replaced.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover source asset links from task cards.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument(
        "--task-cards-csv",
        default="derived/quality/source_import_task_cards_2026-06-16.csv",
    )
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--output-json", default="derived/quality/source_asset_links_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_asset_links_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_asset_links_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        task_cards_csv=args.task_cards_csv,
        date_label=args.date_label,
        timeout=args.timeout,
        no_network=args.no_network,
        retries=args.retries,
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
    write_csv(output_csv, report["flat_link_rows"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
