"""Load golden and held-out test questions."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

EVAL_DIR = Path(__file__).resolve().parent
GROUPS = {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"}
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
    follows: str | None = None
    notes: str | None = None


class GoldenSet(BaseModel):
    dataset: str
    split: str
    questions: list[GoldenQuestion] = Field(default_factory=list)


def load_set(dataset: str, split: str = "golden") -> GoldenSet:
    path = EVAL_DIR / split / f"{dataset}.yaml"
    return GoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
