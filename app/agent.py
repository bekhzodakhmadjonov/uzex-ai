"""Question -> answer pipeline. Numbers and names reach the user only through code.

  1. language + entity hints (resolvers)
  2. primary model plans SQL; a second, different model plans independently in parallel
  3. guard validates, DuckDB executes read-only with a timeout
  4. the two results are compared; if they differ, the primary model adjudicates
  5. the model writes a TEMPLATE with {{row.column}} placeholders; code fills in the values.
     Digits or company names typed by the model are rejected; after one retry the answer
     falls back to a deterministic template.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from typing import Any

from . import db
from .guard import GuardError, check_tables, validate_sql
from .hints import detect_language, hints_for, hints_to_text

SQL_TIMEOUT_S = 8.0
MAX_ROWS = 200
MAX_ROWS_TO_LLM = 30
VERIFY_WAIT_S = 25.0
# An answer later than 90 s counts as no answer, so every model call runs against this clock.
TOTAL_BUDGET_S = 70.0
PLAN_WAIT_S = 45.0
STEP_WAIT_S = 15.0

T = {
    "uz": {"no_rows": "Reyestrda bu shartlarga mos bitim topilmadi (0 ta).", "filters": "Filtrlar", "basis": "Asos",
           "deals": "bitimlar", "rows": "natija qatorlari", "period": "davr", "asof": "30.10.2026 holatiga ko‘ra",
           "unans": "Bu savolga mavjud ma’lumotlar asosida javob berib bo‘lmaydi. Reyestrda 03.01.2022–30.10.2026 davridagi bitimlar, kontraktlar, ishtirokchilar, brokerlar va dollar kursi bor.",
           "err": "So‘rovni bajarib bo‘lmadi. Raqam o‘ylab topmaslik uchun javob berilmadi, iltimos savolni boshqacha ifodalang.", "result": "Natija",
           "yes": "ha", "no": "yo‘q", "ming": "ming", "mln": "mln", "mlrd": "mlrd", "trln": "trln"},
    "uz_cyr": {"no_rows": "Реестрда бу шартларга мос битим топилмади (0 та).", "filters": "Филтрлар", "basis": "Асос",
               "deals": "битимлар", "rows": "натижа қаторлари", "period": "давр", "asof": "30.10.2026 ҳолатига кўра",
               "unans": "Бу саволга мавжуд маълумотлар асосида жавоб бериб бўлмайди. Реестрда 03.01.2022–30.10.2026 давридаги битимлар, контрактлар, иштирокчилар, брокерлар ва доллар курси бор.",
               "err": "Сўровни бажариб бўлмади. Рақам ўйлаб топмаслик учун жавоб берилмади, илтимос саволни бошқача ифодаланг.", "result": "Натижа",
               "yes": "ҳа", "no": "йўқ", "ming": "минг", "mln": "млн", "mlrd": "млрд", "trln": "трлн"},
    "ru": {"no_rows": "В реестре нет сделок, соответствующих этим условиям (0).", "filters": "Фильтры", "basis": "Основание",
           "deals": "сделок", "rows": "строк результата", "period": "период", "asof": "по состоянию на 30.10.2026",
           "unans": "На этот вопрос нельзя ответить на основе имеющихся данных. В реестре есть сделки за 03.01.2022–30.10.2026, контракты, участники, брокеры и курс доллара.",
           "err": "Не удалось выполнить запрос. Чтобы не выдумывать цифры, ответ не дан, попробуйте переформулировать вопрос.", "result": "Результат",
           "yes": "да", "no": "нет", "ming": "тыс", "mln": "млн", "mlrd": "млрд", "trln": "трлн"},
    "en": {"no_rows": "The registry has no deals matching these filters (0).", "filters": "Filters", "basis": "Basis",
           "deals": "deals", "rows": "result rows", "period": "period", "asof": "as of 30.10.2026",
           "unans": "This cannot be answered from the available data. The registry covers deals from 03.01.2022 to 30.10.2026, contracts, participants, brokers and the dollar rate.",
           "err": "The query failed. No answer is given rather than inventing a figure, please rephrase.", "result": "Result",
           "yes": "yes", "no": "no", "ming": "k", "mln": "mln", "mlrd": "bn", "trln": "trn"},
}

LABELS = {
    "n_deals": ("bitimlar soni", "битимлар сони", "число сделок"), "n_annulled": ("shundan bekor qilingan", "шундан бекор қилинган", "из них аннулировано"),
    "month": ("oy", "ой", "месяц"), "year": ("yil", "йил", "год"), "quarter": ("chorak", "чорак", "квартал"), "deal_date": ("sana", "сана", "дата"),
    "unit": ("birlik", "бирлик", "единица"), "currency": ("valyuta", "валюта", "валюта"),
    "region_uz": ("hudud", "ҳудуд", "регион"), "region_ru": ("hudud", "ҳудуд", "регион"),
    "buyer_name": ("xaridor", "харидор", "покупатель"), "seller_name": ("sotuvchi", "сотувчи", "продавец"),
    "buyer_inn": ("xaridor STIR", "харидор СТИР", "ИНН покупателя"), "seller_inn": ("sotuvchi STIR", "сотувчи СТИР", "ИНН продавца"),
    "total_uzs_valid": ("summa, bekor qilinganlarsiz", "сумма, бекор қилинганларсиз", "сумма без аннулированных"),
    "total_uzs_all": ("summa, hammasi", "сумма, ҳаммаси", "сумма, все сделки"),
    "tonnes_valid": ("tonna, bekor qilinganlarsiz", "тонна, бекор қилинганларсиз", "тонн без аннулированных"),
    "tonnes_all": ("tonna, hammasi", "тонна, ҳаммаси", "тонн, все сделки"),
    "avg_price_valid": ("o‘rtacha narx, bekor qilinganlarsiz", "ўртача нарх, бекор қилинганларсиз", "средняя цена без аннулированных"),
    "avg_price_all": ("o‘rtacha narx, hammasi", "ўртача нарх, ҳаммаси", "средняя цена, все сделки"),
    "total_uzs": ("jami summa, so‘m", "жами сумма, сўм", "сумма, сум"), "total_usd": ("jami summa, USD", "жами сумма, USD", "сумма, USD"),
    "amount_uzs": ("summa, so‘m", "сумма, сўм", "сумма, сум"), "amount_usd": ("summa, USD", "сумма, USD", "сумма, USD"),
    "total_amount": ("jami summa", "жами сумма", "сумма"), "amount": ("summa", "сумма", "сумма"),
    "tonnes": ("hajm, tonna", "ҳажм, тонна", "объём, тонн"), "volume_t": ("hajm, tonna", "ҳажм, тонна", "объём, тонн"),
    "volume": ("hajm", "ҳажм", "объём"), "total_volume": ("jami hajm", "жами ҳажм", "объём"), "quantity": ("miqdor", "миқдор", "количество"),
    "avg_price": ("o‘rtacha narx", "ўртача нарх", "средняя цена"), "price": ("narx", "нарх", "цена"), "unit_price": ("birlik narxi", "бирлик нархи", "цена за единицу"),
    "price_per_unit": ("birlik narxi", "бирлик нархи", "цена за единицу"), "price_per_t": ("narx, tonna uchun", "нарх, тонна учун", "цена за тонну"),
    "avg_days": ("o‘rtacha kun", "ўртача кун", "дней в среднем"), "avg_delay_days": ("o‘rtacha kechikish, kun", "ўртача кечикиш, кун", "средняя просрочка, дней"),
    "delay_days": ("kechikish, kun", "кечикиш, кун", "просрочка, дней"), "n_late": ("kechikkan bitimlar", "кечиккан битимлар", "сделок с опозданием"),
    "avg_pct": ("o‘rtacha foiz", "ўртача фоиз", "средний процент"), "share_pct": ("ulush, %", "улуш, %", "доля, %"), "pct": ("foiz", "фоиз", "процент"),
    "product_ru": ("tovar", "товар", "товар"), "product_uz": ("tovar", "товар", "товар"), "family_ru": ("tovar oilasi", "товар оиласи", "семейство"), "family_uz": ("tovar oilasi", "товар оиласи", "семейство"),
    "seller_broker": ("sotuvchi brokeri", "сотувчи брокери", "брокер продавца"), "buyer_broker": ("xaridor brokeri", "харидор брокери", "брокер покупателя"),
    "seller_broker_name": ("sotuvchi brokeri nomi", "сотувчи брокери номи", "брокер продавца"), "buyer_broker_name": ("xaridor brokeri nomi", "харидор брокери номи", "брокер покупателя"),
    "deal_no": ("bitim №", "битим №", "№ сделки"), "deal_time": ("vaqt", "вақт", "время"), "status": ("holat", "ҳолат", "состояние"), "status_ru": ("holat", "ҳолат", "состояние"), "status_uz": ("holat", "ҳолат", "состояние"),
    "min_date": ("birinchi sana", "биринчи сана", "первая дата"), "max_date": ("oxirgi sana", "охирги сана", "последняя дата"),
    "n_participants": ("ishtirokchilar soni", "иштирокчилар сони", "число участников"), "n_sellers": ("sotuvchilar soni", "сотувчилар сони", "число продавцов"), "n_buyers": ("xaridorlar soni", "харидорлар сони", "число покупателей"),
    "from_date": ("boshlanish", "бошланиш", "с"), "to_date": ("tugash", "тугаш", "по"),
}
_LANG_INDEX = {"uz": 0, "uz_cyr": 1, "ru": 2}

_IDENT = re.compile(r"^(year|month|quarter|week|dow|hour|deal_no|contract_no|inn|code|tnved|.*_inn|.*_id|.*_code|.*_broker|.*_year|.*_month)$", re.I)
_DEAL_COUNT = re.compile(r"^(n_deals|deals|deal_count|n|count|cnt|total_deals|deals_count|count_star\(\)|n_(?!annulled|executed|paid|valid|vat|contracts|participants|brokers)\w+)$", re.I)
_TECHNICAL = re.compile(r"\b(usd_rates|deals|contracts|participants|brokers|family_key|product_key|region_id|status_code|sql|select)\b|таблиц|jadval", re.I)
# Answer text is for a business reader, in which 'deals' is an ordinary word
_TECHNICAL_IN_ANSWER = re.compile(r"\b\w+_\w+\b|\bsql\b|\bselect\b|таблиц|jadval|жадвал", re.I)
# Claims about where a figure comes from: the data names no such source
_INVENTED_SOURCE = re.compile(r"markaziy\s+bank|марказий\s+банк|центробанк|центральн\w*\s+банк\w*|central\s+bank|rasmiy|расмий|официальн\w*|official", re.I)
UNITS = {
    "тонна": {"uz": "tonna", "uz_cyr": "тонна", "ru": "тонна", "en": "tonne"},
    "килограмм": {"uz": "kilogramm", "uz_cyr": "килограмм", "ru": "килограмм", "en": "kilogram"},
    "литр": {"uz": "litr", "uz_cyr": "литр", "ru": "литр", "en": "litre"},
    "штука": {"uz": "dona", "uz_cyr": "дона", "ru": "штука", "en": "piece"},
    "куб.метр": {"uz": "kub metr", "uz_cyr": "куб метр", "ru": "куб.метр", "en": "cubic metre"},
    "кв.метр": {"uz": "kvadrat metr", "uz_cyr": "квадрат метр", "ru": "кв.метр", "en": "square metre"},
    "метр": {"uz": "metr", "uz_cyr": "метр", "ru": "метр", "en": "metre"},
}
CURRENCIES = {
    "UZS": {"uz": "so‘m", "uz_cyr": "сўм", "ru": "сум", "en": "so‘m"},
    "USD": {"uz": "dollar", "uz_cyr": "доллар", "ru": "доллар", "en": "dollar"},
}
_PERIOD_COL = re.compile(r"(^|_)(min|max|from|to|first|last|start|end)(_|$)", re.I)
# 'of which <total including annulled>' reads the all-status total as the annulled part of it
_TOTAL_AS_PART = re.compile(r"(shundan|ulardan|шундан|улардан|из них|из которых|of which)\s*\{\{\s*\d+\s*\.\s*\w*_all\b", re.I)
# 'excluding annulled' next to a plain deal count: that count includes them
_COUNT_AS_VALID = re.compile(r"(?:без аннулированных|bekor qilinganlarsiz|бекор қилинганларсиз|excluding annulled)[^{}]{0,40}\{\{\s*\d+\s*\.\s*(\w+)\s*\}\}", re.I)
_DATA_RANGE_NUMBERS = {"03.01.2022", "30.10.2026", "31.10.2026", "30", "2026", "2022"}
NO_RECORDS = {
    "uz": "Reyestrda bu shartlarga mos yozuv topilmadi (0 ta).", "uz_cyr": "Реестрда бу шартларга мос ёзув топилмади (0 та).",
    "ru": "В реестре нет записей, соответствующих этим условиям (0).", "en": "The registry has no records matching these filters (0).",
}
_RU_MONTHS = {"январь": "январе", "февраль": "феврале", "март": "марте", "апрель": "апреле", "май": "мае", "июнь": "июне",
              "июль": "июле", "август": "августе", "сентябрь": "сентябре", "октябрь": "октябре", "ноябрь": "ноябре", "декабрь": "декабре"}


def _ru_deals(m: re.Match) -> str:
    n = int(re.sub(r"\s", "", m.group(1)))
    word = "сделок" if 11 <= n % 100 <= 14 else {1: "сделка", 2: "сделки", 3: "сделки", 4: "сделки"}.get(n % 10, "сделок")
    return f"{m.group(1)} {word}"


@lru_cache(maxsize=1)
def _region_names() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for uz, cyr, ru, en in db.run_sql("SELECT name_uz, name_uz_cyr, name_ru, name_en FROM internal.dim_region").rows:
        names = {"uz": uz, "uz_cyr": cyr, "ru": ru, "en": en}
        out[uz] = out[ru] = names
    return out


def polish(text: str, lang: str) -> str:
    """Grammar the template cannot get right, because a placeholder prints in the nominative. Words only."""
    if lang == "ru":
        text = re.sub(r"\bза тонна\b", "за тонну", text)
        text = re.sub(r"\bза штука\b", "за штуку", text)
        text = re.sub(r"\bв (?:месяц:?\s+)?(" + "|".join(_RU_MONTHS) + r")\b", lambda m: "в " + _RU_MONTHS[m.group(1)], text)
        text = re.sub(r"(\d[\d ]*?) сделок\b", _ru_deals, text)
    elif lang == "uz":
        text = re.sub(r"\bINN\b", "STIR", text)
    elif lang == "uz_cyr":
        text = re.sub(r"\bИНН\b", "СТИР", text)
    return text
MONTHS = {
    "uz": ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"],
    "uz_cyr": ["январ", "феврал", "март", "апрел", "май", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"],
    "ru": ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}
SOURCES = {
    "deals": {"uz": "bitimlar reyestri", "uz_cyr": "битимлар реестри", "ru": "реестр сделок", "en": "deal registry"},
    "contracts": {"uz": "kontrakt kartochkalari", "uz_cyr": "контракт карточкалари", "ru": "карточки контрактов", "en": "contract cards"},
    "participants": {"uz": "ishtirokchilar ro‘yxati", "uz_cyr": "иштирокчилар рўйхати", "ru": "список участников", "en": "participant list"},
    "brokers": {"uz": "brokerlar ro‘yxati", "uz_cyr": "брокерлар рўйхати", "ru": "список брокеров", "en": "broker list"},
    "usd_rates": {"uz": "dollar kursi jadvali", "uz_cyr": "доллар курси жадвали", "ru": "таблица курса доллара", "en": "dollar rate table"},
}
_LEGAL = re.compile(r"\b(MChJ|MCHJ|МЧЖ|AJ|АЖ|XK|ХК|QK|ҚК|OK|ОК|ООО|АО|LLC)\b")
_QUOTED = re.compile(r"[\"«“]([^\"»”]{2,80})[\"»”]")
_PLACEHOLDER = re.compile(r"\{\{\s*(\d+)\s*\.\s*([^\s{}]+)\s*\}\}")
_DIGITS = re.compile(r"\d+(?:[.,:/-]\d+)*")


# ---------------------------------------------------------------------------
# formatting (the only place values become text)
# ---------------------------------------------------------------------------
def _num(x: float, lang: str, decimals: int) -> str:
    s = f"{x:,.{decimals}f}".replace(",", " ")
    return s.replace(".", ",") if lang != "en" else s


def fmt_value(col: str, v: Any, lang: str = "uz") -> str:
    t = T.get(lang, T["uz"])
    if v is None:
        return "—"
    if isinstance(v, bool):
        return t["yes"] if v else t["no"]
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y") if v.time() == dt.time() else v.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, (dt.time, dt.timedelta)):
        return str(v)
    if isinstance(v, (int, float, decimal.Decimal)):
        if col.lower() == "month" and 1 <= float(v) <= 12:
            return MONTHS.get(lang, MONTHS["uz"])[int(v) - 1]
        if _IDENT.match(col):
            return str(int(v)) if float(v).is_integer() else str(v)
        x = float(v)
        s = _num(x, lang, 0 if x.is_integer() else 2)
        for div, name in ((1e12, "trln"), (1e9, "mlrd")):
            if abs(x) >= div:
                return f"{s} ({_num(x / div, lang, 2)} {t[name]})"
        return s
    if col.lower() == "unit" and v in UNITS:
        return UNITS[v].get(lang, str(v))
    if col.lower() == "currency" and v in CURRENCIES:
        return CURRENCIES[v].get(lang, str(v))
    if "region" in col.lower() and v in _region_names():
        return _region_names()[v].get(lang, str(v))
    return str(v)


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.days
    return v


def rows_for_llm(res: db.QueryResult, lang: str) -> str:
    """Formatted values indexed by row, so the model can see what each placeholder prints."""
    out = [{"row": i, **{c: fmt_value(c, v, lang) for c, v in zip(res.columns, r)}} for i, r in enumerate(res.rows[:MAX_ROWS_TO_LLM])]
    return json.dumps(out, ensure_ascii=False)


def label(col: str, lang: str) -> str:
    if col in LABELS and lang in _LANG_INDEX:
        return LABELS[col][_LANG_INDEX[lang]]
    return col.replace("_", " ")


# ---------------------------------------------------------------------------
# template verification
# ---------------------------------------------------------------------------
def render_template(template: str, res: db.QueryResult, question: str, lang: str, sql: str = "") -> tuple[str, list[str]]:
    """Fill placeholders with real values. Returns (text, problems). Any problem rejects the text.
    A typed number may repeat the question, the published data range, or a year the query filters on."""
    problems: list[str] = []
    cols = {c: i for i, c in enumerate(res.columns)}

    def sub(m: re.Match) -> str:
        i, c = int(m.group(1)), m.group(2)
        if i >= len(res.rows) or c not in cols:
            problems.append(f"unknown placeholder {m.group(0)}")
            return ""
        return fmt_value(c, res.rows[i][cols[c]], lang)

    literal = _PLACEHOLDER.sub(" ", template)
    if not _PLACEHOLDER.search(template):
        problems.append("no placeholders used")
    if "{{" in literal or "}}" in literal:
        problems.append("a placeholder must be {{row.column}}; type a year from the question as plain text")
    if _TOTAL_AS_PART.search(template):
        problems.append("a *_all figure is the total including annulled deals, it is not part of another figure")
    for line in template.split("\n"):  # rows are walked in order; going back pairs one row's name with another row's value
        order = [int(i) for i in re.findall(r"\{\{\s*(\d+)\s*\.", line)]
        if order != sorted(order):
            problems.append(f"this line mixes rows {order}: every value about one item must come from that item's row")
    for m in _COUNT_AS_VALID.finditer(template):
        if re.search(r"deals|count|^n_", m.group(1), flags=re.I) and "valid" not in m.group(1).lower():
            problems.append(f"{m.group(1)} includes annulled deals, do not call it excluding them")
    q_low = question.lower()
    q_digits = set(_DIGITS.findall(question)) | _DATA_RANGE_NUMBERS | set(re.findall(r"\b20[12]\d\b", sql))
    for d in _DIGITS.findall(literal):
        if d not in q_digits:
            problems.append(f"typed number {d}")
    for name in _QUOTED.findall(literal):
        if name.lower() not in q_low:
            problems.append(f"typed name {name}")
    for m in _LEGAL.finditer(literal):
        if m.group(0).lower() not in q_low:
            problems.append(f"typed legal form {m.group(0)}")
    m = _TECHNICAL_IN_ANSWER.search(literal)
    if m:
        problems.append(f"technical word {m.group(0)}")
    m = _INVENTED_SOURCE.search(literal)
    if m and m.group(0).lower() not in q_low:
        problems.append(f"source not in the data: {m.group(0)}")
    return _PLACEHOLDER.sub(sub, template).strip(), problems


def reason_is_safe(text: str, question: str, known: str = "") -> bool:
    """A refusal text may only repeat numbers from the question or the published data range.
    `known` is resolver output, whose identifiers a filter note may also repeat."""
    source = f"{question} {known}"
    allowed = set(_DIGITS.findall(source)) | {"03.01.2022", "30.10.2026", "3", "30", "2022", "2026", "01", "10", "31.10.2026"}
    return (bool(text) and all(d in allowed for d in _DIGITS.findall(text))
            and all(m.group(0).lower() in source.lower() for m in _LEGAL.finditer(text))
            and not _TECHNICAL.search(text) and not _INVENTED_SOURCE.search(text))


def clean_note(text: str) -> str:
    """Remove technical fragments like (family_key = 'cement') or (total_uzs_valid) from a note."""
    text = re.sub(r"\s*\([^()]*(?:_|=)[^()]*\)", "", text)
    return "" if re.search(r"\w+_\w+|=", text) else text.strip()


def is_empty_result(res: db.QueryResult) -> bool:
    if not res.rows:
        return True
    if len(res.rows) > 1:
        return False
    vals = res.rows[0]
    if any(isinstance(v, str) for v in vals):
        return False
    nums = [v for v in vals if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool)]
    return bool(nums) and all(float(v) == 0 for v in nums) and all(v is None or v in nums for v in vals) or (not nums and all(v is None for v in vals))


def basis_info(res: db.QueryResult) -> dict[str, Any]:
    n = None
    period = None
    if len(res.rows) == 1:
        row = dict(zip(res.columns, res.rows[0]))
        for c, v in row.items():
            if _DEAL_COUNT.match(c) and isinstance(v, int):
                n = v
                break
        # only a range the query reports: a deal date next to its due date is not a period
        dates = sorted(v.date() if isinstance(v, dt.datetime) else v for c, v in row.items() if isinstance(v, dt.date) and _PERIOD_COL.search(c))
        if len(dates) >= 2:
            period = (dates[0], dates[-1])
    return {"n_deals": n, "period": period}


def basis_line(res: db.QueryResult, lang: str, sql: str = "") -> str:
    t = T.get(lang, T["uz"])
    b = basis_info(res)
    parts = []
    tables = [k for k in SOURCES if re.search(rf"\b(from|join)\s+{k}\b", sql, flags=re.I)]
    if tables:
        parts.append(", ".join(SOURCES[k].get(lang, SOURCES[k]["uz"]) for k in tables))
    if b["n_deals"] is not None:
        parts.append(f"{t['deals']}: {_num(float(b['n_deals']), lang, 0)}")
    elif len(res.rows) > 1:
        parts.append(f"{t['rows']}: {len(res.rows)}")
    if b["period"]:
        parts.append(f"{t['period']}: {b['period'][0]:%d.%m.%Y}–{b['period'][1]:%d.%m.%Y}")
    parts.append(t["asof"])
    return f"{t['basis']}: " + ", ".join(parts) + "."


def template_answer(lang: str, res: db.QueryResult) -> str:
    """Deterministic answer used when the model output is rejected or unavailable."""
    t = T.get(lang, T["uz"])
    shown = [(i, c) for i, c in enumerate(res.columns) if not (_PERIOD_COL.search(c) and any(isinstance(r[i], dt.date) for r in res.rows))]  # the basis line gives the period
    if len(res.rows) == 1:
        return f"{t['result']}: " + "; ".join(f"{label(c, lang)}: {fmt_value(c, res.rows[0][i], lang)}" for i, c in shown) + "."
    lines = [", ".join(f"{label(c, lang)}: {fmt_value(c, r[i], lang)}" for i, c in shown) for r in res.rows[:10]]
    return f"{t['result']}:\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(lines))


# ---------------------------------------------------------------------------
# cross-check of two independent plans
# ---------------------------------------------------------------------------
def fingerprint(res: db.QueryResult) -> list[float]:
    out = []
    for r in res.rows[:5]:
        for c, v in zip(res.columns, r):
            if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool) and not _IDENT.match(c) and abs(float(v)) > 1:
                out.append(float(v))
    return out


_ENTITY_COL = re.compile(r"name|inn|broker|region|product|family|status|code|deal_no|contract_no", re.I)


def _top_entities(res: db.QueryResult) -> set[str]:
    """What the first row is about: the company, region or product a ranking puts on top."""
    if not res.rows:
        return set()
    return {str(v).strip().lower() for c, v in zip(res.columns, res.rows[0]) if v is not None and _ENTITY_COL.search(c)}


def _same_number(x: float, y: float) -> bool:
    return abs(x - y) <= max(0.051, abs(y) * 1e-6)  # one query may round to 1-2 decimals; a count must match exactly


def results_agree(a: db.QueryResult, b: db.QueryResult) -> bool:
    """Agreement must mean the same answer. A false 'no' costs one extra model call, a false 'yes'
    shows a wrong figure with a tick next to it, so every figure of the shorter result has to match."""
    ea, eb = _top_entities(a), _top_entities(b)
    if ea and eb and not ea & eb:
        return False
    fa, fb = fingerprint(a), fingerprint(b)
    if not fa or not fb:
        ta = {str(v) for r in a.rows[:5] for v in r}
        tb = {str(v) for r in b.rows[:5] for v in r}
        return bool(ta & tb) or (not a.rows and not b.rows)
    small, big = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    hits = sum(any(_same_number(x, y) for y in big) for x in small)
    return hits == len(small) if len(small) < 10 else hits / len(small) >= 0.9


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def _run_with_timeout(sql: str) -> db.QueryResult:
    con = db.connection()
    check_tables(con, sql)
    cur = con.cursor()
    timer = threading.Timer(SQL_TIMEOUT_S, cur.interrupt)
    timer.start()
    t = time.perf_counter()
    try:
        r = cur.execute(sql)
        cols = [d[0] for d in r.description]
        rows = r.fetchmany(MAX_ROWS + 1)
    finally:
        timer.cancel()
        cur.close()
    return db.QueryResult(cols, rows[:MAX_ROWS], (time.perf_counter() - t) * 1000, len(rows) > MAX_ROWS)


def _plan_and_run(planner, question, lang, hints_text, history, label_: str, trace) -> dict[str, Any]:
    """Returns {'plan', 'res', 'sql', 'error'}. One retry on guard or SQL errors."""
    plan, sql, last_err = None, "", None
    for _ in range(2):
        try:
            plan = planner.plan(question, lang, hints_text, history, error=last_err)
        except Exception as e:  # noqa: BLE001
            trace["attempts"].append({"stage": label_, "error": repr(e)[:300]})
            return {"plan": plan, "res": None, "sql": sql, "error": "llm"}
        entry = {"stage": label_, "plan": {k: v for k, v in plan.items() if k != "_usage"}, "usage": plan.get("_usage")}
        trace["attempts"].append(entry)
        if plan.get("unanswerable") or (plan.get("clarify") and not plan.get("sql")):
            return {"plan": plan, "res": None, "sql": "", "error": None}
        try:
            sql = validate_sql(plan.get("sql", ""))
            return {"plan": plan, "res": _run_with_timeout(sql), "sql": sql, "error": None}
        except GuardError as e:
            last_err = f"guard: {e}"
        except Exception as e:  # noqa: BLE001
            last_err = f"duckdb: {str(e)[:400]}"
        entry["error"] = last_err
    return {"plan": plan, "res": None, "sql": sql, "error": "sql"}


# Every question uses up to two pool threads, and timed-out calls hold theirs until they return.
_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="agent")
_NO_RUN: dict[str, Any] = {"plan": None, "res": None, "sql": "", "error": "llm"}


def _refused(run: dict[str, Any]) -> bool:
    plan = run["plan"]
    return bool(run["error"] is None and plan and (plan.get("unanswerable") or (plan.get("clarify") and not plan.get("sql"))))


def _wait(future, timeout: float, trace: dict[str, Any], what: str) -> dict[str, Any]:
    try:
        return future.result(timeout=max(timeout, 0.1))
    except FutureTimeout:
        trace[f"{what}_error"] = f"no result within {timeout:.0f} s"
    except Exception as e:  # noqa: BLE001
        trace[f"{what}_error"] = repr(e)[:200]
    return _NO_RUN


def _reconcile(planner, question, lang, main, other, trace, deadline) -> tuple[dict[str, Any], str]:
    """Pick the run to answer from. A refusal is checked like any other answer: a wrong 'no data' costs as
    much as a wrong figure, so when one model answers and the other refuses, the primary model decides."""
    has_main, has_other = main["res"] is not None, other["res"] is not None
    if has_main and has_other:
        if results_agree(main["res"], other["res"]):
            return main, "agreed"
        return _adjudicate(planner, question, lang, main, other, trace, deadline), "adjudicated"
    if has_main:
        if _refused(other) and not is_empty_result(main["res"]):
            return _adjudicate(planner, question, lang, main, other, trace, deadline), "adjudicated"
        return main, "single"
    if has_other:
        if _refused(main) and is_empty_result(other["res"]):
            return main, "single"
        if _refused(main):
            return _adjudicate(planner, question, lang, main, other, trace, deadline), "adjudicated"
        return other, "single"  # the primary model or its SQL failed, the second model has a result
    return main, "single"


def answer_question(question: str, history: list[dict[str, str]], planner, lang_override: str | None = None, verifier=None) -> dict[str, Any]:
    t0 = time.perf_counter()
    deadline = t0 + TOTAL_BUDGET_S
    left = lambda: deadline - time.perf_counter()  # noqa: E731
    lang = lang_override or detect_language(question)
    t = T.get(lang, T["uz"])
    hints_text = hints_to_text(hints_for(question))
    trace: dict[str, Any] = {"hints": hints_text, "attempts": []}

    verify_future = _pool.submit(_plan_and_run, verifier, question, lang, hints_text, history, "verify", trace) if verifier else None
    main_future = _pool.submit(_plan_and_run, planner, question, lang, hints_text, history, "plan", trace)
    main = _wait(main_future, min(PLAN_WAIT_S, left()), trace, "plan")
    other = _wait(verify_future, min(VERIFY_WAIT_S, left()), trace, "verify") if verify_future else None
    chosen, check = _reconcile(planner, question, lang, main, other, trace, deadline) if other else (main, "single")
    plan = chosen["plan"]

    if chosen["error"] == "llm":
        return _finish(lang, question, t0, trace, t["err"], "llm_error")
    if plan and plan.get("unanswerable"):
        reason = plan.get("reason") or ""
        return _finish(lang, question, t0, trace, reason if reason_is_safe(reason, question) else t["unans"], "unanswerable", plan, check=check)
    if plan and plan.get("clarify") and not plan.get("sql"):
        clar = plan["clarify"]
        return _finish(lang, question, t0, trace, clar if reason_is_safe(clar, question) else t["unans"], "clarify", plan, check=check)
    if chosen["res"] is None:
        return _finish(lang, question, t0, trace, t["err"], "sql_error", plan, chosen["sql"])
    res, sql = chosen["res"], chosen["sql"]

    if is_empty_result(res):
        answer = t["no_rows"] if re.search(r"\bfrom\s+deals\b", sql, flags=re.I) else NO_RECORDS.get(lang, NO_RECORDS["uz"])
        filters = [f for f in map(clean_note, plan.get("filters_human") or []) if f and reason_is_safe(f, question, hints_text)]
        if filters:
            answer += f" {t['filters']}: " + "; ".join(filters) + "."
        return _finish(lang, question, t0, trace, answer, "no_rows", plan, sql, res, check)

    status, answer = "ok", ""
    feedback = ""
    for attempt in range(3):
        if left() < 2:  # the direct answer below needs no model
            trace["compose_error"] = "out of time"
            break
        try:
            tpl = _pool.submit(planner.compose, question, lang, sql, plan.get("assumptions", []), rows_for_llm(res, lang), len(res.rows), extra=feedback).result(timeout=min(STEP_WAIT_S, left()))
        except Exception as e:  # noqa: BLE001
            trace["compose_error"] = repr(e)[:300]
            break
        rendered, problems = render_template(tpl, res, question, lang, sql)
        trace.setdefault("compose", []).append({"template": tpl, "problems": problems})
        if not problems:
            answer = polish(rendered, lang)
            break
        feedback = (f"\nIMPORTANT: the previous draft was rejected: {problems}. Use {{{{row.column}}}} placeholders only for these columns: {res.columns}. "
                    "A word the user wrote, such as a product or brand name, is typed as plain text.")
    if not answer:
        answer, status = template_answer(lang, res), "template"
    return _finish(lang, question, t0, trace, f"{answer}\n\n{basis_line(res, lang, sql)}", status, plan, sql, res, check)


def _option(run: dict[str, Any], lang: str) -> dict[str, str]:
    if run["res"] is None:
        return {"sql": "(no query: this option refuses to answer)", "rows": str((run["plan"] or {}).get("reason") or (run["plan"] or {}).get("clarify") or "")}
    return {"sql": run["sql"], "rows": rows_for_llm(run["res"], lang)}


def _adjudicate(planner, question, lang, main, other, trace, deadline):
    """The two models disagree: the primary model reads both outcomes and picks one."""
    try:
        wait = min(STEP_WAIT_S, deadline - time.perf_counter())
        choice = _pool.submit(planner.adjudicate, question, _option(main, lang), _option(other, lang)).result(timeout=max(wait, 0.1))
    except Exception as e:  # noqa: BLE001
        trace["adjudicate_error"] = repr(e)[:200]
        choice = "A"
    trace["adjudication"] = {"choice": choice, "a": main["sql"], "b": other["sql"]}
    return other if choice == "B" else main


def _finish(lang, question, t0, trace, answer, status, plan=None, sql="", res=None, check="none") -> dict[str, Any]:
    meta = db.meta()
    b = basis_info(res) if res else {"n_deals": None, "period": None}
    return {
        "question": question, "language": lang, "status": status, "answer": answer, "check": check,
        "sql": sql,
        "assumptions": [x for x in map(clean_note, (plan or {}).get("assumptions", [])) if x],
        "filters": [x for x in map(clean_note, (plan or {}).get("filters_human", [])) if x],
        "chart": (plan or {}).get("chart", {"type": "none", "x": "", "y": ""}),
        "columns": res.columns if res else [],
        "column_labels": [label(c, lang) for c in res.columns] if res else [],
        "rows": [{c: _jsonable(v) for c, v in zip(res.columns, r)} for r in res.rows] if res else [],
        "rows_fmt": [[fmt_value(c, v, lang) for c, v in zip(res.columns, r)] for r in res.rows] if res else [],
        "n_rows": len(res.rows) if res else 0, "truncated": res.truncated if res else False,
        "basis": {"as_of": meta.get("data_asof"), "data_range": [meta.get("min_deal_date"), meta.get("max_deal_date")],
                  "n_deals": b["n_deals"], "period": [b["period"][0].isoformat(), b["period"][1].isoformat()] if b["period"] else None,
                  "sql_ms": round(res.elapsed_ms, 1) if res else None},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "trace": trace,
    }
