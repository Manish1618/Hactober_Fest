"""Fixed output shapes for every Gemma task (FR-B1). Model output must validate against these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["answerable", "ambiguous", "unsupported", "unsafe", "follow_up_unclear"]


class Understanding(BaseModel):
    """Follow-up rewrite and classification in one call (FR-A2, FR-A3)."""

    model_config = ConfigDict(extra="ignore")

    standalone_question: str = Field(
        description="The question rewritten to stand alone, using earlier turns if it is a "
        "follow-up. Same as the question when it already stands alone."
    )
    used_context: bool = Field(
        default=False, description="True when earlier turns were needed to rewrite it."
    )
    category: Category
    reason: str = Field(default="", description="One short sentence explaining the category.")
    clarifying_question: str = Field(
        default="", description="Only for ambiguous: one question for the user."
    )
    options: list[str] = Field(
        default_factory=list, description="Only for ambiguous: 2 to 4 short answer options."
    )
    alternative: str = Field(
        default="", description="Only for unsupported: a supported question to ask instead."
    )


class SqlDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sql: str = Field(description="One Snowflake SELECT statement answering the question.")
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Plain-language assumptions made while writing the SQL."
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(
        description="One to three plain sentences. Every number is a placeholder such as "
        "{r1.revenue}; never type a number yourself."
    )
    explanation: str = Field(
        default="", description="One or two sentences on how the answer was found, from the facts."
    )
