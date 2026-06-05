"""Registry-derived benchmark for the mcp-okn federation query path.

Two layers (see ``run_benchmark.py``):

1. Smoke — run each adapted reference query against the federation endpoint and
   keep the ones that return rows; their results are the ground truth.
2. Agent — give the natural-language ``summary`` to an agent driving the mcp-okn
   tools and score its answer against the cached reference results.
"""
