"""Fixed output shapes for every Gemma task (FR-B1). Model output must validate against these."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SqlDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sql: str = Field(description="One Snowflake SELECT statement answering the question.")
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Plain-language assumptions made while writing the SQL."
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
