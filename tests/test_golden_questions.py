"""Golden questions: question (uz/ru) → SQL → expected values.
These are the seed of the LLM eval set; they must keep passing after every rebuild."""
from __future__ import annotations

import json
import os

import duckdb
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "uzex.duckdb")
GOLDEN = json.load(open(os.path.join(ROOT, "tests", "golden_questions.json"), encoding="utf-8"))


@pytest.fixture(scope="module")
def con():
    # same config as app.db so both can coexist in one process (DuckDB rejects differing configs)
    c = duckdb.connect(DB, read_only=True, config={"enable_external_access": "false"})
    yield c
    c.close()


@pytest.mark.parametrize("case", GOLDEN, ids=[g["q_uz"][:50] for g in GOLDEN])
def test_golden(con, case):
    r = con.execute(case["sql"])
    cols = [d[0] for d in r.description]
    row = r.fetchone()
    got = {k: (str(v) if v is not None else None) for k, v in zip(cols, row)}
    assert got == case["expected"], case["q_ru"]


def test_every_golden_sql_has_row_count():
    for g in GOLDEN:
        assert "count(*)" in g["sql"].lower() or "limit 1" in g["sql"].lower(), g["q_uz"]
