"""Build data/uzex.duckdb from the raw CSVs and the curated catalog.

Idempotent: deletes and recreates the file. Fails loudly if any raw product
name is unmapped, any region is unmapped, or a golden number does not match.

Run:  .venv/bin/python -I pipeline/build_db.py
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from catalog_draft import base_core  # noqa: E402
from normalize import (  # noqa: E402
    load_suffixes, norm_company, parse_class, parse_deal_name, parse_grade, strip_legal_form,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "uzex_spot_data")
CATALOG = os.path.join(ROOT, "catalog")
DB_PATH = os.path.join(ROOT, "data", "uzex.duckdb")
DATA_ASOF = "2026-10-30"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_catalog(name: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(CATALOG, name), dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# product mapping
# ---------------------------------------------------------------------------
_LABELS = re.compile(r"\b(?:марка|размер|сорт|тип|класс)\b", flags=re.I)
_GRADE_PHRASE = re.compile(
    r"(?:\b(?:[0-9]+|[ivx]+|перв\w*|втор\w*|трет\w*|высш\w*|в/с)\s*[- ]?\s*(?:сорта?|класса?)\b)"
    r"|(?:\b(?:сорта?|класса?)\s+(?:[0-9]+|[ivx]+|перв\w*|втор\w*|трет\w*|высш\w*|в/с)(?:\s*[- ]?\s*(?:сорта?|класса?))?\b)",
    flags=re.I,
)


def spec_of(raw: str, pattern: str) -> str | None:
    """Residual specification after removing the product's base words,
    grade/class phrases and attribute labels. Keeps original case.
    'ПОРТЛАНДЦЕМЕНТ Марка ЦЕМ II/В-К 32,5Н' -> 'ЦЕМ II/В-К 32,5Н'."""
    # the catalog pattern is written for normalized text (lowercase, dashes as spaces);
    # make spaces dash-tolerant and apply it case-insensitively to the raw string
    rx = pattern.replace(" ", r"[\s\-]+")
    s = re.sub(rx, " ", raw, count=1, flags=re.I)
    s = _GRADE_PHRASE.sub(" ", s)
    s = _LABELS.sub(" ", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -,")
    return s or None


def build_product_map(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    prods = read_catalog("products.csv")
    pats = [(r.product_key, r.family_key, re.compile(r.pattern), r.pattern) for r in prods.itertuples()]
    deal_names = [r[0] for r in con.execute('SELECT DISTINCT "Наименование товара" FROM internal.raw_deals').fetchall()]
    contract_names = [r[0] for r in con.execute('SELECT DISTINCT "Наименование продукта" FROM internal.raw_contracts').fetchall()]
    rows, problems = [], []
    for source, names in (("deal", deal_names), ("contract", contract_names)):
        for raw in names:
            p = parse_deal_name(raw)
            core = base_core(p["base"] or raw)
            hits = [(k, f, pat) for k, f, rx, pat in pats if rx.search(core)]
            if len(hits) != 1:
                problems.append((raw, core, [h[0] for h in hits]))
                continue
            key, fam, pat = hits[0]
            rows.append({
                "raw_name": raw, "source": source, "product_key": key, "family_key": fam,
                "grade_sort": parse_grade(raw), "grade_class": parse_class(raw),
                "brand": p["brand"], "size": p["size"], "ptype": p["type"],
                "spec": spec_of(raw, pat), "base_core": core,
            })
    if problems:
        for pr in problems[:20]:
            print("UNMAPPED:", pr)
        raise SystemExit(f"{len(problems)} raw product names are unmapped/ambiguous — fix catalog/products.csv")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# comments (ru | uz) for the LLM-facing tables
# ---------------------------------------------------------------------------
DEALS_COMMENTS = {
    "deal_no": ("Номер сделки (сквозной, некоторые номера пропущены)", "Bitim raqami (ba’zi raqamlar tushib qolgan)"),
    "deal_date": ("Дата сделки", "Bitim sanasi"),
    "deal_time": ("Время сделки (Ташкент)", "Bitim vaqti (Toshkent)"),
    "year": ("Год сделки", "Bitim yili"), "quarter": ("Квартал 1-4", "Chorak 1-4"), "month": ("Месяц 1-12", "Oy 1-12"),
    "week": ("ISO-неделя года", "ISO hafta raqami"), "dow": ("День недели 1=пн..7=вс", "Hafta kuni 1=du..7=ya"), "hour": ("Час сделки 10-16", "Bitim soati 10-16"),
    "trade_type": ("Тип торгов: Английский аукцион / Обратный аукцион / Двусторонний аукцион", "Savdo turi (ruscha qiymatlar)"),
    "trade_type_uz": ("Тип торгов по-узбекски", "Savdo turi: Angliya auksioni / Teskari auksion / Ikki tomonlama auksion"),
    "contract_no": ("Номер контракта → contracts.contract_no", "Shartnoma raqami → contracts.contract_no"),
    "contract_type": ("Внутренний или Экспортный", "Ichki yoki Eksport (ruscha qiymat)"),
    "is_export": ("TRUE = экспортная сделка (валюта USD)", "TRUE = eksport bitimi (valyuta USD)"),
    "contract_form": ("Спот / Форвард / Фьючерс", "Spot / Forvard / Fyuchers (ruscha qiymat)"),
    "product_raw": ("Наименование товара как в реестре (с маркой/сортом/классом)", "Tovar nomi reyestrdagi kabi (marka/nav/klass bilan)"),
    "product_key": ("Ключ канонического товара (cotton_spot, wheat, diesel ...)", "Kanonik tovar kaliti"),
    "product_ru": ("Канонический товар по-русски (объединяет варианты написания)", "Kanonik tovar ruscha"),
    "product_uz": ("Канонический товар по-узбекски", "Kanonik tovar o‘zbekcha"),
    "family_key": ("Ключ семейства товаров (cotton, wheat, flour, cement ...)", "Tovar oilasi kaliti"),
    "family_ru": ("Семейство товаров по-русски", "Tovar oilasi ruscha"), "family_uz": ("Семейство по-узбекски", "Tovar oilasi o‘zbekcha"),
    "grade_sort": ("Сорт: highest / 1 / 2 / 3 или NULL", "Nav: highest (oliy) / 1 / 2 / 3 yoki NULL"),
    "grade_class": ("Класс 1-4 или NULL", "Klass 1-4 yoki NULL"),
    "brand": ("Марка товара (АИ-92, ЦЕМ I 42,5 Н, Б ...)", "Tovar markasi"),
    "size": ("Размер товара", "Tovar o‘lchami"), "ptype": ("Тип товара (хлебопекарная, рафинированное ...)", "Tovar tipi"),
    "spec": ("Спецификация после названия товара (марка/размер/тип одной строкой)", "Tovar nomidan keyingi spetsifikatsiya"),
    "category_ru": ("Категория товара", "Tovar kategoriyasi (ruscha)"), "category_uz": ("Категория по-узбекски", "Tovar kategoriyasi"),
    "industry_ru": ("Отрасль промышленности", "Sanoat tarmog‘i (ruscha)"), "industry_uz": ("Отрасль по-узбекски", "Sanoat tarmog‘i"),
    "tnved": ("Код ТН ВЭД (6 знаков)", "TN VED kodi (6 raqam)"),
    "unit": ("Единица измерения: тонна/килограмм/литр/штука/куб.метр/кв.метр/метр", "O‘lchov birligi (ruscha qiymat)"),
    "unit_uz": ("Единица по-узбекски", "O‘lchov birligi: tonna/kilogramm/litr/dona/kub metr/kvadrat metr/metr"),
    "lot_size": ("Единиц измерения в одном лоте (1, 12, 100, 1000)", "Bir lotdagi birliklar soni"),
    "qty_lots": ("Количество купленных лотов", "Sotib olingan lotlar soni"),
    "volume": ("Объём = qty_lots × lot_size, в единицах unit", "Hajm = qty_lots × lot_size, unit birligida"),
    "volume_t": ("Объём в тоннах (только для тонна/килограмм, иначе NULL)", "Hajm tonnada (faqat tonna/kg, aks holda NULL)"),
    "amount": ("Сумма сделки в валюте контракта (currency)", "Bitim summasi shartnoma valyutasida (currency)"),
    "currency": ("UZS или USD", "UZS yoki USD"),
    "start_price": ("Стартовая цена за единицу измерения, в валюте контракта", "Boshlang‘ich narx 1 birlik uchun, shartnoma valyutasida"),
    "unit_price": ("Цена за единицу = amount / volume, в валюте контракта", "Birlik narxi = amount / volume, shartnoma valyutasida"),
    "price_vs_start_pct": ("Изменение цены к стартовой, % (+ выше старта)", "Narxning boshlang‘ichga nisbatan o‘zgarishi, %"),
    "usd_rate": ("Курс USD на дату сделки, сум", "Bitim kunidagi USD kursi, so‘m"),
    "amount_uzs": ("Сумма в сумах (экспорт пересчитан по курсу даты сделки)", "Summa so‘mda (eksport bitim kunidagi kurs bilan)"),
    "amount_usd": ("Сумма в долларах (внутренние пересчитаны по курсу даты сделки)", "Summa dollarda (ichki bitimlar bitim kunidagi kurs bilan)"),
    "region_id": ("Код региона склада продавца", "Sotuvchi ombori hududi kodi"),
    "region_uz": ("Регион по-узбекски (Toshkent shahri, Farg‘ona viloyati ...)", "Hudud o‘zbekcha"),
    "region_ru": ("Регион по-русски (г. Ташкент, Ферганская область ...)", "Hudud ruscha"),
    "seller_inn": ("ИНН продавца → participants.inn", "Sotuvchi STIR → participants.inn"),
    "seller_name": ("Наименование продавца", "Sotuvchi nomi"),
    "buyer_inn": ("ИНН покупателя → participants.inn", "Xaridor STIR"), "buyer_name": ("Наименование покупателя", "Xaridor nomi"),
    "buyer_activity_ru": ("Вид деятельности покупателя", "Xaridor faoliyat turi (ruscha)"), "buyer_activity_uz": ("Вид деятельности по-узбекски", "Xaridor faoliyat turi"),
    "buyer_is_vat": ("Покупатель — плательщик НДС", "Xaridor QQS to‘lovchi"),
    "seller_broker": ("Код брокера продавца → brokers.code", "Sotuvchi brokeri kodi"), "seller_broker_name": ("Брокер продавца", "Sotuvchi brokeri nomi"),
    "buyer_broker": ("Код брокера покупателя → brokers.code", "Xaridor brokeri kodi"), "buyer_broker_name": ("Брокер покупателя", "Xaridor brokeri nomi"),
    "status_ru": ("Состояние сделки на 30.10.2026 (Исполнен, Аннулирован(Не оплачен), Ждёт оплату ...)", "Bitim holati 30.10.2026 holatiga (ruscha)"),
    "status_uz": ("Состояние по-узбекски", "Bitim holati: Bajarilgan, Bekor qilingan (to‘lanmagan), To‘lov kutilmoqda ..."),
    "status_code": ("executed/partial/annulled/paid/awaiting_payment/concluded/not_signed/new", "Holat kodi"),
    "is_annulled": ("TRUE = аннулирована (не оплачена в срок), сделка не состоялась — исключать из оборота", "TRUE = bekor qilingan — aylanmadan chiqariladi"),
    "is_executed": ("TRUE = исполнена полностью или частично", "TRUE = to‘liq yoki qisman bajarilgan"),
    "is_partial": ("TRUE = исполнена частично", "TRUE = qisman bajarilgan"),
    "is_paid": ("TRUE = оплачена (есть дата оплаты)", "TRUE = to‘langan"),
    "is_open": ("TRUE = ещё в процессе (новый/не подписан/заключен/ждёт оплату/оплачен)", "TRUE = hali jarayonda"),
    "pay_date": ("Дата оплаты, NULL если не оплачена", "To‘lov sanasi, to‘lanmagan bo‘lsa NULL"),
    "delivery_date": ("Дата поставки, NULL если не поставлена", "Yetkazish sanasi, yetkazilmagan bo‘lsa NULL"),
    "delivered_lots": ("Поставлено лотов", "Yetkazilgan lotlar"), "delivered_volume": ("Поставлено в единицах unit", "Yetkazilgan hajm, unit birligida"),
    "delivered_share": ("Доля поставки 0..1", "Yetkazilgan ulush 0..1"),
    "pay_term_days": ("Срок оплаты по контракту, календарных дней от даты сделки", "To‘lov muddati, bitim sanasidan kalendar kun"),
    "delivery_term_days": ("Срок поставки по контракту, календарных дней от даты оплаты", "Yetkazish muddati, to‘lov sanasidan kalendar kun"),
    "pay_due_date": ("Крайний срок оплаты = deal_date + pay_term_days", "To‘lov muddati sanasi = deal_date + pay_term_days"),
    "delivery_due_date": ("Крайний срок поставки = pay_date + delivery_term_days", "Yetkazish muddati sanasi = pay_date + delivery_term_days"),
    "days_to_pay": ("Фактически дней от сделки до оплаты", "Bitimdan to‘lovgacha o‘tgan kunlar"),
    "delivery_delay_days": ("Просрочка поставки в днях (>0 = с опозданием), NULL если не поставлено", "Yetkazish kechikishi kunlarda (>0 kech)"),
    "is_delivery_late": ("TRUE = поставлено позже срока", "TRUE = muddatidan kech yetkazilgan"),
}

TABLE_COMMENTS = {
    "deals": ("Реестр биржевых сделок, 500 000 строк, 03.01.2022–30.10.2026. Одна строка = одна сделка. Все справочные поля уже присоединены.",
              "Birja bitimlari reyestri, 500 000 qator, 03.01.2022–30.10.2026. Bir qator = bir bitim. Barcha ma’lumotnoma maydonlari qo‘shilgan."),
    "contracts": ("Карточки контрактов (6 616): товар, единица, лот, валюта, базисная цена, сроки оплаты/поставки, продавец.",
                  "Shartnoma kartochkalari (6 616): tovar, birlik, lot, valyuta, bazis narx, to‘lov/yetkazish muddatlari, sotuvchi."),
    "participants": ("Компании-участники (14 392): 392 продавца и 14 000 покупателей. Роль фиксирована.",
                     "Ishtirokchi kompaniyalar (14 392): 392 sotuvchi, 14 000 xaridor. Rol qat’iy."),
    "brokers": ("Брокерские конторы (120), коды BK001–BK120.", "Broker idoralari (120), kodlar BK001–BK120."),
    "usd_rates": ("Курс доллара на каждый календарный день 01.01.2022–31.10.2026, сум за 1 USD.", "Har kun uchun dollar kursi, 1 USD uchun so‘m."),
}


def main() -> None:
    t0 = time.time()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    for p in (DB_PATH, DB_PATH + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA internal")

    # ---- raw ---------------------------------------------------------------
    log("raw CSV → internal.raw_*")
    deal_files = sorted(glob.glob(os.path.join(DATA, "deals_*.csv")))
    con.execute(f"CREATE TABLE internal.raw_deals AS SELECT * FROM read_csv({deal_files!r}, all_varchar=true, header=true, union_by_name=true)")
    for name in ("contracts", "participants", "brokers", "usd_rates"):
        con.execute(f"CREATE TABLE internal.raw_{name} AS SELECT * FROM read_csv('{os.path.join(DATA, name + '.csv')}', all_varchar=true, header=true)")

    # ---- catalog dims ------------------------------------------------------
    log("catalog → internal.dim_*")
    for fname, tname in (("regions.csv", "dim_region"), ("statuses.csv", "dim_status"), ("units.csv", "dim_unit"),
                         ("trade_types.csv", "dim_trade_type"), ("families.csv", "dim_family"), ("products.csv", "dim_product"),
                         ("translations.csv", "dim_translation")):
        df = read_catalog(fname)
        con.register("df_tmp", df)
        con.execute(f"CREATE TABLE internal.{tname} AS SELECT * FROM df_tmp")
        con.unregister("df_tmp")
    con.execute("""ALTER TABLE internal.dim_status ALTER is_annulled TYPE BOOLEAN; ALTER TABLE internal.dim_status ALTER is_executed TYPE BOOLEAN;
                   ALTER TABLE internal.dim_status ALTER is_partial TYPE BOOLEAN; ALTER TABLE internal.dim_status ALTER is_paid TYPE BOOLEAN;
                   ALTER TABLE internal.dim_status ALTER is_open TYPE BOOLEAN""")

    # ---- product map -------------------------------------------------------
    log("product map (normalize + products.csv rules)")
    pm = build_product_map(con)
    con.register("pm", pm)
    con.execute("CREATE TABLE internal.map_raw_product AS SELECT * REPLACE (CAST(grade_class AS INTEGER) AS grade_class) FROM pm")
    con.unregister("pm")

    # ---- participants / brokers (python normalization) ---------------------
    log("participants, brokers")
    suffixes = load_suffixes(os.path.join(CATALOG, "company_suffixes.txt"))
    p = con.execute("SELECT * FROM internal.raw_participants").df()
    p.columns = ["inn", "name", "region_uz", "activity_ru", "role_ru", "vat_ru"]
    p["name_norm"] = p["name"].map(norm_company)
    cores = p["name_norm"].map(lambda s: strip_legal_form(s, suffixes))
    p["name_core"] = cores.map(lambda t: t[0])
    p["legal_form"] = cores.map(lambda t: t[1])
    con.register("p_df", p)
    con.execute("""
        CREATE TABLE participants AS
        SELECT p.inn, p.name, p.name_norm, p.name_core, p.legal_form,
               r.region_id, p.region_uz, r.name_ru AS region_ru,
               p.activity_ru, ta.uz AS activity_uz,
               CASE p.role_ru WHEN 'Продавец' THEN 'seller' ELSE 'buyer' END AS role,
               p.role_ru, CASE p.role_ru WHEN 'Продавец' THEN 'Sotuvchi' ELSE 'Xaridor' END AS role_uz,
               p.vat_ru = 'Да' AS is_vat
        FROM p_df p
        LEFT JOIN internal.dim_region r ON r.name_uz = p.region_uz
        LEFT JOIN internal.dim_translation ta ON ta.kind='activity' AND ta.ru = p.activity_ru
        ORDER BY p.inn""")
    con.unregister("p_df")

    b = con.execute("SELECT * FROM internal.raw_brokers").df()
    b.columns = ["code", "name", "inn", "region_uz"]
    b["name_norm"] = b["name"].map(norm_company)
    cores = b["name_norm"].map(lambda s: strip_legal_form(s, suffixes))
    b["name_core"] = cores.map(lambda t: t[0])
    b["legal_form"] = cores.map(lambda t: t[1])
    con.register("b_df", b)
    con.execute("""
        CREATE TABLE brokers AS
        SELECT b.code, b.name, b.name_norm, b.name_core, b.legal_form, b.inn, r.region_id, b.region_uz, r.name_ru AS region_ru
        FROM b_df b LEFT JOIN internal.dim_region r ON r.name_uz = b.region_uz ORDER BY b.code""")
    con.unregister("b_df")

    # ---- usd_rates ---------------------------------------------------------
    con.execute("""CREATE TABLE usd_rates AS
        SELECT strptime("Дата", '%d.%m.%Y')::DATE AS rate_date, CAST("Курс" AS DECIMAL(12,2)) AS rate
        FROM internal.raw_usd_rates ORDER BY 1""")

    # ---- contracts ---------------------------------------------------------
    log("contracts")
    con.execute(r"""
        CREATE TABLE contracts AS
        SELECT c."Номер контракта" AS contract_no,
               c."Наименование продукта" AS product_name,
               m.product_key, dp.name_ru AS product_ru, dp.name_uz AS product_uz,
               m.family_key, df.name_ru AS family_ru, df.name_uz AS family_uz,
               nullif(c."Марка товара",'') AS brand, nullif(c."Размер товара",'') AS size,
               nullif(c."Сорт товара",'') AS sort_raw, nullif(c."Тип товара",'') AS ptype, nullif(c."Класс товара",'') AS class_raw,
               m.grade_sort, m.grade_class,
               c."Код ТН ВЭД" AS tnved, c."Группа товара" AS group_ru, tg.uz AS group_uz,
               c."Категория товара" AS category_ru, tc.uz AS category_uz,
               c."Отрасль промышленности" AS industry_ru, ti.uz AS industry_uz,
               c."Единица измерения" AS unit, du.unit_uz, CAST(c."Лот" AS INTEGER) AS lot_size,
               CASE c."Валюта" WHEN 'Доллар США' THEN 'USD' ELSE 'UZS' END AS currency,
               CAST(replace(c."Базисная цена",' ','') AS DECIMAL(18,2)) AS basis_price,
               CAST(regexp_extract(c."Сумма залога", '[0-9]+') AS INTEGER) AS deposit_pct,
               c."Базис поставки" AS delivery_basis,
               r.region_id, r.name_uz AS region_uz, c."Регион" AS region_ru, c."Адрес склада" AS warehouse_address,
               c."Тип торгов" AS trade_type, c."Тип контракта" AS contract_type, c."Тип контракта"='Экспортный' AS is_export,
               c."Форма контракта" AS contract_form,
               CAST(c."Срок оплаты" AS INTEGER) AS pay_term_days, CAST(c."Срок поставки" AS INTEGER) AS delivery_term_days,
               c."Упаковка" AS packaging, c."ИНН продавца" AS seller_inn,
               strptime(c."Дата регистрации",'%d.%m.%Y')::DATE AS reg_date, strptime(c."Действует до",'%d.%m.%Y')::DATE AS valid_to
        FROM internal.raw_contracts c
        LEFT JOIN internal.map_raw_product m ON m.source='contract' AND m.raw_name = c."Наименование продукта"
        LEFT JOIN internal.dim_product dp ON dp.product_key = m.product_key
        LEFT JOIN internal.dim_family df ON df.family_key = m.family_key
        LEFT JOIN internal.dim_region r ON r.name_ru = c."Регион"
        LEFT JOIN internal.dim_unit du ON du.unit_ru = c."Единица измерения"
        LEFT JOIN internal.dim_translation tg ON tg.kind='group' AND tg.ru = c."Группа товара"
        LEFT JOIN internal.dim_translation tc ON tc.kind='category' AND tc.ru = c."Категория товара"
        LEFT JOIN internal.dim_translation ti ON ti.kind='industry' AND ti.ru = c."Отрасль промышленности"
        ORDER BY contract_no""")
    # contract grade: prefer explicit columns when the name had none
    con.execute("""UPDATE contracts SET grade_sort = COALESCE(grade_sort,
                      CASE WHEN lower(sort_raw) LIKE '%высш%' OR lower(sort_raw) LIKE 'в/с%' THEN 'highest'
                           WHEN regexp_matches(lower(sort_raw), '(^|[^0-9])1|перв|^i ') THEN '1'
                           WHEN regexp_matches(lower(sort_raw), '2|втор|^ii ') THEN '2'
                           WHEN regexp_matches(lower(sort_raw), '3|трет|^iii ') THEN '3' END)
                   WHERE sort_raw IS NOT NULL""")

    # ---- deals -------------------------------------------------------------
    log("deals (wide fact table)")
    con.execute(r"""
        CREATE TABLE deals AS
        WITH d AS (
            SELECT CAST("Номер сделки" AS BIGINT) AS deal_no,
                   strptime("Дата сделки",'%d.%m.%Y')::DATE AS deal_date,
                   strptime("Время сделки",'%H:%M:%S')::TIME AS deal_time,
                   "Тип торгов" AS trade_type, CAST("Количество" AS INTEGER) AS qty_lots,
                   CAST(replace("Сумма сделки",' ','') AS DECIMAL(20,2)) AS amount,
                   CAST(replace("Стартовая цена",' ','') AS DECIMAL(18,2)) AS start_price,
                   "Номер контракта" AS contract_no, "Наименование товара" AS product_raw,
                   CAST("Лот" AS INTEGER) AS lot_size, "Единица измерения" AS unit, "Регион" AS region_uz,
                   "Тип контракта" AS contract_type, "Форма контракта" AS contract_form, "Состояние сделки" AS status_ru,
                   "ИНН продавца" AS seller_inn, "ИНН покупателя" AS buyer_inn,
                   "Брокер продавца" AS seller_broker, "Брокер покупателя" AS buyer_broker,
                   strptime(nullif("Дата оплаты",''),'%d.%m.%Y')::DATE AS pay_date,
                   strptime(nullif("Дата поставки",''),'%d.%m.%Y')::DATE AS delivery_date,
                   CAST(nullif("Поставлено",'') AS INTEGER) AS delivered_lots
            FROM internal.raw_deals)
        SELECT d.deal_no, d.deal_date, d.deal_time,
               year(d.deal_date) AS year, quarter(d.deal_date) AS quarter, month(d.deal_date) AS month,
               isoyear(d.deal_date)*100 + week(d.deal_date) AS week, isodow(d.deal_date) AS dow, hour(d.deal_time) AS hour,
               d.trade_type, tt.value_uz AS trade_type_uz,
               d.contract_no, d.contract_type, d.contract_type='Экспортный' AS is_export, d.contract_form,
               d.product_raw, m.product_key, dp.name_ru AS product_ru, dp.name_uz AS product_uz,
               m.family_key, df.name_ru AS family_ru, df.name_uz AS family_uz,
               m.grade_sort, m.grade_class, m.brand, m.size, m.ptype, m.spec,
               c.category_ru, c.category_uz, c.industry_ru, c.industry_uz, c.tnved,
               d.unit, du.unit_uz, d.lot_size, d.qty_lots,
               CAST(d.qty_lots AS BIGINT) * d.lot_size AS volume,
               CASE d.unit WHEN 'тонна' THEN (d.qty_lots*d.lot_size)::DOUBLE WHEN 'килограмм' THEN (d.qty_lots*d.lot_size)/1000.0 END AS volume_t,
               d.amount, c.currency, d.start_price,
               (d.amount / (d.qty_lots*d.lot_size))::DOUBLE AS unit_price,
               round(((d.amount / (d.qty_lots*d.lot_size)) / d.start_price - 1) * 100, 3)::DOUBLE AS price_vs_start_pct,
               u.rate AS usd_rate,
               CASE WHEN c.currency='USD' THEN round(d.amount * u.rate, 2) ELSE d.amount END::DECIMAL(22,2) AS amount_uzs,
               CASE WHEN c.currency='USD' THEN d.amount ELSE round(d.amount / u.rate, 2) END::DECIMAL(18,2) AS amount_usd,
               r.region_id, d.region_uz, r.name_ru AS region_ru,
               d.seller_inn, ps.name AS seller_name, d.buyer_inn, pb.name AS buyer_name,
               pb.activity_ru AS buyer_activity_ru, pb.activity_uz AS buyer_activity_uz, pb.is_vat AS buyer_is_vat,
               d.seller_broker, bs.name AS seller_broker_name, d.buyer_broker, bb.name AS buyer_broker_name,
               d.status_ru, st.status_uz, st.status_code, st.is_annulled, st.is_executed, st.is_partial, st.is_paid, st.is_open,
               d.pay_date, d.delivery_date, d.delivered_lots,
               CAST(d.delivered_lots AS BIGINT) * d.lot_size AS delivered_volume,
               (d.delivered_lots::DOUBLE / d.qty_lots) AS delivered_share,
               c.pay_term_days, c.delivery_term_days,
               d.deal_date + to_days(c.pay_term_days) AS pay_due_date,
               d.pay_date + to_days(c.delivery_term_days) AS delivery_due_date,
               date_diff('day', d.deal_date, d.pay_date) AS days_to_pay,
               date_diff('day', d.pay_date + to_days(c.delivery_term_days), d.delivery_date) AS delivery_delay_days,
               CASE WHEN d.delivery_date IS NULL THEN NULL ELSE date_diff('day', d.pay_date + to_days(c.delivery_term_days), d.delivery_date) > 0 END AS is_delivery_late
        FROM d
        LEFT JOIN contracts c ON c.contract_no = d.contract_no
        LEFT JOIN internal.map_raw_product m ON m.source='deal' AND m.raw_name = d.product_raw
        LEFT JOIN internal.dim_product dp ON dp.product_key = m.product_key
        LEFT JOIN internal.dim_family df ON df.family_key = m.family_key
        LEFT JOIN internal.dim_region r ON r.name_uz = d.region_uz
        LEFT JOIN internal.dim_unit du ON du.unit_ru = d.unit
        LEFT JOIN internal.dim_trade_type tt ON tt.kind='trade_type' AND tt.value_ru = d.trade_type
        LEFT JOIN internal.dim_status st ON st.status_ru = d.status_ru
        LEFT JOIN usd_rates u ON u.rate_date = d.deal_date
        LEFT JOIN participants ps ON ps.inn = d.seller_inn
        LEFT JOIN participants pb ON pb.inn = d.buyer_inn
        LEFT JOIN brokers bs ON bs.code = d.seller_broker
        LEFT JOIN brokers bb ON bb.code = d.buyer_broker
        ORDER BY d.deal_no""")

    # ---- brand / size fallbacks and canonical brand spelling ---------------
    # names without the 'Марка' label ("Кирпич керамический М-125") carry the brand in spec
    con.execute(r"""UPDATE deals SET
        size  = COALESCE(size,  CASE WHEN spec IS NOT NULL AND regexp_matches(spec, '(мм|\d,\d+\s*л)$') THEN spec END),
        brand = COALESCE(brand, CASE WHEN spec IS NOT NULL AND size IS NULL AND ptype IS NULL
                                      AND NOT regexp_matches(spec, '(мм|\d,\d+\s*л)$') THEN spec END)""")
    con.execute(r"""CREATE TEMP TABLE bmap AS
        SELECT brand_norm, arg_max(brand, n) AS brand_canon
        FROM (SELECT upper(regexp_replace(brand, '\s+', '', 'g')) AS brand_norm, brand, count(*) AS n
              FROM deals WHERE brand IS NOT NULL GROUP BY ALL) GROUP BY 1""")
    con.execute(r"""UPDATE deals SET brand = bmap.brand_canon FROM bmap
                    WHERE upper(regexp_replace(deals.brand, '\s+', '', 'g')) = bmap.brand_norm""")
    con.execute(r"""UPDATE contracts SET brand = bmap.brand_canon FROM bmap
                    WHERE upper(regexp_replace(contracts.brand, '\s+', '', 'g')) = bmap.brand_norm""")
    con.execute(r"""CREATE TEMP TABLE smap AS
        SELECT size_norm, arg_max(size, n) AS size_canon
        FROM (SELECT upper(regexp_replace(size, '\s+', '', 'g')) AS size_norm, size, count(*) AS n
              FROM deals WHERE size IS NOT NULL GROUP BY ALL) GROUP BY 1""")
    con.execute(r"""UPDATE deals SET size = smap.size_canon FROM smap
                    WHERE upper(regexp_replace(deals.size, '\s+', '', 'g')) = smap.size_norm""")

    # ---- macros ------------------------------------------------------------
    log("macros, comments, meta")
    con.execute("CREATE MACRO wavg_price(amount, volume) AS sum(amount) / sum(volume)")
    con.execute("CREATE MACRO fmt_num(x) AS format('{:,.2f}', x::DOUBLE)")

    # ---- comments ----------------------------------------------------------
    for col, (ru, uz) in DEALS_COMMENTS.items():
        con.execute(f"COMMENT ON COLUMN deals.{col} IS {ru + ' | ' + uz!r}")
    for tbl, (ru, uz) in TABLE_COMMENTS.items():
        con.execute(f"COMMENT ON TABLE {tbl} IS {ru + ' | ' + uz!r}")
    # contracts/participants/brokers share names with deals where applicable
    extra = {
        "contracts": {"contract_no": ("Номер контракта", "Shartnoma raqami"), "product_name": ("Наименование продукта как заполнил продавец", "Sotuvchi yozgan mahsulot nomi"),
                      "basis_price": ("Базисная цена за единицу на дату регистрации", "Ro‘yxatdan o‘tgan kundagi bazis narx"),
                      "deposit_pct": ("Залог, % от суммы сделки", "Garov, bitim summasidan %"), "delivery_basis": ("Базис поставки EXW/FCA/DAP/CPT", "Yetkazish bazisi"),
                      "warehouse_address": ("Адрес склада продавца", "Sotuvchi ombori manzili"), "packaging": ("Упаковка", "Qadoq"),
                      "reg_date": ("Дата регистрации контракта", "Shartnoma ro‘yxat sanasi"), "valid_to": ("Действует до", "Amal qilish muddati"),
                      "seller_inn": ("ИНН продавца", "Sotuvchi STIR")},
        "participants": {"inn": ("ИНН, 9 цифр, уникален", "STIR, 9 raqam"), "name": ("Наименование как в реестре", "Nomi reyestrdagi kabi"),
                         "name_norm": ("Нормализованное имя для поиска (латиница, без кавычек)", "Qidiruv uchun normallashgan nom"),
                         "name_core": ("Имя без организационно-правовой формы", "Tashkiliy shaklsiz nom"), "legal_form": ("ОПФ: mchj, aj, xk ...", "Tashkiliy shakl"),
                         "role": ("seller или buyer (фиксировано)", "seller yoki buyer"), "activity_ru": ("Вид деятельности", "Faoliyat turi"), "is_vat": ("Плательщик НДС", "QQS to‘lovchi")},
        "brokers": {"code": ("Код брокера BK001..BK120", "Broker kodi"), "name": ("Наименование", "Nomi"), "inn": ("ИНН брокера", "Broker STIR")},
        "usd_rates": {"rate_date": ("Дата", "Sana"), "rate": ("Сум за 1 USD", "1 USD uchun so‘m")},
    }
    for tbl, cols in extra.items():
        for col, (ru, uz) in cols.items():
            con.execute(f"COMMENT ON COLUMN {tbl}.{col} IS {ru + ' | ' + uz!r}")

    # ---- meta --------------------------------------------------------------
    stats = con.execute("""SELECT count(*), min(deal_date), max(deal_date), count(DISTINCT seller_inn), count(DISTINCT buyer_inn) FROM deals""").fetchone()
    meta = {
        "data_asof": DATA_ASOF, "min_deal_date": str(stats[1]), "max_deal_date": str(stats[2]),
        "n_deals": str(stats[0]), "n_sellers": str(stats[3]), "n_buyers": str(stats[4]),
        "n_contracts": str(con.execute("SELECT count(*) FROM contracts").fetchone()[0]),
        "n_participants": str(con.execute("SELECT count(*) FROM participants").fetchone()[0]),
        "n_brokers": str(con.execute("SELECT count(*) FROM brokers").fetchone()[0]),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "note_ru": "Все данные вымышленные. Состояния сделок — на конец дня 30.10.2026. 2026 год неполный (до 30 октября).",
        "note_uz": "Barcha ma’lumotlar o‘ylab topilgan. Holatlar 30.10.2026 kun oxiri bo‘yicha. 2026 yil to‘liq emas (30-oktabrgacha).",
    }
    con.execute("CREATE TABLE meta(key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))

    # ---- golden checks -----------------------------------------------------
    log("golden checks")
    checks = [
        ("SELECT count(*) FROM deals", 500000),
        ("SELECT count(*) FROM deals WHERE year=2022", 84299), ("SELECT count(*) FROM deals WHERE year=2026", 99306),
        ("SELECT count(*) FROM contracts", 6616), ("SELECT count(*) FROM participants", 14392),
        ("SELECT count(*) FROM brokers", 120), ("SELECT count(*) FROM usd_rates", 1765),
        ("SELECT count(*) FROM deals WHERE product_key IS NULL OR family_key IS NULL", 0),
        ("SELECT count(*) FROM deals WHERE region_id IS NULL", 0),
        ("SELECT count(*) FROM contracts WHERE region_id IS NULL OR product_key IS NULL", 0),
        ("SELECT count(*) FROM participants WHERE region_id IS NULL OR activity_uz IS NULL", 0),
        ("SELECT count(*) FROM deals WHERE status_code IS NULL OR usd_rate IS NULL OR currency IS NULL", 0),
        ("SELECT count(*) FROM deals WHERE seller_name IS NULL OR buyer_name IS NULL OR seller_broker_name IS NULL", 0),
        ("SELECT count(*) FROM deals WHERE is_export", 6590),
        ("SELECT count(*) FROM deals WHERE is_export AND currency<>'USD'", 0),
        ("SELECT count(*) FROM deals WHERE NOT is_export AND currency<>'UZS'", 0),
        ("SELECT count(*) FROM deals WHERE is_annulled", 33719),
        ("SELECT count(*) FROM deals WHERE status_ru='Ждёт оплату'", 1714),
        ("SELECT count(*) FROM deals WHERE is_delivery_late", 17805),
        ("SELECT count(*) FROM deals WHERE pay_date > pay_due_date", 0),
        ("SELECT count(*) FROM deals WHERE is_executed AND NOT is_partial AND delivered_lots <> qty_lots", 0),
        ("SELECT count(*) FROM deals WHERE abs(unit_price*volume - amount) > 0.01", 0),
        ("SELECT sum(amount_uzs)::VARCHAR FROM deals WHERE NOT is_export", "206835219986242.97"),
        ("SELECT sum(amount)::VARCHAR FROM deals WHERE is_export", "1137509729.39"),
        ("SELECT count(*) FROM deals WHERE product_raw ILIKE '%хлопок%'", 136208),
        ("SELECT count(*) FROM deals WHERE family_key='cotton'", 136208),
        ("SELECT round(wavg_price(amount, volume)) FROM deals WHERE product_key='petrol' AND brand='АИ-92' AND year=2025 AND unit='литр'", 10700.0),
        ("SELECT count(*) FROM deals WHERE family_key='cotton' AND grade_sort='1' AND grade_class=2", None),
    ]
    failed = 0
    for sql, exp in checks:
        got = con.execute(sql).fetchone()[0]
        ok = exp is None or got == exp
        failed += not ok
        print(("  ok   " if ok else "  FAIL ") + f"{sql[:95]:<96} -> {got}" + ("" if ok else f"  (expected {exp})"))
    if failed:
        con.close()
        raise SystemExit(f"{failed} golden checks failed")

    con.execute("CHECKPOINT")
    con.close()
    log(f"done in {time.time()-t0:.1f}s → {DB_PATH} ({os.path.getsize(DB_PATH)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
