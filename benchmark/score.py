"""Compare SPARQL result sets — the benchmark's notion of "right answer".

Text-to-SPARQL evaluation can't rely on column names matching: a correct answer
may name its variables differently, reorder columns, or add a harmless extra
column. So we score on *denotation* — the values returned — not on the SELECT
clause:

- ``exact``: the multiset of rows is identical, where each row is the sorted
  tuple of its cell values (column names and column/row order ignored).
- ``jaccard``: overlap of those row-tuples (partial credit), so a near-miss
  scores above zero.

Cell values are normalised to strings; ints/floats that print equally compare
equal (1 vs 1.0 → "1"). This is the standard "execution accuracy" used for SPARQL
benchmarks, kept deliberately simple.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


def _norm_cell(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _row_key(row: dict[str, Any]) -> tuple[str, ...]:
    """A row as the sorted tuple of its (stringified) values, names dropped."""
    return tuple(sorted(_norm_cell(v) for v in row.values()))


def _multiset(rows: Iterable[dict[str, Any]]) -> Counter:
    return Counter(_row_key(r) for r in rows)


@dataclass
class Comparison:
    """Result of comparing a candidate result set against the reference."""

    exact: bool
    jaccard: float
    precision: float
    recall: float
    f1: float
    reference_rows: int
    candidate_rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "exact": self.exact,
            "jaccard": round(self.jaccard, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "reference_rows": self.reference_rows,
            "candidate_rows": self.candidate_rows,
        }


def compare(
    reference: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> Comparison:
    """Compare a candidate result set to the reference."""
    ref = _multiset(reference)
    cand = _multiset(candidate)

    inter = sum((ref & cand).values())
    union = sum((ref | cand).values())
    ref_n = sum(ref.values())
    cand_n = sum(cand.values())

    jaccard = inter / union if union else 1.0
    precision = inter / cand_n if cand_n else (1.0 if ref_n == 0 else 0.0)
    recall = inter / ref_n if ref_n else (1.0 if cand_n == 0 else 0.0)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else (1.0 if ref_n == 0 and cand_n == 0 else 0.0)
    )
    return Comparison(
        exact=ref == cand,
        jaccard=jaccard,
        precision=precision,
        recall=recall,
        f1=f1,
        reference_rows=ref_n,
        candidate_rows=cand_n,
    )
