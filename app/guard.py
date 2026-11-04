"""SQL guard: the only path from LLM output to the database.

validate_sql(sql) -> cleaned sql, or raises GuardError with a reason the LLM can act on.
"""
from __future__ import annotations

import re

MAX_LIMIT = 200
ALLOWED_TABLES = {"deals", "contracts", "participants", "brokers", "usd_rates", "meta"}

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|pragma|install|load|call|"
    r"set|reset|checkpoint|vacuum|grant|revoke|truncate|merge|replace|begin|commit|rollback|"
    r"read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_text|read_blob|glob|"
    r"sniff_csv|parquet_scan|csv_scan|getenv|current_setting|duckdb_settings|duckdb_secrets|"
    r"information_schema|duckdb_extensions|system|internal)\b",
    flags=re.I,
)
_LIKE = re.compile(r"(?<![A-Za-z_])(NOT\s+)?LIKE\b", flags=re.I)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)", flags=re.I)
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", flags=re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")


class GuardError(ValueError):
    pass


def _strip_strings(sql: str) -> str:
    return _STRING.sub("''", sql)


def validate_sql(sql: str) -> str:
    if not sql or not sql.strip():
        raise GuardError("empty SQL")
    s = _COMMENT.sub(" ", sql).strip()
    s = s.rstrip(";").strip()
    bare = _strip_strings(s)
    if ";" in bare:
        raise GuardError("only one statement is allowed")
    head = bare.lstrip("(").lstrip()[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise GuardError("only SELECT (or WITH ... SELECT) is allowed")
    m = _FORBIDDEN.search(bare)
    if m:
        raise GuardError(f"forbidden keyword: {m.group(0)}")
    if re.search(r"\\b", s):
        raise GuardError("regex word boundary \\b is not supported on Cyrillic text; use ILIKE instead")
    # case-sensitive LIKE silently misses Cyrillic case variants -> force ILIKE
    s = _LIKE.sub(lambda mm: (mm.group(1) or "") + "ILIKE", s)
    # cap LIMIT
    lm = _LIMIT.search(bare)
    if lm:
        if int(lm.group(1)) > MAX_LIMIT:
            s = _LIMIT.sub(f"LIMIT {MAX_LIMIT}", s, count=1)
    else:
        s = f"{s}\nLIMIT {MAX_LIMIT}"
    return s


def referenced_tables(sql: str) -> set[str]:
    bare = _strip_strings(_COMMENT.sub(" ", sql))
    return {t.lower() for t in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)", bare, flags=re.I)}


def has_row_count(sql: str) -> bool:
    return bool(re.search(r"\bcount\s*\(", sql, flags=re.I)) or bool(re.search(r"\bLIMIT\s+1\b", sql, flags=re.I))
