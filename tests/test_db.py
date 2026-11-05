"""Golden tests for data/uzex.duckdb. Run: .venv/bin/python -I -m pytest tests -q

Independent of build_db.py: expected numbers come from analysis/ profiling
(pandas) and the dataset README, not from the build itself.
"""
from __future__ import annotations

import glob
import os
import threading
import time

import duckdb
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "uzex.duckdb")
DATA = os.path.join(ROOT, "uzex_spot_data")
RO = {"read_only": True, "config": {"enable_external_access": "false"}}


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(DB, **RO)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


# ---- README counts ---------------------------------------------------------
@pytest.mark.parametrize("sql,exp", [
    ("SELECT count(*) FROM deals", 500000),
    ("SELECT count(*) FROM deals WHERE year=2022", 84299),
    ("SELECT count(*) FROM deals WHERE year=2023", 93985),
    ("SELECT count(*) FROM deals WHERE year=2024", 106647),
    ("SELECT count(*) FROM deals WHERE year=2025", 115763),
    ("SELECT count(*) FROM deals WHERE year=2026", 99306),
    ("SELECT count(*) FROM contracts", 6616),
    ("SELECT count(*) FROM participants", 14392),
    ("SELECT count(*) FROM participants WHERE role='seller'", 392),
    ("SELECT count(*) FROM brokers", 120),
    ("SELECT count(*) FROM usd_rates", 1765),
    ("SELECT min(deal_date)::VARCHAR FROM deals", "2022-01-03"),
    ("SELECT max(deal_date)::VARCHAR FROM deals", "2026-10-30"),
])
def test_readme_counts(con, sql, exp):
    assert one(con, sql) == exp


# ---- integrity -------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM deals WHERE product_key IS NULL OR family_key IS NULL OR product_ru IS NULL OR product_uz IS NULL",
    "SELECT count(*) FROM deals WHERE region_id IS NULL OR region_ru IS NULL OR region_uz IS NULL",
    "SELECT count(*) FROM deals WHERE status_code IS NULL OR status_uz IS NULL",
    "SELECT count(*) FROM deals WHERE usd_rate IS NULL OR amount_uzs IS NULL OR amount_usd IS NULL",
    "SELECT count(*) FROM deals WHERE seller_name IS NULL OR buyer_name IS NULL OR seller_broker_name IS NULL OR buyer_broker_name IS NULL",
    "SELECT count(*) FROM deals WHERE unit_uz IS NULL OR trade_type_uz IS NULL OR category_uz IS NULL OR industry_uz IS NULL",
    "SELECT count(*) FROM deals WHERE is_export AND currency<>'USD'",
    "SELECT count(*) FROM deals WHERE NOT is_export AND currency<>'UZS'",
    "SELECT count(*) FROM deals WHERE abs(unit_price*volume - amount) > 0.01",
    "SELECT count(*) FROM deals WHERE volume <> qty_lots*lot_size",
    "SELECT count(*) FROM deals WHERE pay_date < deal_date OR delivery_date < pay_date",
    "SELECT count(*) FROM deals WHERE pay_date > pay_due_date",              # known fact: no late payments
    "SELECT count(*) FROM deals WHERE is_paid AND pay_date IS NULL",
    "SELECT count(*) FROM deals WHERE NOT is_paid AND pay_date IS NOT NULL",
    "SELECT count(*) FROM deals WHERE status_code='executed' AND delivered_lots <> qty_lots",
    "SELECT count(*) FROM deals WHERE status_code='partial' AND delivered_lots >= qty_lots",
    "SELECT count(*) FROM deals WHERE is_annulled AND (pay_date IS NOT NULL OR delivery_date IS NOT NULL)",
    "SELECT count(*) FROM deals d LEFT JOIN contracts c USING(contract_no) WHERE c.contract_no IS NULL",
    "SELECT count(*) FROM deals d JOIN contracts c USING(contract_no) WHERE d.deal_date < c.reg_date OR d.deal_date > c.valid_to",
    "SELECT count(*) FROM contracts WHERE region_id IS NULL OR product_key IS NULL OR unit_uz IS NULL",
    "SELECT count(*) FROM participants WHERE region_id IS NULL OR activity_uz IS NULL OR length(inn)<>9",
    "SELECT count(*) FROM brokers WHERE region_id IS NULL",
    "SELECT count(*) - count(DISTINCT deal_no) FROM deals",
    "SELECT count(*) - count(DISTINCT inn) FROM participants",
    "SELECT count(*) FROM deals WHERE volume_t IS NULL AND unit IN ('тонна','килограмм')",
    "SELECT count(*) FROM deals WHERE volume_t IS NOT NULL AND unit NOT IN ('тонна','килограмм')",
])
def test_integrity_zero(con, sql):
    assert one(con, sql) == 0, sql


# ---- known facts from profiling -------------------------------------------
@pytest.mark.parametrize("sql,exp", [
    ("SELECT count(*) FROM deals WHERE is_export", 6590),
    ("SELECT count(*) FROM deals WHERE is_annulled", 33719),
    ("SELECT count(*) FROM deals WHERE status_ru='Ждёт оплату'", 1714),
    ("SELECT count(*) FROM deals WHERE status_code='new'", 964),
    ("SELECT count(*) FROM deals WHERE is_partial", 22648),
    ("SELECT count(*) FROM deals WHERE is_delivery_late", 17805),
    ("SELECT count(*) FROM deals WHERE family_key='cotton'", 136208),
    ("SELECT count(*) FROM deals WHERE product_key='cotton_futures'", 6845),
    ("SELECT count(*) FROM deals WHERE contract_form='Фьючерс' AND family_key<>'cotton'", 0),
    ("SELECT count(*) FROM deals WHERE product_raw ILIKE '%хлопок%'", 136208),
    ("SELECT count(*) FROM deals WHERE region_uz='Toshkent shahri'", 98435),
    ("SELECT count(*) FROM deals WHERE region_uz='Toshkent viloyati'", 35914),
    ("SELECT count(*) FROM deals WHERE product_key='diesel'", 15736 + 13011),
    ("SELECT count(*) FROM deals WHERE product_key='cement'", 30588 + 8558),
    ("SELECT count(DISTINCT product_raw) FROM deals", 697),
    ("SELECT count(DISTINCT product_key) FROM deals", 44),
    ("SELECT count(*) FROM deals WHERE seller_broker = buyer_broker", 4562),
    ("SELECT sum(amount_uzs)::VARCHAR FROM deals WHERE NOT is_export", "206835219986242.97"),
    ("SELECT sum(amount)::VARCHAR FROM deals WHERE is_export", "1137509729.39"),
    ("SELECT round(wavg_price(amount, volume)) FROM deals WHERE product_key='petrol' AND brand='АИ-92' AND year=2025 AND unit='литр'", 10700.0),
    ("SELECT round(avg(delivery_delay_days)) FROM deals WHERE is_delivery_late", 8.0),
])
def test_known_facts(con, sql, exp):
    assert one(con, sql) == exp


def test_grade_parsing_examples(con):
    rows = dict(con.execute("""SELECT product_raw, grade_sort || '/' || coalesce(grade_class::VARCHAR,'-') FROM deals
        WHERE product_raw IN ('Хлопок сырец (спот) 1-сорта 2 класс','ХЛОПОК СЫРЕЦ (СПОТ) ПЕРВЫЙ СОРТ I КЛАСС',
        'Хлопок-сырец (спот) Сорт 2 сорт Класс II класс','Мука пшеничная Сорт высший сорт Тип хлебопекарная') GROUP BY 1,2""").fetchall())
    assert rows['Хлопок сырец (спот) 1-сорта 2 класс'] == '1/2'
    assert rows['ХЛОПОК СЫРЕЦ (СПОТ) ПЕРВЫЙ СОРТ I КЛАСС'] == '1/1'
    assert rows['Хлопок-сырец (спот) Сорт 2 сорт Класс II класс'] == '2/2'
    assert rows['Мука пшеничная Сорт высший сорт Тип хлебопекарная'] == 'highest/-'


def test_word_order_synonyms_merged(con):
    n = one(con, "SELECT count(DISTINCT product_key) FROM deals WHERE product_raw ILIKE 'Топливо дизельное%' OR product_raw ILIKE 'Дизельное топливо%'")
    assert n == 1
    n = one(con, "SELECT count(DISTINCT product_key) FROM deals WHERE product_raw ILIKE 'Карбамид%' OR product_raw ILIKE 'Мочевина%'")
    assert n == 1


# ---- independent recomputation with pandas ---------------------------------
def test_amount_uzs_matches_pandas(con):
    d = pd.concat([pd.read_csv(f, dtype=str, usecols=["Номер сделки", "Дата сделки", "Сумма сделки", "Тип контракта"])
                   for f in sorted(glob.glob(os.path.join(DATA, "deals_*.csv")))])
    r = pd.read_csv(os.path.join(DATA, "usd_rates.csv"), dtype=str)
    rate = dict(zip(r["Дата"], pd.to_numeric(r["Курс"])))
    d["amt"] = pd.to_numeric(d["Сумма сделки"].str.replace(" ", "", regex=False))
    d["k"] = d["Дата сделки"].map(rate)
    d["uzs"] = d.apply(lambda x: round(x.amt * x.k, 2) if x["Тип контракта"] == "Экспортный" else x.amt, axis=1)
    total_pd = d["uzs"].sum()
    total_db = float(one(con, "SELECT sum(amount_uzs) FROM deals"))
    assert abs(total_pd - total_db) < total_db * 1e-12  # pandas float64 accumulation noise only
    # spot check 200 random deals
    sample = d.sample(200, random_state=7)
    got = dict(con.execute(f"SELECT deal_no, amount_uzs FROM deals WHERE deal_no IN ({','.join(sample['Номер сделки'])})").fetchall())
    for _, row in sample.iterrows():
        assert abs(float(got[int(row['Номер сделки'])]) - row.uzs) < 0.011


# ---- performance and concurrency -------------------------------------------
QUERIES = [
    "SELECT year, month, product_key, unit, count(*), sum(amount_uzs), wavg_price(amount, volume) FROM deals WHERE NOT is_annulled GROUP BY ALL",
    "SELECT seller_name, count(*) n, sum(amount_uzs) s FROM deals WHERE year=2025 GROUP BY 1 ORDER BY s DESC LIMIT 10",
    "SELECT region_uz, count(*) FROM deals WHERE family_key='cotton' AND year=2024 GROUP BY 1 ORDER BY 2 DESC",
    "SELECT * FROM deals WHERE product_raw ILIKE '%сахар%' AND deal_date BETWEEN '2025-09-01' AND '2025-09-30' ORDER BY amount DESC LIMIT 20",
    "SELECT buyer_activity_ru, count(*), sum(volume_t) FROM deals WHERE product_key='wheat' GROUP BY 1",
    "SELECT status_uz, count(*) FROM deals WHERE year=2026 GROUP BY 1",
    "SELECT seller_broker_name, count(*) FROM deals GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
    "SELECT deal_date, count(*) FROM deals WHERE is_delivery_late GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
]


def test_query_speed(con):
    for q in QUERIES:
        t = time.perf_counter()
        con.execute(q).fetchall()
        assert time.perf_counter() - t < 0.5, q


def test_parallel_readonly_connections():
    errors = []

    def worker():
        try:
            c = duckdb.connect(DB, **RO)
            for q in QUERIES[:4]:
                c.execute(q).fetchall()
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors


def test_readonly_blocks_writes_and_files(con):
    with pytest.raises(Exception):
        con.execute("CREATE TABLE x AS SELECT 1")
    with pytest.raises(Exception):
        con.execute("SELECT * FROM read_csv('/etc/passwd')")


def test_comments_present(con):
    n = one(con, "SELECT count(*) FROM duckdb_columns() WHERE table_name='deals' AND comment IS NULL")
    assert n == 0
    assert one(con, "SELECT count(*) FROM duckdb_tables() WHERE schema_name='main' AND comment IS NOT NULL") == 5


# ---- regressions from the 05.11 probe: counted from the raw CSV, not from the database --------
def _raw_contracts():
    import csv
    with open(os.path.join(ROOT, "uzex_spot_data", "contracts.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_contract_class_matches_raw_card(con):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    from normalize import parse_class
    raw = _raw_contracts()
    expected = sum(1 for c in raw if c["Наименование продукта"].lower().startswith("хлопок")
                   and (parse_class(c["Класс товара"]) or parse_class(c["Наименование продукта"])) == 2)
    assert expected == 720  # the card column holds 664 of them, the name the other 56
    assert con.execute("SELECT count(*) FROM contracts WHERE family_key='cotton' AND grade_class=2").fetchone()[0] == expected


def test_contract_brand_matches_raw_card(con):
    n = sum(1 for c in _raw_contracts() if "аи-92" in (c["Наименование продукта"] + " " + c["Марка товара"]).lower())
    assert con.execute("SELECT count(*) FROM contracts WHERE brand='АИ-92'").fetchone()[0] == n == 108


def test_due_dates_are_dates(con):
    assert con.execute("SELECT typeof(pay_due_date), typeof(delivery_due_date) FROM deals LIMIT 1").fetchone() == ("DATE", "DATE")


def test_at_start_price_matches_raw_decimal_comparison(con):
    from decimal import Decimal
    d = pd.concat([pd.read_csv(f, dtype=str) for f in sorted(glob.glob(os.path.join(DATA, "deals_*.csv")))], ignore_index=True)
    d = d[d["Тип торгов"] == "Английский аукцион"]
    n = sum(Decimal(a.replace(" ", "")) == Decimal(s.replace(" ", "")) * int(q) * int(l) for a, s, q, l in zip(d["Сумма сделки"], d["Стартовая цена"], d["Количество"], d["Лот"]))
    assert one(con, "SELECT count(*) FROM deals WHERE trade_type='Английский аукцион' AND is_at_start_price") == n == 88816
