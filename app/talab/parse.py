"""Text or a construction estimate (smeta) -> structured talabnoma(s).

Deterministic on purpose: no LLM is needed, so it costs nothing, cannot be prompt-injected and
behaves the same on stage. Anything the parser is not sure about becomes a clarifying question
instead of a guess.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any

from .common import families, norm, regions, today, unit_short

MONTHS = [("yanv", 1), ("fev", 2), ("mart", 3), ("apr", 4), ("may", 5), ("maj", 5), ("iyun", 6), ("iyul", 7),
          ("avg", 8), ("sen", 9), ("okt", 10), ("noy", 11), ("dek", 12)]
NUM = r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)"
MULT = r"(ming|mln|million|milliard|mlrd|t[iy]s(?:yach\w*)?)"
UNIT = r"(t|tn|tonna\w*|tonn\w*|ton|kg|kilo\w*|dona\w*|ta|sht\w*|m3|kub\w*|litr\w*|l)"
QTY_RE = re.compile(NUM + r"\s*" + MULT + r"?\s*" + UNIT + r"(?![a-z'])")
MONEY_RE = re.compile(NUM + r"\s*" + MULT + r"?\s*(so'm\w*|som\w*|sum\w*|sym)?")
GRADE_RE = re.compile(r"(?<![a-z])(?:m|pts|pc)\s?-?(\d{3})(?!\d)")
SPLIT_RE = re.compile(r"[\n;]+|(?<!\d),|,(?!\d)|\.(?=\s)|\s(?:va|hamda|i|plyus)\s|\s\+\s")
PRICE_HINT = re.compile(r"oshmasin|oshmasa|gacha|qimmat emas|ko'pi bilan|max|maks|do |ne dorozh|ne bolee|narx|tonnasi|donasi|"
                        r"har bir|za tonn|za sht|budjet|byudjet|jami|pul")
MULTV = {"ming": 1e3, "tys": 1e3, "tis": 1e3, "mln": 1e6, "million": 1e6, "milliard": 1e9, "mlrd": 1e9}


def _num(s: str) -> float:
    s = s.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?: \d{3})+", s):
        return float(s.replace(" ", ""))
    return float(s.replace(",", "."))


def _mult(m: str | None) -> float:
    if not m:
        return 1.0
    for k, v in MULTV.items():
        if m.startswith(k):
            return v
    return 1.0


def _unit_of(tok: str) -> str:
    if tok in ("t", "tn", "ton") or tok.startswith("tonn"):
        return "тонна"
    if tok == "kg" or tok.startswith("kilo"):
        return "килограмм"
    if tok in ("ta",) or tok.startswith("dona") or tok.startswith("sht"):
        return "штука"
    if tok == "m3" or tok.startswith("kub"):
        return "куб.метр"
    return "литр"


EXTRA_ALIASES = {"flour": ["muki", "muku", "mukoy"], "brick": ["kirpicha", "kirpichey"], "rebar": ["armaturi", "armaturu"]}


def _alias_res() -> list[tuple[re.Pattern, str]]:
    out = []
    for fk, f in families().items():
        for a in f["aliases"] + EXTRA_ALIASES.get(fk, []):
            if len(a) <= 4:
                pat = r"(?<![a-z'])" + re.escape(a) + r"(?:ni|ga|dan|da|i|si|lar|ning)?(?![a-z'0-9])"
            else:
                stem = a[:-1] if len(a) >= 5 and a[-1] in "aeiouy" else a
                pat = r"(?<![a-z'])" + re.escape(stem) + r"[a-z']{0,7}(?![a-z'0-9])"
            out.append((re.compile(pat), fk))
    out.sort(key=lambda x: -len(x[0].pattern))
    return out


_FAM_RES: list | None = None


def find_family(text: str) -> str | None:
    global _FAM_RES
    if _FAM_RES is None:
        _FAM_RES = _alias_res()
    best = None
    for rx, fk in _FAM_RES:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), fk)
    return best[1] if best else None


def find_region(text: str) -> str | None:
    best = None
    for rid, r in regions().items():
        for a in r["aliases"]:
            stem = a[:-1] if len(a) >= 5 and a[-1] in "aeiouy" else a
            m = re.search(r"(?<![a-z'])" + re.escape(stem) + r"[a-z']{0,6}(?![a-z'])", text)
            if m:
                cand = (len(a), -m.start(), rid)
                if best is None or cand > best:
                    best = cand
    if best is None:
        return None
    if best[2] == "TAO" and "viloyat" not in text and "oblast" not in text:
        return "TAS"
    return best[2]


def find_deadline(text: str, ref: date) -> tuple[date | None, str | None]:
    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else ref.year
        y = y + 2000 if y < 100 else y
        try:
            dd = date(y, mo, d)
            if dd < ref and not m.group(3):
                dd = date(y + 1, mo, d)
            return dd, m.group(0)
        except ValueError:
            pass
    # "25-dekabr", "25 dekabrga", "до 25 декабря" -> that exact day
    m = re.search(r"(?<!\d)(\d{1,2})\s*-?\s*(yanv|fev|mart|apr|may|maj|iyun|iyul|avg|sen|okt|noy|dek)[a-z']*", text)
    if m:
        mo = dict(MONTHS)[m.group(2)]
        y = ref.year if (mo, int(m.group(1))) >= (ref.month, ref.day) else ref.year + 1
        try:
            return date(y, mo, int(m.group(1))), m.group(0)
        except ValueError:
            pass
    m = re.search(r"(\d+)\s*(kun|den|dn|hafta|nedel|oy|mesyac|mes)", text)
    if m:
        n, u = int(m.group(1)), m.group(2)
        days = n * (7 if u in ("hafta", "nedel") else 30 if u in ("oy", "mesyac", "mes") else 1)
        return ref + timedelta(days=days), m.group(0)
    if re.search(r"ertaga|zavtra", text):
        return ref + timedelta(days=1), "ertaga"
    if re.search(r"shoshilinch|tezda|zudlik|srochno|tezroq", text):
        return ref + timedelta(days=7), "shoshilinch"
    for stem, mo in MONTHS:
        mm = re.search(r"(?<![a-z])(do\s+)?" + stem + r"[a-z']*", text)
        if mm:
            word = mm.group(0)
            y = ref.year if mo >= ref.month else ref.year + 1
            if mm.group(1) or "gacha" in word:   # "dekabrgacha" / "do dekabrya" -> before the month starts
                dd = date(y, mo, 1)
                if dd <= ref:
                    dd = date(y, mo, calendar.monthrange(y, mo)[1])
            else:                                  # "dekabrda" -> within that month
                dd = date(y, mo, calendar.monthrange(y, mo)[1])
            return dd, word
    return None, None


def _clauses(text: str) -> list[str]:
    parts = [p.strip(" .:-—–") for p in SPLIT_RE.split(text)]
    return [p for p in parts if p]


def parse(text: str, ref: date | None = None, median_price: dict[str, float] | None = None) -> dict[str, Any]:
    """Return {"items": [talabnoma...], "unmatched": [...], "questions": [...], "context": {...}}.

    median_price: family_key -> recent median unit price, used only to tell a unit price ("tonnasi
    950 ming") from a total budget ("jami 10 mln").
    """
    ref = ref or today()
    t = norm(text)
    region = find_region(t)
    deadline, deadline_src = find_deadline(t, ref)
    grades = sorted({"M" + g for g in GRADE_RE.findall(t)})

    items: list[dict] = []
    unmatched: list[str] = []
    loose_money: list[float] = []
    for cl in _clauses(t):
        fk = find_family(cl)
        qm = QTY_RE.search(cl)
        qty, unit = None, None
        if qm:
            qty = _num(qm.group(1)) * _mult(qm.group(2))
            unit = _unit_of(qm.group(3))
        # money: numbers not used as quantity, with a multiplier or so'm or a price word nearby
        rest = cl[:qm.start()] + " " + cl[qm.end():] if qm else cl
        rest = re.sub(r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?", " ", rest)  # dates are not money
        money = None
        for mm in MONEY_RE.finditer(rest):
            v = _num(mm.group(1)) * _mult(mm.group(2))
            if (mm.group(2) or mm.group(3) or PRICE_HINT.search(rest)) and v >= 1000:
                money = v
                break
        if fk and qty is None:
            m2 = re.search(NUM + r"\s*" + MULT + r"\s+[a-z']+", cl)
            if m2 and families()[fk]["unit"] == "штука":
                qty, unit = _num(m2.group(1)) * _mult(m2.group(2)), "штука"
                if money == qty:
                    money = None
        per_tonne = bool(re.search(r"tonnasi|za tonn|tonnasiga|bir tonna", t))
        if fk:
            items.append({"family_key": fk, "qty": qty, "unit_in": unit, "money": money, "text": cl, "per_tonne": per_tonne})
        elif qty is not None:
            unmatched.append(cl)
        elif money is not None:
            loose_money.append(money)

    if not items:
        fk = find_family(t)
        if fk:
            items.append({"family_key": fk, "qty": None, "unit_in": None, "money": loose_money[0] if loose_money else None, "text": t})
    if len(items) == 1 and items[0]["money"] is None and loose_money:
        items[0]["money"] = loose_money[0]

    out_items, questions = [], []
    fams = families()
    for i, it in enumerate(items):
        f = fams[it["family_key"]]
        unit = f["unit"]
        qty = it["qty"]
        if qty is not None and it["unit_in"] == "килограмм" and unit == "тонна":
            qty = qty / 1000
        elif qty is not None and it["unit_in"] == "тонна" and unit == "килограмм":
            qty = qty * 1000
        price_max, budget = None, None
        if it["money"] is not None:
            med = (median_price or {}).get(it["family_key"])
            v = it["money"]
            if it.get("per_tonne") and unit == "килограмм":
                price_max = v / 1000
            elif med and qty and v > 3 * med:
                budget, price_max = v, v / qty
            else:
                price_max = v
        missing = []
        if qty is None:
            missing.append("qty")
            questions.append({"item": i, "field": "qty", "q": f"{f['name_uz']}: qancha kerak? ({unit_short(unit)})"})
        out_items.append({
            "id": f"T{i + 1}", "family_key": it["family_key"], "family_uz": f["name_uz"], "family_ru": f["name_ru"],
            "unit": unit, "unit_short": unit_short(unit), "lot_size": f["lot_size"], "qty": qty,
            "price_max": price_max, "price_source": "user" if price_max else None, "budget": budget,
            "deadline": deadline.isoformat() if deadline else None, "deadline_src": deadline_src,
            "region_id": region, "region_uz": regions()[region]["name_uz"] if region else None,
            "grades": grades if it["family_key"] == "cement" else [], "min_trust": 60, "text": it["text"], "missing": missing,
        })
    if out_items and deadline is None:
        questions.append({"item": None, "field": "deadline", "q": "Qachongacha kerak? (masalan: dekabrgacha, 2 hafta ichida)"})
        for it in out_items:
            it["missing"].append("deadline")
    if out_items and region is None:
        questions.append({"item": None, "field": "region", "q": "Qaysi viloyatga yetkazish kerak?"})
        for it in out_items:
            it["missing"].append("region")
    if not out_items:
        questions.append({"item": None, "field": "family", "q": "Qaysi tovar kerak? Masalan: sement, armatura, g'isht, un, shakar."})
    return {"items": out_items, "unmatched": unmatched, "questions": questions,
            "context": {"today": ref.isoformat(), "region_id": region, "deadline": deadline.isoformat() if deadline else None}}
