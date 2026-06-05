"""Run the registry benchmark: smoke-test the references, then score an agent.

    # Layer 1 only — do the curated queries still return data?
    python -m benchmark.run_benchmark --layer smoke

    # Both layers with the harness self-check agent (should score ~1.0):
    python -m benchmark.run_benchmark --agent reference

    # Both layers with a real text-to-SPARQL agent (needs ANTHROPIC_API_KEY):
    python -m benchmark.run_benchmark --agent claude --model claude-sonnet-4-6

Layer 2 only scores questions whose reference query passed layer 1 (so there is a
ground-truth answer to compare against). Use --limit / --kg to run a subset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import Any

from . import dataset, score, smoke


def _list_kgs(records: list[dict]) -> None:
    """Print the KGs that have runnable queries, with counts, and the total."""
    counts = Counter(r["mapped_kgs"][0] for r in dataset.iter_runnable(records))
    if not counts:
        print("No runnable queries in dataset.jsonl — run `fetch_registry` first.")
        return
    print(f"{len(counts)} KGs with runnable queries (use with --kg):\n")
    for kg, n in sorted(counts.items()):
        print(f"  {kg:20} {n}")
    print(f"  {'TOTAL':20} {sum(counts.values())}")


def _select(records: list[dict], kg: str | None, limit: int | None) -> list[dict]:
    runnable = list(dataset.iter_runnable(records))
    if kg:
        runnable = [r for r in runnable if kg in r.get("mapped_kgs", [])]
    if limit:
        runnable = runnable[:limit]
    return runnable


async def _smoke(records: list[dict], args) -> smoke.SmokeReport:
    print(f"\n── Layer 1: smoke-testing {len(records)} reference queries ──")
    report = await smoke.run(records, concurrency=args.concurrency, timeout=args.timeout)
    for r in sorted(report.results, key=lambda r: r.id):
        mark = "✓" if r.ok else "✗"
        detail = f"{r.row_count} rows, {r.elapsed_s}s" if r.ok else r.error
        trunc = " [truncated]" if r.truncated else ""
        print(f"  {mark} {r.id:48} {detail}{trunc}")
    print(
        f"\n  passed {len(report.passed)}/{len(report.results)} "
        f"({len(report.passed) / max(len(report.results), 1):.0%})"
    )
    return report


def _make_agent(args):
    if args.agent == "reference":
        from .agents import ReferenceAgent

        return ReferenceAgent()
    if args.agent == "claude":
        from .agents import ClaudeAgent

        return ClaudeAgent(model=args.model)
    raise SystemExit(f"unknown agent {args.agent!r}")


async def _agent_layer(records: list[dict], args) -> dict[str, Any]:
    cache = dataset.load_results_cache()
    scored = [r for r in records if r["id"] in cache]
    skipped = len(records) - len(scored)
    if not scored:
        raise SystemExit(
            "No reference results cached — run with --layer both (or smoke) first."
        )

    agent = _make_agent(args)
    print(
        f"\n── Layer 2: {agent.name} on {len(scored)} questions "
        f"({skipped} skipped — no reference) ──"
    )

    rows_out: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(args.concurrency)

    async def _one(rec: dict) -> dict[str, Any]:
        async with sem:
            result = await agent.solve(rec)
        ref = cache[rec["id"]]["rows"]
        cmp = score.compare(ref, result.rows)
        return {
            "id": rec["id"],
            "summary": rec["summary"],
            "comparison": cmp.as_dict(),
            "agent_error": result.error,
            "agent_sparql": result.sparql,
        }

    rows_out = await asyncio.gather(*(_one(r) for r in scored))

    n = len(rows_out)
    exact = sum(r["comparison"]["exact"] for r in rows_out)
    mean_f1 = sum(r["comparison"]["f1"] for r in rows_out) / n
    for r in sorted(rows_out, key=lambda r: r["id"]):
        c = r["comparison"]
        mark = "✓" if c["exact"] else ("~" if c["f1"] > 0 else "✗")
        note = f" [{r['agent_error']}]" if r["agent_error"] else ""
        print(
            f"  {mark} {r['id']:48} exact={c['exact']} f1={c['f1']:.2f} "
            f"(ref {c['reference_rows']} / got {c['candidate_rows']}){note}"
        )
    print(
        f"\n  exact-match {exact}/{n} ({exact / n:.0%})   mean F1 {mean_f1:.2f}"
    )
    return {"agent": agent.name, "n": n, "exact": exact, "mean_f1": mean_f1, "rows": rows_out}


async def _main(args) -> None:
    all_records = dataset.load()
    if args.list_kgs:
        _list_kgs(all_records)
        return

    records = _select(all_records, args.kg, args.limit)
    if not records:
        raise SystemExit("No runnable records match the selection.")

    if args.layer in ("smoke", "both"):
        await _smoke(records, args)
    if args.layer in ("agent", "both"):
        summary = await _agent_layer(records, args)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"\n  wrote detailed results to {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--layer", choices=["smoke", "agent", "both"], default="both",
        help="which layer(s) to run (default: both)",
    )
    ap.add_argument(
        "--agent", choices=["reference", "claude"], default="reference",
        help="layer-2 agent (default: reference self-check)",
    )
    ap.add_argument("--model", default="claude-sonnet-4-6", help="model for --agent claude")
    ap.add_argument(
        "--list-kgs", action="store_true",
        help="list KGs that have runnable queries (with counts) and exit",
    )
    ap.add_argument("--kg", help="restrict to one KG shortname")
    ap.add_argument("--limit", type=int, help="cap number of questions")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", help="write detailed layer-2 results to this JSON file")
    args = ap.parse_args()

    # --agent implies running layer 2; default --layer both already covers it.
    if args.layer == "smoke" and args.agent != "reference":
        pass  # agent ignored for smoke-only; harmless
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
