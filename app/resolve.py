"""Entity resolution: map user words (ru / uz-latin / uz-cyrillic / en, with typos)
to database keys. Never guesses silently: every function returns a ranked list of
candidates with a score; the caller decides what counts as a confident match.

    resolve_family("paxta")        -> [Match(kind='family', key='cotton', label='Хлопок-сырец', score=1.0, ...)]
    resolve_product("АИ-92")       -> product/brand candidates
    resolve_company("Sehim Savdo") -> participants with inn + region
    resolve_broker("kahir")        -> brokers
    resolve_region("Фарғона")      -> region_id
    resolve_status("bekor")        -> status_code
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from rapidfuzz.distance import JaroWinkler  # noqa: E402
from normalize import norm_company, norm_text, strip_apostrophe, translit_cyr_to_lat  # noqa: E402

from .db import run_sql  # noqa: E402

CONFIDENT = 0.92   # >= this and a clear gap to #2 -> auto-pick
EXACT_NAME = 0.97  # >= this the name itself matched (at worst apostrophes differ); below it the names are only similar
MIN_SHOW = 0.70    # below this candidates are not shown at all


@dataclass
class Match:
    kind: str            # family | product | company | broker | region | status
    key: str             # family_key / product_key / inn / broker code / region_id / status_code
    label: str           # human label (ru)
    label_uz: str = ""
    score: float = 0.0
    via: str = ""        # which alias/field matched
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "label": self.label, "label_uz": self.label_uz,
                "score": round(self.score, 3), "via": self.via, **self.extra}


def _q(text: str) -> str:
    """Query normalization: lowercase, translit Cyrillic→Latin for Latin-script matching
    is done separately; here we only unify apostrophes and whitespace."""
    s = strip_apostrophe(norm_text(text))
    return s.strip()


def _variants(text: str) -> list[str]:
    base = _q(text)
    out = [base]
    lat = strip_apostrophe(translit_cyr_to_lat(base))
    if lat != base:
        out.append(lat)
    return out


def _sim(a: str, b: str) -> float:
    """Jaro-Winkler similarity 0..1 (rapidfuzz, pure in-process)."""
    return JaroWinkler.similarity(a, b)


def _typo_sim(a: str, b: str) -> float:
    """Similarity for catching typos in short dictionary words. A different first letter is a
    different word here: without this rule 'fargona' matched 'argon'."""
    return _sim(a, b) if a and b and a[0] == b[0] else 0.0


# ---------------------------------------------------------------------------
# families / products
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _family_index() -> list[tuple[str, str, str, str]]:
    """(alias_norm, family_key, name_ru, name_uz) — aliases + names, apostrophes stripped."""
    rows = run_sql("SELECT family_key, name_ru, name_uz, name_en, aliases FROM internal.dim_family", max_rows=1000).rows
    idx = []
    for fk, ru, uz, en, aliases in rows:
        for a in {ru, uz, en, *aliases.split("|")}:
            if a:
                idx.append((strip_apostrophe(norm_text(a)), fk, ru, uz))
    return idx


@lru_cache(maxsize=1)
def _product_index() -> list[tuple[str, str, str, str, str]]:
    """(alias_norm, product_key, family_key, name_ru, name_uz) from canonical names + raw names + brands."""
    rows = run_sql("""SELECT p.product_key, p.family_key, p.name_ru, p.name_uz, p.name_en,
                             list(DISTINCT m.raw_name) AS raws
                      FROM internal.dim_product p LEFT JOIN internal.map_raw_product m USING(product_key)
                      GROUP BY ALL""", max_rows=1000).rows
    idx = []
    for pk, fk, ru, uz, en, raws in rows:
        names = {ru, uz, en, *(raws or [])}
        for a in names:
            if a:
                idx.append((strip_apostrophe(norm_text(a)), pk, fk, ru, uz))
    return idx


def resolve_family(text: str, limit: int = 5) -> list[Match]:
    best: dict[str, Match] = {}
    for v in _variants(text):
        for alias, fk, ru, uz in _family_index():
            if v == alias:
                score = 1.0
            elif len(v) >= 3 and (v in alias.split() or alias in v.split()):
                score = 0.85  # partial word match ('paxta' inside 'paxta yog‘i'): shown as candidate, never auto-picked
            else:
                score = _typo_sim(v, alias)
            if score >= MIN_SHOW and (fk not in best or best[fk].score < score):
                best[fk] = Match("family", fk, ru, uz, score, alias)
    return sorted(best.values(), key=lambda m: -m.score)[:limit]


def _tokens(s: str) -> set[str]:
    return {t for t in s.replace("(", " ").replace(")", " ").split() if t}


def resolve_product(text: str, limit: int = 5) -> list[Match]:
    best: dict[str, Match] = {}
    for v in _variants(text):
        vt = _tokens(v)
        for alias, pk, fk, ru, uz in _product_index():
            if v == alias:
                score = 1.0
            elif len(v) >= 3 and v in alias:
                score = 0.9
            elif len(vt) >= 2 and vt <= _tokens(alias):
                score = 0.9  # all query words present, any order: "spot paxta"
            else:
                score = _sim(v, alias)
            if score >= MIN_SHOW and (pk not in best or best[pk].score < score):
                best[pk] = Match("product", pk, ru, uz, score, alias, {"family_key": fk})
    return sorted(best.values(), key=lambda m: -m.score)[:limit]


def resolve_brand(text: str, limit: int = 5) -> list[Match]:
    """Brands like АИ-92, ЦЕМ I 42,5 Н, М-125, HD-6018 (compared on normalized text)."""
    v = _q(text)
    rows = run_sql("""SELECT brand, product_key, product_ru, count(*) n FROM deals WHERE brand IS NOT NULL GROUP BY ALL""", max_rows=1000).rows
    out = []
    for brand, pk, ru, n in rows:
        b = _q(brand)
        score = 1.0 if v == b else (0.9 if v and v in b else _sim(v, b))
        if score >= MIN_SHOW:
            out.append(Match("brand", brand, f"{ru} {brand}", "", score, b, {"product_key": pk, "n_deals": n}))
    return sorted(out, key=lambda m: -m.score)[:limit]


# ---------------------------------------------------------------------------
# companies / brokers (name_norm is Latin, lower, quotes removed, apostrophe U+2018)
# ---------------------------------------------------------------------------
def _name_query(text: str) -> str:
    return norm_company(text)


def resolve_company(text: str, limit: int = 5, role: str | None = None) -> list[Match]:
    """Returns participants; exact INN wins; else name_norm / name_core match; else fuzzy top-N.
    The caller must show inn + region when more than one candidate scores high."""
    t = text.strip()
    if t.isdigit() and len(t) == 9:
        rows = run_sql("SELECT inn, name, region_uz, role, activity_ru FROM participants WHERE inn = ?", [t]).rows
        return [Match("company", r[0], r[1], "", 1.0, "inn", {"inn": r[0], "region": r[2], "role": r[3], "activity": r[4]}) for r in rows]
    q = _name_query(t)
    q_plain = strip_apostrophe(q)
    role_sql = " AND role = ?" if role else ""
    params: list = [q, q, q_plain, q_plain, q, q, q_plain, q_plain]
    if role:
        params.append(role)
    rows = run_sql(f"""
        SELECT inn, name, region_uz, role, activity_ru,
               CASE WHEN name_norm = ? OR name_core = ? THEN 1.0
                    WHEN replace(name_norm, '‘', '') = ? OR replace(name_core, '‘', '') = ? THEN 0.97
                    WHEN name_core LIKE '%' || ? || '%' THEN 0.9
                    ELSE greatest(jaro_winkler_similarity(name_core, ?), jaro_winkler_similarity(replace(name_core,'‘',''), ?),
                                  jaro_winkler_similarity(replace(name_norm,'‘',''), ?)) END AS score
        FROM participants WHERE 1=1 {role_sql}
        ORDER BY score DESC, name LIMIT {int(limit)}""", params).rows
    return [Match("company", r[0], r[1], "", float(r[5]), "name", {"inn": r[0], "region": r[2], "role": r[3], "activity": r[4]})
            for r in rows if r[5] >= MIN_SHOW]


@lru_cache(maxsize=1)
def _company_index() -> dict[str, list[tuple]]:
    """Exact-name lookup: both the full name and the name without its legal form, apostrophes stripped."""
    idx: dict[str, list[tuple]] = {}
    for inn, name, region, role, activity, norm, core in run_sql(
            "SELECT inn, name, region_uz, role, activity_ru, name_norm, name_core FROM participants", max_rows=20000).rows:
        for key in {strip_apostrophe(norm), strip_apostrophe(core)}:
            idx.setdefault(key, []).append((inn, name, region, role, activity))
    return idx


def resolve_company_exact(text: str) -> list[Match]:
    """Whole-name match in any script and letter case; never fuzzy, so safe to run on any phrase."""
    rows = _company_index().get(strip_apostrophe(norm_company(text)), [])
    return [Match("company", r[0], r[1], "", 1.0, "exact", {"inn": r[0], "region": r[2], "role": r[3], "activity": r[4]}) for r in rows]


def resolve_broker(text: str, limit: int = 5) -> list[Match]:
    t = text.strip()
    if t.upper().startswith("BK") and t[2:].isdigit():
        code = "BK" + t[2:].zfill(3)
        rows = run_sql("SELECT code, name, region_uz FROM brokers WHERE code = ?", [code]).rows
        return [Match("broker", r[0], r[1], "", 1.0, "code", {"code": r[0], "region": r[2]}) for r in rows]
    q = _name_query(t)
    q_core = q.replace(" broker", "").strip()
    rows = run_sql("""
        SELECT code, name, region_uz,
               CASE WHEN name_norm = ? OR name_core = ? THEN 1.0
                    WHEN replace(name_core, ' broker', '') = ? THEN 0.98
                    WHEN name_core LIKE '%' || ? || '%' THEN 0.9
                    ELSE jaro_winkler_similarity(replace(name_core, ' broker', ''), ?) END AS score
        FROM brokers ORDER BY score DESC, code LIMIT ?""", [q, q, q_core, q_core, q_core, limit]).rows
    return [Match("broker", r[0], r[1], "", float(r[3]), "name", {"code": r[0], "region": r[2]}) for r in rows if r[3] >= MIN_SHOW]


# ---------------------------------------------------------------------------
# regions / statuses
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _region_index() -> list[tuple[str, str, str, str]]:
    rows = run_sql("SELECT region_id, name_uz, name_uz_cyr, name_ru, name_en, aliases FROM internal.dim_region").rows
    idx = []
    for rid, uz, cyr, ru, en, aliases in rows:
        for a in {uz, cyr, ru, en, *aliases.split("|")}:
            idx.append((strip_apostrophe(norm_text(a)), rid, uz, ru))
    return idx


def resolve_region(text: str, limit: int = 3) -> list[Match]:
    best: dict[str, Match] = {}
    for v in _variants(text):
        v_short = v.replace(" viloyati", "").replace(" vil", "").replace(" область", "").replace(" области", "").replace(" обл", "").strip()
        for alias, rid, uz, ru in _region_index():
            a_short = alias.replace(" viloyati", "").replace(" область", "").strip()
            if v == alias or v_short == a_short:
                score = 1.0
            elif len(v_short) >= 4 and v_short in alias:
                score = 0.93
            else:
                score = _typo_sim(v_short, a_short)
            if score >= MIN_SHOW and (rid not in best or best[rid].score < score):
                best[rid] = Match("region", rid, ru, uz, score, alias)
    out = sorted(best.values(), key=lambda m: -m.score)[:limit]
    # "Toshkent" alone is ambiguous: return both city and region with equal score
    if out and out[0].key in ("TAS", "TAO") and _q(text) in ("toshkent", "tashkent", "ташкент", "тошкент"):
        both = [m for m in best.values() if m.key in ("TAS", "TAO")]
        for m in both:
            m.score = 0.9
            m.via = "ambiguous: city or region"
        return both
    return out


_STATUS_ALIASES = {
    "executed": ["исполнен", "исполнена", "исполненные", "выполнен", "bajarilgan", "bajarildi", "ijro etilgan", "to‘liq bajarilgan", "completed", "executed", "бажарилган"],
    "partial": ["частично", "исполнен(частично)", "qisman", "qisman bajarilgan", "partially", "қисман"],
    "annulled": ["аннулирован", "аннулированные", "отменен", "отменён", "не оплачен", "bekor", "bekor qilingan", "annulled", "cancelled", "бекор", "бекор қилинган"],
    "paid": ["оплачен", "оплачена", "to‘langan", "tolangan", "paid", "тўланган"],
    "awaiting_payment": ["ждёт оплату", "ждет оплату", "ожидает оплаты", "to‘lov kutilmoqda", "tolov kutilmoqda", "awaiting payment", "тўлов кутилмоқда"],
    # generic words 'tuzilgan' / 'заключено' mean "deals made" in questions, so only explicit status phrases map here
    "concluded": ["статус заключен", "состояние заключен", "holati tuzilgan", "holati imzolangan", "imzolangan forvard", "concluded status"],
    "not_signed": ["не подписан", "imzolanmagan", "not signed", "имзоланмаган"],
    "new": ["новый", "новые", "yangi", "new", "янги"],
}


def resolve_status(text: str, limit: int = 3) -> list[Match]:
    labels = dict(run_sql("SELECT status_code, status_ru || ' | ' || status_uz FROM internal.dim_status").rows)
    best: dict[str, Match] = {}
    for v in _variants(text):
        for code, aliases in _STATUS_ALIASES.items():
            for a in aliases:
                a_n = strip_apostrophe(norm_text(a))
                score = 1.0 if v == a_n else (0.9 if len(v) >= 4 and v in a_n else _sim(v, a_n))
                if score >= MIN_SHOW and (code not in best or best[code].score < score):
                    best[code] = Match("status", code, labels[code], "", score, a)
    return sorted(best.values(), key=lambda m: -m.score)[:limit]


def is_confident(matches: list[Match]) -> bool:
    if not matches:
        return False
    if len(matches) == 1:
        return matches[0].score >= CONFIDENT
    return matches[0].score >= CONFIDENT and matches[0].score - matches[1].score >= 0.05
