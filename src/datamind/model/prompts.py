"""Versioned prompt templates (FR-H5). Templates live in datamind/prompts/<name>_v<N>.txt."""

from __future__ import annotations

from importlib import resources

from datamind.config.models import DatasetConfig

DRAFT_SQL = "draft_sql_v1"


def load_template(name: str) -> str:
    return resources.files("datamind.prompts").joinpath(f"{name}.txt").read_text(encoding="utf-8")


def _neutralise(text: str) -> str:
    """Stop user text from closing or opening a data block."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def describe_schema(cfg: DatasetConfig) -> str:
    lines: list[str] = []
    for name, table in cfg.tables.items():
        lines.append(f"TABLE {name}: {table.description}")
        for col, c in table.columns.items():
            extra = f" [{c.type}]" if c.type else ""
            syn = f" (also called: {', '.join(c.synonyms)})" if c.synonyms else ""
            lines.append(f"  - {col}{extra}: {c.description}{syn}")
    if cfg.joins:
        lines.append("JOINS:")
        lines.extend(f"  - {j}" for j in cfg.joins)
    return "\n".join(lines)


def render_draft_sql(cfg: DatasetConfig, question: str) -> str:
    metrics = (
        "\n".join(
            f"- {name} = {m.sql} ({m.description}; synonyms: {', '.join(m.synonyms)})"
            for name, m in cfg.metrics.items()
        )
        or "- none"
    )
    defaults = "\n".join(f"- {d.term}: {d.assumption}" for d in cfg.defaults) or "- none"
    examples = "\n\n".join(f"Q: {e.question}\nSQL: {e.sql.strip()}" for e in cfg.examples) or "none"
    time_note = cfg.time.note if cfg.time and cfg.time.note else "No time column configured."
    return (
        load_template(DRAFT_SQL)
        .replace("{schema}", describe_schema(cfg))
        .replace("{metrics}", metrics)
        .replace("{defaults}", defaults)
        .replace("{time_note}", time_note)
        .replace("{examples}", examples)
        .replace("{question}", _neutralise(question))
    )
