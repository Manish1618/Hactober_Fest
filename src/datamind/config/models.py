"""Typed shape of a dataset configuration (dataset.yaml + examples.yaml)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_JOIN_RE = re.compile(r"^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnowflakeLocation(_Strict):
    database: str
    schema_: str = Field(alias="schema")
    role: str = "DATAMIND_READER"


class TimeAnchor(_Strict):
    anchor_table: str
    anchor_column: str
    note: str | None = None


class DefaultTerm(_Strict):
    term: str
    assumption: str
    metric: str | None = None


class Metric(_Strict):
    description: str
    sql: str
    unit: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class Column(_Strict):
    type: str | None = None
    description: str
    unit: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class Table(_Strict):
    description: str
    columns: dict[str, Column]

    @field_validator("columns")
    @classmethod
    def _upper_columns(cls, v: dict[str, Column]) -> dict[str, Column]:
        return {k.upper(): c for k, c in v.items()}


class Join(_Strict):
    left_table: str
    left_column: str
    right_table: str
    right_column: str

    @classmethod
    def parse(cls, text: str) -> Join:
        m = _JOIN_RE.match(text)
        if not m:
            raise ValueError(f"join must look like TABLE.COL = TABLE.COL, got {text!r}")
        lt, lc, rt, rc = (g.upper() for g in m.groups())
        return cls(left_table=lt, left_column=lc, right_table=rt, right_column=rc)

    def __str__(self) -> str:
        return f"{self.left_table}.{self.left_column} = {self.right_table}.{self.right_column}"


class NumberFormat(_Strict):
    prefix: str = ""
    suffix: str = ""
    decimals: int = 2


class Example(_Strict):
    question: str
    sql: str


class DatasetConfig(_Strict):
    dataset: str
    display_name: str
    description: str = ""
    licence_note: str = ""
    snowflake: SnowflakeLocation
    time: TimeAnchor | None = None
    defaults: list[DefaultTerm] = Field(default_factory=list)
    metrics: dict[str, Metric] = Field(default_factory=dict)
    tables: dict[str, Table]
    joins: list[Join] = Field(default_factory=list)
    formats: dict[str, NumberFormat] = Field(default_factory=dict)
    starter_questions: list[str] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)

    @field_validator("tables")
    @classmethod
    def _upper_tables(cls, v: dict[str, Table]) -> dict[str, Table]:
        return {k.upper(): t for k, t in v.items()}

    @field_validator("joins", mode="before")
    @classmethod
    def _parse_joins(cls, v: object) -> object:
        if isinstance(v, list):
            return [Join.parse(j) if isinstance(j, str) else j for j in v]
        return v

    @model_validator(mode="after")
    def _check_references(self) -> DatasetConfig:
        def known(table: str, column: str) -> bool:
            return table in self.tables and column in self.tables[table].columns

        for j in self.joins:
            for t, c in ((j.left_table, j.left_column), (j.right_table, j.right_column)):
                if not known(t, c):
                    raise ValueError(f"join {j} refers to unknown column {t}.{c}")
        if self.time and not known(self.time.anchor_table.upper(), self.time.anchor_column.upper()):
            anchor = f"{self.time.anchor_table}.{self.time.anchor_column}"
            raise ValueError(f"time anchor {anchor} is not a known column")
        for d in self.defaults:
            if d.metric and d.metric not in self.metrics:
                raise ValueError(f"default {d.term!r} refers to unknown metric {d.metric!r}")
        for name, m in self.metrics.items():
            if m.unit and self.formats and m.unit not in self.formats:
                raise ValueError(f"metric {name!r} uses unit {m.unit!r} with no entry in formats")
        return self

    def catalog(self) -> dict[str, set[str]]:
        """Tables and columns described by this configuration, upper-cased."""
        return {t: set(tbl.columns) for t, tbl in self.tables.items()}
