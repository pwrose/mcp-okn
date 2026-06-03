"""FastMCP server exposing the FRINK federated SPARQL endpoint.

All queries go to the single federation endpoint
(https://frink.apps.renci.org/federation/sparql) and are scoped to named graphs
of the form https://purl.org/okn/frink/kg/{shortname}. The per-KG endpoints in
the registry are never used.
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import registry, schema, session
from .sparql import (
    FEDERATION_ENDPOINT,
    SparqlError,
    named_graph,
    normalize_schema_org,
    run_sparql,
)

INSTRUCTIONS = """\
Query the FRINK federated SPARQL endpoint over the Proto-OKN knowledge graphs.

Workflow:
1. Call `list_kgs` to see the available knowledge graphs and their descriptions,
   then choose which one(s) are relevant to the question.
2. Optionally call `describe_kg` for richer prose context on a chosen KG.
3. Call `get_schema` for each chosen KG to learn its classes, predicates, and
   property names BEFORE writing SPARQL — each KG has its own schema. This also
   reveals which IDENTIFIER SCHEME / ontology the KG actually stores (e.g. DOID
   vs MONDO, NCBI Gene vs Ensembl vs symbol). Do not assume — probe first.
4. Call `sparql_query` with a SPARQL query that scopes each KG with
   `GRAPH <https://purl.org/okn/frink/kg/{shortname}> { ... }`. A single query
   may span multiple named graphs (that is the point of federation).

TRANSCRIPTS: Substantive `sparql_query`/`expand_ontology_term` calls are logged
automatically — but queries that error or return no rows are NOT logged, and you
can pass `exploratory=True` to `sparql_query` to keep schema-probing or
trial-and-error queries out of the record. Call `reset_query_log` at the START of
an analysis to scope the log, and `create_chat_transcript` at the END to emit a
reproducible markdown record (prompts, answers, and the verbatim queries +
results that actually produced findings). SAVE the full transcript markdown the
tool returns — verbatim and complete — as a downloadable `.md` file via your
file-creation capability (the same behavior as "save the transcript as a file":
the `.md` appears in the preview panel, downloadable from the chat); a Markdown
ARTIFACT / document does the same. A sentence describing it is not enough. Only
if you cannot write a file, output the complete markdown in a fenced ```markdown
block. NEVER say the transcript is "ready", "in the preview panel", or "saved"
unless you actually wrote the file or emitted its full content — do not fabricate
a preview. (The rendered markdown is also published as the MCP resource
`transcript://session/latest`, which a client can fetch/save directly even for
remote servers.)

ONTOLOGY EXPANSION (read this before answering "all X under category Y" questions):
Whenever a question covers a CATEGORY of ontology terms — e.g. "all
cardiovascular diseases", "any kind of asthma", "diseases that are subtypes of
X", "chemicals in class Y" — you MUST expand the category using `ubergraph`'s
PRECOMPUTED transitive closure with a property path, in ONE query:

    ?descendant rdfs:subClassOf* <parent-term-IRI>   # category + all subtypes

Ubergraph already materialises every inferred edge, so this returns the complete
subtree in a single step. Use `*` (reflexive) to INCLUDE the category term
itself — usually what you want for "all X" questions; use `+` if you want strict
subtypes only and must exclude the term itself.

Do NOT, under any circumstances:
  - fetch the ontology tree level by level / walk children iteratively;
  - retrieve the hierarchy "separately" and then filter in your head;
  - enumerate subtypes by hand or guess them.

PREFER a single FEDERATED query that expands the category in the `ubergraph`
graph and joins the expanded terms against the target KG in the same query, e.g.
"find all cardiovascular diseases (MONDO:0004995) mentioned in <kg>":

    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?disease ?label WHERE {
      GRAPH <https://purl.org/okn/frink/kg/ubergraph> {
        ?disease rdfs:subClassOf* <http://purl.obolibrary.org/obo/MONDO_0004995> .
        OPTIONAL { ?disease rdfs:label ?label }
      }
      GRAPH <https://purl.org/okn/frink/kg/SOME_TARGET_KG> {
        ?record some:predicate ?disease .
      }
    }

If you only need the list of terms in the category (no join), call
`expand_ontology_term` instead of writing the query yourself.

CHOOSE THE RIGHT ONTOLOGY (do this before picking a parent-term IRI):
A KG may reference the same domain through more than one ontology — e.g. disease
via BOTH DOID and MONDO, or genes via NCBI Gene, Ensembl, AND symbol. Do NOT
assume which one a KG uses or default to the first that comes to mind: call
`get_schema(target_kg)` and/or run a small `exploratory=True` `sparql_query` that
samples the actual predicate/object IRIs in that graph, and confirm which
ontology is actually stored. If several are present, pick the one with the right
coverage for the question. Anchor the `subClassOf*` expansion on a term IRI from
the ontology the KG ACTUALLY uses — expanding MONDO when the KG only links DOID
(or vice versa) silently returns no rows.

SCHEMA.ORG URIs: `https://schema.org/...` in a query is rewritten to
`http://schema.org/...` automatically before it runs — the KGs store the
canonical `http://` form, and the two are distinct IRIs to the engine. You may
write either scheme; both match.

SCHEMA VISUALIZATION: `visualize_schema` returns a ready-made Mermaid diagram,
pre-wrapped in a fenced block as `mermaid_block`. Output that `mermaid_block`
VERBATIM and nothing else. Do NOT redraw it as SVG/PNG/HTML/an image/an artifact
or a hand-built diagram — Mermaid clients render the fenced block natively, and
producing your own graphic yields a messy, incorrect picture.

IMPORTANT: Only the federation endpoint is used. Do not attempt to use the
per-KG SPARQL endpoints — they are not exposed and time out on complex queries.
"""

mcp = FastMCP("mcp-okn", instructions=INSTRUCTIONS)


@mcp.tool()
async def list_kgs() -> list[dict[str, Any]]:
    """List all Proto-OKN knowledge graphs available on the FRINK federation.

    Returns one entry per KG with its `shortname`, `title`, `description`,
    `homepage`, and the `named_graph` URI to use inside
    `GRAPH <...> { ... }` blocks. Use the descriptions to decide which graph(s)
    to query.
    """
    return await registry.list_kgs()


@mcp.tool()
async def describe_kg(shortname: str) -> str:
    """Return the full registry documentation for one KG.

    Args:
        shortname: The KG shortname (e.g. `prokn`, `sawgraph`, `ubergraph`),
            as returned by `list_kgs`.

    Returns the registry markdown (title, description, and prose) for deeper
    context before writing a query.
    """
    return await registry.fetch_kg_doc(shortname)


@mcp.tool()
async def get_schema(shortname: str, compact: bool = True) -> dict[str, Any]:
    """Get the schema (classes, predicates, edge/node properties) for one KG.

    Call this BEFORE writing a `sparql_query` for a KG, to learn its specific
    entity types, predicates, and property names. Prefers curated metadata and
    falls back to probing the federation endpoint for the distinct classes and
    predicates used in the KG's named graph.

    Args:
        shortname: The KG shortname (e.g. `prokn`, `sawgraph`), as returned by
            `list_kgs`.
        compact: If True (default), return the compact schema. Set False to also
            include an `edge_property_summary` highlighting relationships that
            carry edge properties (with ready-to-use reification query templates).

    Returns:
        `{"shortname": ..., "schema": {"classes", "predicates",
        "edge_properties", "node_properties"}}`. Each of `classes`/`predicates`/
        `node_properties` is a `{"columns", "data", "count"}` table;
        `edge_properties` maps relationship names to their properties and a
        `query_template` showing the RDF reification pattern to query them.
    """
    return await schema.get_schema(shortname, compact=compact)


@mcp.tool()
async def visualize_schema(shortname: str) -> dict[str, Any]:
    """Generate a Mermaid class diagram of a KG's schema.

    Builds the diagram deterministically from `get_schema` (no drafting needed):
    node classes become class boxes (with node properties as members), edge
    predicates become labeled arrows, and predicates that carry edge properties
    become intermediary classes with typed fields wired `source --> edge -->
    target`. Node (entity) classes are colored light blue and edge
    (relationship) classes orange, with a legend showing both. When the curated
    metadata names predicates but not their endpoints (e.g. `sawgraph`), edges
    are recovered from the graph's `rdfs:domain`/`rdfs:range`, scoped to the
    curated classes; any predicate still without endpoints is listed as a `%%`
    comment rather than guessed at.

    Args:
        shortname: The KG shortname (e.g. `spoke-genelab`), as returned by
            `list_kgs`.

    Returns:
        `{"shortname": ..., "mermaid": ..., "mermaid_block": ...}`.
        `mermaid_block` is the diagram ALREADY wrapped in a ```mermaid fenced
        code block; `mermaid` is the same diagram fence-free (for saving as a
        `.mermaid` file).

    PRESENTATION (required): output `mermaid_block` VERBATIM, and nothing else.
    Do NOT redraw, re-render, or convert it — in particular do NOT emit SVG, PNG,
    HTML, an image, an artifact, or a hand-built diagram. Mermaid clients render
    the fenced block natively; producing your own graphic yields a messy,
    incorrect picture.

    The diagram is logged to the session automatically (like queries), so
    `create_chat_transcript` renders it without you re-supplying it.
    """
    result = await schema.visualize_schema(shortname)
    if "mermaid" in result:
        session.record_visualization(shortname, result["mermaid"])
        # Pre-fenced form so the model can echo it verbatim without redrawing.
        result["mermaid_block"] = f"```mermaid\n{result['mermaid']}\n```"
    return result


@mcp.tool()
async def sparql_query(
    query: str, format: str = "json", exploratory: bool = False
) -> Any:
    """Run a SPARQL query against the FRINK federation endpoint.

    Scope each knowledge graph with its named graph, e.g.::

        PREFIX up: <http://purl.uniprot.org/core/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT DISTINCT ?mondo ?label WHERE {
          GRAPH <https://purl.org/okn/frink/kg/prokn> {
            ?d a up:Disease ; rdfs:seeAlso ?mondo .
          }
        }

    For category/subtype questions ("all cardiovascular diseases", "any asthma",
    "subtypes of X"), expand the category INLINE using ubergraph's precomputed
    transitive closure and join it to the target KG in the SAME query — do not
    walk the hierarchy level by level or fetch the tree separately::

        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT DISTINCT ?disease ?label WHERE {
          GRAPH <https://purl.org/okn/frink/kg/ubergraph> {
            ?disease rdfs:subClassOf* <http://purl.obolibrary.org/obo/MONDO_0004995> .
            OPTIONAL { ?disease rdfs:label ?label }
          }
          GRAPH <https://purl.org/okn/frink/kg/SOME_TARGET_KG> {
            ?record some:predicate ?disease .
          }
        }

    Args:
        query: A complete SPARQL query string.
        format: `json` (default; parsed into rows), `csv`, or `tsv` (raw text).
        exploratory: Set True for schema-probing, sampling, or trial-and-error
            queries you don't want in the transcript. Exploratory queries are
            never logged. (Queries that error or return no rows are skipped
            automatically, exploratory or not.)

    Returns:
        For json: `{"vars": [...], "rows": [...], "row_count": N}`.
        For csv/tsv: `{"format": ..., "text": "..."}`.

    Note: The endpoint runs on a read-only filesystem, so queries needing a
    large external sort over a full-graph scan may fail; add a `LIMIT`, narrow
    the pattern, or scope to a named graph.
    """
    # Normalize up front so the logged/transcript query matches what executes
    # (run_sparql normalizes again; the substitution is idempotent).
    query = normalize_schema_org(query)
    try:
        result = await run_sparql(query, fmt=format)
        if not exploratory:
            session.record(query, format, result=result)
        return result
    except SparqlError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def expand_ontology_term(
    term: str,
    relation: str = "subClassOf",
    direction: str = "descendants",
    include_self: bool = True,
    limit: int = 1000,
) -> Any:
    """Expand an ontology term to its full subtree/closure via `ubergraph`.

    USE THIS (or the equivalent inline `rdfs:subClassOf*` pattern) for any
    "all X under category Y" / "subtypes of" / "descendants of" question, e.g.
    "all cardiovascular diseases". Ubergraph stores precomputed inferred edges,
    so this returns the COMPLETE subtree in one call. Do not walk the hierarchy
    level by level or fetch the tree separately.

    Args:
        term: The ontology term as a full URI
            (e.g. `http://purl.obolibrary.org/obo/MONDO_0003847`) or a CURIE
            with an OBO prefix (e.g. `MONDO:0003847`, `CHEBI:24431`).
        relation: `subClassOf` (default) or `partOf`.
        direction: `descendants` (terms under `term`) or `ancestors`.
        include_self: If True (default), include `term` itself in the results
            (reflexive `*` path); if False, return only strict descendants/
            ancestors (non-reflexive `+` path).
        limit: Max rows to return.

    Returns the matching terms with their `rdfs:label`.
    """
    term_uri = _to_uri(term)
    rel = {
        "subClassOf": "rdfs:subClassOf",
        "partof": "<http://purl.obolibrary.org/obo/BFO_0000050>",
        "partOf": "<http://purl.obolibrary.org/obo/BFO_0000050>",
    }.get(relation, "rdfs:subClassOf")

    # `*` is reflexive (includes `term`); `+` is strict (excludes it).
    op = "*" if include_self else "+"
    if direction == "ancestors":
        pattern = f"<{term_uri}> {rel}{op} ?term ."
    else:
        pattern = f"?term {rel}{op} <{term_uri}> ."

    graph = named_graph("ubergraph")
    query = f"""\
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?term ?label WHERE {{
  GRAPH <{graph}> {{
    {pattern}
    OPTIONAL {{ ?term rdfs:label ?label . }}
  }}
}} LIMIT {int(limit)}"""
    try:
        result = await run_sparql(query)
        session.record(query, "json", result=result)
        return result
    except SparqlError as exc:
        return {"error": str(exc), "query": query}


@mcp.tool()
async def reset_query_log() -> dict[str, Any]:
    """Clear the session's query log (and logged diagrams) for a fresh scope.

    Call this at the START of a new analysis. Every subsequent `sparql_query`
    (and `expand_ontology_term`) call is logged automatically, as is every
    `visualize_schema` diagram, and `create_chat_transcript` renders them as the
    ground-truth record of what actually ran — so you don't have to re-supply
    queries or diagrams from memory.
    """
    removed = session.reset()
    return {"cleared": removed}


@mcp.tool()
async def get_query_log() -> list[dict[str, Any]]:
    """Return the SPARQL queries logged so far this session, in execution order.

    Only queries that returned rows and were not marked exploratory are present.
    Each entry has `timestamp`, `sparql` (verbatim), `graphs` (KG shortnames),
    `format`, `row_count`, and `results` (capped sample). Useful to inspect what
    will appear in `create_chat_transcript`.
    """
    return session.entries()


@mcp.tool()
async def create_chat_transcript(
    model: str,
    exchanges: list[dict[str, Any]] | None = None,
    kgs_used: list[str] | None = None,
    date: str | None = None,
    format: str = "markdown",
    title: str = "Proto-OKN Chat Transcript",
    include_query_log: bool = True,
    include_intermediate_rows: bool = False,
    include_visualizations: bool = True,
) -> Any:
    """Build a reproducible, detailed transcript of a Proto-OKN session.

    Captures the FULL working detail — not just a summary — so the session can
    be reproduced and audited: the user prompts and your answers, every SPARQL
    query that actually ran (verbatim) with the rows it returned, plus session
    provenance (date, model version, knowledge graphs, endpoint).

    Queries come from the automatic session log: each `sparql_query` /
    `expand_ontology_term` call is recorded as it runs, and (when
    `include_query_log` is true) rendered here as ground truth — you do NOT need
    to re-supply them. Call `reset_query_log` at the start of an analysis to
    scope the log to that session. You still supply the prompts and your
    narrative answers via `exchanges`.

    Args:
        model: The model version that produced the analysis
            (e.g. `claude-opus-4-8`). Use the exact model ID.
        exchanges: The conversation turns, in order. Each is a dict with
            `prompt` (str) and optional `answer` (str). You may also attach an
            explicit `queries` list per turn (same shape as the log entries) if
            you want queries shown inline with a specific prompt instead of —
            or in addition to — the auto-logged appendix. Attach ONLY queries
            that produced findings; never attach exploratory/schema-probing
            queries. A query's optional `description` is a plain, user-facing
            label of what the query finds (e.g. "Diseases linked to PFAS") —
            never internal bookkeeping such as "(exploratory, not logged)",
            "(intermediate)", or notes about logging state.
        kgs_used: Shortnames of the knowledge graphs queried. If omitted, they
            are inferred from the logged queries. Each is expanded to its
            federation named-graph URI.
        date: ISO date (`YYYY-MM-DD`) of the session. Defaults to today.
        format: `markdown` (default) for a rendered document string, or `json`
            for the structured fields.
        title: Heading for the transcript.
        include_query_log: If true (default), append the auto-logged queries
            as a "SPARQL queries executed" section.
        include_intermediate_rows: If false (default), only the FINAL logged
            query renders its result rows; earlier (intermediate) queries show
            their SPARQL and row count but omit the result table, to keep the
            transcript focused on the queries that produced the findings. Set
            true to render the full result rows for every logged query.
            (Queries attached inline to an exchange via `queries` always render
            in full, regardless of this flag.)
        include_visualizations: If true (default), append a "Schema
            visualizations" section with every `visualize_schema` diagram logged
            this session, each in a fenced ```mermaid block. These are recorded
            automatically — you do NOT need to re-supply them.

    Returns:
        For `markdown`: the transcript string. Each conversation turn is
        rendered in the mcp-proto-okn style — a "👤 **User**" block (the prompt)
        and a "🧠 **Assistant**" block (the answer), separated by a rule — with
        queries in fenced ```sparql blocks (plus result tables) and schema
        diagrams in fenced ```mermaid blocks under the answer.
        For `json`: a dict with `title`, `date`, `model`, `exchanges`,
        `knowledge_graphs`, `query_log`, `visualizations`, and
        `sparql_endpoint`.

    OUTPUT HANDLING (required): SAVE the transcript as a downloadable file.
    Write the full markdown returned by this tool — verbatim and in its
    entirety — to a `.md` file using your file-creation capability (the same
    thing that happens when a user says "save the transcript as a file": the
    `.md` is written and shown in the preview panel, downloadable directly from
    the chat). A Markdown ARTIFACT / document achieves the same result (Claude
    Desktop and claude.ai render it in a side panel the user can save as `.md`
    or export to PDF; a hosted `present_files`-style tool also works). Creating
    the file is the goal — a sentence describing or summarizing the transcript
    is NOT a substitute.

    Only if you genuinely cannot write a file or artifact, fall back to
    outputting the complete markdown in a fenced ```markdown block in your reply
    so the user can copy/save it.

    NEVER claim the transcript is "ready", "in the preview panel", or "saved"
    unless you actually wrote the file (or emitted its full content) — do not
    fabricate a preview. Either the file exists / the document content is present
    in your response, or you state plainly that you could not produce it.

    The rendered markdown is also published as the read-only MCP resource
    `transcript://session/latest`, so a client can fetch/save it directly
    (transport-agnostic; works for remote servers) regardless of how you present
    it. You may point the user there.
    """
    when = date or _date.today().isoformat()
    exchanges = exchanges or []
    log = session.entries() if include_query_log else []
    visualizations = session.visualizations() if include_visualizations else []

    # Infer KGs from the log (and any diagrams) when not passed explicitly.
    if kgs_used is None:
        names: list[str] = []
        for entry in log:
            for name in entry.get("graphs", []):
                if name not in names:
                    names.append(name)
        for viz in visualizations:
            name = viz.get("shortname")
            if name and name not in names:
                names.append(name)
        kgs_used = names
    kgs = [
        {"shortname": name, "named_graph": named_graph(name)}
        for name in kgs_used
    ]

    if format == "json":
        return {
            "title": title,
            "date": when,
            "model": model,
            "exchanges": exchanges,
            "knowledge_graphs": kgs,
            "query_log": log,
            "visualizations": visualizations,
            "sparql_endpoint": FEDERATION_ENDPOINT,
        }

    if format != "markdown":
        return {"error": f"Unsupported format {format!r}; use 'markdown' or 'json'."}

    lines = [
        f"# {title}",
        "",
        f"- **Date:** {when}",
        f"- **Model:** {model}",
        f"- **SPARQL endpoint:** {FEDERATION_ENDPOINT}",
        "",
        "## Knowledge graphs used",
        "",
    ]
    if kgs:
        lines += [f"- `{kg['shortname']}` — <{kg['named_graph']}>" for kg in kgs]
    else:
        lines.append("- _None queried._")

    lines += ["", "## Conversation", ""]
    if not exchanges:
        lines += ["_No prompts recorded._", ""]
    for exchange in exchanges:
        # mcp-proto-okn style: each turn is a 👤 User block and a 🧠 Assistant
        # block separated by a rule; queries/diagrams render under the answer.
        lines += [
            "👤 **User**",
            "",
            exchange.get("prompt", "(no prompt)"),
            "",
            "---",
            "",
            "🧠 **Assistant**",
            "",
        ]
        answer = (exchange.get("answer") or "").strip()
        if answer:
            lines += [answer, ""]
        # Only findings-producing queries belong in the transcript; drop any
        # the model flagged exploratory so schema-probing never leaks in.
        shown = [q for q in (exchange.get("queries") or []) if not q.get("exploratory")]
        for j, q in enumerate(shown, start=1):
            lines += _render_query(q, f"Query {j}")
        # Optional Mermaid diagram(s) attached inline to this turn.
        inline = exchange.get("mermaid")
        for diagram in [inline] if isinstance(inline, str) else (inline or []):
            if (diagram or "").strip():
                lines += ["```mermaid", diagram.strip(), "```", ""]

    if log:
        lines += ["## SPARQL queries executed", ""]
        for k, entry in enumerate(log, start=1):
            ctx = entry.get("timestamp", "")
            graphs = entry.get("graphs") or []
            if graphs:
                ctx += " · " + ", ".join(f"`{g}`" for g in graphs)
            # By default only the final query's rows are shown; intermediate
            # queries list their text and row count but omit the result table.
            show_results = include_intermediate_rows or k == len(log)
            lines += _render_query(
                entry, f"Query {k}", subheading=ctx, show_results=show_results
            )

    if visualizations:
        lines += ["## Schema visualizations", ""]
        for viz in visualizations:
            shortname = viz.get("shortname", "")
            ctx = viz.get("timestamp", "")
            lines += [f"### `{shortname}` schema", ""]
            if ctx:
                lines += [f"_{ctx}_", ""]
            lines += ["```mermaid", (viz.get("mermaid") or "").strip(), "```", ""]

    markdown = "\n".join(lines)
    # Publish for direct client fetch/save via the transcript resource.
    session.set_last_transcript(markdown)
    return markdown


@mcp.resource(
    "transcript://session/latest",
    name="Latest chat transcript",
    description=(
        "The most recent transcript rendered by create_chat_transcript this "
        "session, as Markdown. Lets a client fetch/save the document directly, "
        "independent of how the model re-emits it."
    ),
    mime_type="text/markdown",
)
def latest_transcript_resource() -> str:
    """Return the last rendered transcript, or a placeholder if none yet."""
    md = session.last_transcript()
    if not md:
        return (
            "# No transcript yet\n\n"
            "Call the `create_chat_transcript` tool (markdown format) first; the "
            "rendered document then appears here."
        )
    return md


# Internal bookkeeping the model sometimes buries in a query `description`
# (e.g. "Explore NDE schema (exploratory, not logged)"). It has no value to the
# user, so strip it from the rendered heading. Matches a parenthetical/bracketed
# group, or a trailing dash/comma note, containing a bookkeeping keyword.
_DESC_NOISE_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:exploratory|not\s+logged|intermediate|logging)\b[^\)\]]*[\)\]]"
    r"|\s*[—–\-,]\s*(?:exploratory|not\s+logged|intermediate)\b[^.;]*",
    re.IGNORECASE,
)


def _clean_description(desc: str | None) -> str:
    """Strip internal bookkeeping noise from a query description for display."""
    text = _DESC_NOISE_RE.sub("", desc or "")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.rstrip(" —–-,;:").strip()


def _render_query(
    q: dict[str, Any],
    label: str,
    subheading: str = "",
    show_results: bool = True,
) -> list[str]:
    """Render one query (verbatim text + results or error) as markdown lines.

    When ``show_results`` is False, the result rows are omitted and replaced by a
    one-line row-count note — used for intermediate queries in the log appendix.
    """
    desc = _clean_description(q.get("description"))
    heading = f"#### {label}" + (f" — {desc}" if desc else "")
    lines = [heading, ""]
    if subheading:
        lines += [f"_{subheading}_", ""]
    lines += ["```sparql", (q.get("sparql") or "").strip(), "```", ""]
    if q.get("error"):
        lines += [f"**Error:** {q['error']}", ""]
    elif show_results:
        lines += _render_results(q.get("results"))
    else:
        count = q.get("row_count")
        note = (
            f"{count} row(s) — results omitted"
            if count is not None
            else "results omitted"
        )
        lines += [f"_{note}_", ""]
    return lines


def _render_results(results: Any) -> list[str]:
    """Render a query's results as markdown lines (table, code block, or note)."""
    if results is None:
        return []
    # SPARQL json shape from `sparql_query`: {"vars", "rows", "row_count"}.
    if isinstance(results, dict) and "rows" in results:
        rows = results.get("rows") or []
        cols = results.get("vars") or (list(rows[0].keys()) if rows else [])
        count = results.get("row_count", len(rows))
        return [f"_{count} row(s)_", ""] + _rows_to_table(cols, rows)
    # csv/tsv shape: {"format", "text"}.
    if isinstance(results, dict) and "text" in results:
        fmt = results.get("format", "")
        return [f"```{fmt}".rstrip(), str(results["text"]).strip(), "```", ""]
    # A bare list of row dicts.
    if isinstance(results, list):
        cols = list(results[0].keys()) if results and isinstance(results[0], dict) else []
        return [f"_{len(results)} row(s)_", ""] + _rows_to_table(cols, results)
    # Anything else: show as text.
    return ["```", str(results).strip(), "```", ""]


def _rows_to_table(cols: list[str], rows: list[dict[str, Any]]) -> list[str]:
    """Render rows (list of {col: value}) as a GitHub-flavored markdown table."""
    if not cols or not rows:
        return ["_(no rows)_", ""]

    def cell(value: Any) -> str:
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")

    out = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    out += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    out.append("")
    return out


_OBO_PREFIXES = (
    "MONDO", "CHEBI", "GO", "HP", "UBERON", "CL", "PR", "NCBITaxon",
    "DOID", "SO", "PATO", "BFO", "ENVO", "FOODON", "OBI",
)


def _to_uri(term: str) -> str:
    """Convert an OBO CURIE (PREFIX:1234567) to a full purl URI; pass URIs through."""
    if term.startswith(("http://", "https://")):
        return term
    if ":" in term:
        prefix, _, local = term.partition(":")
        if prefix in _OBO_PREFIXES:
            return f"http://purl.obolibrary.org/obo/{prefix}_{local}"
    return term


def main() -> None:
    """Console entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
