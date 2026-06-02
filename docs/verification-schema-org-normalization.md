# Verification: schema.org http/https normalization

Confirms that `normalize_schema_org` (in `src/mcp_okn/sparql.py`) fixes the
`http://schema.org/` vs `https://schema.org/` mismatch that otherwise causes
queries to silently return no rows.

## Setup

The DREAM-KG (`dreamkg`) named graph stores schema.org terms under the canonical
`http://schema.org/` form. `http://schema.org/Rating` has **3762** instances.

## Result

Running a count of `schema.org/Rating` instances against `dreamkg`, written with
the `https://` form a model commonly produces:

| Path | Rows |
| --- | --- |
| `<https://schema.org/Rating>` sent verbatim (no normalization) | **0** |
| Same query via `run_sparql` (normalization applied) | **3762** |
| Ground truth: `<http://schema.org/Rating>` instance count | 3762 |

The unmodified `https://` query silently matches nothing because it is a distinct
IRI from the `http://` form the data uses. `normalize_schema_org` rewrites it to
`http://` and recovers all 3762 matches.

## Reproduce

```python
import asyncio
from mcp_okn.sparql import run_sparql, named_graph

g = named_graph("dreamkg")
q = f'SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{g}> {{ ?s a <https://schema.org/Rating> }} }}'
print(asyncio.run(run_sparql(q)))  # -> 3762 rows, despite the https:// IRI
```
