"""Parse okn-registry ``.rq`` files and adapt them to the federation endpoint.

The registry query files (frink-okn/okn-registry) are written against each KG's
*own* SPARQL endpoint, so their `WHERE` patterns are unscoped. The mcp-okn server
only ever talks to the single FRINK *federation* endpoint, where every pattern
must be scoped to a named graph:

    GRAPH <https://purl.org/okn/frink/kg/{shortname}> { ... }

For a single-KG query that means wrapping the whole `WHERE` group in one `GRAPH`
block. Queries that already use `GRAPH`/`SERVICE`, that target more than one KG
(the registry's ``federation`` set), or that aren't `SELECT`/`ASK` can't be
wrapped mechanically — we flag those ``manual`` and leave them for layer-2 only.

Everything here is pure (no network) so it can be unit-tested offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

# A KG's federation named-graph URI. Mirrors mcp_okn.sparql.GRAPH_URI so the
# benchmark stays runnable even if imported without the package installed.
GRAPH_URI = "https://purl.org/okn/frink/kg/{shortname}"

# Function namespaces the QLever-backed federation endpoint doesn't implement, so
# any query using them can't run there however we adapt it (e.g. GraphDB's
# ``ofn:asDays``). These are kept in the dataset as ``incompatible`` — distinct
# from a query bug — and excluded from the runnable set. Matched against the raw
# query text, since the namespace appears inside a PREFIX IRI.
QLEVER_UNSUPPORTED_NAMESPACES = (
    "http://www.ontotext.com/sparql/functions/",
)

# Registry header lines look like ``#+ summary: ...`` / ``#+ tags:`` / ``#+   - x``.
_SUMMARY_RE = re.compile(r"^#\+\s*summary:\s*(.*)$", re.IGNORECASE)
_TAGS_RE = re.compile(r"^#\+\s*tags:\s*(.*)$", re.IGNORECASE)
_TAG_ITEM_RE = re.compile(r"^#\+\s*-\s*(.+?)\s*$")


@dataclass
class ParsedQuery:
    """One registry ``.rq`` file, parsed and (where possible) adapted."""

    summary: str
    tags: list[str]
    sparql: str  # the original query body, header stripped
    #: Federation-ready query (WHERE wrapped in the KG's GRAPH), or None when the
    #: query can't be wrapped mechanically (see ``adaptation``).
    federated: str | None = None
    #: "auto" — wrapped programmatically; "manual" — needs a human/agent (already
    #: scoped, multi-KG, or non-SELECT). Set with a reason in ``adaptation_note``.
    adaptation: str = "auto"
    adaptation_note: str = ""
    extra: dict = field(default_factory=dict)


def parse_rq(text: str) -> tuple[str, list[str], str]:
    """Split a ``.rq`` file into (summary, tags, sparql-body).

    The ``#+`` header block is stripped from the returned body. A file with no
    header yields an empty summary / no tags and the whole text as the body.
    """
    summary = ""
    tags: list[str] = []
    body_lines: list[str] = []
    in_tags = False
    header_done = False

    for line in text.splitlines():
        if not header_done:
            m = _SUMMARY_RE.match(line)
            if m:
                summary = m.group(1).strip()
                in_tags = False
                continue
            if _TAGS_RE.match(line):
                inline = _TAGS_RE.match(line).group(1).strip()
                if inline:  # rare ``tags: [a, b]`` inline form
                    tags.extend(_split_inline_tags(inline))
                in_tags = True
                continue
            if in_tags:
                m = _TAG_ITEM_RE.match(line)
                if m:
                    tags.append(m.group(1).strip())
                    continue
                # A non-item line ends the tag list.
                in_tags = False
            if line.startswith("#+"):
                # Some other ``#+`` directive we don't model — skip it.
                continue
            # First non-header line: the SPARQL body starts here.
            header_done = True
        body_lines.append(line)

    return summary, tags, "\n".join(body_lines).strip()


def _split_inline_tags(value: str) -> list[str]:
    value = value.strip().lstrip("[").rstrip("]")
    return [t.strip().strip("'\"") for t in value.split(",") if t.strip()]


# --- SPARQL-aware scanning -------------------------------------------------
#
# Brace matching and keyword detection must ignore braces/keywords that appear
# inside comments (``# ...`` to end of line), string literals, or IRIs (<...>).
# A tiny hand scanner is enough and avoids a SPARQL-parser dependency.


def _significant_spans(query: str) -> Iterator[tuple[int, str]]:
    """Yield ``(index, char)`` for every char that is *code* (not in a comment,
    string literal, or IRI)."""
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if c == "#":  # comment to end of line
            while i < n and query[i] != "\n":
                i += 1
            continue
        if c == "<" and _looks_like_iri(query, i):
            while i < n and query[i] != ">":
                i += 1
            i += 1  # consume the '>'
            continue
        if c in "\"'":
            i = _skip_string(query, i)
            continue
        yield i, c
        i += 1


def _looks_like_iri(query: str, i: int) -> bool:
    """Heuristic: ``<`` opens an IRI unless it's the ``<``/``<=`` operator.

    IRIs contain no whitespace and end with ``>``; the comparison operators are
    followed by a space or ``=``. Good enough for the registry corpus.
    """
    j = query.find(">", i)
    if j == -1:
        return False
    inner = query[i + 1 : j]
    return not any(ch.isspace() for ch in inner)


def _skip_string(query: str, i: int) -> int:
    quote = query[i]
    triple = query[i : i + 3] in ("'''", '"""')
    delim = query[i : i + 3] if triple else quote
    i += len(delim)
    n = len(query)
    while i < n:
        if query[i] == "\\":
            i += 2
            continue
        if query[i : i + len(delim)] == delim:
            return i + len(delim)
        i += 1
    return n


def unsupported_namespace(query: str) -> str | None:
    """Return the first QLever-unsupported function namespace the query uses."""
    for ns in QLEVER_UNSUPPORTED_NAMESPACES:
        if ns in query:
            return ns
    return None


def _has_keyword(query: str, keyword: str) -> bool:
    """True if ``keyword`` appears as a real SPARQL keyword in code.

    Ignores matches inside comments/strings/IRIs (via :func:`_significant_spans`)
    and matches that are actually a variable (``?service``), a prefixed-name
    local part (``treatment:Service``), or a substring of a longer identifier
    (``containsService``). The leading ``[?$:\\w]`` lookbehind rules those out.
    """
    code = "".join(c for _, c in _significant_spans(query))
    return re.search(rf"(?<![?$:\w]){keyword}(?![\w])", code, re.IGNORECASE) is not None


def _main_where_braces(query: str) -> tuple[int, int] | None:
    """Return the (open, close) indices of the outermost query ``{ ... }`` group.

    That is the `WHERE` group for a SELECT/ASK query. Returns None if no balanced
    top-level group is found.
    """
    open_idx = None
    depth = 0
    for idx, c in _significant_spans(query):
        if c == "{":
            if depth == 0:
                open_idx = idx
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and open_idx is not None:
                return open_idx, idx
            if depth < 0:
                return None
    return None


def graph_wrap(query: str, shortname: str) -> str:
    """Wrap a single-KG query's WHERE group in its federation GRAPH block.

    Raises ValueError if the query has no identifiable top-level group.
    """
    span = _main_where_braces(query)
    if span is None:
        raise ValueError("no top-level WHERE group found")
    open_idx, close_idx = span
    graph_uri = GRAPH_URI.format(shortname=shortname)
    inner = query[open_idx + 1 : close_idx]
    return (
        query[:open_idx]
        + "{\n  GRAPH <"
        + graph_uri
        + "> {"
        + inner
        + "}\n}"
        + query[close_idx + 1 :]
    )


def adapt(
    summary: str,
    tags: list[str],
    sparql: str,
    served: set[str],
    tag_map: dict[str, str] | None = None,
) -> ParsedQuery:
    """Map tags to served shortnames and GRAPH-wrap a single-KG SELECT/ASK query.

    Args:
        summary, tags, sparql: as returned by :func:`parse_rq`.
        served: shortnames the mcp-okn server actually exposes.
        tag_map: optional registry-tag → served-shortname overrides.

    The returned :class:`ParsedQuery` always carries the original ``sparql``;
    ``federated`` is set only when adaptation succeeds (``adaptation == "auto"``).
    """
    tag_map = tag_map or {}
    mapped = [tag_map.get(t, t) for t in tags]
    kgs = [t for t in mapped if t in served]
    pq = ParsedQuery(summary=summary, tags=tags, sparql=sparql)
    pq.extra["mapped_kgs"] = kgs

    if not kgs:
        pq.adaptation = "skip"
        pq.adaptation_note = f"no served KG among tags {tags}"
        return pq
    ns = unsupported_namespace(sparql)
    if ns:
        pq.adaptation = "incompatible"
        pq.adaptation_note = f"uses function namespace <{ns}> unsupported by QLever"
        return pq
    if len(kgs) > 1:
        pq.adaptation = "manual"
        pq.adaptation_note = f"multi-KG (federation) over {kgs}"
        return pq
    if _has_keyword(sparql, "GRAPH") or _has_keyword(sparql, "SERVICE"):
        pq.adaptation = "manual"
        pq.adaptation_note = "already scoped with GRAPH/SERVICE"
        return pq
    if not (_has_keyword(sparql, "SELECT") or _has_keyword(sparql, "ASK")):
        pq.adaptation = "manual"
        pq.adaptation_note = "not a SELECT/ASK query"
        return pq

    try:
        pq.federated = graph_wrap(sparql, kgs[0])
    except ValueError as e:
        pq.adaptation = "manual"
        pq.adaptation_note = str(e)
    return pq
