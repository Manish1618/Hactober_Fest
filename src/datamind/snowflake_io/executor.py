"""Query execution. Only validated SQL reaches an executor.

SnowflakeExecutor is the real path (FR-B6, FR-B8). LocalExecutor runs the same SQL on an
in-memory DuckDB fixture so the pipeline can be tested offline; it is never used for real answers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlglot

from datamind.config.models import DatasetConfig
from datamind.settings import SnowflakeSettings


class ExecutionError(Exception):
    """The query failed to run. The message is safe to show and to feed to a repair step."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    query_id: str
    elapsed_seconds: float
    executed_at: str
    backend: str
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class Executor(Protocol):
    backend: str

    def run(self, sql: str, *, request_id: str, max_rows: int) -> QueryResult: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SnowflakeExecutor:
    backend = "snowflake"

    def __init__(self, settings: SnowflakeSettings, dataset: DatasetConfig):
        self.settings = settings
        self.dataset = dataset
        self._conn = None

    def _connect(self):
        if self._conn is not None and not self._conn.is_closed():
            return self._conn
        if not self.settings.configured:
            raise ExecutionError(
                "Snowflake is not configured. Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and "
                "SNOWFLAKE_PRIVATE_KEY_PATH in .env."
            )
        import snowflake.connector  # imported lazily so offline tests do not need it

        params: dict[str, Any] = {
            "account": self.settings.account,
            "user": self.settings.user,
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": self.settings.private_key_path,
            "role": self.dataset.snowflake.role or self.settings.role,
            "warehouse": self.settings.warehouse,
            "database": self.dataset.snowflake.database,
            "schema": self.dataset.snowflake.schema_,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": self.settings.statement_timeout_seconds,
            },
            "application": "DataMind",
        }
        if self.settings.private_key_passphrase:
            params["private_key_file_pwd"] = self.settings.private_key_passphrase
        try:
            self._conn = snowflake.connector.connect(**params)
        except Exception as e:  # connector raises many types; none should crash the app
            raise ExecutionError(f"Could not connect to Snowflake: {type(e).__name__}: {e}") from e
        return self._conn

    def run(self, sql: str, *, request_id: str, max_rows: int) -> QueryResult:
        conn = self._connect()
        cur = conn.cursor()
        started = time.perf_counter()
        try:
            # Code-issued, fixed statement: tags every query with the request ID (FR-B8).
            cur.execute("ALTER SESSION SET QUERY_TAG = %s", (f"datamind:{request_id}",))
            cur.execute(sql)
            rows = cur.fetchmany(max_rows + 1)
            columns = [d[0] for d in cur.description or []]
            query_id = cur.sfqid
        except Exception as e:
            raise ExecutionError(f"Snowflake rejected the query: {e}") from e
        finally:
            cur.close()
        truncated = len(rows) > max_rows
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows[:max_rows]],
            query_id=query_id or "",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            executed_at=_now(),
            backend=self.backend,
            truncated=truncated,
            meta={"role": conn.role, "warehouse": conn.warehouse},
        )

    def smoke(self) -> str:
        r = self.run(
            "SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), COUNT(*) FROM REGION",
            request_id="smoke",
            max_rows=1,
        )
        role, wh, regions = r.rows[0]
        return f"role={role} warehouse={wh} REGION rows={regions} query_id={r.query_id}"


class LocalExecutor:
    """DuckDB stand-in for offline development and tests. Not a data source for real answers."""

    backend = "local-fixture"

    def __init__(self, connection):
        self._conn = connection

    def run(self, sql: str, *, request_id: str, max_rows: int) -> QueryResult:
        started = time.perf_counter()
        try:
            local_sql = sqlglot.transpile(sql, read="snowflake", write="duckdb")[0]
            cur = self._conn.execute(local_sql)
            rows = cur.fetchmany(max_rows + 1)
            columns = [d[0] for d in cur.description or []]
        except Exception as e:
            raise ExecutionError(f"The local fixture rejected the query: {e}") from e
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows[:max_rows]],
            query_id=f"local-{uuid.uuid4().hex[:12]}",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            executed_at=_now(),
            backend=self.backend,
            truncated=len(rows) > max_rows,
        )
