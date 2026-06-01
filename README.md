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
| `list_kgs` | List all KGs with `shortname`, `title`, `description`, and `named_graph`. Served from a bundled snapshot for instant cold start. |
| `describe_kg(shortname)` | Full registry doc for one KG, for deeper context. |
| `sparql_query(query, format="json")` | Run a SPARQL query on the federation endpoint. |
| `expand_ontology_term(term, relation, direction)` | Expand an ontology term via the `ubergraph` graph. |

## Setup

```bash
uv sync
uv run mcp-okn   # starts the server on stdio
```

## Register with Claude Code

```bash
claude mcp add mcp-okn -- uv --directory /Users/peter/work/claude_ex1 run mcp-okn
```

Or add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mcp-okn": {
      "command": "uv",
      "args": ["--directory", "/Users/peter/work/claude_ex1", "run", "mcp-okn"]
    }
  }
}
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

## Development

```bash
uv run pytest                 # unit tests (offline)
# live smoke test:
uv run python -c "import asyncio; from mcp_okn.sparql import run_sparql; \
print(asyncio.run(run_sparql('SELECT ?s WHERE { ?s ?p ?o } LIMIT 3')))"
```

## KG snapshot

`list_kgs` serves a static snapshot bundled at `src/mcp_okn/data/kgs.json`, so
the first call returns instantly without fetching ~42 registry files. The live
registry is only contacted when the snapshot is missing. To refresh the snapshot
after the registry changes:

```bash
uv run python scripts/refresh_snapshot.py
```

## Notes

- The federation endpoint is QLever-backed and runs on a read-only filesystem.
  Queries needing a large external sort over a full-graph scan (unbounded
  `ORDER BY`/`GROUP BY`/`DISTINCT`) may fail; add a `LIMIT` or scope the pattern.
