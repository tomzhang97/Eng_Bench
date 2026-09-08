"""Shared future-auditor instructions; issued workbooks remain immutable."""
import json
from pathlib import Path


def category_guide() -> str:
    rubric = json.loads(Path(__file__).with_name("auditor_microtext_rubric.json").read_text(encoding="utf-8"))
    return rubric["target_rule"] + "\n" + "\n".join(
        f"{category}: {meaning}" for category, meaning in rubric["categories"].items())
