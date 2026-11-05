"""Read-only DuckDB access for the API. One shared connection, a cursor per call."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("UZEX_DB", os.path.join(ROOT, "data", "uzex.duckdb"))
RO_CONFIG = {"enable_external_access": "false"}  # no TimeZone: needs the icu extension, which RO+no-external-access cannot autoload

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def connection() -> duckdb.DuckDBPyConnection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = duckdb.connect(DB_PATH, read_only=True, config=RO_CONFIG)
            _conn.execute("SET memory_limit='300MB'")  # a bad LLM query must not take the server down
        return _conn


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    elapsed_ms: float
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]


def run_sql(sql: str, params: list | None = None, max_rows: int = 200) -> QueryResult:
    """Execute a (pre-validated) SELECT on a fresh cursor. Caller enforces the guard."""
    cur = connection().cursor()
    t = time.perf_counter()
    try:
        res = cur.execute(sql, params or [])
        cols = [d[0] for d in res.description]
        rows = res.fetchmany(max_rows + 1)
    finally:
        cur.close()
    truncated = len(rows) > max_rows
    return QueryResult(cols, rows[:max_rows], (time.perf_counter() - t) * 1000, truncated)


def meta() -> dict[str, str]:
    return dict(run_sql("SELECT key, value FROM meta").rows)
