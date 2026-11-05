import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.guard import GuardError, validate_sql  # noqa: E402


def test_select_unchanged():
    assert validate_sql("SELECT count(*) FROM deals;") == "SELECT count(*) FROM deals"


def test_with_cte_ok():
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_like_rewritten_outside_strings_only():
    s = validate_sql("SELECT * FROM deals WHERE product_raw LIKE '%like%' AND seller_name NOT LIKE '%a%'")
    assert "ILIKE '%like%'" in s and "NOT ILIKE '%a%'" in s


def test_double_dash_inside_string_survives():
    assert "'it--s'" in validate_sql("SELECT * FROM deals WHERE seller_name = 'it--s' -- note")


def test_legit_functions_and_aliases_allowed():
    validate_sql("SELECT replace(seller_name, 'a', 'b') AS system FROM deals")


@pytest.mark.parametrize("sql", [
    "DROP TABLE deals", "SELECT 1; SELECT 2", "CREATE TABLE x AS SELECT 1", "SELECT * FROM read_csv('/etc/passwd')",
    "UPDATE deals SET amount=0", "SELECT * FROM information_schema.tables", "SET threads=1", "SELECT * FROM internal.raw_deals",
    'SELECT * FROM "internal"."raw_deals"', "SELECT * FROM duckdb_tables()", "SELECT * FROM sqlite_master", "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM query('select 1')", "SELECT * FROM deals WHERE regexp_matches(product_raw, '\\bмука\\b')", "", "EXPLAIN SELECT 1", "not sql at all",
])
def test_rejected(sql):
    with pytest.raises(GuardError):
        validate_sql(sql)


def test_semicolon_inside_string_ok():
    s = validate_sql("SELECT * FROM deals WHERE seller_name = 'a;b'")
    assert "a;b" in s


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM deals d WHERE d.year=2025",
    "SELECT count(*) FROM deals AS d WHERE d.year=2025",
    "SELECT count(*) FROM deals d JOIN participants p ON d.buyer_inn=p.inn WHERE p.region_id='FAR'",
    "SELECT count(*) FROM contracts c WHERE NOT EXISTS (SELECT 1 FROM deals d WHERE d.contract_no=c.contract_no)",
    "WITH x AS (SELECT d.buyer_inn FROM deals d) SELECT count(*) FROM x",
    "SELECT count(*) FROM deals JOIN contracts USING(contract_no)",
])
def test_table_aliases_pass_the_allowlist(sql):  # regression: DuckDB reports 'deals AS d', which was read as a table name
    from app import db
    from app.guard import check_tables
    check_tables(db.connection(), validate_sql(sql))


@pytest.mark.parametrize("name", ["secret", "secret AS deals", "internal.deals", "internal.deals AS d", '"internal"."deals"'])
def test_table_allowlist_still_blocks(name):
    from app.guard import _table_allowed
    assert not _table_allowed(name)
