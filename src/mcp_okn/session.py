"""In-memory log of SPARQL queries executed during a session.

Every query run through the server is appended here so `create_chat_transcript`
can render a faithful, ground-truth record of what actually hit the endpoint —
rather than relying on the model to re-supply queries from memory.

The log lives for the lifetime of the server process. Call `reset()` (exposed as
the `reset_query_log` tool) at the start of a new analysis to scope a transcript
to just that session.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: Cap on rows stored per query, so a huge result set can't grow the log without
#: bound. The true row count is always kept; only the stored sample is capped.
MAX_LOGGED_ROWS = 1000

_GRAPH_RE = re.compile(r"GRAPH\s*<https://purl\.org/okn/frink/kg/([^>]+)>")

_log: list[dict[str, Any]] = []
_visualizations: list[dict[str, Any]] = []
_last_transcript: str | None = None


def graphs_in(query: str) -> list[str]:
    """Return the KG shortnames referenced via ``GRAPH <.../kg/{name}>``, in order."""
    seen: list[str] = []
    for name in _GRAPH_RE.findall(query):
        if name not in seen:
            seen.append(name)
    return seen


def _result_row_count(result: Any) -> int:
    """Number of rows a `run_sparql` result holds (0 if empty/unknown)."""
    if not isinstance(result, dict):
        return 0
    if "rows" in result:
        return result.get("row_count") or len(result.get("rows") or [])
    if "text" in result:
        # csv/tsv: a header line plus at least one data line means rows.
        lines = [ln for ln in str(result.get("text") or "").splitlines() if ln.strip()]
        return max(len(lines) - 1, 0)
    return 0


def record(
    query: str, fmt: str, result: Any = None, error: str | None = None
) -> bool:
    """Append one executed query to the session log if it returned results.

    Queries that errored or returned zero rows are NOT logged — the transcript
    is meant to record only the queries that produced findings. Exploratory
    queries are skipped at the call site (they are never passed here).

    Args:
        query: The exact SPARQL text that was sent.
        fmt: The requested result format (``json``/``csv``/``tsv``).
        result: The value returned by ``run_sparql`` on success.
        error: The error message if the query failed.

    Returns:
        True if the query was logged, False if it was skipped (error/empty).
    """
    if error is not None or _result_row_count(result) == 0:
        return False

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sparql": query,
        "graphs": graphs_in(query),
        "format": fmt,
        "row_count": None,
        "results": None,
    }
    if isinstance(result, dict) and "rows" in result:
        rows = result.get("rows") or []
        total = result.get("row_count", len(rows))
        sample = rows[:MAX_LOGGED_ROWS]
        entry["row_count"] = total
        entry["results"] = {
            "vars": result.get("vars", []),
            "rows": sample,
            "row_count": total,
            "truncated": len(rows) > len(sample),
        }
    elif isinstance(result, dict) and "text" in result:
        entry["row_count"] = _result_row_count(result)
        entry["results"] = {
            "format": result.get("format", fmt),
            "text": result.get("text", ""),
        }
    _log.append(entry)
    return True


def entries() -> list[dict[str, Any]]:
    """Return a shallow copy of the logged queries, in execution order."""
    return list(_log)


def record_visualization(shortname: str, mermaid: str) -> None:
    """Record a schema visualization (Mermaid diagram) for the transcript.

    Like queries, diagrams are logged automatically as they are produced so
    `create_chat_transcript` can render them without the model re-supplying the
    diagram. Re-visualizing the same KG replaces its earlier diagram (keeping
    the original position) so only the latest diagram per KG is kept.
    """
    if not mermaid:
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shortname": shortname,
        "mermaid": mermaid,
    }
    for i, existing in enumerate(_visualizations):
        if existing.get("shortname") == shortname:
            _visualizations[i] = entry
            return
    _visualizations.append(entry)


def visualizations() -> list[dict[str, Any]]:
    """Return a shallow copy of the logged schema visualizations, in order."""
    return list(_visualizations)


def set_last_transcript(markdown: str) -> None:
    """Store the most recently rendered transcript markdown.

    Exposed read-only via the ``transcript://session/latest`` MCP resource so a
    client can fetch/save the document directly, independent of how (or whether)
    the model re-emits it.
    """
    global _last_transcript
    _last_transcript = markdown


def last_transcript() -> str | None:
    """Return the most recently rendered transcript markdown, or None."""
    return _last_transcript


def reset() -> int:
    """Clear the session log (queries, visualizations, last transcript).

    Returns the number of logged queries removed.
    """
    global _last_transcript
    n = len(_log)
    _log.clear()
    _visualizations.clear()
    _last_transcript = None
    return n
