"""Load and iterate the extracted benchmark dataset (``dataset.jsonl``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

DATASET_PATH = Path(__file__).resolve().parent / "dataset.jsonl"
RESULTS_CACHE_PATH = Path(__file__).resolve().parent / "reference_results.json"


def load(path: Path | None = None) -> list[dict[str, Any]]:
    """Return every dataset record (one per registry ``.rq`` file)."""
    path = path or DATASET_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m benchmark.fetch_registry` first."
        )
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def iter_runnable(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield only records that adapted automatically (have a federated query)."""
    for r in records:
        if r.get("adaptation") == "auto" and r.get("federated"):
            yield r


def write(records: list[dict[str, Any]], path: Path | None = None) -> Path:
    """Write records as JSONL, sorted by id for stable diffs."""
    path = path or DATASET_PATH
    records = sorted(records, key=lambda r: r["id"])
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def load_results_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or RESULTS_CACHE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_results_cache(cache: dict[str, Any], path: Path | None = None) -> Path:
    path = path or RESULTS_CACHE_PATH
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
