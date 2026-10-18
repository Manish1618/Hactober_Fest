"""Answer text: placeholders filled by code from result cells, and a code-only fallback."""

from __future__ import annotations

import re
from typing import Any

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
