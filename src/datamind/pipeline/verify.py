"""Verifier (FR-C2, FR-D2): every number in the answer and explanation must trace to a result
cell, a query fact or the question, and the explanation must mention every key query fact.
An answer that fails is replaced by the code-built answer, which passes by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from datamind.config.models import DatasetConfig
from datamind.pipeline.facts import QueryFacts

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twenty": 20, "fifty": 50, "hundred": 100,
}  # fmt: skip


@dataclass
class Verification:
    passed: bool
    untraced_numbers: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return "passed"
        parts = []
        if self.untraced_numbers:
            parts.append(f"untraced numbers {', '.join(self.untraced_numbers)}")
        if self.missing_facts:
            parts.append(f"facts not mentioned: {'; '.join(self.missing_facts)}")
        return "failed: " + "; ".join(parts)


def _to_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def _allowed(values: list[Any]) -> set[Decimal]:
    """Each value plus its roundings to 0, 1 and 2 decimals (how code formats numbers)."""
    out: set[Decimal] = set()
    for v in values:
        d = _to_number(v)
        if d is None or not d.is_finite():
            continue
        out.add(d.normalize())
        for places in (0, 1, 2):
            out.add(round(d, places).normalize())
    return out


def _strings(values: list[Any]) -> list[str]:
    """Text values that may contain digits (Customer#000000042, 1-URGENT, dates)."""
    return sorted(
        {str(v) for v in values if isinstance(v, str) and any(ch.isdigit() for ch in v)},
        key=len,
        reverse=True,
    )


def untraced_numbers(
    text: str, cells: list[Any], extra_text: str = "", numbers: list[Any] | None = None
) -> list[str]:
    """Numbers in `text` that are not a result cell, one of `numbers` or in `extra_text`.

    Text cells that contain digits (Customer#000000042, 1-URGENT, dates) are removed from the
    text first, so their digits are not read as numbers.
    """
    scrubbed = text
    for s in _strings(cells):
        scrubbed = re.sub(re.escape(s), " ", scrubbed, flags=re.IGNORECASE)
    allowed = _allowed([*cells, *(numbers or []), *(_NUMBER.findall(extra_text))])
    bad = []
    for raw in _NUMBER.findall(scrubbed):
        n = _to_number(raw)
        if n is None or n.normalize() not in allowed:
            bad.append(raw)
    return bad


def fact_numbers(facts: QueryFacts | None) -> list[Any]:
    """Numbers a correct explanation may use: dates, limits and literal filter values."""
    if facts is None:
        return []
    out: list[Any] = []
    text = " ".join(facts.lines())
    out += [m for m in _NUMBER.findall(text)]
    for part in re.findall(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = part.split("-")
        out += [part, y, int(m), int(d)]
    return out


def _terms(column: str, cfg: DatasetConfig | None) -> set[str]:
    table, _, col = column.rpartition(".")
    terms = {col.lower(), col.split("_", 1)[-1].lower()}
    if cfg and table in cfg.tables and col in cfg.tables[table].columns:
        c = cfg.tables[table].columns[col]
        terms |= {s.lower() for s in c.synonyms}
        terms.add(c.description.lower())
    return {t for t in terms if t}


def missing_facts(
    explanation: str, facts: QueryFacts, cfg: DatasetConfig | None = None
) -> list[str]:
    """Key facts the explanation leaves out: filter values, the date range, grouping, limit."""
    text = explanation.lower()
    missing = []
    for f in facts.filters:
        if f.column == facts.date_column:
            continue
        if not any(v.lower() in text for v in f.values):
            missing.append(f.describe())
    if facts.date_range:
        years = {d[:4] for d in facts.date_range if d[:4].isdigit()}
        if years and not any(y in text for y in years):
            missing.append(f"dates {facts.date_range[0]} to {facts.date_range[1]}")
    for g in facts.group_by:
        column = g if "." in g else _qualified(g, facts)
        if not any(t in text for t in _terms(column, cfg)):
            missing.append(f"grouped by {g}")
    if facts.limit is not None:
        words = {w for w, n in _WORDS.items() if n == facts.limit}
        if str(facts.limit) not in text and not any(w in text for w in words):
            missing.append(f"limit {facts.limit}")
    return missing


def _qualified(column: str, facts: QueryFacts) -> str:
    for used in facts.columns_used:
        if used.endswith("." + column.upper()):
            return used
    return column.upper()


def verify(
    answer: str,
    explanation: str,
    question: str,
    rows: list[list[Any]],
    facts: QueryFacts | None,
    row_count: int,
    cfg: DatasetConfig | None = None,
) -> Verification:
    cells = [v for r in rows for v in r]
    numbers = [*fact_numbers(facts), row_count, len(rows)]
    fact_text = " ".join(facts.lines()) if facts else ""
    bad = untraced_numbers(answer, cells, question, numbers) + untraced_numbers(
        explanation, [*cells, *re.findall(r"\d{4}-\d{2}-\d{2}", fact_text)], question, numbers
    )
    missing = missing_facts(explanation, facts, cfg) if facts and explanation else []
    return Verification(passed=not bad and not missing, untraced_numbers=bad, missing_facts=missing)
