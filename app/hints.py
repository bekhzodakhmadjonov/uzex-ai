"""Turn a user question into resolver hints for the SQL model.

We do not try to parse the question; we only find candidate entities so the LLM
uses exact keys (family_key, region_id, status_code, inn, broker code) instead of
guessing spellings. Ambiguities are passed through so the model can state them.
"""
from __future__ import annotations

import re
from functools import lru_cache
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
    "назови", "назовите", "покажи", "покажите", "перечисли", "перечислите", "выведи", "найди", "найдите", "дай", "дайте", "укажи", "укажите", "посчитай", "определи",
    "ayting", "ayt", "aytib", "bering", "ber", "korsat", "ko‘rsat", "ko‘rsating", "sanab", "toping", "hisobla", "hisoblang", "айтинг", "кўрсатинг", "беринг",
    # en
    "the", "a", "an", "of", "in", "for", "and", "by", "how", "many", "much", "what", "which", "who", "total", "average", "price",
    # uz cyrillic ('битим' otherwise matches 'битум' as a typo)
    "битим", "битимлар", "битимлари", "битимни", "битимда", "битимлардан", "нечта", "неча", "қанча", "канча", "йил", "йилда", "йили", "йилги",
    "нарх", "нархи", "сумма", "суммаси", "ҳажм", "ҳажми", "ўртача", "уртача", "жами", "умумий", "энг", "кўп", "кам", "бўйича", "буйича",
    "сотилган", "сотиб", "олган", "тузилган", "қайси", "кайси", "ким", "нима", "қачон", "учун", "билан", "ва", "ойда", "ой",
}
# Words about places, never places themselves
_GENERIC_PLACE = {
    "viloyat", "viloyati", "viloyatlar", "вилоят", "вилояти", "вилоятлар", "область", "области", "областях", "обл", "регион", "регионе", "регионы", "региона", "регионов",
    "region", "regions", "hudud", "hududi", "hududlar", "ҳудуд", "ҳудуди", "худуд", "shahar", "shahri", "шаҳар", "шаҳри", "город", "городе", "города", "city",
}
# Words covering several families. They apply only when the question does not name one of them:
# 'масло' is both oils, 'подсолнечное масло' is one.
_GROUPS = (
    (re.compile(r"\b(?:масл\w*|yog|yogi|yogl\w*|ёғ\w*|oils?)\b"), ("cottonseed_oil", "sunflower_oil")),
    (re.compile(r"\b(?:ogit\w*|ўғит\w*|удобрен\w*|fertili[sz]er\w*)\b"), ("urea", "ammonium_nitrate")),
    (re.compile(r"\b(?:yoqilgi\w*|ёқилғи\w*|топлив\w*|fuels?)\b"), ("petrol", "diesel", "lpg", "fuel_oil")),
)
# (negated phrase, status codes it must not resolve to, note for the model)
_NEGATIONS = (
    (re.compile(r"bekor\s+qilinmagan|бекор\s+қилинмаган|не\s*аннулирован|неаннулирован|not\s+annulled|non-annulled", re.I), ("annulled",), "means NOT is_annulled"),
    (re.compile(r"bajarilmagan|бажарилмаган|не\s*исполнен|неисполнен|not\s+executed", re.I), ("executed", "partial"), "means NOT is_executed"),
    (re.compile(r"to.?lanmagan|тўланмаган|не\s*оплачен|неоплачен|unpaid|not\s+paid", re.I), ("annulled", "paid"), "means NOT is_paid: annulled deals plus deals not yet due, say which you count"),
)
_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9, "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "январь": 1, "января": 1, "январе": 1, "февраль": 2, "февраля": 2, "феврале": 2, "март": 3, "марта": 3, "марте": 3, "апрель": 4, "апреля": 4, "апреле": 4,
    "май": 5, "мая": 5, "мае": 5, "июнь": 6, "июня": 6, "июне": 6, "июль": 7, "июля": 7, "июле": 7, "август": 8, "августа": 8, "августе": 8,
    "сентябрь": 9, "сентября": 9, "сентябре": 9, "октябрь": 10, "октября": 10, "октябре": 10, "ноябрь": 11, "ноября": 11, "ноябре": 11, "декабрь": 12, "декабря": 12, "декабре": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


# Uzbek Cyrillic is often typed without ў қ ғ ҳ ('Пахта нархи канча?'), so words decide too.
_UZ_CYR_WORDS = re.compile(r"\b(?:нарх\w*|канча|қанча|нечта|неча|йил\w*|битим\w*|сотил\w*|сотиб|тузил\w*|ойда|ойи\w*|учун|билан|ва|бўйича|буйича|ўртача|уртача|жами|энг|сўм\w*|сумда|пахта\w*|шакар\w*|буғдой\w*|бугдой\w*|ҳудуд\w*|худуд\w*|вилоят\w*|тошкент\w*|самарқанд\w*|бухоро\w*|қайси|кайси|ким)\b")
_EN_WORDS = re.compile(r"\b(?:how|what|which|who|many|much|total|average|price|the|in|of|deals?|buyers?|sellers?|brokers?|largest|biggest|most|sold|bought|number|show|give)\b")
_UZ_LAT_WORDS = re.compile(r"\b(?:qancha|nechta|yil\w*|narx\w*|bitim\w*|so‘m|so'm|bo‘yicha|boyicha|eng|sotil\w*|kim|qaysi|xaridor\w*|sotuvchi\w*)\b")


def detect_language(text: str) -> str:
    """'uz' (Latin), 'uz_cyr', 'ru' or 'en'."""
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if cyr > lat:
        if re.search(r"[ўқғҳЎҚҒҲ]", text) or _UZ_CYR_WORDS.search(text.lower()):
            return "uz_cyr"
        return "ru"
    low = text.lower()
    if _EN_WORDS.search(low) and not _UZ_LAT_WORDS.search(low):
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


_LOOKALIKE = str.maketrans({"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "I": "И"})


def _fold_brand(s: str) -> str:
    """Upper case with Latin look-alikes as Cyrillic, so AI-92 and АИ-92 compare equal."""
    return s.upper().translate(_LOOKALIKE)


@lru_cache(maxsize=1)
def _brand_patterns() -> list[tuple[str, re.Pattern, str]]:
    """One tolerant pattern per (brand, product): 'ai92', 'AI-92' and 'АИ 92' all match АИ-92."""
    rows = R.run_sql("SELECT DISTINCT brand, product_key FROM deals WHERE brand IS NOT NULL", max_rows=1000).rows
    out = []
    for brand, pk in rows:
        if not (re.search(r"\d", brand) or (brand.isupper() and len(brand) <= 4)):
            continue  # descriptive words such as 'высший' are not brand codes
        chars = [c for c in _fold_brand(brand) if not c.isspace() and c not in "-_"]
        body = r"[\s\-_]*".join(re.escape(c) for c in chars)
        out.append((brand, re.compile(rf"(?<![\w]){body}(?![\w])"), pk))
    return out


def _not_a_company_phrase(phrase: str, question: str) -> bool:
    """A capitalised phrase that should not be searched among company names."""
    words = phrase.split()
    if any(ch.isdigit() for ch in phrase) or (phrase.isupper() and len(phrase) <= 4):
        return True
    if R.resolve_family(phrase, limit=1) and R.resolve_family(phrase, limit=1)[0].score == 1.0:
        return True
    return len(words) == 1 and (len(phrase) < 4 or question.startswith(phrase))  # a sentence-initial capital says nothing


def hints_for(question: str) -> dict[str, Any]:
    q = question.strip()
    words = [w for w in _WORD.findall(q)]
    lower_words = [w.lower() for w in words]
    out: dict[str, Any] = {"families": [], "products": [], "brands": [], "regions": [], "statuses": [], "companies": [], "brokers": [],
                           "inns": [], "years": [], "months": [], "ambiguous": [], "groups": [], "notes": []}

    # numbers: INN (9 digits), years, broker codes
    for m in re.finditer(r"\b\d{9}\b", q):
        out["inns"].append(m.group())
    out["years"] = sorted({int(y) for y in re.findall(r"\b(20[12]\d)\b", q)})
    for w in lower_words:
        if w in _MONTHS:
            out["months"].append(_MONTHS[w])
    for m in re.finditer(r"\bBK\s?\d{1,3}\b", q, flags=re.I):
        out["brokers"] += [x.as_dict() for x in R.resolve_broker(m.group().replace(" ", ""))]

    seen_fam, seen_reg, seen_st, seen_prod = set(), set(), set(), set()
    raw_content = [w for w in lower_words if w not in _STOP and not w.isdigit() and len(w) >= 2]
    content = [_strip_uz_suffix(w) for w in raw_content]
    # the words as typed and the words without suffixes, longest phrases first
    grams = sorted(dict.fromkeys([*_ngrams(raw_content, 3), *_ngrams(content, 3)]), key=lambda g: -len(g.split()))
    named_as_family: set[str] = set()  # words of an exact multi-word name: 'paxta yog‘i' is not also 'paxta'
    for g in grams:
        words = g.split()
        if len(g) < 3:  # two letters are too short for typo matching, but 'un' and 'ip' are real aliases
            for mt in R.resolve_family(g, limit=1):
                if mt.score == 1.0 and mt.key not in seen_fam and g not in named_as_family:
                    seen_fam.add(mt.key); out["families"].append(mt.as_dict() | {"text": g})
            continue
        if not named_as_family & set(words):
            found = R.resolve_family(g, limit=2)
            exact_only = bool(found) and found[0].score == 1.0  # an exact name leaves no room for similar ones
            for mt in found:
                if mt.score >= (1.0 if exact_only else 0.9) and mt.key not in seen_fam:
                    seen_fam.add(mt.key); out["families"].append(mt.as_dict() | {"text": g})
                if mt.score == 1.0 and len(words) > 1:
                    named_as_family |= set(words)
        if not all(w in _GENERIC_PLACE for w in words):
            found = R.resolve_region(g, limit=2)
            exact_only = bool(found) and found[0].score >= 0.99
            for mt in found:
                if mt.score >= (0.99 if exact_only else 0.9) and mt.key not in seen_reg:
                    seen_reg.add(mt.key); out["regions"].append(mt.as_dict() | {"text": g})
        for mt in R.resolve_status(g, limit=1):
            if mt.score >= 0.95 and mt.key not in seen_st:
                seen_st.add(mt.key); out["statuses"].append(mt.as_dict() | {"text": g})
        for mt in R.resolve_product(g, limit=2):
            if mt.score >= 0.95 and mt.key not in seen_prod:
                seen_prod.add(mt.key); out["products"].append(mt.as_dict() | {"text": g})
    # brands like АИ-92, М-125, ЦЕМ I 42,5 Н, HD-6018, in any script and with any separator
    folded_q = _fold_brand(q)
    for brand, rx, pk in _brand_patterns():
        m = rx.search(folded_q)
        if m and (not brand.isdigit() or re.search(r"podshipnik|подшипник|bearing", q, flags=re.I)):  # a bare 6210 is usually just a number
            out["brands"].append({"key": brand, "product_key": pk, "text": m.group()})
    # families named by a word that covers several of them
    plain_q = R.strip_apostrophe(q.lower())
    for rx, members in _GROUPS:
        word = rx.search(plain_q)
        if not word:
            continue
        specific = [f for f in out["families"] if f["key"] in members and not all(rx.fullmatch(R.strip_apostrophe(w)) for w in f["text"].split())]
        if not specific:
            out["families"] = [f for f in out["families"] if f["key"] not in members]
            out["groups"].append({"text": word.group(), "members": members})
    # company names: the whole name in any script and letter case, which needs no capital letters
    for g in dict.fromkeys(g for g in [*_ngrams(raw_content, 3), *_ngrams(content, 3)] if len(g) >= 4):
        found = R.resolve_company_exact(g)
        if found and not any(c["candidates"][0]["inn"] == found[0].key for c in out["companies"]):
            out["companies"].append({"text": g, "candidates": [x.as_dict() for x in found], "confident": len(found) == 1})
    for g in dict.fromkeys(_ngrams(lower_words, 3)):
        if re.search(r"broker|брокер", g) and len(g.split()) > 1:
            for mt in R.resolve_broker(g, limit=1):
                if mt.score >= 0.9:
                    out["brokers"].append(mt.as_dict() | {"text": g})
    # otherwise quoted strings or capitalised 1-3 word phrases, which may be typos. A capital alone
    # means little: it starts every sentence, and 'Sement' must not become a company called '... Sement'.
    quoted = set(re.findall(r"[\"«“]([^\"»”]{3,60})[\"»”]", q))
    caps = re.findall(r"(?:(?<=\s)|^)([A-ZА-ЯЎҚҒҲ][\w‘’'ʻ\-]+(?:\s+[A-ZА-ЯЎҚҒҲ][\w‘’'ʻ\-]+){0,2})", q)
    named = {w for c in out["companies"] for w in c["text"].split()}
    for c in quoted | {c for c in caps if c.lower() not in _STOP and c.lower() not in _MONTHS}:
        if R.resolve_region(c) and R.resolve_region(c)[0].score >= 0.95:
            continue
        if len(c) >= 4:
            bm = R.resolve_broker(c, limit=2)
            if bm and bm[0].score >= 0.9:
                out["brokers"].append(bm[0].as_dict() | {"text": c})
        if c not in quoted and (_not_a_company_phrase(c, q) or set(c.lower().split()) <= named):
            continue
        cm = R.resolve_company(c, limit=3)
        if cm and cm[0].score >= 0.85:
            out["companies"].append({"text": c, "candidates": [x.as_dict() for x in cm], "confident": R.is_confident(cm)})
    if re.search(r"bekor qilinganlarsiz|бекор қилинганларсиз|без аннулирован|кроме аннулирован", q, flags=re.I):
        out["statuses"] = [x for x in out["statuses"] if x["key"] != "annulled"]  # "without annulled" is the opposite of the status
    for rx, codes, meaning in _NEGATIONS:
        phrase = rx.search(q)
        if phrase:  # a status word under a negation means the opposite of that status
            out["statuses"] = [x for x in out["statuses"] if x["key"] not in codes]
            out["notes"].append(f"'{phrase.group()}' {meaning}")
    regions = {r["key"] for r in out["regions"]}
    if {"TAS", "TAO"} <= regions:
        low = q.lower()
        city = re.search(r"shahar|shahri|шаҳар|шаҳри|город|г\.\s*ташкент|\bcity\b", low)
        province = re.search(r"viloyat|вилоят|област|oblast|province", low)
        if bool(city) != bool(province):  # the question says which Tashkent it means
            drop = "TAO" if city else "TAS"
            out["regions"] = [r for r in out["regions"] if r["key"] != drop]
        elif not city:
            out["ambiguous"].append("'Toshkent' may mean Toshkent shahri (TAS) or Toshkent viloyati (TAO) — show both unless the question says which.")
    for c in out["companies"]:
        # only equally exact names are a real choice; a set of near matches is usually no match
        if not c["confident"] and len(c["candidates"]) > 1 and c["candidates"][0]["score"] >= R.EXACT_NAME:
            out["ambiguous"].append(f"Company '{c['text']}' matches several participants: " + "; ".join(f"{x['label']} (inn {x['inn']}, {x['region']})" for x in c["candidates"]))
    out["brokers"] = list({b["key"]: b for b in reversed(out["brokers"])}.values())
    out["language"] = detect_language(q)
    return out


def hints_to_text(h: dict[str, Any]) -> str:
    lines = []
    if h["families"]:
        lines.append("families: " + "; ".join(f"'{x['text']}' → family_key='{x['key']}' ({x['label']})" for x in h["families"]))
    for g in h["groups"]:
        members = ", ".join(f"'{m}'" for m in g["members"])
        lines.append(f"group word: '{g['text']}' covers several families → family_key IN ({members}); return one row per family plus the total, and say this in assumptions")
    if h["products"]:
        lines.append("products: " + "; ".join(f"'{x['text']}' → product_key='{x['key']}' ({x['label']})" for x in h["products"]))
    if h["brands"]:
        lines.append("brands: " + "; ".join(f"'{x['text']}' → brand='{x['key']}' (product_key='{x['product_key']}')" for x in h["brands"]))
    if h["regions"]:
        lines.append("regions: " + "; ".join(f"'{x['text']}' → region_id='{x['key']}' ({x['label_uz']})" for x in h["regions"]))
    if h["statuses"]:
        lines.append("statuses: " + "; ".join(f"'{x['text']}' → status_code='{x['key']}'" for x in h["statuses"]))
    if h["inns"]:
        parts = []
        for inn in h["inns"]:
            m = R.resolve_company(inn)
            parts.append(f"{inn} ({m[0].label}, role={m[0].extra.get('role')}, region={m[0].extra.get('region')})" if m
                         else f"{inn} (NOT a registered participant: the registry has no company with this inn)")
        lines.append("inn mentioned: " + "; ".join(parts))
    if h["companies"]:
        for c in h["companies"]:
            kind = "company" if c["candidates"][0]["score"] >= R.EXACT_NAME else "NOT an exact match, only similar names, do not answer for them"
            lines.append(f"{kind} '{c['text']}': " + "; ".join(f"{x['label']} inn={x['inn']} role={x['role']} region={x['region']} (score {x['score']})" for x in c["candidates"]))
    if h["brokers"]:
        lines.append("brokers: " + "; ".join(f"'{x.get('text', x['key'])}' → code='{x['key']}' ({x['label']})" for x in h["brokers"]))
    if h["years"]:
        lines.append("years mentioned: " + ", ".join(map(str, h["years"])))
    if h["months"]:
        lines.append("months mentioned: " + ", ".join(map(str, h["months"])))
    for n in h["notes"]:
        lines.append("negation: " + n)
    if h["ambiguous"]:
        lines.append("AMBIGUITY: " + " | ".join(h["ambiguous"]))
    return "\n".join(lines) if lines else "(no entities resolved — rely on the schema and ILIKE on product_raw / names)"
