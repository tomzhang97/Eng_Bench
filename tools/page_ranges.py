"""Shared PDF page selection helpers.

Page specs are 1-based for the CLI, while returned page indexes are 0-based
so rendered filenames and annotation page indexes stay aligned with the source
PDF.
"""
from __future__ import annotations


def parse_page_selection(spec: str | None, page_count: int) -> list[int]:
    """Parse a comma-separated page spec into sorted 0-based page indexes."""
    if page_count < 0:
        raise ValueError("page_count must be non-negative")
    if spec is None or not spec.strip() or spec.strip().lower() == "all":
        return list(range(page_count))

    selected: list[int] = []
    seen: set[int] = set()
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left)
            end = int(right)
        else:
            start = end = int(token)
        if start < 1 or end < 1:
            raise ValueError(f"Page specs are 1-based; got {token!r}")
        if end < start:
            raise ValueError(f"Invalid descending page range {token!r}")
        if end > page_count:
            raise ValueError(f"Page range {token!r} exceeds document page count {page_count}")
        for page_number in range(start, end + 1):
            page_index = page_number - 1
            if page_index in seen:
                continue
            seen.add(page_index)
            selected.append(page_index)

    if not selected:
        raise ValueError("Page selection did not contain any pages")
    return selected
