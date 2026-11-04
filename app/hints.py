"""Turn a user question into resolver hints for the SQL model.

We do not try to parse the question; we only find candidate entities so the LLM
uses exact keys (family_key, region_id, status_code, inn, broker code) instead of
guessing spellings. Ambiguities are passed through so the model can state them.
"""
from __future__ import annotations

import re
from typing import Any

from . import resolve as R

_WORD = re.compile(r"[\w‘’'ʻ\-]+", flags=re.U)
_STOP = {
    # uz
    "va", "bilan", "uchun", "yilda", "yili", "yil", "oyda", "oy", "qancha", "nechta", "necha", "eng", "ko‘p", "kop", "kam",
    "bo‘yicha", "boyicha", "bo'yicha", "jami", "umumiy", "o‘rtacha", "ortacha", "narx", "narxi", "summa", "summasi", "hajm", "hajmi",
    "bitim", "bitimlar", "bitimlari", "sotilgan", "sotildi", "sotib", "olgan", "olindi", "qaysi", "kim", "nima", "qachon", "da", "ga", "ni", "ning",
    # ru
    "и", "в", "на", "по", "за", "для", "с", "со", "от", "до", "из", "какой", "какая", "какие", "сколько", "самый", "самая", "самые",
    "год", "году", "года", "месяц", "месяце", "сделок", "сделки", "сделка", "сумма", "объем", "объём", "цена", "средняя", "средний",
    "продано", "куплено", "всего", "общая", "общий", "топ", "кто", "что", "когда", "где",
    # en
    "the", "a", "an", "of", "in", "for", "and", "by", "how", "many", "much", "what", "which", "who", "total", "average", "price",
}
_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9, "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "январь": 1, "января": 1, "январе": 1, "февраль": 2, "февраля": 2, "феврале": 2, "март": 3, "марта": 3, "марте": 3, "апрель": 4, "апреля": 4, "апреле": 4,
    "май": 5, "мая": 5, "мае": 5, "июнь": 6, "июня": 6, "июне": 6, "июль": 7, "июля": 7, "июле": 7, "август": 8, "августа": 8, "августе": 8,
    "сентябрь": 9, "сентября": 9, "сентябре": 9, "октябрь": 10, "октября": 10, "октябре": 10, "ноябрь": 11, "ноября": 11, "ноябре": 11, "декабрь": 12, "декабря": 12, "декабре": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def detect_language(text: str) -> str:
    """'uz' (Latin), 'uz_cyr', 'ru' or 'en'."""
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if cyr > lat:
        if re.search(r"[ўқғҳЎҚҒҲ]", text) or re.search(r"\b(қанча|нечта|йил|нарх|сўм|бўйича|энг|ойда)\b", text.lower()):
            return "uz_cyr"
        return "ru"
    low = text.lower()
    if re.search(r"\b(how|what|which|many|much|total|average|price|the|in|of|deals?)\b", low) and not re.search(r"\b(qancha|nechta|yil|narx|bitim|so‘m|so'm|bo‘yicha|eng)\b", low):
        return "en"
    return "uz"


_UZ_SUFFIXES = ("dagi", "ning", "lari", "larni", "lardan", "larda", "larga", "dan", "da", "ga", "ni", "lar", "ning", "ими", "ами", "ах", "ов", "ев", "ам", "ом", "ой", "ую", "ые", "ых", "ая", "ое", "ом", "ем", "ях", "ях")


def _strip_uz_suffix(w: str) -> str:
    """'toshkentda' -> 'toshkent', 'sementni' -> 'sement', 'хлопка' -> 'хлопка' (ru handled by aliases)."""
    for s in sorted(_UZ_SUFFIXES, key=len, reverse=True):
        if w.endswith(s) and len(w) - len(s) >= 4:
            return w[: -len(s)]
    return w


def _ngrams(words: list[str], n_max: int = 3):
    for n in range(n_max, 0, -1):
        for i in range(len(words) - n + 1):
            yield " ".join(words[i:i + n])


def hints_for(question: str) -> dict[str, Any]:
    q = question.strip()
    words = [w for w in _WORD.findall(q)]
    lower_words = [w.lower() for w in words]
    out: dict[str, Any] = {"families": [], "products": [], "brands": [], "regions": [], "statuses": [], "companies": [], "brokers": [],
                           "inns": [], "years": [], "months": [], "ambiguous": []}

    # numbers: INN (9 digits), years, broker codes
    for m in re.finditer(r"\b\d{9}\b", q):
        out["inns"].append(m.group())
    out["years"] = sorted({int(y) for y in re.findall(r"\b(20[12]\d)\b", q)})
    for w in lower_words:
        if w in _MONTHS:
            out["months"].append(_MONTHS[w])
    for m in re.finditer(r"\bBK\s?\d{1,3}\b", q, flags=re.I):
        out["brokers"] += [x.as_dict() for x in R.resolve_broker(m.group())]

    seen_fam, seen_reg, seen_st, seen_prod = set(), set(), set(), set()
    content = [_strip_uz_suffix(w) for w in lower_words if w not in _STOP and not w.isdigit() and len(w) >= 2]
    for g in _ngrams(content, 3):
        if len(g) < 3:
            continue
        for mt in R.resolve_family(g, limit=2):
            if mt.score >= 0.9 and mt.key not in seen_fam:
                seen_fam.add(mt.key); out["families"].append(mt.as_dict() | {"text": g})
        for mt in R.resolve_region(g, limit=2):
            if mt.score >= 0.9 and mt.key not in seen_reg:
                seen_reg.add(mt.key); out["regions"].append(mt.as_dict() | {"text": g})
        for mt in R.resolve_status(g, limit=1):
            if mt.score >= 0.95 and mt.key not in seen_st:
                seen_st.add(mt.key); out["statuses"].append(mt.as_dict() | {"text": g})
        for mt in R.resolve_product(g, limit=2):
            if mt.score >= 0.95 and mt.key not in seen_prod:
                seen_prod.add(mt.key); out["products"].append(mt.as_dict() | {"text": g})
    # brands like АИ-92, М-125, ЦЕМ I 42,5 Н, HD-6018
    for m in re.finditer(r"\b(аи|ai|м|m|hd|ll|pe|ld|pp|sg|ne|бнд|вр)[\s\-]?\d[\w,/\.]*\b", q, flags=re.I):
        for mt in R.resolve_brand(m.group(), limit=2):
            if mt.score >= 0.9:
                out["brands"].append(mt.as_dict() | {"text": m.group()})
    # company / broker names: quoted strings or capitalised 1-3 word phrases
    cands = set(re.findall(r"[\"«“]([^\"»”]{3,60})[\"»”]", q))
    caps = re.findall(r"(?:(?<=\s)|^)([A-ZА-ЯЎҚҒҲ][\w‘’'ʻ\-]+(?:\s+[A-ZА-ЯЎҚҒҲ][\w‘’'ʻ\-]+){0,2})", q)
    cands |= {c for c in caps if c.lower() not in _STOP and c.lower() not in _MONTHS}
    for c in cands:
        if R.resolve_region(c) and R.resolve_region(c)[0].score >= 0.95:
            continue
        cm = R.resolve_company(c, limit=3)
        if cm and cm[0].score >= 0.85:
            out["companies"].append({"text": c, "candidates": [x.as_dict() for x in cm], "confident": R.is_confident(cm)})
        bm = R.resolve_broker(c, limit=2)
        if bm and bm[0].score >= 0.9:
            out["brokers"].append(bm[0].as_dict() | {"text": c})
    for mt in out["regions"]:
        if mt.get("via", "").startswith("ambiguous"):
            out["ambiguous"].append("'Toshkent' may mean Toshkent shahri (TAS) or Toshkent viloyati (TAO) — show both unless the question says which.")
    for c in out["companies"]:
        if not c["confident"] and len(c["candidates"]) > 1:
            out["ambiguous"].append(f"Company '{c['text']}' matches several participants: " + "; ".join(f"{x['label']} (inn {x['inn']}, {x['region']})" for x in c["candidates"]))
    out["language"] = detect_language(q)
    return out


def hints_to_text(h: dict[str, Any]) -> str:
    lines = []
    if h["families"]:
        lines.append("families: " + "; ".join(f"'{x['text']}' → family_key='{x['key']}' ({x['label']})" for x in h["families"]))
    if h["products"]:
        lines.append("products: " + "; ".join(f"'{x['text']}' → product_key='{x['key']}' ({x['label']})" for x in h["products"]))
    if h["brands"]:
        lines.append("brands: " + "; ".join(f"'{x['text']}' → brand='{x['key']}' (product_key='{x['product_key']}')" for x in h["brands"]))
    if h["regions"]:
        lines.append("regions: " + "; ".join(f"'{x['text']}' → region_id='{x['key']}' ({x['label_uz']})" for x in h["regions"]))
    if h["statuses"]:
        lines.append("statuses: " + "; ".join(f"'{x['text']}' → status_code='{x['key']}'" for x in h["statuses"]))
    if h["inns"]:
        lines.append("inn mentioned: " + ", ".join(h["inns"]))
    if h["companies"]:
        for c in h["companies"]:
            lines.append(f"company '{c['text']}': " + "; ".join(f"{x['label']} inn={x['inn']} role={x['role']} region={x['region']} (score {x['score']})" for x in c["candidates"]))
    if h["brokers"]:
        lines.append("brokers: " + "; ".join(f"'{x['text']}' → code='{x['key']}' ({x['label']})" for x in h["brokers"]))
    if h["years"]:
        lines.append("years mentioned: " + ", ".join(map(str, h["years"])))
    if h["months"]:
        lines.append("months mentioned: " + ", ".join(map(str, h["months"])))
    if h["ambiguous"]:
        lines.append("AMBIGUITY: " + " | ".join(h["ambiguous"]))
    return "\n".join(lines) if lines else "(no entities resolved — rely on the schema and ILIKE on product_raw / names)"
