"""Load golden and held-out test questions from evaluation/<split>/<dataset>.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datamind.config.loader import datasets_dir

GROUPS = {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"}  # T9: near-miss traps
STATUSES = {"ANSWERED", "CLARIFY", "NO_DATA", "UNSUPPORTED", "BLOCKED", "ERROR"}


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: str
    question: str
    expected_status: str
    ground_truth_sql: str | None = None
    attack_sql: str | None = None
    order_matters: bool = False
    expected_assumption: str | None = None
    ambiguous: bool = False  # a vague question: CLARIFY or a stated assumption both count
    follows: str | None = None
    change_sql: list[str] = Field(default_factory=list)  # T8: edits made between two asks
    notes: str | None = None

    @field_validator("group")
    @classmethod
    def _group(cls, v: str) -> str:
        if v not in GROUPS:
            raise ValueError(f"unknown group {v!r}")
        return v

    @field_validator("expected_status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"unknown status {v!r}")
        return v


class GoldenSet(BaseModel):
    dataset: str
    split: str
    questions: list[GoldenQuestion] = Field(default_factory=list)


def evaluation_dir() -> Path:
    return datasets_dir().parent / "evaluation"


def load_set(dataset: str, split: str = "golden") -> GoldenSet:
    path = evaluation_dir() / split / f"{dataset}.yaml"
    return GoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
