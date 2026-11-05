"""SQL guard: the only path from LLM output to the database.

Uses DuckDB's own parser for the statement type instead of keyword regexes, so legitimate
SQL (replace(), an alias called "system") is not rejected and text inside string literals
is never touched. Row count is capped when fetching (app.agent), not by editing the SQL.
"""
from __future__ import annotations

import re

import duckdb

ALLOWED_TABLES = {"deals", "contracts", "participants", "brokers", "usd_rates", "meta"}

# string literals and comments, in the order they appear
_TOKENS = re.compile(r"('(?:[^']|'')*'|--[^\n]*|/\*.*?\*/)", flags=re.S)
_LIKE = re.compile(r"(?<![A-Za-z_])LIKE\b", flags=re.I)
_DENIED_NAMES = re.compile(
    r"\b(read_\w+|glob|sniff_csv|parquet_\w+|csv_scan|getenv|current_setting|which_secret|"
    r"duckdb_\w+|pragma_\w+|sqlite_\w+|pg_\w+|information_schema|internal)\b", flags=re.I)
_DENIED_CALLS = re.compile(r"\b(query|query_table)\s*\(", flags=re.I)


class GuardError(ValueError):
    pass


def _code_parts(sql: str) -> list[str]:
    """Odd indexes hold string literals or comments, even indexes hold SQL code."""
    return _TOKENS.split(sql)


def validate_sql(sql: str) -> str:
    if not sql or not sql.strip():
        raise GuardError("empty SQL")
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as e:
        raise GuardError(f"cannot parse SQL: {str(e)[:200]}") from e
    if len(statements) != 1:
        raise GuardError("only one statement is allowed")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise GuardError("only SELECT (or WITH ... SELECT) is allowed")

    parts = _code_parts(sql)
    code = " ".join(parts[0::2])
    for rx in (_DENIED_NAMES, _DENIED_CALLS):
        m = rx.search(code)
        if m:
            raise GuardError(f"forbidden name: {m.group(0)}")
    if "\\b" in sql:
        raise GuardError("regex word boundary \\b does not work on Cyrillic text; use ILIKE")

    # case-sensitive LIKE silently misses Cyrillic case variants, so force ILIKE in code only
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part if part.startswith("'") else " ")
        else:
            out.append(_LIKE.sub("ILIKE", part))
    return "".join(out).strip().rstrip(";").strip()


_TABLE_ALIAS = re.compile(r"\s+AS\s+.*$", flags=re.I | re.S)


def _table_allowed(name: str) -> bool:
    """get_table_names(qualified=True) reports an aliased table as 'deals AS d'."""
    base = _TABLE_ALIAS.sub("", name).strip()
    return not base.lower().startswith(("internal.", '"internal".')) and base.split(".")[-1].strip('"').lower() in ALLOWED_TABLES


def check_tables(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Allowlist the tables a statement reads. Binder errors surface later, at execution."""
    try:
        tables = con.get_table_names(sql, qualified=True)
    except duckdb.Error:
        try:  # the qualified form binds the query and fails on some valid joins, e.g. USING
            tables = con.get_table_names(sql, qualified=False)
        except duckdb.Error:
            return
    bad = {t for t in tables if not _table_allowed(t)}
    if bad:
        raise GuardError(f"table not allowed: {sorted(bad)}")
