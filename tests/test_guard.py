import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.guard import GuardError, validate_sql  # noqa: E402


def test_select_gets_limit():
    assert validate_sql("SELECT count(*) FROM deals").endswith("LIMIT 200")


def test_with_cte_ok():
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_like_rewritten():
    s = validate_sql("SELECT * FROM deals WHERE product_raw LIKE '%сахар%' AND seller_name NOT LIKE '%a%' AND buyer_name ILIKE 'b'")
    assert "ILIKE '%сахар%'" in s and "NOT ILIKE '%a%'" in s and s.count("ILIKE") == 3


def test_limit_capped():
    assert "LIMIT 200" in validate_sql("SELECT * FROM deals LIMIT 5000")
    assert "LIMIT 5\n" in validate_sql("SELECT * FROM deals LIMIT 5\n") or "LIMIT 5" in validate_sql("SELECT * FROM deals LIMIT 5")


@pytest.mark.parametrize("sql", [
    "DROP TABLE deals", "SELECT 1; SELECT 2", "CREATE TABLE x AS SELECT 1", "SELECT * FROM read_csv('/etc/passwd')",
    "UPDATE deals SET amount=0", "SELECT * FROM information_schema.tables", "SET threads=1", "SELECT * FROM internal.raw_deals",
    "SELECT * FROM deals WHERE regexp_matches(product_raw, '\\bмука\\b')", "", "EXPLAIN SELECT 1",
])
def test_rejected(sql):
    with pytest.raises(GuardError):
        validate_sql(sql)


def test_semicolon_inside_string_ok():
    s = validate_sql("SELECT * FROM deals WHERE seller_name = 'a;b'")
    assert "a;b" in s
