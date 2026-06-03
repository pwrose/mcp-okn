"""Re-run every SPARQL query saved in a transcript and check it reproduces.

The transcript records each non-exploratory query verbatim (the ```sparql blocks
in the markdown, or the ``query_log`` in the JSON format), which is what makes a
session reproducible. This script replays those queries, in order, against the
FRINK federation endpoint and reports each query's row count against the count
recorded in the transcript.

Usage:

    uv run python scripts/replay_transcript.py path/to/transcript.md
    uv run python scripts/replay_transcript.py path/to/transcript.json

Exits non-zero if any query errors or its row count differs from the recorded
one (a difference can mean the underlying KG changed since the transcript was
made — FRINK is a live endpoint).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from mcp_okn.sparql import SparqlError, run_sparql

#: (sparql, recorded_row_count_or_None) pairs in execution order.
Query = tuple[str, "int | None"]


def queries_from_json(doc: dict) -> list[Query]:
    """Pull (sparql, row_count) from a `format="json"` transcript's query_log."""
    return [
        (e["sparql"], e.get("row_count"))
        for e in doc.get("query_log", [])
        if e.get("sparql")
    ]


def queries_from_markdown(md: str) -> list[Query]:
    """Pull (sparql, recorded_count) from the ```sparql blocks of a markdown
    transcript. The recorded count is read from the ``_N row(s)_`` marker that
    follows a block (present for appendix queries); ``None`` when absent."""
    queries: list[Query] = []
    for m in re.finditer(r"```sparql\n(.*?)\n```", md, re.S):
        tail = md[m.end() : m.end() + 200]
        count = re.search(r"_(\d+) row\(s\)", tail)
        queries.append((m.group(1).strip(), int(count.group(1)) if count else None))
    return queries


def load_queries(path: Path) -> list[Query]:
    """Read queries from a transcript file (JSON query_log or markdown blocks)."""
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return queries_from_markdown(text)
    if isinstance(doc, dict) and "query_log" in doc:
        return queries_from_json(doc)
    return queries_from_markdown(text)


async def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: replay_transcript.py <transcript.md|transcript.json>")
    path = Path(sys.argv[1])
    if not path.is_file():
        sys.exit(f"no such file: {path}")

    queries = load_queries(path)
    if not queries:
        sys.exit(f"no SPARQL queries found in {path}")

    print(f"Replaying {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} "
          f"from {path}\n")
    mismatches = 0
    for i, (sparql, recorded) in enumerate(queries, start=1):
        try:
            result = await run_sparql(sparql)
        except SparqlError as exc:
            mismatches += 1
            print(f"Query {i}: ERROR — {str(exc).splitlines()[0]}")
            continue
        actual = result["row_count"]
        if recorded is None:
            print(f"Query {i}: {actual} rows (no recorded count to compare)")
        elif actual == recorded:
            print(f"Query {i}: {actual} rows  ✓ matches recorded")
        else:
            mismatches += 1
            print(f"Query {i}: {actual} rows  ✗ recorded {recorded}")

    print()
    if mismatches:
        sys.exit(f"{mismatches} quer{'y' if mismatches == 1 else 'ies'} "
                 "errored or did not match the recorded count")
    print("All queries reproduced the recorded row counts.")


if __name__ == "__main__":
    asyncio.run(main())
