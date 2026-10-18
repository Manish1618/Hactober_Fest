"""Evaluation runner (PRD section 13, FR-K1).

Each golden question goes through the full pipeline. A question passes when the status matches
and, for questions with ground_truth_sql, the system's result matches the ground-truth result:
- every ground-truth column must match some system column (extra system columns are allowed);
- numbers match to 6 significant digits, so rounding and DECIMAL vs FLOAT do not matter;
- row order is compared only when order_matters is set.
"""

from __future__ import annotations

import copy
import itertools
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from datamind.evaluation.golden import GoldenQuestion, GoldenSet
from datamind.pipeline.context import Conversation
from datamind.pipeline.facts import extract_facts
from datamind.pipeline.run import Pipeline
from datamind.pipeline.verify import verify
from datamind.snowflake_io.executor import ExecutionError

FETCH_ROWS = 5000  # enough for every golden result; more rows than this is a failure anyway
GATE_GROUPS = ("T1", "T2")
GATE_ACCURACY = 0.60
# ERROR needs an outage, so `datamind statuses` demonstrates it instead of the golden set.
TRUST_STATUSES = {"ANSWERED", "CLARIFY", "NO_DATA", "UNSUPPORTED", "BLOCKED"}
EVIDENCE_FIELDS = (
    "question_as_understood",
    "sql",
    "query_id",
    "columns",
    "tables",
    "query_facts",
    "dataset",
    "licence_note",
    "executed_at",
    "data_latest_date",
    "model",
    "prompt_versions",
)


def _norm(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        f = float(value)
        return float(f"{f:.6g}") if f else 0.0
    if isinstance(value, datetime):
        return (
            value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        s = value.strip()
        try:  # numbers that came back as text
            return _norm(Decimal(s))
        except Exception:
            return s
    return str(value)


def compare_results(
    truth: list[tuple], got: list[tuple], order_matters: bool = False, limited: bool = False
) -> tuple[bool, str]:
    """True when every ground-truth column is matched by a system column over the same rows.

    With order_matters, rows tied on a sort column may come in any order, and when the ground
    truth is cut by a LIMIT the last tied block may hold different (equally correct) rows.
    """
    if len(truth) != len(got):
        return False, f"expected {len(truth)} rows, got {len(got)}"
    if not truth:
        return True, "both empty"
    t = [[_norm(v) for v in r] for r in truth]
    g = [[_norm(v) for v in r] for r in got]
    ok, detail = _compare(t, g, order_matters)
    if not ok and order_matters and _match_with_ties(t, g, limited):
        return True, "match (tied rows in a different order)"
    return ok, detail


def _compare(t: list[list], g: list[list], order_matters: bool) -> tuple[bool, str]:
    t_cols, g_cols = list(zip(*t, strict=True)), list(zip(*g, strict=True))

    def same(a, b) -> bool:
        return list(a) == list(b) if order_matters else Counter(a) == Counter(b)

    candidates = [[i for i, gc in enumerate(g_cols) if same(tc, gc)] for tc in t_cols]
    for j, c in enumerate(candidates):
        if not c:
            return False, f"no result column matches expected column {j + 1}"
    # Columns match one by one; check they also line up row by row.
    expected = [tuple(r) for r in t]
    for mapping in itertools.islice(itertools.product(*candidates), 200):
        projected = [tuple(r[i] for i in mapping) for r in g]
        if (projected == expected) if order_matters else Counter(projected) == Counter(expected):
            return True, "match"
    return False, "columns match separately but rows do not line up"


def _match_with_ties(t: list[list], g: list[list], limited: bool) -> bool:
    """Ordered match where a sort column has ties: each tied block must match as a set."""
    g_cols = list(zip(*g, strict=True))
    for key in zip(*t, strict=True):
        if len(set(key)) == len(key) and not limited:
            continue  # no ties, and nothing cut off by a LIMIT
        if not any(list(key) == list(gc) for gc in g_cols):
            continue
        blocks = [list(grp) for _, grp in itertools.groupby(range(len(key)), key.__getitem__)]
        if limited:
            blocks = blocks[:-1]  # the LIMIT may cut a tie: any rows from that block are right
        if all(_compare([t[i] for i in b], [g[i] for i in b], False)[0] for b in blocks):
            return True
    return False


@dataclass
class QuestionResult:
    id: str
    group: str
    question: str
    expected_status: str
    status: str
    passed: bool
    detail: str
    sql: str = ""
    assumptions: list[str] = field(default_factory=list)
    repair_attempts: int = 0
    seconds: float = 0.0
    faithful: bool | None = None  # ANSWERED only: every number traces, explanation covers facts
    evidence_complete: bool | None = None  # ANSWERED and NO_DATA
    trust_detail: str = ""
    answer: str = ""
    explanation: str = ""
    rejected_sql: str = ""


@dataclass
class EvalReport:
    dataset: str
    split: str
    backend: str
    model: str
    started_at: str
    results: list[QuestionResult] = field(default_factory=list)

    def by_group(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for r in self.results:
            p, n = out.get(r.group, (0, 0))
            out[r.group] = (p + r.passed, n + 1)
        return dict(sorted(out.items()))

    def trust(self) -> tuple[bool, str]:
        """Phase 2 gate: every status seen, 100% faithfulness, 100% evidence completeness."""
        faithful = [r.faithful for r in self.results if r.faithful is not None]
        complete = [r.evidence_complete for r in self.results if r.evidence_complete is not None]
        statuses = sorted({r.status for r in self.results})
        missing = sorted(TRUST_STATUSES - set(statuses))
        ok = all(faithful) and all(complete) and not missing
        summary = (
            f"faithfulness {sum(faithful)}/{len(faithful)}, "
            f"evidence {sum(complete)}/{len(complete)}, statuses seen {', '.join(statuses)}"
            + (f" (missing {', '.join(missing)})" if missing else "")
        )
        return ok, summary

    def gate(self) -> tuple[bool, str]:
        groups = self.by_group()
        parts, ok = [], True
        for g in GATE_GROUPS:
            if g not in groups:
                continue
            p, n = groups[g]
            ok &= p / n >= GATE_ACCURACY
            parts.append(f"{g} {p}/{n} = {p / n:.0%}")
        if "T7" in groups:
            p, n = groups["T7"]
            ok &= p == n
            parts.append(f"T7 blocked {p}/{n}")
        return ok, ", ".join(parts)

    def to_dict(self) -> dict:
        ok, summary = self.gate()
        trust_ok, trust_summary = self.trust()
        return {
            "trust": {"passed": trust_ok, "summary": trust_summary},
            "dataset": self.dataset,
            "split": self.split,
            "backend": self.backend,
            "model": self.model,
            "started_at": self.started_at,
            "groups": {g: {"passed": p, "total": n} for g, (p, n) in self.by_group().items()},
            "gate": {"passed": ok, "summary": summary},
            "results": [asdict(r) for r in self.results],
        }

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "").replace("-", "")[:15]
        path = directory / f"{self.dataset}-{self.split}-{stamp}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path


def _full_rows(pipeline: Pipeline, sql: str) -> list[tuple]:
    return pipeline.executor.run(sql, request_id="eval", max_rows=FETCH_ROWS).rows


def evaluate_question(
    pipeline: Pipeline, q: GoldenQuestion, conversation: Conversation | None = None
) -> QuestionResult:
    started = time.perf_counter()
    resp = pipeline.ask(q.question, conversation)
    ev = resp.evidence
    res = QuestionResult(
        id=q.id,
        group=q.group,
        question=q.question,
        expected_status=q.expected_status,
        status=resp.status.value,
        passed=False,
        detail="",
        sql=ev.sql,
        assumptions=list(resp.assumptions),
        repair_attempts=ev.repair_attempts,
    )
    res.answer, res.explanation, res.rejected_sql = resp.answer, resp.explanation, ev.rejected_sql
    _check_trust(pipeline, resp, res)
    if res.status != q.expected_status:
        res.detail = f"expected {q.expected_status}, got {res.status}: {resp.reason}".strip()
    elif q.ground_truth_sql:
        try:
            truth = _full_rows(pipeline, q.ground_truth_sql)
            got = (
                _full_rows(pipeline, ev.sql)
                if ev.rows_returned > ev.rows_shown
                else [tuple(r) for r in ev.rows]
            )
            res.passed, res.detail = compare_results(
                truth, got, q.order_matters, limited="LIMIT" in q.ground_truth_sql.upper()
            )
        except ExecutionError as e:
            res.detail = f"could not run ground truth or system SQL: {e}"
    else:
        res.passed, res.detail = True, "status matches"
    res.seconds = round(time.perf_counter() - started, 2)
    return res


def _check_trust(pipeline: Pipeline, resp, res: QuestionResult) -> None:
    """Faithfulness and evidence completeness, checked independently of the pipeline's own run."""
    ev = resp.evidence
    notes = []
    if resp.status.value in ("ANSWERED", "NO_DATA"):
        empty = [f for f in EVIDENCE_FIELDS if not getattr(ev, f)]
        if resp.status.value == "ANSWERED" and not ev.rows:
            empty.append("rows")
        if not resp.explanation:
            empty.append("explanation")
        res.evidence_complete = not empty
        if empty:
            notes.append(f"evidence missing {', '.join(empty)}")
    if resp.status.value == "ANSWERED":
        limit_added = not any(line.startswith("Row limit") for line in ev.query_facts)
        facts = extract_facts(ev.sql, pipeline.catalog, pipeline.date_columns, limit_added)
        v = verify(
            resp.answer,
            resp.explanation,
            ev.question_as_understood,
            ev.rows,
            facts,
            ev.rows_returned,
            pipeline.dataset,
        )
        res.faithful = v.passed
        if not v.passed:
            notes.append(f"faithfulness {v.summary()}")
    res.trust_detail = "; ".join(notes)


def run_eval(
    pipeline: Pipeline,
    golden: GoldenSet,
    groups: set[str] | None = None,
    progress=None,
    workers: int = 1,
) -> EvalReport:
    """Run the golden questions. With workers > 1, independent questions run in parallel
    (a hosted model is network-bound); follow-ups always run after the question they follow.
    """
    report = EvalReport(
        dataset=golden.dataset,
        split=golden.split,
        backend=pipeline.executor.backend,
        model=pipeline.model.name,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    selected = [q for q in golden.questions if not groups or q.group in groups]
    pipeline.latest_date()  # read once before threads start
    conversations: dict[str, Conversation] = {}
    results: dict[str, QuestionResult] = {}

    def one(q: GoldenQuestion) -> QuestionResult:
        # A follow-up continues the conversation of the question it follows.
        base = conversations.get(q.follows) if q.follows else None
        own = copy.deepcopy(base) if base is not None else Conversation()
        result = evaluate_question(pipeline, q, own)
        conversations[q.id] = own
        if progress:
            progress(result)
        return result

    first = [q for q in selected if not q.follows]
    later = [q for q in selected if q.follows]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for q, r in zip(first, pool.map(one, first), strict=True):
            results[q.id] = r
    for q in later:
        results[q.id] = one(q)
    report.results = [results[q.id] for q in selected]
    return report
