"""Opt-in live smoke gate for the benchmark (layer 1).

Hits the real FRINK federation endpoint, so it's skipped unless you opt in:

    RUN_BENCHMARK_SMOKE=1 pytest tests/test_benchmark_smoke.py

It runs a small slice of the adapted reference queries and asserts they still
return rows — a regression alarm for the curated corpus / live data. Set
BENCHMARK_SMOKE_LIMIT to change how many queries are checked (default 6).
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import dataset, smoke  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BENCHMARK_SMOKE") != "1",
    reason="set RUN_BENCHMARK_SMOKE=1 to run the live federation smoke test",
)


async def test_reference_queries_still_return_rows():
    limit = int(os.environ.get("BENCHMARK_SMOKE_LIMIT", "6"))
    runnable = list(dataset.iter_runnable(dataset.load()))[:limit]
    assert runnable, "no runnable records — run fetch_registry first"

    report = await smoke.run(runnable, concurrency=4, update_cache=False)
    failures = [(r.id, r.error) for r in report.failed]
    # Allow nothing to silently vanish: every adapted reference must still work.
    assert not failures, f"reference queries returned no rows: {failures}"
