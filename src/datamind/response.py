"""Request and response contract shared by the library, CLI and interface (guide section 6)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

MAX_QUESTION_CHARS = 500
EVIDENCE_ROWS = 50


class Status(StrEnum):
    ANSWERED = "ANSWERED"
    CLARIFY = "CLARIFY"
    NO_DATA = "NO_DATA"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class Clarification(BaseModel):
    question: str
    options: list[str]


class Evidence(BaseModel):
    question_as_understood: str = ""
    sql: str = ""
    query_id: str = ""
    backend: str = ""
    elapsed_seconds: float | None = None
    rows_returned: int = 0
    rows_shown: int = 0
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    dataset: str = ""
    licence_note: str = ""
    executed_at: str = ""
    data_latest_date: str = ""
    data_coverage: str = ""
    columns_used: list[str] = Field(default_factory=list)
    query_facts: list[str] = Field(default_factory=list)
    warehouse: str = ""
    role: str = ""
    verification: str = ""
    rejected_sql: str = ""
    context_used: str = ""
    repair_attempts: int = 0
    model: str = ""
    prompt_versions: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)


class Response(BaseModel):
    request_id: str
    status: Status
    answer: str = ""
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    clarification: Clarification | None = None
    evidence: Evidence = Field(default_factory=Evidence)
    suggestions: list[str] = Field(default_factory=list)
    reason: str = ""
