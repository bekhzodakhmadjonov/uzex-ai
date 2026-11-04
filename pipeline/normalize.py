"""Pure normalization helpers shared by the pipeline and the API.

Everything here is deterministic and has no I/O. Rules are documented in
analysis/TRAPS.md and the approved plan.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

# ---------------------------------------------------------------------------
# Numbers and dates
# ---------------------------------------------------------------------------
_SPACES = re.compile(r"[\s  ]")


def parse_num(s: Optional[str]) -> Optional[Decimal]:
    """'304 547 938.4' -> Decimal('304547938.4'). Empty -> None."""
    if s is None:
        return None
    s = _SPACES.sub("", str(s)).replace(",", ".")
    if s == "" or s.lower() == "nan":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_int(s: Optional[str]) -> Optional[int]:
    d = parse_num(s)
    return int(d) if d is not None else None


def parse_pct(s: Optional[str]) -> Optional[int]:
    """'10 %' -> 10"""
    if s is None:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else None


def parse_date(s: Optional[str]) -> Optional[date]:
    if s is None or str(s).strip() == "" or str(s).lower() == "nan":
        return None
    return datetime.strptime(str(s).strip(), "%d.%m.%Y").date()


# ---------------------------------------------------------------------------
# Apostrophes / scripts
# ---------------------------------------------------------------------------
APOSTROPHES = "‘’ʻʼ`´'"
_APOS_RE = re.compile("[" + re.escape(APOSTROPHES) + "]")


def unify_apostrophe(s: str) -> str:
    """Map every apostrophe variant to U+2018 (the one used in the data)."""
    return _APOS_RE.sub("‘", s)


def strip_apostrophe(s: str) -> str:
    return _APOS_RE.sub("", s)


# Uzbek Cyrillic -> Latin (official 1995 table, apostrophe as U+2018)
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "’", "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ў": "o‘", "қ": "q", "ғ": "g‘", "ҳ": "h",
}


def translit_cyr_to_lat(s: str) -> str:
    out = []
    for ch in s:
        low = ch.lower()
        if low in _CYR2LAT:
            rep = _CYR2LAT[low]
            out.append(rep.upper() if ch.isupper() and rep else rep)
        else:
            out.append(ch)
    return "".join(out)


def script_of(s: str) -> str:
    """'cyr', 'lat' or 'mixed'/'none' for a string."""
    has_c = has_l = False
    for ch in s:
        if ch.isalpha():
            n = unicodedata.name(ch, "")
            if "CYRILLIC" in n:
                has_c = True
            elif "LATIN" in n:
                has_l = True
    if has_c and has_l:
        return "mixed"
    if has_c:
        return "cyr"
    if has_l:
        return "lat"
    return "none"


# ---------------------------------------------------------------------------
# Product names
# ---------------------------------------------------------------------------
_DASHES = re.compile(r"[-‐-―]")
_PUNCT = re.compile(r"[^\w\s(),./]", flags=re.U)
_MULTI = re.compile(r"\s+")


def norm_text(s: str) -> str:
    """Lowercase, ё->е, dashes->space, drop punctuation except , . / ( ).

    Decimal commas like '32,5Н' and '0,5л' are kept intact.
    """
    s = str(s).lower().replace("ё", "е")
    s = _DASHES.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = s.replace("(", " (").replace(")", ") ")
    return _MULTI.sub(" ", s).strip()


def bag_key(s: str) -> str:
    """Order-independent key: sorted unique tokens of norm_text."""
    toks = norm_text(s).replace("(", "").replace(")", "").split()
    return " ".join(sorted(set(toks)))


# grade (сорт) and class (класс) parsing -------------------------------------
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
_WORD_GRADE = {
    "первый": 1, "первого": 1, "первая": 1,
    "второй": 2, "второго": 2, "вторая": 2,
    "третий": 3, "третьего": 3, "третья": 3,
    "четвертый": 4, "четвертого": 4,
}
_HIGHEST = {"в/с", "вс", "высший", "высшего", "высшый", "высшая", "экстра"}

_GRADE_RE = re.compile(
    r"(?:(?P<pre>[0-9]+|[ivx]+|[а-я]+)\s*[- ]?\s*сорта?\b)"  # '1-сорта', 'первый сорт', 'i сорт'
    r"|(?:\bсорт\s+(?P<post>[0-9]+|[ivx]+|[а-я/]+))",  # 'сорт первый', 'сорт 1', 'сорт в/с'
    flags=re.I,
)
_CLASS_RE = re.compile(
    r"(?:(?P<pre>[0-9]+|[ivx]+)\s*[- ]?\s*класса?\b)"
    r"|(?:\bкласс\s+(?P<post>[0-9]+|[ivx]+))",
    flags=re.I,
)


def _to_grade(tok: str) -> Optional[str]:
    tok = tok.lower().replace("ё", "е")
    if tok in _HIGHEST:
        return "highest"
    if tok.isdigit():
        return str(int(tok))
    if tok in _ROMAN:
        return str(_ROMAN[tok])
    if tok in _WORD_GRADE:
        return str(_WORD_GRADE[tok])
    return None


def parse_grade(name: str) -> Optional[str]:
    """Return 'highest', '1', '2', '3', ... or None."""
    low = name.lower().replace("ё", "е")
    if any(h in low for h in ("в/с", "высш", "экстра")):
        return "highest"
    for m in _GRADE_RE.finditer(low):
        tok = m.group("pre") or m.group("post")
        g = _to_grade(tok)
        if g:
            return g
    return None


def parse_class(name: str) -> Optional[int]:
    low = name.lower().replace("ё", "е")
    for m in _CLASS_RE.finditer(low):
        tok = m.group("pre") or m.group("post")
        g = _to_grade(tok)
        if g and g != "highest":
            return int(g)
    return None


# Deal names look like "Base Марка X Размер Y Сорт Z Тип W Класс V"
_ATTR_LABELS = ["марка", "размер", "сорт", "тип", "класс"]
_ATTR_SPLIT = re.compile(r"\s+(?=(?:марка|размер|сорт|тип|класс)\s)", flags=re.I)


def parse_deal_name(name: str) -> dict:
    """Split a registry product string into base + labelled attributes.

    Returns {'base': str, 'brand':..., 'size':..., 'sort_raw':..., 'type':..., 'class_raw':...}
    with None for missing parts. Labels are matched case-insensitively
    ("МАРКА" in upper-case names too).
    """
    toks = name.strip().split()
    key = {"марка": "brand", "размер": "size", "сорт": "sort_raw", "тип": "type", "класс": "class_raw"}
    # a token is a label start only if it is followed by a value that is not itself a label
    # ("Сорт 2 сорт Класс II класс": positions 0 and 3 are labels, 2 and 5 are value words)
    starts = [i for i, t in enumerate(toks)
              if t.lower() in key and i + 1 < len(toks) and toks[i + 1].lower() not in key]
    res = {"base": None, "brand": None, "size": None, "sort_raw": None, "type": None, "class_raw": None}
    if not starts:
        res["base"] = " ".join(toks)
        return res
    res["base"] = " ".join(toks[: starts[0]]) or None
    for j, s in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(toks)
        res[key[toks[s].lower()]] = " ".join(toks[s + 1: end]) or None
    return res


# ---------------------------------------------------------------------------
# Company / broker names
# ---------------------------------------------------------------------------
_QUOTES = re.compile(r"[\"«»“”„]")


def norm_company(name: str) -> str:
    """Search key for a company name (NOT an identifier).

    lower, quotes removed, apostrophes unified to U+2018, Cyrillic transliterated
    to Latin, whitespace collapsed. Legal-form suffixes are KEPT (they carry
    identity in this data set); use strip_legal_form() for the looser key.
    """
    s = _QUOTES.sub(" ", str(name))
    s = unify_apostrophe(s)
    s = translit_cyr_to_lat(s)
    s = s.lower()
    s = re.sub(r"[.,;:]", " ", s)
    return _MULTI.sub(" ", s).strip()


def load_suffixes(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        suf = [norm_company(l.strip()) for l in f if l.strip()]
    return sorted(set(suf), key=len, reverse=True)


def strip_legal_form(name_norm: str, suffixes: list[str]) -> tuple[str, Optional[str]]:
    """Remove a trailing/leading legal form from a normalized name.

    Returns (core_name, legal_form).
    """
    s = name_norm
    for suf in suffixes:
        if s.endswith(" " + suf):
            return s[: -len(suf) - 1].strip(), suf
        if s.startswith(suf + " "):
            return s[len(suf) + 1:].strip(), suf
    return s, None
