"""Price truth: fair-price band, weekly series, seasonality, factory listing rhythm, buy/wait signal.

All numbers come straight from executed (non-annulled) deals; nothing is predicted by a model.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache
from statistics import median
from typing import Any

from .common import families, money, q, q1, regions, today, unit_short

MONTH_UZ = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]


def _unit(fk: str) -> str:
    return families()[fk]["unit"]


@lru_cache(maxsize=256)
def band(fk: str, region_id: str | None = None, weeks: int = 8) -> dict[str, Any]:
    """P10..P90 of unit price over the last `weeks` weeks; region = seller warehouse region if it has >= 30 deals."""
    t = today()
    since = t - timedelta(weeks=weeks)
    base = "from deals where family_key=? and unit=? and not is_annulled and deal_date>? and deal_date<=?"
    params = [fk, _unit(fk), since, t]
    scope, rid = "national", None
    if region_id:
        n = q1(f"select count(*) n {base} and region_id=?", params + [region_id])["n"]
        if n >= 30:
            base += " and region_id=?"
            params.append(region_id)
            scope, rid = "region", region_id
    r = q1(f"""select count(*) n, quantile_cont(unit_price,0.10) p10, quantile_cont(unit_price,0.25) p25,
               quantile_cont(unit_price,0.5) p50, quantile_cont(unit_price,0.75) p75, quantile_cont(unit_price,0.90) p90,
               min(deal_date) d0, max(deal_date) d1 {base}""", params)
    return {"family_key": fk, "unit": _unit(fk), "unit_short": unit_short(_unit(fk)), "weeks": weeks, "scope": scope,
            "region_id": rid, "region_uz": regions()[rid]["name_uz"] if rid else "butun respublika",
            "n": r["n"], "p10": r["p10"], "p25": r["p25"], "p50": r["p50"], "p75": r["p75"], "p90": r["p90"],
            "from": str(r["d0"]) if r["d0"] else None, "to": str(r["d1"]) if r["d1"] else None,
            "why": f"so'nggi {weeks} haftadagi {r['n']} ta bajarilgan bitim ({'hudud: ' + regions()[rid]['name_uz'] if rid else 'butun respublika'})"}


def median_prices() -> dict[str, float]:
    return {fk: band(fk)["p50"] for fk in families() if band(fk)["p50"]}


def position(price: float | None, b: dict) -> dict[str, Any]:
    if price is None or not b.get("p50"):
        return {"label": None}
    if price < b["p25"]:
        lab, txt = "past", "past: bozordagi bitimlarning 75% idan arzon"
    elif price <= b["p75"]:
        lab, txt = "odatiy", "odatiy diapazon ichida"
    else:
        lab, txt = "yuqori", "yuqori: bitimlarning 75% idan qimmat"
    return {"label": lab, "text": txt, "vs_median_pct": round((price / b["p50"] - 1) * 100, 1)}


@lru_cache(maxsize=64)
def series(fk: str, weeks: int = 52) -> list[dict]:
    t = today()
    rows = q("""select date_trunc('week', deal_date) wk, count(*) n, quantile_cont(unit_price,0.25) p25,
                quantile_cont(unit_price,0.5) p50, quantile_cont(unit_price,0.75) p75
                from deals where family_key=? and unit=? and not is_annulled and deal_date>? and deal_date<=?
                group by 1 order by 1""", [fk, _unit(fk), t - timedelta(weeks=weeks), t])
    return [{"week": str(r["wk"])[:10], "n": r["n"], "p25": r["p25"], "p50": r["p50"], "p75": r["p75"]} for r in rows if r["n"] >= 3]


@lru_cache(maxsize=64)
def seasonality(fk: str, years: tuple[int, ...] | None = None) -> dict[int, float]:
    """Month -> price index (100 = that year's median of monthly medians), averaged over full years."""
    t = today()
    ys = years or tuple(range(2022, t.year))
    ph = ",".join("?" * len(ys))
    rows = q(f"""with m as (select year, month, median(unit_price) p from deals
                 where family_key=? and unit=? and not is_annulled and year in ({ph}) group by 1,2),
                 y as (select year, median(p) py from m group by 1)
                 select month, avg(p/py*100) idx, count(*) ny from m join y using(year) group by 1 order by 1""", [fk, _unit(fk), *ys])
    return {int(r["month"]): round(r["idx"], 1) for r in rows}


def _months_between(a: date, b: date) -> list[tuple[int, int]]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


@lru_cache(maxsize=1)
def _backtest() -> dict:
    import json
    from .common import ROOT
    p = ROOT / "data" / "talab_evidence.json"
    return json.loads(p.read_text(encoding="utf-8"))["signal_backtest"]["by_family"] if p.exists() else {}


def track_record(fk: str) -> dict[str, Any] | None:
    """How the 'wait' signal did historically for this family (tests/talab_evidence.py, no look-ahead)."""
    b = _backtest().get(fk)
    if not b or not b.get("wait_signals"):
        return None
    ok = b["wait_hit_pct"] >= 55 and (b["avg_saving_when_wait_pct"] or 0) > 0
    return {"enabled": ok, "wait_signals": b["wait_signals"], "wait_right": b["wait_right"], "hit_pct": round(b["wait_hit_pct"]),
            "avg_saving_pct": round(b["avg_saving_when_wait_pct"], 1),
            "text": f"2023–2026 tarixida 'kuting' signali {b['wait_signals']} marta berilgan, {b['wait_right']} tasida to'g'ri chiqqan ({b['wait_hit_pct']:.0f}%)."
                    + ("" if ok else " Bu tovarda signal ishonchli emas, shuning uchun o'chirilgan.")}


def signal(fk: str, deadline: date | None, price_label: str | None = None, season: dict[int, float] | None = None,
           ref: date | None = None) -> dict[str, Any]:
    """Buy now / wait / no signal, from seasonality only. Honest: says 'no signal' when the pattern is weak
    or when the rule lost money for this family in the backtest."""
    r = _signal(fk, deadline, price_label, season, ref)
    tr = track_record(fk)
    r["track"] = tr
    if r["action"] in ("wait", "buy") and tr and not tr["enabled"] and r.get("cur_idx") is not None and price_label != "past":
        r = {"action": "none", "text": "Aniq signal yo'q: bu tovarda mavsumiy signal tarixda ishonchli chiqmagan.", "track": tr}
    return r


def _signal(fk: str, deadline: date | None, price_label: str | None = None, season: dict[int, float] | None = None,
            ref: date | None = None) -> dict[str, Any]:
    t = ref or today()
    s = season or seasonality(fk)
    if not s or t.month not in s:
        return {"action": "none", "text": "Aniq signal yo'q: tarix yetarli emas."}
    dl = deadline or (t + timedelta(days=60))
    end = dl if dl.day >= 10 else dl - timedelta(days=dl.day)  # 'dekabrgacha' (1 Dec) does not include December
    window = [(y, m) for (y, m) in _months_between(t, end) if m in s]
    cur = s[t.month]
    later = [(y, m) for (y, m) in window if (y, m) != (t.year, t.month)]
    if price_label == "past":
        return {"action": "buy", "text": "Hozir oling: bugungi narx odatiy diapazondan past.", "cur_idx": cur}
    if later:
        best = min(later, key=lambda ym: s[ym[1]])
        worst = max(later, key=lambda ym: s[ym[1]])
        if s[best[1]] <= cur - 2:
            return {"action": "wait", "text": f"Kuting: so'nggi yillarda {MONTH_UZ[best[1] - 1]}da narx odatda {str(round(cur - s[best[1]], 1)).replace('.', ',')}% arzonroq bo'lgan.",
                    "wait_until": date(best[0], best[1], 1).isoformat(), "cur_idx": cur, "best_idx": s[best[1]]}
        if s[worst[1]] >= cur + 1:
            return {"action": "buy", "text": f"Hozir oling: muddatingizgacha narx odatda oshadi ({MONTH_UZ[worst[1] - 1]}da +{str(round(s[worst[1]] - cur, 1)).replace('.', ',')}%).",
                    "cur_idx": cur, "worst_idx": s[worst[1]]}
    return {"action": "none", "text": "Aniq signal yo'q: muddatingiz ichida mavsumiy farq kichik. Shoshilmasdan, narx shiftingiz bilan kuting.", "cur_idx": cur}


@lru_cache(maxsize=64)
def rhythm(fk: str, region_id: str | None = None, top: int = 5) -> list[dict]:
    """Factory listing rhythm: median gap between a seller's lot registrations, and the next expected listing."""
    t = today()
    params: list = [fk, t]
    reg = ""
    if region_id:
        reg = " and c.region_id=?"
        params.append(region_id)
    rows = q(f"""select c.seller_inn, coalesce(any_value(p.name), c.seller_inn) name, list(c.reg_date order by c.reg_date) ds,
                 any_value(c.region_id) region_id
                 from contracts c left join participants p on p.inn=c.seller_inn
                 where c.family_key=? and c.reg_date<=?{reg} group by 1""", params)
    out = []
    for r in rows:
        ds = sorted(set(r["ds"]))
        if len(ds) < 3:
            continue
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        g = median(gaps)
        nxt = ds[-1] + timedelta(days=round(g))
        while nxt < t:
            nxt += timedelta(days=round(g))
        out.append({"seller_inn": r["seller_inn"], "name": r["name"], "region_id": r["region_id"], "n_lots": len(ds),
                    "median_gap_days": round(g), "last_listing": ds[-1].isoformat(), "next_expected": nxt.isoformat()})
    out.sort(key=lambda x: x["next_expected"])
    return out[:top]


def market(fk: str, region_id: str | None, price: float | None, deadline: date | None) -> dict[str, Any]:
    b = band(fk, region_id)
    pos = position(price, b)
    s = seasonality(fk)
    return {"band": b, "position": pos, "series": series(fk), "season": [{"month": m, "name": MONTH_UZ[m - 1], "idx": v} for m, v in sorted(s.items())],
            "signal": signal(fk, deadline, pos.get("label"), s), "rhythm": rhythm(fk, region_id) or rhythm(fk),
            "band_text": {"p25": money(b["p25"]), "p50": money(b["p50"]), "p75": money(b["p75"])}}
