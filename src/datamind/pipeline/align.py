"""Does the query answer the question that was asked? (alignment check)

A query can be valid, safe and about the right tables, yet answer a near-miss: another year, no
top-N limit, no breakdown. Two layers catch that:

1. Code checks on the question and the query facts. They are narrow on purpose, so they never
   reject a correct query: a year named in the question must be used by the query; "top 5" or
   "which five ... most" needs LIMIT 5; "by month" or "per country" over an aggregate needs a
   GROUP BY.
2. A second, independent Gemma pass that restates the query in plain words and says whether it
   answers the question (prompt align_v1). It sees the SQL and the facts, not the first draft's
   reasoning.

The pipeline repairs once with the problems as feedback. A code mismatch that survives the repair
becomes CLARIFY; a model-only doubt is shown on the answer as flagged.
"""

from __future__ import annotations

import re

from datamind.pipeline.facts import QueryFacts

_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
_BARE_YEAR = re.compile(r"(?<![\d-])(1[5-9]\d\d|20\d\d)(?![\d-])")  # 1995, not '1995-01-01'
_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20,
}  # fmt: skip
_N = r"(\d{1,3}|" + "|".join(_NUMBERS) + r")"
_TOP_N = re.compile(rf"\b(?:top|first|bottom|which|the)\s+{_N}\b", re.I)
_RANKING = re.compile(
    r"\b(top|bottom|most|least|highest|lowest|largest|smallest|biggest|best|worst|"
    r"richest|poorest|longest|shortest|fewest|greatest)\b",
    re.I,
)
_BREAKDOWN = re.compile(
    r"\b(?:per|for each|each|every|by|broken down by|breakdown by)\s+"
    r"(year|month|quarter|week|day|country|nation|region|segment|continent|customer|"
    r"supplier|part|status|priority|mode|category)\b"
    r"|\b(?:year|month|quarter|week|day) (?:by|over) (?:year|month|quarter|week|day)\b",
    re.I,
)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBERS[token.lower()]


def code_problems(question: str, sql: str, facts: QueryFacts) -> list[str]:
    """Mismatches between the question and the query that code can prove."""
    problems: list[str] = []
    q = question
    ends = [int(e[:4]) for e in facts.date_range or () if e[:4].isdigit()]
    if len(ends) == 2:  # a date filter: use its inclusive range, not its exclusive end date
        years_in_sql = set(_BARE_YEAR.findall(sql)) | {str(y) for y in range(ends[0], ends[1] + 1)}
    else:  # no date filter, or an open-ended one: any year written in the SQL counts
        years_in_sql = set(_YEAR.findall(sql))
    for year in dict.fromkeys(_YEAR.findall(q)):
        if year not in years_in_sql:
            problems.append(f"The question names {year}, but the query does not use {year}.")

    top = _TOP_N.search(q)
    if top and _RANKING.search(q):
        n = _number(top.group(1))
        if 1 <= n <= 100 and facts.limit != n:
            got = f"LIMIT {facts.limit}" if facts.limit is not None else "no row limit"
            problems.append(f"The question asks for {n} results, but the query has {got}.")

    breakdown = _BREAKDOWN.search(q)
    if breakdown and facts.measures and not facts.group_by:
        problems.append(
            f'The question asks for a breakdown ("{breakdown.group(0)}"), '
            "but the query has no GROUP BY."
        )
    return problems
