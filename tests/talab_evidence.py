"""Compute the evidence numbers for UZEX TALAB from the hackathon dataset (SQL only, no LLM).

    .venv/bin/python -m tests.talab_evidence      -> writes data/talab_evidence.json

Every number in the pitch comes from this file. Results are reported as they come out, including
the ones that do not support our idea (for example, history does not predict annulments here).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
con = duckdb.connect(str(ROOT / "data" / "uzex.duckdb"), read_only=True)


def one(sql, p=None):
    cur = con.execute(sql, p or [])
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return {c: (float(v) if hasattr(v, "is_finite") else v) for c, v in zip(cols, r)}


def rows(sql, p=None):
    cur = con.execute(sql, p or [])
    cols = [d[0] for d in cur.description]
    return [{c: (float(v) if hasattr(v, "is_finite") else v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def baseline():
    a = one("select avg(is_annulled::int)*100 annul_pct, sum(amount_uzs) filter (where is_annulled)/1e12 annul_trln, count(*) n from deals")
    by_year = rows("select year, sum(amount_uzs) filter (where is_annulled)/1e12 trln, count(*) filter (where is_annulled) n from deals group by 1 order by 1")
    sp = one("""with w as (select product_key, date_trunc('week', deal_date) wk, count(*) n, (max(unit_price)-min(unit_price))/median(unit_price) s
                from deals where not is_annulled and year>=2025 group by 1,2 having count(*)>=10)
                select median(s)*100 spread_med, quantile_cont(s,0.75)*100 spread_p75, count(*) n_weeks from w""")
    st = one("select avg(is_at_start_price::int)*100 at_start_pct from deals where is_executed")
    sm = one("select avg((volume<10)::int)*100 lt10_pct, quantile_cont(volume,0.10) p10 from deals where family_key='cement' and unit='тонна' and deal_date>date '2025-10-30'")
    late = one("select avg(is_delivery_late::int)*100 late_pct from deals where is_delivery_late is not null")
    return {"annul_pct": a["annul_pct"], "annul_trln": a["annul_trln"], "annul_by_year": by_year,
            "spread_median_pct": sp["spread_med"], "spread_p75_pct": sp["spread_p75"], "spread_n_product_weeks": sp["n_weeks"],
            "at_start_price_pct": st["at_start_pct"], "cement_lt10t_pct": sm["lt10_pct"], "cement_p10_volume_t": sm["p10"],
            "late_delivery_pct": late["late_pct"],
            "note": "Bir xil mahsulot, bir xil hafta (>=10 bitim, 2025-2026); farq qisman hudud va yetkazish shartlaridan."}


def history_predicts():
    """Honest check: does a buyer's 2022-24 annulment history predict 2025? (It does not in this dataset.)"""
    b = rows("""with h as (select buyer_inn, count(*) n, avg(is_annulled::int) ar from deals where year<=2024 group by 1),
                f as (select buyer_inn, count(*) n25, avg(is_annulled::int) ar25 from deals where year=2025 group by 1)
                select case when h.n is null or h.n<5 then 'yangi/kam tarix' when h.ar=0 then 'toza tarix' when h.ar<0.05 then '<5%'
                            when h.ar<0.15 then '5-15%' else '>=15%' end bucket,
                count(*) buyers, sum(n25) deals_2025, sum(ar25*n25)/sum(n25)*100 annul_2025_pct from f left join h using(buyer_inn) group by 1 order by 1""")
    disp = one("""select stddev(ar)*100 sd_obs, avg(sqrt(0.0674*0.9326/n))*100 sd_binomial from
                  (select buyer_inn, count(*) n, avg(is_annulled::int) ar from deals group by 1 having count(*)>=50)""")
    return {"buckets": b, "dispersion": disp,
            "conclusion": "Datasetda xaridorning o'tmishi kelajakdagi bekor qilishni bashorat qilmaydi (hamma guruhda ~7%). Shuning uchun ishonch balli tarix sifatida ko'rsatiladi, bashorat sifatida emas."}


def deposit_effect():
    by = rows("select deposit_pct, count(*) n, avg(is_annulled::int)*100 annul_pct from deals group by 1 order by 1")
    within = one("""with x as (select family_key, deposit_pct>=15 hi, count(*) n, avg(is_annulled::int) ar from deals group by 1,2)
                    select count(*) families, avg(a.ar-b.ar)*100 diff_pp, sum(b.n) n_high from x a join x b
                    on a.family_key=b.family_key and not a.hi and b.hi where b.n>=200""")
    return {"by_deposit": by, "within_family": within,
            "conclusion": "Zakalat yuqori bo'lgan bitimlarda bekor qilish kamroq. Riskka qarab zakalat iqtisodiy rag'bat beradi."}


def band_savings():
    """Deals that paid above the P75 of the same family+region over the previous 8 weeks (information available at the time)."""
    t0 = time.time()
    r = one("""with d as (select family_key, region_id, unit, deal_date, unit_price, volume, amount_uzs, currency,
                  quantile_cont(unit_price, 0.75) over (partition by family_key, region_id, unit order by deal_date
                       range between interval 56 days preceding and interval 1 day preceding) p75,
                  count(*) over (partition by family_key, region_id, unit order by deal_date
                       range between interval 56 days preceding and interval 1 day preceding) nprev
                from deals where not is_annulled and deal_date>=date '2024-11-01' and currency='UZS')
               select count(*) filter (where deal_date>=date '2025-01-01') n_deals,
                      count(*) filter (where deal_date>=date '2025-01-01' and nprev>=20 and unit_price>p75) n_above,
                      sum((unit_price-p75)*volume) filter (where deal_date>=date '2025-01-01' and nprev>=20 and unit_price>p75)/1e9 overpay_mlrd,
                      sum(amount_uzs) filter (where deal_date>=date '2025-01-01')/1e12 total_trln
               from d""")
    cem = one("""with d as (select deal_date, unit_price, volume,
                  quantile_cont(unit_price, 0.75) over (partition by region_id order by deal_date
                       range between interval 56 days preceding and interval 1 day preceding) p75,
                  count(*) over (partition by region_id order by deal_date range between interval 56 days preceding and interval 1 day preceding) nprev
                from deals where family_key='cement' and unit='тонна' and not is_annulled and deal_date>=date '2024-11-01')
               select count(*) filter (where deal_date>=date '2025-01-01' and nprev>=20 and unit_price>p75) n_above,
                      count(*) filter (where deal_date>=date '2025-01-01') n,
                      sum((unit_price-p75)*volume) filter (where deal_date>=date '2025-01-01' and nprev>=20 and unit_price>p75)/1e9 overpay_mlrd
               from d""")
    return {**r, "above_pct": r["n_above"] / r["n_deals"] * 100, "cement": cem, "seconds": round(time.time() - t0, 1),
            "period": "2025-01-01 .. 2026-10-30, faqat so'mdagi bitimlar",
            "method": "Har bitim narxi shu tovar+hudud bo'yicha oldingi 8 haftadagi bitimlarning P75 (yuqori chegara) bilan solishtirildi (o'sha paytda ma'lum bo'lgan ma'lumot). Ortiqcha to'lov = (narx − P75) × hajm.",
            "caveat": "Ortiqcha to'lovning bir qismi sifat, yetkazish bazisi va hajm farqidan bo'lishi mumkin; bu yuqori baho, aniq tejam emas."}


def signal_backtest(families=("cement", "rebar", "brick", "flour", "sugar", "diesel", "petrol", "polyethylene")):
    """Seasonal buy/wait rule vs 'always buy now'. Decision at each month of 2023-01..2026-08, horizon 2 months.
    Seasonality is estimated only from years before the decision year (no look-ahead)."""
    out = {}
    for fk in families:
        unit = one("select arg_max(unit, c) u from (select unit, count(*) c from deals where family_key=? group by 1)", [fk])["u"]
        mm = rows("select year, month, median(unit_price) p from deals where family_key=? and unit=? and not is_annulled group by 1,2 order by 1,2", [fk, unit])
        P = {(r["year"], r["month"]): r["p"] for r in mm}
        res = []
        for y in range(2023, 2027):
            # seasonality from years < y
            idx = {}
            for m in range(1, 13):
                vals = []
                for yy in range(2022, y):
                    ym = [P[(yy, k)] for k in range(1, 13) if (yy, k) in P]
                    if len(ym) == 12 and (yy, m) in P:
                        med = sorted(ym)[6] if False else sorted(ym)[len(ym) // 2]
                        vals.append(P[(yy, m)] / med * 100)
                if vals:
                    idx[m] = sum(vals) / len(vals)
            for m in range(1, 13):
                nxt = [((y + (m + k - 1) // 12), (m + k - 1) % 12 + 1) for k in (1, 2)]
                if (y, m) not in P or any(n not in P for n in nxt) or m not in idx:
                    continue
                cur = idx[m]
                best = min(nxt, key=lambda ym: idx.get(ym[1], 999))
                if idx.get(best[1], 999) <= cur - 2:
                    action, paid = "wait", P[best]
                else:
                    action, paid = "now", P[(y, m)]
                res.append({"y": y, "m": m, "action": action, "now": P[(y, m)], "paid": paid})
        waits = [r for r in res if r["action"] == "wait"]
        out[fk] = {"decisions": len(res), "wait_signals": len(waits),
                   "wait_right": sum(r["paid"] < r["now"] for r in waits),
                   "wait_hit_pct": (sum(r["paid"] < r["now"] for r in waits) / len(waits) * 100) if waits else None,
                   "avg_saving_when_wait_pct": (sum((r["now"] - r["paid"]) / r["now"] for r in waits) / len(waits) * 100) if waits else None,
                   "avg_saving_all_pct": sum((r["now"] - r["paid"]) / r["now"] for r in res) / len(res) * 100 if res else None}
    return {"by_family": out, "rule": "Keyingi 2 oy ichida mavsumiy indeks hozirgidan kamida 2 punkt past bo'lsa 'kuting', aks holda 'hozir oling'. Indeks faqat qaror yilidan oldingi yillardan.",
            "baseline": "Har doim darhol sotib olish"}


def main():
    t0 = time.time()
    ev = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "dataset": "UZEX hakaton dataseti (500 000 bitim, 03.01.2022–30.10.2026, fiktiv)",
          "baseline": baseline(), "history_predicts": history_predicts(), "deposit_effect": deposit_effect(),
          "band_savings": band_savings(), "signal_backtest": signal_backtest()}
    p = ROOT / "tests" / "talab_parse_eval.json"
    if p.exists():
        ev["parser"] = json.loads(p.read_text(encoding="utf-8"))
    ev["seconds"] = round(time.time() - t0, 1)
    (ROOT / "data" / "talab_evidence.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(ev, ensure_ascii=False, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
