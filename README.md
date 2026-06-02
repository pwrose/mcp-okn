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
| `sparql_query(query, format="json", exploratory=False)` | Run a SPARQL query on the federation endpoint. Substantive results are logged for the transcript unless `exploratory=True`. |
| `expand_ontology_term(term, relation="subClassOf", direction="descendants", include_self=True, limit=1000)` | Expand an ontology term to its full subtree/closure via the `ubergraph` graph. |
| `reset_query_log()` | Clear the session query log. Call at the **start** of an analysis to scope a transcript. |
| `get_query_log()` | Return the queries logged so far this session (only those that returned rows and weren't exploratory). |
| `create_chat_transcript(model, exchanges, ...)` | Emit a reproducible markdown (or JSON) record of a session — prompts, answers, and the verbatim queries + results that produced findings. Call at the **end** of an analysis. |

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

## Development

```bash
uv run python -m pytest       # unit tests (offline)
# live smoke test:
uv run python -c "import asyncio; from mcp_okn.sparql import run_sparql; \
print(asyncio.run(run_sparql('SELECT ?s WHERE { ?s ?p ?o } LIMIT 3')))"
```

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
