"""Conversation memory (FR-F1): the last few turns, each with its SQL and a result summary."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

MAX_TURNS = 6


@dataclass
class Turn:
    question: str
    standalone_question: str
    status: str
    sql: str = ""
    result_summary: str = ""


@dataclass
class Conversation:
    turns: deque[Turn] = field(default_factory=lambda: deque(maxlen=MAX_TURNS))

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)

    def clear(self) -> None:
        self.turns.clear()

    def __bool__(self) -> bool:
        return bool(self.turns)

    def history_text(self) -> str:
        lines = []
        for i, t in enumerate(self.turns, 1):
            lines.append(f"Turn {i} question: {t.standalone_question}")
            if t.sql:
                lines.append(f"Turn {i} SQL: {' '.join(t.sql.split())}")
            if t.result_summary:
                lines.append(f"Turn {i} result: {t.result_summary}")
        return "\n".join(lines)
