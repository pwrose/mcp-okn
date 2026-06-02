# mcp-okn

An MCP server for querying the **FRINK federated SPARQL endpoint**
(`https://frink.apps.renci.org/federation/sparql`) over the
[Proto-OKN](https://www.proto-okn.net/) knowledge graphs.

It lets an LLM discover which knowledge graphs are relevant (from the
[okn-registry](https://github.com/frink-okn/okn-registry) descriptions), then run
SPARQL queries scoped to one or more named graphs of the form
`https://purl.org/okn/frink/kg/{shortname}`.

> **Only the federation endpoint is used.** The per-KG SPARQL/TPF endpoints in
> the registry (Apache Jena instances) are intentionally not exposed — they time
> out or run out of memory on complex queries.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_kgs` | List all KGs with `shortname`, `title`, `description`, `homepage`, and `named_graph`. Served from a bundled snapshot for instant cold start. |
| `describe_kg(shortname)` | Full registry doc (frontmatter + prose) for one KG, for deeper context. |
| `get_schema(shortname, compact=True)` | Schema for one KG — classes, predicates, edge properties (with reification query templates), and node properties. Uses curated metadata when available, else probes the endpoint for distinct classes/predicates. Call **before** writing a query. |
| `visualize_schema(shortname)` | Deterministic Mermaid `classDiagram` of a KG's schema, built server-side from `get_schema` — class boxes, labeled edges, and edge-property predicates as intermediary classes with typed fields (colored distinctly from node classes). Returns `mermaid_block` (already wrapped in a ` ```mermaid ` fence) — output it **verbatim**; don't redraw it as SVG/an image. Rendered examples: [spoke-genelab](docs/spoke-genelab-schema.png), [dreamkg](docs/dreamkg-schema.png), [rdkg](docs/rdkg-schema.png) ([details](docs/verification-visualize-schema.md)). |
| `sparql_query(query, format="json", exploratory=False)` | Run a SPARQL query on the federation endpoint. Substantive results are logged for the transcript unless `exploratory=True`. `https://schema.org/` URIs are normalized to the canonical `http://schema.org/` form the KGs store, so either scheme matches. |
| `expand_ontology_term(term, relation="subClassOf", direction="descendants", include_self=True, limit=1000)` | Expand an ontology term to its full subtree/closure via the `ubergraph` graph. |
| `reset_query_log()` | Clear the session query log. Call at the **start** of an analysis to scope a transcript. |
| `get_query_log()` | Return the queries logged so far this session (only those that returned rows and weren't exploratory). |
| `create_chat_transcript(model, exchanges, ...)` | Emit a reproducible markdown (or JSON) record of a session — prompts, answers, the verbatim queries + results that produced findings, and any `visualize_schema` diagrams. Call at the **end** of an analysis. |

## Setup

```bash
uv sync
uv run mcp-okn   # starts the server on stdio
```

## Register with Claude Code

```bash
claude mcp add mcp-okn -- uv --directory /path/to/mcp-okn run mcp-okn
```

Or add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mcp-okn": {
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-okn", "run", "mcp-okn"]
    }
  }
}
```

Replace `/path/to/mcp-okn` with the absolute path to your checkout.

## Usage

A typical session walks the tools in order — **discover → inspect → query**.
Once the server is registered, just ask in natural language; the model drives
the tools. For example:

> *"Which UniProt diseases in ProKN have a MONDO cross-reference?"*

The model would:

1. **`list_kgs()`** → find `prokn` (the Protein Knowledge Network).
2. **`get_schema("prokn")`** → confirm it has a `up:Disease` class and that
   diseases carry `rdfs:seeAlso` cross-references (34 classes, 232 predicates).
3. **`sparql_query(...)`** → run the query scoped to the `prokn` named graph:

   ```sparql
   PREFIX up:   <http://purl.uniprot.org/core/>
   PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
   SELECT DISTINCT ?disease ?mondo WHERE {
     GRAPH <https://purl.org/okn/frink/kg/prokn> {
       ?d a up:Disease ; rdfs:label ?disease ; rdfs:seeAlso ?mondo .
     }
   } LIMIT 3
   ```

   ```json
   {
     "vars": ["disease", "mondo"],
     "row_count": 3,
     "rows": [
       {"disease": "16p13.2 microdeletion syndrome",
        "mondo": "http://purl.obolibrary.org/obo/MONDO_0014805"},
       {"disease": "16p13.2 microdeletion syndrome",
        "mondo": "http://www.orpha.net/ORDO/Orphanet_643538"},
       {"disease": "16p13.2 microdeletion syndrome",
        "mondo": "https://www.omim.org/entry/616863"}
     ]
   }
   ```

To call the tools directly (e.g. from a script) without an MCP client:

```python
import asyncio
from mcp_okn import schema
from mcp_okn.sparql import run_sparql

async def main():
    print(await schema.get_schema("prokn"))          # inspect the schema
    result = await run_sparql("""
        PREFIX up:   <http://purl.uniprot.org/core/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT DISTINCT ?disease ?mondo WHERE {
          GRAPH <https://purl.org/okn/frink/kg/prokn> {
            ?d a up:Disease ; rdfs:label ?disease ; rdfs:seeAlso ?mondo .
          }
        } LIMIT 3""")
    print(result["row_count"], "rows")

asyncio.run(main())
```

## Example query

Scope each KG with its named graph (a single query may span several):

```sparql
PREFIX up:   <http://purl.uniprot.org/core/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?mondo ?label WHERE {
  GRAPH <https://purl.org/okn/frink/kg/prokn> {
    ?d a up:Disease ; rdfs:seeAlso ?mondo .
  }
}
```

Use the `ubergraph` graph to expand ontology terms, e.g. all subclasses of a
MONDO disease:

```sparql
GRAPH <https://purl.org/okn/frink/kg/ubergraph> {
  ?mondo rdfs:subClassOf+ <http://purl.obolibrary.org/obo/MONDO_0003847> .
}
```

## Reproducible transcripts

Every `sparql_query` / `expand_ontology_term` call that returns rows is logged
in-memory for the lifetime of the server process, so a session can be replayed
and audited without the model re-supplying queries from memory.

- Queries that **error** or return **no rows** are never logged.
- Pass `exploratory=True` to `sparql_query` to keep schema-probing or
  trial-and-error queries out of the log.
- Call `reset_query_log` at the **start** of an analysis to scope the log.
- Call `create_chat_transcript` at the **end** to render a markdown (or JSON)
  document: session provenance (date, model, endpoint), the knowledge graphs
  used, the conversation (prompts + your answers), and every logged query
  verbatim. Up to `MAX_LOGGED_ROWS` (1000) rows are stored per query; the true
  row count is always preserved.
- By default only the **final** logged query's result rows are rendered;
  intermediate queries show their SPARQL and row count but omit the table, to
  keep the transcript focused on the queries that produced the findings. Pass
  `include_intermediate_rows=True` to render full results for every query.
  (Queries attached inline to an exchange via `queries` always render in full.)
- `visualize_schema` diagrams are logged too, and rendered in a **Schema
  visualizations** section (each in a ` ```mermaid ` block) — so a "visualize
  schema" turn shows up in the transcript without re-supplying the diagram. Pass
  `include_visualizations=False` to omit them, or attach a `mermaid` field to an
  exchange to place a diagram inline with that turn.

## Development

```bash
uv run python -m pytest       # unit tests (offline)
# live smoke test:
uv run python -c "import asyncio; from mcp_okn.sparql import run_sparql; \
print(asyncio.run(run_sparql('SELECT ?s WHERE { ?s ?p ?o } LIMIT 3')))"
```

### Verification notes

Reproducible checks of behaviors that aren't covered by the offline unit tests:

- [schema.org http/https normalization](docs/verification-schema-org-normalization.md)
  — an `https://schema.org/` query returns 0 rows verbatim but the full result
  set once normalized.
- [visualize_schema rendering](docs/verification-visualize-schema.md) — the
  generated Mermaid renders cleanly as a class diagram via `mermaid-cli` across
  all three schema paths (curated, class-only, probe fallback), and survives the
  `create_chat_transcript` round-trip.

## KG snapshot

`list_kgs` serves a static snapshot bundled at `src/mcp_okn/data/kgs.json` (~41
KGs), so the first call returns instantly without fetching the individual
registry files. The live registry is only contacted when the snapshot is missing
(or when an internal `refresh=True` is passed). To refresh the snapshot after the
registry changes:

```bash
uv run python scripts/refresh_snapshot.py
```

KGs that are in the registry but not actually loaded under their expected
federation named graph (currently just `semopenalex`) are filtered out, so
`list_kgs` only returns graphs that are queryable.

## Notes

- The federation endpoint is QLever-backed and runs on a read-only filesystem.
  Queries needing a large external sort over a full-graph scan (unbounded
  `ORDER BY`/`GROUP BY`/`DISTINCT`) may fail; add a `LIMIT` or scope the pattern.
