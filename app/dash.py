"""Deterministic market panel for the web UI: no LLM, parameterised SQL only.

GET /api/dash?from=YYYY-MM-DD&to=YYYY-MM-DD&family=cotton&region=TAS&cur=uzs|usd&annulled=exclude|include&opts=1
Money is summed from amount_uzs or amount_usd (each deal converted at its own deal-date rate).
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from collections import OrderedDict
from typing import Any

from . import db

_cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()
_KEY = re.compile(r"^[a-z0-9_]{1,40}$")
_REG = re.compile(r"^[A-Z]{2,4}$")


def _date(s: str | None, default: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return default


def _rows(sql: str, params: list) -> list[dict[str, Any]]:
    con = db.connection()
    with _lock:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = cur.fetchall()
    res = []
    for r in out:
        res.append({c: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, float)) else v) for c, v in zip(cols, r)})
    return res


def dashboard(date_from: str | None, date_to: str | None, family: str | None, region: str | None,
              cur: str | None, annulled: str | None, opts: bool = False) -> dict[str, Any]:
    m = db.meta()
    dmin = _date(m.get("min_deal_date"), dt.date(2022, 1, 3))
    dmax = _date(m.get("max_deal_date") or m.get("data_asof"), dt.date(2026, 10, 30))
    f = max(dmin, _date(date_from, dmax.replace(year=dmax.year - 1) + dt.timedelta(days=1)))
    t = min(dmax, _date(date_to, dmax))
    if t < f:
        f, t = t, f
    family = family if family and _KEY.match(family) else None
    region = region if region and _REG.match(region) else None
    usd = (cur or "uzs").lower() == "usd"
    excl = (annulled or "exclude") != "include"
    key = (f, t, family, region, usd, excl, opts)
    with _lock:
        hit = _cache.get(key)
    if hit:
        return hit

    amt = "amount_usd" if usd else "amount_uzs"
    where = ["deal_date BETWEEN ? AND ?"]
    params: list[Any] = [f, t]
    if family:
        where.append("family_key = ?")
        params.append(family)
    if region:
        where.append("region_id = ?")
        params.append(region)
    w_all = " AND ".join(where)                       # all statuses
    w = w_all + (" AND NOT is_annulled" if excl else "")  # money/deal base

    span = (t - f).days + 1
    pf, pt = f - dt.timedelta(days=span), f - dt.timedelta(days=1)
    p_params = [pf, pt] + params[2:]

    k = _rows(f"""SELECT count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,
                      sum({amt}) AS turnover, sum(volume_t) AS tonnes,
                      count(DISTINCT buyer_inn) AS n_buyers, count(DISTINCT seller_inn) AS n_sellers,
                      count(DISTINCT product_key) AS n_products,
                      avg({amt}) AS avg_deal
               FROM deals WHERE {w_all}""" + ("" if not excl else "") , params)[0]
    # money KPIs on the chosen base (with or without annulled)
    kb = _rows(f"SELECT count(*) AS n, sum({amt}) AS turnover, sum(volume_t) AS tonnes, avg({amt}) AS avg_deal FROM deals WHERE {w}", params)[0]
    prev = _rows(f"SELECT count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w.replace('deal_date BETWEEN ? AND ?', 'deal_date BETWEEN ? AND ?')}", p_params)[0]

    monthly = _rows(f"""SELECT strftime(deal_date, '%Y-%m') AS ym, count(*) AS n, sum({amt}) AS turnover,
                               count(*) FILTER (WHERE is_annulled) AS n_annulled, sum(volume_t) AS tonnes
                        FROM deals WHERE {w_all} GROUP BY 1 ORDER BY 1""", params)
    top_buyers = _rows(f"SELECT buyer_name AS name, buyer_inn AS inn, count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w} GROUP BY 1,2 ORDER BY turnover DESC NULLS LAST LIMIT 10", params)
    top_sellers = _rows(f"SELECT seller_name AS name, seller_inn AS inn, count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w} GROUP BY 1,2 ORDER BY turnover DESC NULLS LAST LIMIT 10", params)
    top_brokers = _rows(f"""SELECT code, max(name) AS name, count(*) AS n, sum(a) AS turnover FROM (
                               SELECT seller_broker AS code, seller_broker_name AS name, {amt} AS a FROM deals WHERE {w}
                               UNION ALL
                               SELECT buyer_broker, buyer_broker_name, {amt} FROM deals WHERE {w})
                            WHERE code IS NOT NULL GROUP BY code ORDER BY n DESC LIMIT 10""", params + params)
    regions = _rows(f"SELECT region_id AS id, region_uz AS uz, region_ru AS ru, count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w} GROUP BY 1,2,3 ORDER BY turnover DESC NULLS LAST", params)
    families = _rows(f"SELECT family_key AS key, family_uz AS uz, family_ru AS ru, count(*) AS n, sum({amt}) AS turnover, sum(volume_t) AS tonnes FROM deals WHERE {w} GROUP BY 1,2,3 ORDER BY turnover DESC NULLS LAST LIMIT 10", params)
    statuses = _rows(f"SELECT status_code AS code, status_uz AS uz, status_ru AS ru, count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w_all} GROUP BY 1,2,3 ORDER BY n DESC", params)
    late = _rows(f"""SELECT count(*) FILTER (WHERE is_delivery_late) AS n_late, count(*) AS n_base,
                            avg(delivery_delay_days) FILTER (WHERE is_delivery_late) AS avg_delay
                     FROM deals WHERE {w} AND (is_executed OR is_partial)""", params)[0]

    export = _rows(f"SELECT is_export, count(*) AS n, sum({amt}) AS turnover, sum(volume_t) AS tonnes FROM deals WHERE {w} GROUP BY 1 ORDER BY 1", params)
    trade_types = _rows(f"SELECT trade_type AS ru, trade_type_uz AS uz, count(*) AS n, sum({amt}) AS turnover FROM deals WHERE {w} GROUP BY 1,2 ORDER BY n DESC", params)
    # Month-of-year profile over the whole registry (same family/region/annulled filters, no date filter)
    w_season = " AND ".join(where[1:] + (["NOT is_annulled"] if excl else [])) or "TRUE"
    seasonal = _rows(f"SELECT month, count(*) AS n, sum({amt}) AS turnover, sum(volume_t) AS tonnes, count(DISTINCT year) AS ny FROM deals WHERE {w_season} GROUP BY 1 ORDER BY 1", params[2:])
    products = _rows(f"SELECT product_uz AS uz, product_ru AS ru, unit, currency, count(*) AS n, sum({amt}) AS turnover, sum(volume_t) AS tonnes FROM deals WHERE {w} GROUP BY 1,2,3,4 ORDER BY turnover DESC NULLS LAST LIMIT 10", params)
    price_monthly: list[dict[str, Any]] = []
    if family:
        price_monthly = _rows(f"""SELECT strftime(deal_date, '%Y-%m') AS ym, unit, currency, count(*) AS n,
                                         sum(amount) / nullif(sum(volume), 0) AS price
                                  FROM deals WHERE {w} AND volume > 0 GROUP BY 1,2,3 ORDER BY 1""", params)

    out: dict[str, Any] = {
        "filters": {"from": f.isoformat(), "to": t.isoformat(), "family": family, "region": region,
                    "cur": "usd" if usd else "uzs", "annulled": "exclude" if excl else "include"},
        "data_range": [dmin.isoformat(), dmax.isoformat()], "as_of": m.get("data_asof"),
        "prev_period": [pf.isoformat(), pt.isoformat()],
        "kpi": {"n_deals": k["n_deals"], "n_annulled": k["n_annulled"], "n_base": kb["n"],
                "turnover": kb["turnover"], "tonnes": kb["tonnes"], "avg_deal": kb["avg_deal"],
                "n_buyers": k["n_buyers"], "n_sellers": k["n_sellers"], "n_products": k["n_products"],
                "prev_n": prev["n"], "prev_turnover": prev["turnover"]},
        "monthly": monthly, "top_buyers": top_buyers, "top_sellers": top_sellers, "top_brokers": top_brokers,
        "regions": regions, "families": families, "statuses": statuses, "late": late,
        "export": export, "trade_types": trade_types, "seasonal": seasonal, "products": products, "price_monthly": price_monthly,
    }
    if opts:
        out["options"] = {
            "families": _rows("SELECT family_key AS key, family_uz AS uz, family_ru AS ru, count(*) AS n FROM deals GROUP BY 1,2,3 ORDER BY n DESC", []),
            "regions": _rows("SELECT region_id AS id, region_uz AS uz, region_ru AS ru, count(*) AS n FROM deals GROUP BY 1,2,3 ORDER BY n DESC", []),
        }
    with _lock:
        _cache[key] = out
        while len(_cache) > 300:
            _cache.popitem(last=False)
    return out


_ticker_cache: dict[str, Any] = {}


def ticker() -> dict[str, Any]:
    """Per-family weighted price for the last full month vs the month before (dominant unit/currency), plus turnover."""
    if _ticker_cache:
        return _ticker_cache
    m = db.meta()
    dmax = _date(m.get("max_deal_date") or m.get("data_asof"), dt.date(2026, 10, 30))
    last = dmax.replace(day=1)                      # first day of the last month in the data
    prev = (last - dt.timedelta(days=1)).replace(day=1)
    rows = _rows("""SELECT family_key AS key, family_uz AS uz, family_ru AS ru, unit, currency, strftime(deal_date, '%Y-%m') AS ym,
                           sum(amount) AS a, sum(volume) AS v, count(*) AS n, sum(amount_uzs) AS t_uzs
                    FROM deals WHERE NOT is_annulled AND volume > 0 AND deal_date >= ? GROUP BY ALL""", [prev])
    fam: dict[str, dict[str, Any]] = {}
    for r in rows:
        f = fam.setdefault(r["key"], {"key": r["key"], "uz": r["uz"], "ru": r["ru"], "units": {}})
        u = f["units"].setdefault((r["unit"], r["currency"]), {"n": 0, "m": {}})
        u["n"] += r["n"]
        u["m"][r["ym"]] = r
    out = []
    lm, pm = last.strftime("%Y-%m"), prev.strftime("%Y-%m")
    for f in fam.values():
        (unit, cur), u = max(f["units"].items(), key=lambda kv: kv[1]["n"])
        cur_m, prev_m = u["m"].get(lm), u["m"].get(pm)
        price = (cur_m["a"] / cur_m["v"]) if cur_m and cur_m["v"] else None
        price_prev = (prev_m["a"] / prev_m["v"]) if prev_m and prev_m["v"] else None
        pct = ((price - price_prev) / price_prev * 100) if price and price_prev else None
        n_last = sum(x["m"].get(lm, {}).get("n", 0) for x in f["units"].values())
        t_last = sum(x["m"].get(lm, {}).get("t_uzs", 0) or 0 for x in f["units"].values())
        if price is None:
            continue
        out.append({"key": f["key"], "uz": f["uz"], "ru": f["ru"], "unit": unit, "currency": cur, "price": round(price, 2),
                    "price_prev": round(price_prev, 2) if price_prev else None, "pct": round(pct, 2) if pct is not None else None,
                    "n": n_last, "turnover_uzs": t_last})
    out.sort(key=lambda x: -x["turnover_uzs"])
    res = {"month": lm, "prev_month": pm, "items": out}
    _ticker_cache.update(res)
    return res
