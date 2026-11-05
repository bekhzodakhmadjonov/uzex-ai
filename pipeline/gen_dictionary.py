"""Generate docs/schema_for_llm.md — the bilingual data dictionary that goes
into the LLM system prompt. Built from the database itself (COMMENT ON and
live value lists) so it never drifts from the data.

Run:  .venv/bin/python -I pipeline/gen_dictionary.py
"""
from __future__ import annotations

import os

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "uzex.duckdb")
OUT = os.path.join(ROOT, "docs", "schema_for_llm.md")


def main() -> None:
    con = duckdb.connect(DB, read_only=True)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    L: list[str] = []
    w = L.append

    w("# UZEX deals database — schema for the SQL agent (DuckDB)\n")
    w(f"Data as of **{meta['data_asof']}**. Deals from {meta['min_deal_date']} to {meta['max_deal_date']} "
      f"({int(meta['n_deals']):,} rows). **2026 is incomplete (Jan–Oct only). No data for Nov–Dec 2026 or before 2022.** All companies, INNs and amounts are fictional.\n")
    w("Tables visible to you: `deals` (use this for almost everything — all lookups are already joined), `contracts`, `participants`, `brokers`, `usd_rates`, `meta`.\n")

    w("## Hard rules (ru | uz)\n")
    rules = [
        "Only SELECT. One statement. Always add `count(*) AS n_deals` (or an equivalent row count) so the answer can show its basis.",
        "Never do arithmetic yourself — compute everything in SQL and read the numbers from the result.",
        "Text filters: use ILIKE or exact equality on the normalized keys (`product_key`, `family_key`, `region_id`, `status_code`). Never LIKE (case-sensitive) and never regex `\\b` (does not work on Cyrillic).",
        "Average price = sum(amount)/sum(volume), ALWAYS grouped by `unit` and `currency`. `wavg_price(amount, volume)` is that formula as a macro. Never avg(unit_price), never mix litres with tonnes or USD with UZS.",
        "Population: deal counts cover every registry row, with n_annulled next to them. Money, volume and average price lead with the figure excluding annulled deals (the README says such a deal did not take place) and always also return the all-status figure.",
        "Export deals are in USD (`currency='USD'`, only copper cathode and cotton yarn). `amount_uzs` already converts them at the deal-date rate; `amount_usd` converts domestic deals to USD at the deal-date rate.",
        "Physical volume = `volume` (in `unit`) or `volume_t` (tonnes, only for тонна/килограмм rows). Delivered volume = `delivered_volume`.",
        "Status is as of 2026-10-30. There are NO overdue unpaid deals: anything unpaid past its term is already `is_annulled`. Late deliveries exist: `is_delivery_late`.",
        "Status spelling in Russian uses ё: 'Ждёт оплату'. Prefer `status_code` in filters.",
        "'Toshkent shahri' (city) and 'Toshkent viloyati' (region) are different regions.",
        "`region_*` is the seller's warehouse region. `buyer_region_*` is the buyer's region. They differ in most deals.",
        "`brand`, `ptype`, `size`, `grade_sort` and `grade_class` mean the same on `deals` and `contracts`. `ptype` is lower case.",
        "When the question is about a family ('paxta', 'un', 'sement'), filter `family_key`; when about a specific product/grade, add `product_key`, `grade_sort`, `grade_class`, `brand`.",
        "If a filter returns 0 rows, do NOT invent a number — report that the data has no such records and name the filter that failed.",
    ]
    for r in rules:
        w(f"- {r}")
    w("")

    for tbl in ("deals", "contracts", "participants", "brokers", "usd_rates"):
        tcomment = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name=? AND schema_name='main'", [tbl]).fetchone()[0]
        w(f"## `{tbl}`\n")
        if tcomment:
            w(tcomment + "\n")
        w("| column | type | meaning (ru \\| uz) |\n|---|---|---|")
        for name, typ, comment in con.execute(
            "SELECT column_name, data_type, comment FROM duckdb_columns() WHERE table_name=? AND schema_name='main' ORDER BY column_index", [tbl]).fetchall():
            w(f"| `{name}` | {typ} | {comment or ''} |")
        w("")

    w("## Value lists\n")
    def vlist(title: str, cols: str, order: str = "n DESC") -> None:
        sql = f"SELECT concat_ws(' | ', {cols}) AS v, count(*) AS n FROM deals GROUP BY ALL ORDER BY {order}"
        items = [f"{v}  ({n:,})" for v, n in con.execute(sql).fetchall()]
        w(f"**{title}**: " + "; ".join(items) + "\n")

    vlist("status_code | status_ru | status_uz", "status_code, status_ru, status_uz")
    vlist("trade_type | trade_type_uz", "trade_type, trade_type_uz")
    vlist("contract_form", "contract_form")
    vlist("contract_type | is_export | currency", "contract_type, is_export, currency")
    vlist("unit | unit_uz | lot_size", "unit, unit_uz, lot_size")
    vlist("region_id | region_uz | region_ru", "region_id, region_uz, region_ru")
    vlist("category_ru | category_uz", "category_ru, category_uz")
    vlist("buyer_activity_ru | buyer_activity_uz", "buyer_activity_ru, buyer_activity_uz")
    vlist("group_ru | group_uz", "group_ru, group_uz")
    vlist("packaging (ru only; deals and contracts)", "packaging")
    vlist("delivery_basis", "delivery_basis")
    vlist("grade_sort", "coalesce(grade_sort,'NULL')", "v")
    vlist("grade_class", "coalesce(grade_class::VARCHAR,'NULL')", "v")

    w("## Product catalog (family_key → product_key, with units and deal counts)\n")
    w("| family_key | family_ru | family_uz | product_key | product_ru | product_uz | units | deals | brands/specs (top) |\n|---|---|---|---|---|---|---|---|---|")
    rows = con.execute("""
        SELECT family_key, family_ru, family_uz, product_key, product_ru, product_uz,
               string_agg(DISTINCT unit, ', ') AS units, count(*) AS n,
               (SELECT string_agg(b, ', ') FROM (SELECT brand AS b, count(*) c FROM deals x WHERE x.product_key=d.product_key AND brand IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6)) AS brands
        FROM deals d GROUP BY ALL ORDER BY n DESC""").fetchall()
    for r in rows:
        w("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    w("")

    w("## Family aliases (user words → family_key)\n")
    for fk, al in con.execute("SELECT family_key, aliases FROM internal.dim_family ORDER BY family_key").fetchall():
        w(f"- `{fk}`: {al.replace('|', ', ')}")
    w("")

    w("## Macros\n")
    w("- `wavg_price(amount, volume)` = sum(amount)/sum(volume) — volume-weighted average unit price. Group by unit and currency.")
    w("- `fmt_num(x)` — formats a number with thousands separators.\n")

    w("## Example SQL\n")
    ex = [
        ("Paxta 2025: bitimlar soni, hajm tonnada, o‘rtacha narx (butun reyestr + bekor qilinganlar alohida)",
         "SELECT count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,\n       sum(volume_t) AS tonnes, sum(volume_t) FILTER (WHERE NOT is_annulled) AS tonnes_valid,\n       sum(amount) FILTER (WHERE NOT is_annulled) / sum(volume) FILTER (WHERE NOT is_annulled) AS price_per_kg_valid,\n       wavg_price(amount, volume) AS price_per_kg_all, min(deal_date) AS from_date, max(deal_date) AS to_date\nFROM deals WHERE family_key='cotton' AND year=2025"),
        ("Средняя цена АИ-92 в 2025 по единицам",
         "SELECT unit, currency, count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,\n       sum(amount) FILTER (WHERE NOT is_annulled) / sum(volume) FILTER (WHERE NOT is_annulled) AS avg_price_valid, wavg_price(amount, volume) AS avg_price_all\nFROM deals WHERE product_key='petrol' AND brand='АИ-92' AND year=2025 GROUP BY ALL"),
        ("Топ-5 покупателей цемента по сумме, 2024",
         "SELECT buyer_name, buyer_inn, count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,\n       sum(amount_uzs) FILTER (WHERE NOT is_annulled) AS total_uzs_valid, sum(amount_uzs) AS total_uzs_all\nFROM deals WHERE product_key='cement' AND year=2024 GROUP BY ALL ORDER BY total_uzs_valid DESC LIMIT 5"),
        ("Dollardagi kontraktlar soni (contracts jadvali, deals emas)",
         "SELECT count(*) AS n_contracts FROM contracts WHERE currency='USD'"),
        ("Сколько участников платят НДС (таблица participants)",
         "SELECT count(*) AS n_participants, count(*) FILTER (WHERE is_vat) AS n_vat FROM participants"),
        ("Eng ko‘p bekor qilingan bitimlar qaysi brokerda (sotuvchi tomoni)",
         "SELECT seller_broker, seller_broker_name, count(*) AS n_deals\nFROM deals WHERE is_annulled GROUP BY ALL ORDER BY n_deals DESC LIMIT 5"),
        ("Kechikkan yetkazishlar 2026 yil bo‘yicha oylarda",
         "SELECT month, count(*) AS n_deals, round(avg(delivery_delay_days),1) AS avg_days_late\nFROM deals WHERE year=2026 AND is_delivery_late GROUP BY 1 ORDER BY 1"),
    ]
    for title, sql in ex:
        w(f"**{title}**\n```sql\n{sql}\n```")
    con.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
