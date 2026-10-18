"""Versioned prompt templates (FR-H5). Templates live in datamind/prompts/<name>_v<N>.txt."""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from datamind.config.models import DatasetConfig

UNDERSTAND = "understand_v3"
DRAFT_SQL = "draft_sql_v2"
ANSWER = "answer_v2"


def load_template(name: str) -> str:
    return resources.files("datamind.prompts").joinpath(f"{name}.txt").read_text(encoding="utf-8")


def _neutralise(text: str) -> str:
    """Stop user or data text from closing or opening a data block."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _fill(template: str, trusted: dict[str, str], untrusted: dict[str, str]) -> str:
    # One pass over the template only, so inserted text is never scanned for placeholders.
    values = {**trusted, **{k: _neutralise(v) for k, v in untrusted.items()}}
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def describe_schema(cfg: DatasetConfig, with_joins: bool = True) -> str:
    lines: list[str] = []
    for name, table in cfg.tables.items():
        lines.append(f"TABLE {name}: {table.description}")
        for col, c in table.columns.items():
            extra = f" [{c.type}]" if c.type else ""
            syn = f" (also called: {', '.join(c.synonyms)})" if c.synonyms else ""
            lines.append(f"  - {col}{extra}: {c.description}{syn}")
    if cfg.joins and with_joins:
        lines.append("JOINS:")
        lines.extend(f"  - {j}" for j in cfg.joins)
    return "\n".join(lines)


def _defaults(cfg: DatasetConfig) -> str:
    return "\n".join(f"- {d.term}: {d.assumption}" for d in cfg.defaults) or "- none"


def _time_note(cfg: DatasetConfig) -> str:
    return cfg.time.note if cfg.time and cfg.time.note else "No time column configured."


def render_understand(cfg: DatasetConfig, question: str, history: str) -> str:
    return _fill(
        load_template(UNDERSTAND),
        trusted={
            "dataset": f"{cfg.display_name}. {cfg.description}".strip(),
            "schema": describe_schema(cfg, with_joins=False),
            "defaults": _defaults(cfg),
            "time_note": _time_note(cfg),
        },
        untrusted={"history": history or "(no earlier turns)", "question": question},
    )


def render_draft_sql(
    cfg: DatasetConfig,
    question: str,
    latest_date: str = "unknown",
    failed_attempts: list[tuple[str, str]] | None = None,
) -> str:
    metrics = (
        "\n".join(
            f"- {name} = {m.sql} ({m.description}; synonyms: {', '.join(m.synonyms)})"
            for name, m in cfg.metrics.items()
        )
        or "- none"
    )
    examples = "\n\n".join(f"Q: {e.question}\nSQL: {e.sql.strip()}" for e in cfg.examples) or "none"
    repair = ""
    if failed_attempts:
        parts = [
            f"Attempt {i}:\n{sql.strip()}\nError: {err}"
            for i, (sql, err) in enumerate(failed_attempts, 1)
        ]
        repair = (
            "\nEarlier attempts failed. Write a corrected query that avoids these errors:\n"
            + "\n\n".join(parts)
            + "\n"
        )
    return _fill(
        load_template(DRAFT_SQL),
        trusted={
            "schema": describe_schema(cfg),
            "metrics": metrics,
            "defaults": _defaults(cfg),
            "time_note": _time_note(cfg),
            "latest_date": latest_date,
            "examples": examples,
            "repair": repair,
        },
        untrusted={"question": question},
    )


def format_rows(columns: list[str], rows: list[list[Any]]) -> str:
    """Rows numbered from 1, one per line, as column=value pairs."""
    return "\n".join(
        f"r{i}: " + ", ".join(f"{c}={v}" for c, v in zip(columns, row, strict=True))
        for i, row in enumerate(rows, 1)
    )


def render_answer(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    row_count: int,
    facts: list[str] | None = None,
) -> str:
    return _fill(
        load_template(ANSWER),
        trusted={"row_count": str(row_count)},
        untrusted={
            "question": question,
            "rows": format_rows(columns, rows),
            "facts": "\n".join(facts or []) or "(none)",
        },
    )
