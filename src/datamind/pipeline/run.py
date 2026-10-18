"""The question-to-answer pipeline (PRD 8.1).

intake -> guard (code) -> understand (Gemma: rewrite + classify) -> draft SQL (Gemma)
-> validate (code) -> execute (Snowflake) -> repair up to 2 times -> answer (Gemma placeholders,
filled by code) -> response with evidence.

Query facts, the full number verifier and explanation checks arrive in phase 2.
"""

from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal

import sqlglot
from sqlglot import exp

from datamind.config.models import DatasetConfig
from datamind.model.adapter import ModelAdapter, ModelError
from datamind.model.prompts import (
    ANSWER,
    DRAFT_SQL,
    UNDERSTAND,
    render_answer,
    render_draft_sql,
    render_understand,
)
from datamind.model.schemas import AnswerDraft, SqlDraft, Understanding
from datamind.pipeline.answer import (
    explain,
    fill_placeholders,
    result_summary,
    stray_numbers,
    template_answer,
)
from datamind.pipeline.context import Conversation, Turn
from datamind.pipeline.facts import QueryFacts, extract_facts
from datamind.pipeline.guards import unsafe_reason
from datamind.pipeline.validator import Catalog, validate
from datamind.pipeline.verify import untraced_numbers, verify
from datamind.response import (
    EVIDENCE_ROWS,
    MAX_QUESTION_CHARS,
    Clarification,
    Evidence,
    Response,
    Status,
)
from datamind.snowflake_io.executor import (
    BackendUnavailable,
    ExecutionError,
    Executor,
    QueryResult,
    QueryTimeout,
)

log = logging.getLogger("datamind")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_REPAIRS = 2  # FR-B7
ANSWER_ROWS = 20  # rows shown to the model when phrasing the answer (NFR-15 caps at 50)
# Validator codes that mean the model tried to do something unsafe, not just made a mistake.
_UNSAFE_CODES = {"not_select", "denied_operation", "denied_function", "multiple_statements"}


def clean_question(text: str) -> str:
    return re.sub(r"\s+", " ", _CONTROL_CHARS.sub(" ", text or "")).strip()


def _cell(value):
    """JSON-friendly cell value for the evidence panel."""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class Pipeline:
    def __init__(
        self,
        dataset: DatasetConfig,
        model: ModelAdapter,
        executor: Executor,
        max_rows: int = 1000,
        phrase_answers: bool = True,
    ):
        self.dataset = dataset
        self.model = model
        self.executor = executor
        self.max_rows = max_rows
        self.phrase_answers = phrase_answers
        self.catalog = Catalog(
            tables=dataset.catalog(),
            database=dataset.snowflake.database,
            schema=dataset.snowflake.schema_,
            joins=dataset.join_pairs(),
        )
        self._latest_date: str | None = None
        self._coverage = ""
        self.hidden_tables: list[str] | None = None  # set on the first question (US-14)
        self.hidden_terms: list[str] = []  # words that name hidden data, such as "gdp per capita"
        self.date_columns = {
            f"{t}.{c}"
            for t, table in dataset.tables.items()
            for c, col in table.columns.items()
            if (col.type or "").upper() in ("DATE", "TIMESTAMP", "TIMESTAMP_NTZ")
        }

    # ---- helpers -------------------------------------------------------------------------

    def _read_coverage(self) -> None:
        t = self.dataset.time
        self._latest_date, self._coverage = "unknown", ""
        if not t:
            return
        try:
            r = self.executor.run(
                f"SELECT MIN({t.anchor_column}), MAX({t.anchor_column}) FROM {t.anchor_table}",
                request_id="time-anchor",
                max_rows=1,
            )
        except ExecutionError as e:
            log.warning("could not read the data's date range: %s", e)
            return
        if r.rows and r.rows[0][1] is not None:
            first, last = (str(_cell(v)) for v in r.rows[0])
            self._latest_date = last
            self._coverage = f"{t.anchor_table}.{t.anchor_column} from {first} to {last}"

    def check_access(self) -> list[str]:
        """Drop configured tables the Snowflake role cannot see (US-14, FR-G4, EC-10).

        INFORMATION_SCHEMA lists only what the current role may read. If the listing fails or
        is empty (for example the offline fixture), nothing is hidden. Hidden tables leave the
        schema Gemma sees and the validator's allow-list, so they cannot be queried or guessed.
        """
        if self.hidden_tables is not None:
            return self.hidden_tables
        self.hidden_tables = []
        schema = self.dataset.snowflake.schema_.upper().replace("'", "")
        try:
            r = self.executor.run(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                f"WHERE UPPER(TABLE_SCHEMA) = '{schema}'",
                request_id="access-check",
                max_rows=10000,
            )
        except ExecutionError as e:
            log.warning("could not list the tables this role can see: %s", e)
            return self.hidden_tables
        visible = {str(row[0]).upper() for row in r.rows}
        if not visible:
            return self.hidden_tables
        self.hidden_tables = sorted(t for t in self.dataset.tables if t not in visible)
        if self.hidden_tables:
            log.info("tables hidden from role: %s", ", ".join(self.hidden_tables))
            kept = {t: v for t, v in self.dataset.tables.items() if t in visible}
            joins = [j for j in self.dataset.joins if {j.left_table, j.right_table} <= set(kept)]
            metrics = {
                k: m for k, m in self.dataset.metrics.items() if _metric_tables(m.sql) <= set(kept)
            }
            defaults = [d for d in self.dataset.defaults if not d.metric or d.metric in metrics]
            self.hidden_terms = _hidden_terms(self.dataset, kept, metrics)
            self.dataset = self.dataset.model_copy(
                update={"tables": kept, "joins": joins, "metrics": metrics, "defaults": defaults}
            )
            self.catalog = Catalog(
                tables=self.dataset.catalog(),
                database=self.dataset.snowflake.database,
                schema=self.dataset.snowflake.schema_,
                joins=self.dataset.join_pairs(),
            )
        return self.hidden_tables

    def _names_hidden(self, question: str) -> bool:
        """Whether the question asks about data the role cannot see (answer it, never swap)."""
        text = question.lower()
        return any(re.search(rf"\b{re.escape(t)}\b", text) for t in self.hidden_terms)

    def _no_access_reason(self, hidden: list[str]) -> str:
        return (
            f"Your Snowflake role ({self.dataset.snowflake.role}) has no access to "
            f"{', '.join(hidden)}, so questions about them cannot be answered here."
        )

    def latest_date(self) -> str:
        """Latest value of the configured time column; anchors relative dates (FR-A4)."""
        if self._latest_date is None:
            self._read_coverage()
        return self._latest_date or "unknown"

    def coverage(self) -> str:
        self.latest_date()
        return self._coverage

    def _draft_and_run(
        self, question: str, request_id: str, evidence: Evidence
    ) -> tuple[SqlDraft | None, QueryResult | None, str, str, bool]:
        """Draft, validate and execute with up to MAX_REPAIRS repairs.

        Returns (draft, result, failure_code, failure_reason, limit_added); result is None on
        failure. BackendUnavailable and QueryTimeout propagate: new SQL will not fix them.
        """
        failed: list[tuple[str, str]] = []
        draft: SqlDraft | None = None
        code, reason = "", ""
        for attempt in range(MAX_REPAIRS + 1):
            draft = self.model.generate(
                render_draft_sql(self.dataset, question, self.latest_date(), failed),
                SqlDraft,
                prompt_version=DRAFT_SQL,
            )
            checked = validate(draft.sql, self.catalog, max_rows=self.max_rows)
            if not checked.ok:
                code, reason = checked.code, checked.reason
                evidence.limits.append(f"Rejected SQL ({checked.code}): {checked.reason}")
                evidence.rejected_sql = draft.sql
                if code in _UNSAFE_CODES:
                    return draft, None, code, reason, False  # never "repair" an unsafe draft
                failed.append((draft.sql, reason))
                evidence.repair_attempts = attempt + 1 if attempt < MAX_REPAIRS else attempt
                continue
            evidence.sql = checked.sql
            evidence.tables = checked.tables
            try:
                result = self.executor.run(
                    checked.sql, request_id=request_id, max_rows=self.max_rows
                )
            except (BackendUnavailable, QueryTimeout):
                raise
            except ExecutionError as e:
                code, reason = "execution_error", str(e)
                evidence.rejected_sql = checked.sql
                evidence.sql = ""
                failed.append((checked.sql, reason))
                evidence.repair_attempts = attempt + 1 if attempt < MAX_REPAIRS else attempt
                continue
            evidence.rejected_sql = ""
            return draft, result, "", "", checked.limit_added
        return draft, None, code, reason, False

    def _phrase(
        self,
        question: str,
        columns: list[str],
        rows: list[list],
        n: int,
        facts: QueryFacts,
    ) -> tuple[str, str, str]:
        """Answer, explanation and verification summary (FR-C1, FR-C2, FR-D2).

        Gemma phrases with placeholders that code fills; the verifier then checks every number
        and that the explanation covers every key fact. Anything that fails falls back to the
        code-built text, which is faithful by construction.
        """
        shown = rows[:ANSWER_ROWS]
        code_answer = template_answer(columns, rows, n)
        code_explanation = explain(facts, self.dataset)
        if not self.phrase_answers:
            v = verify(code_answer, code_explanation, question, rows, facts, n, self.dataset)
            return code_answer, code_explanation, "code-built answer: " + v.summary()
        try:
            out = self.model.generate(
                render_answer(question, columns, shown, n, facts.lines()),
                AnswerDraft,
                prompt_version=ANSWER,
            )
        except ModelError as e:
            log.warning("answer phrasing failed, using code-built answer: %s", e)
            return code_answer, code_explanation, "code-built answer (model unavailable)"
        answer, unresolved = fill_placeholders(out.answer, columns, shown)
        answer = answer.strip()
        explanation = out.explanation.strip()
        notes = []
        if unresolved or not answer:
            answer, notes = code_answer, [f"model answer had bad references {unresolved}"]
        elif stray_numbers(out.answer, question) and not self._traces(answer, question, rows, n):
            answer, notes = code_answer, ["model typed its own numbers"]
        v_expl = verify("", explanation, question, rows, facts, n, self.dataset)
        if not explanation or not v_expl.passed:
            notes.append(f"model explanation {v_expl.summary() if explanation else 'empty'}")
            explanation = code_explanation
        final = verify(answer, explanation, question, rows, facts, n, self.dataset)
        if not final.passed:  # should not happen; never show an unverified number
            notes.append(f"final check {final.summary()}")
            answer, explanation = code_answer, code_explanation
            final = verify(answer, explanation, question, rows, facts, n, self.dataset)
        summary = final.summary()
        return answer, explanation, summary + (f" (replaced: {'; '.join(notes)})" if notes else "")

    @staticmethod
    def _traces(answer: str, question: str, rows: list[list], n: int) -> bool:
        return not untraced_numbers(answer, [v for r in rows for v in r], question, [n, len(rows)])

    def _fix_case(
        self, sql: str, facts: QueryFacts, limit_added: bool, request_id: str
    ) -> tuple[str, QueryFacts, QueryResult, str] | None:
        """Rerun an empty query once when a filter value only differs from a stored one by case.

        'AFRICA' for 'Africa', or 'India' for 'INDIA': the model cannot see the stored casing.
        """
        for f in facts.filters:
            if f.op not in ("=", "IN") or f.column == facts.date_column:
                continue
            table, _, col = f.column.rpartition(".")
            if table not in self.catalog.tables:
                continue
            for value in f.values:
                if _exists(self, table, col, value, request_id):
                    continue
                stored = _same_but_case(self, table, col, value, request_id)
                if not stored:
                    continue
                checked = validate(
                    _replace_literal(sql, value, stored), self.catalog, max_rows=self.max_rows
                )
                if not checked.ok:
                    return None
                try:
                    result = self.executor.run(
                        checked.sql, request_id=request_id, max_rows=self.max_rows
                    )
                except ExecutionError:
                    return None
                if result.row_count == 0:
                    return None
                new_facts = extract_facts(checked.sql, self.catalog, self.date_columns, limit_added)
                return (
                    checked.sql,
                    new_facts,
                    result,
                    f"Matched {value!r} to the stored value {stored!r}.",
                )
        return None

    def _diagnose_empty(
        self, facts: QueryFacts, question: str, request_id: str
    ) -> tuple[str, list[str]]:
        """Likely cause of an empty result and valid alternatives (FR-E2, EC-03, EC-04)."""
        for f in facts.filters:
            if f.op not in ("=", "IN", "LIKE", "ILIKE") or f.column == facts.date_column:
                continue
            table, _, col = f.column.rpartition(".")
            if table not in self.catalog.tables:
                continue
            missing = []
            for value in f.values:
                if not _exists(self, table, col, value, request_id):
                    missing.append(value)
            if not missing:
                continue
            valid = _top_values(self, table, col, request_id)
            cause = f"There is no {col} equal to {', '.join(repr(m) for m in missing)} in {table}."
            if valid:
                cause += f" Values that exist include {', '.join(valid[:8])}."
            suggestions = [
                _swap(question, missing[0], v) for v in valid[:3] if _swap(question, missing[0], v)
            ]
            return cause, suggestions
        if facts.date_range and self.coverage():
            return (
                f"No rows match. The query asked for {facts.date_range[0]} to "
                f"{facts.date_range[1]}; the data covers {self.coverage()}.",
                [],
            )
        return (
            "Every filter value exists on its own, but no rows match all of them together.",
            [],
        )

    # ---- main entry ----------------------------------------------------------------------

    def ask(self, question: str, conversation: Conversation | None = None) -> Response:
        request_id = uuid.uuid4().hex[:16]
        q = clean_question(question)
        evidence = Evidence(
            question_as_understood=q,
            dataset=self.dataset.display_name,
            licence_note=self.dataset.licence_note,
            model=self.model.name,
            backend=self.executor.backend,
        )

        def done(status: Status, **kw) -> Response:
            resp = Response(request_id=request_id, status=status, evidence=evidence, **kw)
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "status": status.value,
                    "sql": evidence.sql,
                    "rows": evidence.rows_returned,
                    "model": evidence.model,
                    "prompt_versions": evidence.prompt_versions,
                },
            )
            if conversation is not None:
                conversation.add(
                    Turn(
                        question=q,
                        standalone_question=evidence.question_as_understood,
                        status=status.value,
                        sql=evidence.sql,
                        result_summary=result_summary(
                            evidence.columns, evidence.rows, evidence.rows_returned
                        )
                        if evidence.query_id
                        else "",
                    )
                )
            return resp

        # 1. Intake
        if not q:
            return done(Status.UNSUPPORTED, reason="Please type a question.")
        if len(q) > MAX_QUESTION_CHARS:
            return done(
                Status.UNSUPPORTED,
                reason=f"Questions are limited to {MAX_QUESTION_CHARS} characters.",
            )

        if not _looks_english(q):
            return done(
                Status.UNSUPPORTED,
                reason="DataMind only understands questions in English for now.",
                suggestions=self.dataset.starter_questions[:3],
            )

        # 2. Code guard: write requests never reach the model or Snowflake.
        unsafe = unsafe_reason(q)
        if unsafe:
            log.warning("blocked request %s: %s", request_id, unsafe)
            return done(Status.BLOCKED, reason=unsafe)

        # 3. Understand: rewrite follow-ups and classify (Gemma).
        hidden = self.check_access()
        if self._names_hidden(q):
            return done(Status.UNSUPPORTED, reason=self._no_access_reason(hidden))
        history = conversation.history_text() if conversation else ""
        try:
            evidence.prompt_versions.append(UNDERSTAND)
            u = self.model.generate(
                render_understand(self.dataset, q, history),
                Understanding,
                prompt_version=UNDERSTAND,
            )
        except ModelError as e:
            return done(Status.ERROR, reason=f"The language model could not help: {e}")

        standalone = clean_question(u.standalone_question) or q
        evidence.question_as_understood = standalone
        if u.used_context and conversation:
            last = conversation.turns[-1]
            evidence.context_used = f"Previous question: {last.standalone_question}"

        if self._names_hidden(standalone):
            return done(Status.UNSUPPORTED, reason=self._no_access_reason(hidden))

        # The rewritten question gets the same code guard as the original.
        unsafe = unsafe_reason(standalone)
        if u.category == "unsafe" or unsafe:
            return done(Status.BLOCKED, reason=unsafe or u.reason or "This request is not allowed.")
        if u.category == "follow_up_unclear" or (u.used_context and not history):
            return done(
                Status.CLARIFY,
                reason="This looks like a follow-up, but there is no earlier question to build on.",
                clarification=Clarification(
                    question="What would you like to know? Please ask the full question.",
                    options=self.dataset.starter_questions[:3],
                ),
            )
        if u.category == "ambiguous":
            options = [o for o in u.options if o.strip()][:4]
            if len(options) >= 2:
                return done(
                    Status.CLARIFY,
                    reason=u.reason,
                    clarification=Clarification(
                        question=u.clarifying_question or "Which did you mean?", options=options
                    ),
                )
            # No usable options: answer with the most common reading instead of asking badly.
        if u.category == "unsupported":
            suggestions = [u.alternative] if u.alternative else []
            reason = u.reason or "The data cannot answer this question."
            if hidden:  # EC-10: say what this role cannot see instead of guessing
                reason += " " + self._no_access_reason(hidden)
            return done(Status.UNSUPPORTED, reason=reason, suggestions=suggestions)

        # 4-7. Draft, validate, execute, repair.
        evidence.data_latest_date = self.latest_date()
        evidence.data_coverage = self.coverage()
        evidence.prompt_versions.append(DRAFT_SQL)
        try:
            draft, result, code, reason, limit_added = self._draft_and_run(
                standalone, request_id, evidence
            )
        except ModelError as e:
            return done(Status.ERROR, reason=f"The language model could not help: {e}")
        except BackendUnavailable as e:
            return done(Status.ERROR, reason=f"Snowflake is not reachable right now. {e}")
        except QueryTimeout:
            return done(
                Status.ERROR,
                reason="The query took longer than the time limit. Try a narrower question, "
                "for example one year or one region.",
            )
        assumptions = draft.assumptions if draft else []
        if result is None:
            if code in _UNSAFE_CODES:
                return done(Status.BLOCKED, reason=reason)
            return done(
                Status.ERROR,
                reason="I could not build a working query for this question "
                f"after {MAX_REPAIRS} repair attempts. Last error: {reason}",
                assumptions=assumptions,
            )

        facts = extract_facts(evidence.sql, self.catalog, self.date_columns, limit_added)
        if result.row_count == 0:  # a filter value written in the wrong case: fix it once
            fixed = self._fix_case(evidence.sql, facts, limit_added, request_id)
            if fixed:
                evidence.sql, facts, result, note = fixed
                assumptions = [*assumptions, note]
        evidence.query_facts = facts.lines()
        evidence.columns_used = facts.columns_used
        evidence.role = str(result.meta.get("role") or "")
        evidence.warehouse = str(result.meta.get("warehouse") or "")
        if facts.date_range:
            assumptions = [
                *assumptions,
                f"Dates used: {facts.date_range[0]} to {facts.date_range[1]} "
                f"({facts.date_column.split('.')[-1]}).",
            ]
        elif self.dataset.time and self.coverage() and not _time_filtered(facts, self):
            assumptions = [
                *assumptions,
                f"No time period given: all dates in the data ({self.coverage()}).",
            ]
        evidence.query_id = result.query_id
        evidence.elapsed_seconds = result.elapsed_seconds
        evidence.executed_at = result.executed_at
        evidence.columns = result.columns
        evidence.rows_returned = result.row_count
        evidence.rows = [[_cell(v) for v in row] for row in result.rows[:EVIDENCE_ROWS]]
        evidence.rows_shown = len(evidence.rows)
        if result.truncated or (limit_added and result.row_count >= self.max_rows):
            evidence.limits.append(f"Only the first {self.max_rows} rows were fetched.")
        if evidence.rows_shown < result.row_count:
            evidence.limits.append(f"Showing {evidence.rows_shown} of {result.row_count} rows.")

        all_null = result.row_count > 0 and all(v is None for row in result.rows for v in row)
        if result.row_count == 0 or all_null:
            if all_null:  # e.g. SUM over no matching rows returns one NULL
                evidence.limits.append("The query returned only empty (NULL) values.")
            cause, suggestions = self._diagnose_empty(facts, standalone, request_id)
            return done(
                Status.NO_DATA,
                answer="The query ran but no rows matched.",
                reason=cause,
                explanation=explain(facts, self.dataset),
                assumptions=assumptions,
                suggestions=suggestions,
            )

        # 9. Answer: Gemma phrases with placeholders; code fills every number.
        if self.phrase_answers:
            evidence.prompt_versions.append(ANSWER)
        answer, explanation, verification = self._phrase(
            standalone, result.columns, evidence.rows, result.row_count, facts
        )
        evidence.verification = verification
        return done(
            Status.ANSWERED,
            answer=answer,
            explanation=explanation,
            assumptions=assumptions,
        )


def _looks_english(text: str) -> bool:
    """EC-15: most letters are Latin letters and the question has some common English words."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return True
    if sum(c.isascii() for c in letters) / len(letters) < 0.8:
        return False
    words = set(re.findall(r"\w+", text.lower()))
    foreign = {"cuántos", "cuantos", "qué", "cuál", "quels", "combien", "wie", "viele", "welche"}
    return not (words & foreign)


def _metric_tables(sql: str) -> set[str]:
    return {m.upper() for m in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.", sql)}


def _hidden_terms(cfg: DatasetConfig, kept: dict, metrics: dict) -> list[str]:
    """Names and synonyms of hidden tables and metrics that no visible one also uses."""

    def words(name: str) -> str:
        return name.lower().replace("_", " ")

    visible = {words(t) for t in kept} | {words(k) for k in metrics}
    visible |= {s.lower() for m in metrics.values() for s in m.synonyms}
    visible |= {s.lower() for t in kept.values() for c in t.columns.values() for s in c.synonyms}
    terms = set()
    for name, table in cfg.tables.items():
        if name not in kept:
            terms.add(words(name))
            terms |= {s.lower() for c in table.columns.values() for s in c.synonyms}
    for name, m in cfg.metrics.items():
        if name not in metrics:
            terms |= {words(name), *(s.lower() for s in m.synonyms)}
    return sorted(t for t in terms if t and t not in visible)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _exists(p: Pipeline, table: str, column: str, value: str, request_id: str) -> bool:
    sql = f"SELECT 1 FROM {table} WHERE {column} = {_quote(value)} LIMIT 1"
    if not validate(sql, p.catalog).ok:
        return True
    try:
        return bool(p.executor.run(sql, request_id=request_id, max_rows=1).rows)
    except ExecutionError:
        return True  # unknown, so do not blame this filter


def _same_but_case(p: Pipeline, table: str, column: str, value: str, request_id: str) -> str:
    """The one stored value equal to value ignoring case, or "" when there is none or several."""
    sql = (
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE UPPER({column}) = UPPER({_quote(value)}) LIMIT 2"
    )
    if not validate(sql, p.catalog).ok:
        return ""
    try:
        rows = p.executor.run(sql, request_id=request_id, max_rows=2).rows
    except ExecutionError:
        return ""
    return str(rows[0][0]) if len(rows) == 1 and rows[0][0] is not None else ""


def _replace_literal(sql: str, old: str, new: str) -> str:
    """The SQL with every string literal equal to old replaced by new."""
    tree = sqlglot.parse_one(sql, read="snowflake")
    tree = tree.transform(
        lambda n: (
            exp.Literal.string(new)
            if isinstance(n, exp.Literal) and n.is_string and n.this == old
            else n
        )
    )
    return tree.sql(dialect="snowflake")


def _top_values(p: Pipeline, table: str, column: str, request_id: str) -> list[str]:
    sql = f"SELECT {column}, COUNT(*) AS n FROM {table} GROUP BY {column} ORDER BY n DESC LIMIT 10"
    if not validate(sql, p.catalog).ok:
        return []
    try:
        rows = p.executor.run(sql, request_id=request_id, max_rows=10).rows
    except ExecutionError:
        return []
    return [str(r[0]) for r in rows if r[0] is not None]


def _swap(question: str, old: str, new: str) -> str:
    """The question with the missing value replaced, when the value appears in it."""
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    return pattern.sub(new, question, count=1) if pattern.search(question) else ""


def _time_filtered(facts: QueryFacts, pipe: Pipeline) -> bool:
    """Whether the query limits time at all: a date column, or any column named like the anchor."""
    anchor = pipe.dataset.time.anchor_column.upper() if pipe.dataset.time else ""
    names = [f.column for f in facts.filters]
    if any(n in pipe.date_columns or n.split(".")[-1] == anchor for n in names):
        return True
    text = " ".join(facts.other_conditions + facts.group_by).upper()
    return bool(anchor and anchor in text) or any(
        c.split(".")[-1] in text for c in pipe.date_columns
    )
