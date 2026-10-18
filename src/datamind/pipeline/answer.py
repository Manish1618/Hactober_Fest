"""Answer text: placeholders filled by code from result cells, and a code-only fallback."""

from __future__ import annotations

import re
from typing import Any

from datamind.config.models import DatasetConfig
from datamind.pipeline.facts import QueryFacts

_PLACEHOLDER = re.compile(r"\{r(\d+)\.([A-Za-z_][\w]*)\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def format_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return str(value)


def fill_placeholders(
    text: str, columns: list[str], rows: list[list[Any]]
) -> tuple[str, list[str]]:
    """Replace {rN.column} with the formatted cell. Returns the text and unresolved placeholders."""
    index = {c.lower(): i for i, c in enumerate(columns)}
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        row, col = int(m.group(1)), m.group(2).lower()
        if 1 <= row <= len(rows) and col in index:
            return format_value(rows[row - 1][index[col]])
        missing.append(m.group(0))
        return m.group(0)

    return _PLACEHOLDER.sub(sub, text), missing


def stray_numbers(raw_answer: str, question: str) -> list[str]:
    """Numbers the model typed itself (outside placeholders) that are not in the question."""
    outside = _PLACEHOLDER.sub(" ", raw_answer)
    allowed = set(_NUMBER.findall(question))
    return [n for n in _NUMBER.findall(outside) if n not in allowed]


def template_answer(columns: list[str], rows: list[list[Any]], row_count: int) -> str:
    """Code-only answer used when the model's answer cannot be trusted or is unavailable."""
    if row_count == 1 and len(columns) == 1:
        return f"{columns[0]}: {format_value(rows[0][0])}"
    if row_count == 1:
        return "; ".join(f"{c}: {format_value(v)}" for c, v in zip(columns, rows[0], strict=True))
    shown = rows[:5]
    lines = [", ".join(format_value(v) for v in r) for r in shown]
    more = f" (first {len(shown)} of {row_count} shown)" if row_count > len(shown) else ""
    return f"{row_count} rows returned{more}: " + "; ".join(lines)


def result_summary(columns: list[str], rows: list[list[Any]], row_count: int) -> str:
    """Short summary kept in conversation memory for follow-ups."""
    head = "; ".join(
        ", ".join(f"{c}={format_value(v)}" for c, v in zip(columns, r, strict=True))
        for r in rows[:3]
    )
    return f"{row_count} rows. First rows: {head}" if row_count else "no rows"


def _friendly(column: str, cfg: DatasetConfig | None) -> str:
    """A reader-friendly name for TABLE.COLUMN: its first synonym, else the column name."""
    table, _, col = column.rpartition(".")
    if cfg and table in cfg.tables and col in cfg.tables[table].columns:
        c = cfg.tables[table].columns[col]
        if c.synonyms:
            return f"{c.synonyms[0]} ({col})"
    return col or column


def _join_words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def explain(facts: QueryFacts, cfg: DatasetConfig | None = None) -> str:
    """Plain-language explanation built by code from the query facts (always faithful)."""
    parts = [f"The query read {_join_words(facts.tables)}"]
    if facts.joins:
        parts[0] += ", linked on their shared keys"
    conditions = []
    if facts.date_range:
        conditions.append(
            f"{_friendly(facts.date_column, cfg)} is from {facts.date_range[0]} "
            f"to {facts.date_range[1]}"
        )
    for f in facts.filters:
        if f.column == facts.date_column:
            continue
        name = _friendly(f.column, cfg)
        if f.op == "=":
            conditions.append(f"{name} is {f.values[0]}")
        elif f.op == "IN":
            conditions.append(f"{name} is {_join_words(f.values).replace(' and ', ' or ')}")
        else:
            conditions.append(f.describe().replace(f.column.split(".")[-1], name, 1))
    conditions += facts.other_conditions
    if conditions:
        parts.append("kept rows where " + _join_words(conditions))
    if facts.measures:
        parts.append("calculated " + _join_words(facts.measures))
    if facts.group_by:
        names = [_friendly(_column_for(g, facts), cfg) for g in facts.group_by]
        parts.append("grouped the results by " + _join_words(names))
    if facts.having:
        parts.append("kept groups where " + _join_words(facts.having))
    if facts.order_by:
        order = [
            o.rsplit(" ", 1)[0]
            + (" from highest to lowest" if o.endswith("DESC") else " from lowest to highest")
            for o in facts.order_by
        ]
        parts.append("sorted by " + _join_words(order))
    if facts.limit is not None:
        parts.append(f"kept the first {facts.limit} rows")
    return _join_words(parts) + "."


def _column_for(expr: str, facts: QueryFacts) -> str:
    for used in facts.columns_used:
        if used.endswith("." + expr.upper()):
            return used
    return expr
