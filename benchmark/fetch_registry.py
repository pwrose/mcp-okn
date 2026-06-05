"""Build ``dataset.jsonl`` from the frink-okn/okn-registry query files.

Walks ``docs/registry/queries/*/`` in the registry, parses each ``.rq`` file,
maps its tags to the KGs the mcp-okn server actually serves, and GRAPH-wraps the
single-KG SELECT/ASK queries for the federation endpoint. Multi-KG, pre-scoped,
or non-SELECT queries are kept too but flagged ``manual``; queries whose tags map
to no served KG are flagged ``skip``.

    python -m benchmark.fetch_registry            # refresh dataset.jsonl
    python -m benchmark.fetch_registry --report   # also print a coverage table

No query is executed here, and the registry is pulled as a single repo tarball
(via codeload, which isn't subject to the GitHub API rate limit).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import tarfile
from collections import Counter

import httpx

from mcp_okn import registry

from . import adapt, dataset

_TARBALL = "https://github.com/frink-okn/okn-registry/archive/refs/heads/main.tar.gz"
# Match ``<repo>-<sha>/docs/registry/queries/<dir>/<name>.rq`` inside the tarball.
_QUERY_RE = re.compile(r"^[^/]+/docs/registry/queries/([^/]+)/([^/]+\.rq)$")

# Registry tag → served shortname overrides, for the few that don't match
# verbatim. Extend as `--report` surfaces unmapped tags. (The registry's
# ``federation`` tag is intentionally absent: those are cross-KG queries with no
# single named graph to scope to, so they fall through to ``skip``.)
TAG_MAP: dict[str, str] = {
    "fio-kg": "fiokg",
}


async def _served_shortnames() -> set[str]:
    kgs = await registry.list_kgs()
    return {k["shortname"] for k in kgs}


async def _download_tarball() -> bytes:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(_TARBALL)
        resp.raise_for_status()
        return resp.content


async def build() -> list[dict]:
    served = await _served_shortnames()
    raw = await _download_tarball()

    records: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            m = _QUERY_RE.match(member.name)
            if not (member.isfile() and m):
                continue
            d, filename = m.group(1), m.group(2)
            stem = filename[:-3]
            text = tar.extractfile(member).read().decode("utf-8")
            summary, tags, sparql = adapt.parse_rq(text)
            pq = adapt.adapt(summary, tags, sparql, served, TAG_MAP)
            records.append(
                {
                    "id": f"{d}/{stem}",
                    "source_path": f"docs/registry/queries/{d}/{filename}",
                    "dir": d,
                    "summary": pq.summary,
                    "tags": pq.tags,
                    "mapped_kgs": pq.extra.get("mapped_kgs", []),
                    "sparql": pq.sparql,
                    "federated": pq.federated,
                    "adaptation": pq.adaptation,
                    "adaptation_note": pq.adaptation_note,
                }
            )
    return records


def _report(records: list[dict]) -> None:
    by_status = Counter(r["adaptation"] for r in records)
    print(f"\n{len(records)} registry queries:")
    for status in ("auto", "manual", "incompatible", "skip"):
        print(f"  {status:12} {by_status.get(status, 0)}")

    auto_by_kg = Counter(
        r["mapped_kgs"][0] for r in records if r["adaptation"] == "auto"
    )
    print("\nrunnable (auto) per KG:")
    for kg, n in sorted(auto_by_kg.items()):
        print(f"  {kg:24} {n}")

    skipped_tags = Counter(
        t for r in records if r["adaptation"] == "skip" for t in r["tags"]
    )
    if skipped_tags:
        print("\nunmapped/excluded tags (add to TAG_MAP if these should map):")
        for tag, n in sorted(skipped_tags.items()):
            print(f"  {tag:24} {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="print a coverage table")
    args = ap.parse_args()

    records = asyncio.run(build())
    path = dataset.write(records)
    print(f"wrote {len(records)} records to {path}")
    if args.report:
        _report(records)


if __name__ == "__main__":
    main()
