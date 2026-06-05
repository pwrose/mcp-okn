"""Layer 1 — run the adapted reference queries against the federation endpoint.

A reference query "passes" when it executes without error and returns at least
one row. Passing queries' results become the ground truth that layer 2 (the
agent) is scored against, so they're cached to ``reference_results.json``.

This is the same network path the mcp-okn server uses (`mcp_okn.sparql`), so a
pass here means the curated query still works end-to-end on the live data.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from mcp_okn import sparql

from . import dataset

#: Cap on reference rows cached per query. A query returning more is still a
#: "pass", but its results are flagged truncated and excluded from strict
#: (exact) layer-2 scoring, since we can't compare against a partial reference.
MAX_REFERENCE_ROWS = 5000


@dataclass
class SmokeResult:
    id: str
    ok: bool
    row_count: int = 0
    elapsed_s: float = 0.0
    error: str | None = None
    truncated: bool = False


@dataclass
class SmokeReport:
    results: list[SmokeResult] = field(default_factory=list)

    @property
    def passed(self) -> list[SmokeResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[SmokeResult]:
        return [r for r in self.results if not r.ok]


async def _run_one(
    record: dict[str, Any], sem: asyncio.Semaphore, timeout: float
) -> tuple[SmokeResult, list[dict] | None]:
    qid = record["id"]
    async with sem:
        start = time.perf_counter()
        try:
            out = await sparql.run_sparql(record["federated"], fmt="json", timeout=timeout)
        except Exception as e:  # SparqlError, timeouts, transport errors
            return SmokeResult(qid, ok=False, error=str(e).splitlines()[0][:300]), None
        elapsed = time.perf_counter() - start

    rows = out.get("rows", [])
    total = out.get("row_count", len(rows))
    truncated = total > MAX_REFERENCE_ROWS
    result = SmokeResult(
        id=qid,
        ok=total > 0,
        row_count=total,
        elapsed_s=round(elapsed, 2),
        error=None if total > 0 else "empty result",
        truncated=truncated,
    )
    cached = None if truncated else rows[:MAX_REFERENCE_ROWS]
    return result, cached


async def run(
    records: list[dict[str, Any]],
    concurrency: int = 4,
    timeout: float = 120.0,
    update_cache: bool = True,
) -> SmokeReport:
    """Run the smoke layer over ``records`` (already filtered to runnable)."""
    sem = asyncio.Semaphore(concurrency)
    pairs = await asyncio.gather(*(_run_one(r, sem, timeout) for r in records))

    report = SmokeReport(results=[p[0] for p in pairs])
    if update_cache:
        cache = dataset.load_results_cache()
        for (result, rows) in pairs:
            if result.ok and rows is not None:
                cache[result.id] = {
                    "rows": rows,
                    "row_count": result.row_count,
                }
        dataset.save_results_cache(cache)
    return report
