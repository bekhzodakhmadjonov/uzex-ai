"""Transparent trust score (0-100) built from the criteria of the UZEX rules, section XXI, clause 298:
turnover over the last four quarters, length of participation, and execution of deals.

Honest framing: the score describes a participant's history. It is NOT a prediction. In the hackathon
dataset past annulments do not predict future ones (see data/talab_evidence.json), so the economic
lever we rely on is the deposit, whose effect on annulments is measurable.
"""
from __future__ import annotations

import math
from datetime import timedelta
from functools import lru_cache
from typing import Any

from .common import q, q1, rule, today


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


@lru_cache(maxsize=4096)
def trust(inn: str, role: str = "seller") -> dict[str, Any]:
    t = today()
    side = "seller_inn" if role == "seller" else "buyer_inn"
    s = q1(f"""select count(*) n, count(*) filter (where deal_date > ?) n4q, min(deal_date) first,
               avg(is_delivery_late::int) filter (where is_delivery_late is not null) late,
               avg(delivery_delay_days) filter (where is_delivery_late) delay,
               avg(delivered_share) filter (where is_executed or is_partial) dshare,
               avg(is_annulled::int) ann, count(*) filter (where is_delivery_late) n_late
               from deals where {side}=? and deal_date<=?""", [t - timedelta(days=365), inn, t])
    name = q1("select name from participants where inn=?", [inn])
    base = {"inn": inn, "role": role, "name": name["name"] if name else inn,
            "basis": "UZEX qoidalari, XXI bo'lim, 298-band mezonlari", "rule": rule("298"),
            "disclaimer": "Ball o'tmishdagi tarixni ko'rsatadi, kelajakni bashorat qilmaydi."}
    if not s or not s["n"]:
        return {**base, "score": None, "tier": "yangi", "tier_uz": "Yangi ishtirokchi", "reasons": [
            {"code": "new", "text": "Birjada hali bitim yo'q: tarix yo'q, ball hisoblanmaydi.", "points": 0, "max": 0}], "evidence": [], "stats": {}}
    years = (t - s["first"]).days / 365.25
    reasons = [
        {"code": "period", "text": f"Savdoda qatnashish davri: {years:.1f} yil", "points": round(15 * _clamp(years / 3)), "max": 15},
        {"code": "turnover", "text": f"So'nggi 4 chorakdagi bitimlar: {s['n4q']}", "points": round(15 * _clamp(math.log10(s['n4q'] + 1) / 2)), "max": 15},
    ]
    if role == "seller":
        late = s["late"] or 0.0
        dsh = s["dshare"] if s["dshare"] is not None else 1.0
        reasons += [
            {"code": "on_time", "text": f"Kechikib yetkazilgan bitimlar: {late * 100:.1f}% (o'rtacha {s['delay'] or 0:.0f} kun)",
             "points": round(40 * _clamp(1 - late / 0.08)), "max": 40},
            {"code": "full_delivery", "text": f"O'rtacha yetkazilgan ulush: {dsh * 100:.1f}%", "points": round(30 * _clamp((dsh - 0.95) / 0.05)), "max": 30},
        ]
        ev = q("""select deal_no, deal_date, delivery_delay_days from deals where seller_inn=? and is_delivery_late and deal_date<=?
                  order by deal_date desc limit 3""", [inn, t])
        evidence = [{"deal_no": e["deal_no"], "date": str(e["deal_date"]), "text": f"{e['delivery_delay_days']} kun kechikkan"} for e in ev]
    else:
        ann = s["ann"] or 0.0
        bad = q1("""select max(c) m from (select date_trunc('month', deal_date) mo, count(*) c from deals
                    where buyer_inn=? and is_annulled and deal_date>? and deal_date<=? group by 1)""", [inn, t - timedelta(days=365), t])
        worst = bad["m"] or 0
        reasons += [
            {"code": "payment", "text": f"To'lanmagani uchun bekor qilingan bitimlar: {ann * 100:.1f}%", "points": round(50 * _clamp(1 - ann / 0.15)), "max": 50},
            {"code": "rule_303", "text": "So'nggi 12 oyda bir oyda 2 tadan ko'p bekor qilish bo'lmagan" if worst < 2 else
             f"So'nggi 12 oyda bir oyda {worst} ta bekor qilish bo'lgan (303-band chegarasi: 2)", "points": 20 if worst < 2 else 0, "max": 20},
        ]
        ev = q("select deal_no, deal_date from deals where buyer_inn=? and is_annulled and deal_date<=? order by deal_date desc limit 3", [inn, t])
        evidence = [{"deal_no": e["deal_no"], "date": str(e["deal_date"]), "text": "to'lanmagan, bekor qilingan"} for e in ev]
    score = sum(r["points"] for r in reasons)
    tier, tier_uz = ("high", "Ishonchli") if score >= 80 else ("normal", "Odatiy") if score >= 60 else ("low", "Ehtiyotkorlik kerak")
    return {**base, "score": score, "tier": tier, "tier_uz": tier_uz, "reasons": reasons, "evidence": evidence,
            "stats": {"n": s["n"], "n4q": s["n4q"], "first": str(s["first"])}}


def deposit_for(base_pct: float, tr: dict) -> dict[str, Any]:
    """Risk-based deposit (clauses 94, 299): proven participants pay less, risky ones more, newcomers the standard."""
    tier = tr.get("tier")
    if tier == "high":
        pct, why = max(base_pct - 3, 5), "Ishonchli ishtirokchi: zakalat 3 punktga kamaytirildi (94-band, umumiy garov tamoyili)."
    elif tier == "low":
        pct, why = base_pct + 5, "Ishonch balli past: zakalat 5 punktga oshirildi."
    else:
        pct, why = base_pct, "Standart zakalat (sotuvchi kontraktda belgilagan, 165-band)."
    return {"base_pct": base_pct, "pct": pct, "why": why, "rules": [rule("94"), rule("165")]}
