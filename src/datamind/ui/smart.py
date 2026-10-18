"""Pure helpers for the interface: which chart fits a result, follow-up questions, exports.

Kept free of Streamlit so they can be tested. Charts and exports only use the rows already shown
as evidence, so the page never draws or exports a number the evidence panel does not show.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from datamind.config.models import DatasetConfig
from datamind.response import Response, Status

TIME_WORDS = ("date", "year", "month", "quarter", "week", "day", "period")
MAX_BARS = 15
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ].*)?$")


@dataclass
class ChartSpec:
    kind: str  # "kpi", "line" or "bar"
    x: str = ""
    y: str = ""
    label: str = ""  # KPI caption


def _is_number(v) -> bool:
    return isinstance(v, int | float | Decimal) and not isinstance(v, bool)


def _is_time(name: str, values: list) -> bool:
    if any(w in name.lower() for w in TIME_WORDS):
        return True
    present = [v for v in values if v is not None]
    return bool(present) and all(
        isinstance(v, date | datetime) or (isinstance(v, str) and _ISO_DATE.match(v))
        for v in present
    )


def chart_spec(columns: list[str], rows: list[list]) -> ChartSpec | None:
    """One number -> KPI card; time + number -> line; label + number -> bar; else no chart."""
    if not columns or not rows:
        return None
    if len(rows) == 1 and len(columns) == 1 and _is_number(rows[0][0]):
        return ChartSpec("kpi", y=columns[0], label=columns[0].replace("_", " ").lower())
    if len(columns) < 2 or len(rows) < 2:
        return None
    values = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
    numeric = [c for c in columns if all(_is_number(v) for v in values[c] if v is not None)]
    if not numeric:
        return None
    measures = [c for c in numeric if not _is_time(c, values[c])]
    if not measures:
        return None
    y = measures[-1]
    others = [c for c in columns if c != y]
    time_cols = [c for c in others if _is_time(c, values[c])]
    if time_cols:
        return ChartSpec("line", x=time_cols[0], y=y)
    labels = [c for c in others if c not in numeric]
    if labels and len(rows) <= MAX_BARS:
        return ChartSpec("bar", x=labels[0], y=y)
    return None


def follow_ups(resp: Response, cfg: DatasetConfig, limit: int = 3) -> list[str]:
    """Next questions built from this question and the dataset, never from a fixed script."""
    if resp.status is not Status.ANSWERED:
        return []
    q = resp.evidence.question_as_understood.strip().rstrip("?.! ")
    out: list[str] = []
    year = _YEAR.search(q)
    over_time = re.search(r"\b(by|per|each|every) (year|quarter|month|week|day)", q, re.I)
    if year:
        y = int(year.group(1))
        out.append(_YEAR.sub(str(y - 1), q, count=1) + "?")
        if not over_time:
            out.append(f"{q[: year.start()].rstrip(' ,')} by year?".replace(" in by", " by"))
    elif cfg.time and not over_time:
        out.append(f"{q} by year?")
    if len(resp.evidence.rows) > 5 and not over_time:
        out.append(f"{q}, showing only the top 5?")
    asked = {resp.evidence.question_as_understood.lower()}
    out += [s for s in cfg.starter_questions if s.lower() not in asked]
    seen: list[str] = []
    for s in out:
        if s.lower() not in {x.lower() for x in seen} and len(s) > 3:
            seen.append(s)
    return seen[:limit]


def rows_csv(resp: Response) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(resp.evidence.columns)
    w.writerows(resp.evidence.rows)
    return buf.getvalue()


def proof_bundle(resp: Response) -> dict:
    """The audit record: question, SQL, facts, query ID and times. It holds no keys or settings."""
    ev = resp.evidence
    return {
        "request_id": resp.request_id,
        "status": resp.status.value,
        "question": ev.question_as_understood,
        "answer": resp.answer,
        "explanation": resp.explanation,
        "assumptions": resp.assumptions,
        "sql": ev.sql,
        "query_facts": ev.query_facts,
        "snowflake": {
            "query_id": ev.query_id,
            "role": ev.role,
            "warehouse": ev.warehouse,
            "executed_at": ev.executed_at,
            "elapsed_seconds": ev.elapsed_seconds,
            "backend": ev.backend,
        },
        "result": {
            "columns": ev.columns,
            "rows_shown": ev.rows,
            "rows_returned": ev.rows_returned,
        },
        "dataset": ev.dataset,
        "data_coverage": ev.data_coverage,
        "model": ev.model,
        "prompt_versions": ev.prompt_versions,
        "verification": ev.verification,
        "limits": ev.limits,
        "licence_note": ev.licence_note,
    }


def snowsight_url(account: str, query_id: str) -> str:
    """Snowsight's page for one query, from an ORG-ACCOUNT identifier such as ABCDEF-XY12345."""
    org, sep, acct = account.strip().lower().partition("-")
    if not (sep and org and acct and query_id) or "." in account:
        return ""
    return f"https://app.snowflake.com/{org}/{acct}/#/compute/history/queries/{query_id}/detail"
