import json
import os
from pathlib import Path


def load_jsonl(path):
    rows = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def image_paths(root, row):
    paths = []
    for image_path in row.get("images", []) or []:
        candidate = Path(image_path)
        paths.append(candidate if candidate.is_absolute() else Path(root) / candidate)
    return paths


def load_eng_bench(
    root=".",
    task="all",
    split="all",
    input_file="eng_bench.jsonl",
    verify_images=False,
):
    """
    Load unified Eng_Bench rows from eng_bench.jsonl.

    Args:
        root: Eng_Bench root directory.
        task: "all", "microtext", or "visualdiff".
        split: "all", "train", "dev", or "test".
        input_file: Unified JSONL filename.
        verify_images: When true, raise FileNotFoundError if any row image is missing.

    Returns:
        A list of row dictionaries with a `resolved_images` list of absolute paths.
    """
    root = Path(root)
    rows = load_jsonl(root / input_file)
    if task != "all":
        rows = [row for row in rows if row.get("task") == task]
    if split != "all":
        rows = [row for row in rows if row.get("split") == split]

    loaded = []
    missing = []
    for row in rows:
        out = dict(row)
        resolved = [str(path) for path in image_paths(root, row)]
        out["resolved_images"] = resolved
        if verify_images:
            missing.extend(path for path in resolved if not Path(path).exists())
        loaded.append(out)
    if missing:
        sample = ", ".join(missing[:5])
        raise FileNotFoundError(f"Missing Eng_Bench image paths: {sample}")
    return loaded

def load_dataset(base_path, split="test"):
    """
    Loads docs, evidence, and questions from the NeurIPS-ready schema.
    """
    data_dir = os.path.join(base_path, "neurips_data")
    
    docs = {}
    docs_file = os.path.join(data_dir, "docs.jsonl")
    if os.path.exists(docs_file):
        with open(docs_file, "r", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                docs[f"{d['doc_id']}__{d['version_id']}"] = d

    evidence = {}
    ev_file = os.path.join(data_dir, "evidence.jsonl")
    if os.path.exists(ev_file):
        with open(ev_file, "r", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                evidence[e['evidence_id']] = e

    questions = []
    qs_file = os.path.join(data_dir, "questions.jsonl")
    if os.path.exists(qs_file):
        with open(qs_file, "r", encoding="utf-8") as f:
            for line in f:
                q = json.loads(line)
                if q['metadata'].get("split") == split:
                    questions.append(q)

    return {
        "docs": docs,
        "evidence": evidence,
        "questions": questions
    }
