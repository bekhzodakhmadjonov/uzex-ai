"""Judge-style held-out set: one named entity (INN / broker code / product+region), one period, one exact metric,
in the style of the judges' sample question. Answer key: pandas on the raw CSVs only (fullscale_cases.load).
Where a question has two plausible readings, `alt` holds the other values and `note` says which is which.
Run: .venv/bin/python -I tests/judge_cases.py   (writes tests/judge_cases.json)
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullscale_cases import ROOT, TN, load  # noqa: E402

PART = "Исполнен(Частично)"


def cases():
    d, c, p, b, r = load()
    name = p.set_index("ИНН")["Наименование"]
    out = []

    def add(i, lang, q, expect, kind="num", note="", alt=None):
        out.append({"id": i, "lang": lang, "q": q, "expect": expect, "kind": kind, "note": note, "alt": alt or {}})

    def near(x, tol=0.006):
        return {"near": round(float(x), 4), "tol": tol}

    def pct(a, n):
        return round(a / n * 100, 4)

    S = lambda inn: d[d["ИНН продавца"] == inn]  # noqa: E731
    B = lambda inn: d[d["ИНН покупателя"] == inn]  # noqa: E731
    v = lambda x: x[~x.ann]  # noqa: E731
    due_pay = d.pay + pd.to_timedelta(d.dlv_t, unit="D")  # registry definition: pay date + delivery term
    due_deal = d.date + pd.to_timedelta(d.dlv_t, unit="D")  # alternative: deal date + delivery term
    d["late_p"] = d.dlv.notna() & (d.dlv > due_pay)
    d["late_d"] = d.dlv.notna() & (d.dlv > due_deal)
    d["delay_p"] = (d.dlv - due_pay).dt.days
    d["delay_d"] = (d.dlv - due_deal).dt.days
    d["atstart"] = (d["sum"] - d.start * d.vol).abs() < 0.005

    # 1 annulled share, seller
    x = S("932546264")[lambda z: z.y == 2023]
    add("ann_seller", "ru", "Какая доля сделок продавца с ИНН 932546264 за 2023 год аннулирована за неуплату? В процентах от числа сделок.",
        [near(pct(x.ann.sum(), len(x)))], note=f"{x.ann.sum()}/{len(x)}")
    # 2 buyer count in a quarter
    x = B("955014087")[lambda z: (z.y == 2025) & (z.quart == 3)]
    add("buyer_q3", "ru", "Сколько сделок заключил покупатель с ИНН 955014087 в третьем квартале 2025 года?", [len(x)],
        note=f"all statuses; annulled {x.ann.sum()}", alt={"excl_annulled": int((~x.ann).sum())})
    # 3 seller sum in a month
    x = S("980514566")[lambda z: (z.y == 2025) & (z.m == 3)]
    add("seller_mar", "ru", "Какова общая сумма сделок продавца ИНН 980514566 в марте 2025 года в сумах, без учёта аннулированных?",
        [round(float(v(x).uzs.sum()), 2)], note=f"n={len(x)} valid={len(v(x))}", alt={"all": round(float(x.uzs.sum()), 2)})
    # 4 export USD and UZS
    x = S("917899526")[lambda z: (z.y == 2025) & z.exp]
    add("export_usd_uzs", "ru", "Какой объём экспортных сделок продавца ИНН 917899526 в 2025 году в долларах США и в сумах, без аннулированных?",
        [round(float(v(x).usd.sum()), 2), near(v(x).uzs.sum(), 1.0)], note=f"n_export={len(x)} valid={len(v(x))}; seller also has domestic deals",
        alt={"all_usd": round(float(x.usd.sum()), 2), "all_uzs": round(float(x.uzs.sum()), 2)})
    # 5 weighted average price per tonne
    x = S("957478014")[lambda z: z.y == 2024]
    add("wavg_t", "ru", "Какая средневзвешенная цена за тонну в сделках продавца ИНН 957478014 за 2024 год?",
        [near(v(x)["sum"].sum() / v(x).vol.sum(), 0.011)], note=f"unit {x.unit.unique().tolist()}; valid",
        alt={"all": round(float(x["sum"].sum() / x.vol.sum()), 2)})
    # 6 max deal of a buyer
    x = B("909884005")[lambda z: z.y == 2023]
    top = x.loc[x["sum"].idxmax()]
    topv = v(x).loc[v(x)["sum"].idxmax()]
    add("max_buyer", "ru", "Какая самая крупная сделка покупателя ИНН 909884005 в 2023 году? Номер сделки и сумма.",
        [top["Номер сделки"], round(float(top["sum"]), 2)], note=f"status of max: {top.st}",
        alt={"valid_no": topv["Номер сделки"], "valid_sum": round(float(topv["sum"]), 2)})
    # 7 late delivery share
    x = S("964459091")[lambda z: z.y == 2024]
    dl = x[x.dlv.notna()]
    add("late_share", "ru", "Какая доля поставок продавца ИНН 964459091 за 2024 год была с опозданием относительно срока поставки по контракту? В процентах.",
        [near(pct(dl.late_p.sum(), len(dl)))], note=f"due=pay+term: {dl.late_p.sum()}/{len(dl)} delivered; all deals n={len(x)}",
        alt={"due_deal_date": pct(dl.late_d.sum(), len(dl)), "of_all_deals": pct(dl.late_p.sum(), len(x))})
    # 8 avg days to payment, buyer
    x = B("970300739")[lambda z: (z.y == 2025) & z.pay.notna()]
    days = (x.pay - x.date).dt.days
    add("pay_days", "ru", "Сколько дней в среднем проходит от даты сделки до оплаты у покупателя ИНН 970300739 в 2025 году?",
        [near(days.mean(), 0.06)], note=f"paid deals {len(x)}", alt={"median": float(days.median())})
    # 9 top-3 buyers of a seller
    x = v(S("980514566")[lambda z: z.y == 2024]).groupby("ИНН покупателя").uzs.sum().sort_values(ascending=False)
    add("top3_buyers", "ru", "Назови трёх крупнейших покупателей продавца ИНН 980514566 в 2024 году по сумме сделок (без аннулированных) и их суммы.",
        [name[x.index[0]], name[x.index[1]], name[x.index[2]], round(float(x.iloc[0]), 2), round(float(x.iloc[1]), 2), round(float(x.iloc[2]), 2)],
        note=f"4th: {name[x.index[3]]} {x.iloc[3]:.2f}")
    # 10 YoY count change
    a, bb = len(S("932546264")[lambda z: z.y == 2023]), len(S("932546264")[lambda z: z.y == 2024])
    add("yoy_cnt", "ru", "На сколько процентов изменилось число сделок продавца ИНН 932546264 в 2024 году по сравнению с 2023 годом?",
        [near((bb / a - 1) * 100, 0.06)], note=f"{a} -> {bb}")
    # 11 broker market share by deals
    y = d[d.y == 2025]
    s_side, b_side = (y["Брокер продавца"] == "BK014"), (y["Брокер покупателя"] == "BK014")
    add("broker_share", "ru", "Какую долю рынка по числу сделок занимал брокер BK014 в 2025 году? В процентах.",
        [{"any": [near(pct(s_side.sum(), len(y))), near(pct(b_side.sum(), len(y))), near(pct((s_side | b_side).sum(), len(y))), near(pct(s_side.sum() + b_side.sum(), 2 * len(y)))]}],
        note=f"n2025={len(y)} seller-side {s_side.sum()} buyer-side {b_side.sum()} either {(s_side | b_side).sum()}",
        alt={"seller": pct(s_side.sum(), len(y)), "buyer": pct(b_side.sum(), len(y)), "either": pct((s_side | b_side).sum(), len(y)), "sides/2n": pct(s_side.sum() + b_side.sum(), 2 * len(y))})
    # 12 buyer concentration
    x = v(S("958388291")[lambda z: z.y == 2025])
    g = x.groupby("ИНН покупателя").uzs.sum().sort_values(ascending=False)
    xa = S("958388291")[lambda z: z.y == 2025]
    ga = xa.groupby("ИНН покупателя").uzs.sum().sort_values(ascending=False)
    add("concentration", "ru", "Какую долю оборота продавца ИНН 958388291 в 2025 году обеспечил его крупнейший покупатель? В процентах.",
        [near(g.iloc[0] / g.sum() * 100)], note=f"top buyer {g.index[0]} {name[g.index[0]]}",
        alt={"all_status": round(float(ga.iloc[0] / ga.sum() * 100), 4), "top_all": ga.index[0]})
    # 13 partial execution share
    x = S("913023166")[lambda z: z.y == 2026]
    add("partial_share", "ru", "Какой процент сделок продавца ИНН 913023166 в 2026 году исполнен частично?",
        [near(pct((x.st == PART).sum(), len(x)))], note=f"{(x.st == PART).sum()}/{len(x)}",
        alt={"of_executed": pct((x.st == PART).sum(), x.st.isin(["Исполнен", PART]).sum())})
    # 14 deals at start price
    x = S("911479188")[lambda z: z.y == 2023]
    add("at_start", "ru", "Сколько сделок продавца ИНН 911479188 в 2023 году прошли точно по стартовой цене?", [int(x.atstart.sum())],
        note=f"of {len(x)}; valid {int(v(x).atstart.sum())}", alt={"excl_annulled": int(v(x).atstart.sum())})
    # 15 export vs domestic
    x = S("908739236")[lambda z: z.y == 2025]
    add("exp_dom", "ru", "Сколько экспортных и сколько внутренних сделок было у продавца ИНН 908739236 в 2025 году?",
        [int(x.exp.sum()), int((~x.exp).sum())], note=f"annulled exp {int((x.exp & x.ann).sum())} dom {int((~x.exp & x.ann).sum())}")
    # 16 unique buyers
    x = S("906995753")[lambda z: z.y == 2025]
    add("uniq_buyers", "ru", "Сколько разных покупателей было у продавца ИНН 906995753 в 2025 году?", [int(x["ИНН покупателя"].nunique())],
        alt={"excl_annulled": int(v(x)["ИНН покупателя"].nunique())})
    # 17 broker buyer side in a month
    x = d[(d["Брокер покупателя"] == "BK083") & (d.y == 2026) & (d.m == 10)]
    add("broker_buy_oct", "ru", "Сколько сделок провёл брокер BK083 на стороне покупателя в октябре 2026 года?", [len(x)], note=f"annulled {x.ann.sum()}")
    # 18 product in region in a quarter
    x = d[(d.tn == TN["cement"]) & (d["Регион"] == "Samarqand viloyati") & (d.y == 2024) & (d.quart == 4)]
    add("cement_sam_q4", "ru", "Какой оборот по цементу в Самаркандской области в четвёртом квартале 2024 года, в сумах?",
        [round(float(v(x).uzs.sum()), 2)], note=f"seller region; n={len(x)}", alt={"all": round(float(x.uzs.sum()), 2)})
    # 19 min deal
    x = S("964459091")[lambda z: z.y == 2025]
    mn = x.loc[x["sum"].idxmin()]
    add("min_deal", "ru", "Какая минимальная сумма сделки у продавца ИНН 964459091 в 2025 году?", [round(float(mn["sum"]), 2)],
        note=f"deal {mn['Номер сделки']} status {mn.st}", alt={"valid": round(float(v(x)["sum"].min()), 2)})
    # 20 same broker on both sides
    x = d[(d.y == 2025) & (d["Брокер продавца"] == "BK025") & (d["Брокер покупателя"] == "BK025")]
    add("same_broker", "ru", "Сколько сделок в 2025 году брокер BK025 провёл одновременно и за продавца, и за покупателя?", [len(x)])
    # 21 annulled share, buyer
    x = B("956224213")[lambda z: z.y == 2024]
    add("ann_buyer", "ru", "Какая доля сделок покупателя ИНН 956224213 за 2024 год аннулирована за неуплату? В процентах.",
        [near(pct(x.ann.sum(), len(x)))], note=f"{x.ann.sum()}/{len(x)}")
    # 22 average delay of late deliveries
    x = S("957478014")[lambda z: z.y == 2025]
    lp, ld = x[x.late_p], x[x.late_d]
    add("avg_delay", "ru", "На сколько дней в среднем опаздывали поставки продавца ИНН 957478014 в 2025 году? Считай только опоздавшие поставки.",
        [near(lp.delay_p.mean(), 0.06)] if len(lp) else [], kind="num" if len(lp) else "zero",
        note=f"due=pay+term: {len(lp)} late", alt={"due_deal_date_mean": round(float(ld.delay_d.mean()), 2) if len(ld) else None, "n_late_deal_date": len(ld)})

    # Uzbek Latin
    x = S("906995753")[lambda z: z.y == 2025]
    add("uz_ann", "uz", "STIR 906995753 bo‘lgan sotuvchining 2025 yildagi bitimlarining necha foizi to‘lanmagani uchun bekor qilingan?",
        [near(pct(x.ann.sum(), len(x)))], note=f"{x.ann.sum()}/{len(x)}")
    x = B("902175329")[lambda z: z.y == 2024]
    add("uz_buyer_sum", "uz", "STIR 902175329 xaridor 2024 yilda jami qancha summalik xarid qilgan, bekor qilinganlarsiz, so‘mda?",
        [round(float(v(x).uzs.sum()), 2)], note=f"n={len(x)}", alt={"all": round(float(x.uzs.sum()), 2)})
    x = d[(d["Брокер продавца"] == "BK006") & (d.y == 2024)]
    add("uz_broker_turn", "uz", "BK006 broker sotuvchi tomonida 2024 yilda qancha aylanma qilgan, so‘mda (bekor qilinganlarsiz)?",
        [near(v(x).uzs.sum(), 1.0)], note=f"n={len(x)} export {x.exp.sum()}", alt={"all": round(float(x.uzs.sum()), 2)})
    x = S("911479188")[lambda z: (z.y == 2023) & (z.m == 12)]
    add("uz_dec", "uz", "STIR 911479188 sotuvchi 2023 yil dekabr oyida nechta bitim tuzgan?", [len(x)], note=f"annulled {x.ann.sum()}")
    x = S("932546264")[lambda z: (z.y == 2025) & (z.quart == 1)]
    add("uz_wavg_q1", "uz", "STIR 932546264 sotuvchining 2025 yil 1-choragidagi o‘rtacha tortilgan narxi 1 kg uchun qancha?",
        [near(v(x)["sum"].sum() / v(x).vol.sum(), 0.011)], note=f"unit {x.unit.unique().tolist()} n={len(x)}",
        alt={"all": round(float(x["sum"].sum() / x.vol.sum()), 2)})
    x = S("972149282")[lambda z: (z.y == 2024) & z.exp]
    add("uz_export_usd", "uz", "STIR 972149282 sotuvchining 2024 yildagi eksport bitimlari jami necha AQSh dollari bo‘lgan?",
        [round(float(v(x).usd.sum()), 2)], note=f"n={len(x)}", alt={"all": round(float(x.usd.sum()), 2)})
    a = S("917899526")[lambda z: (z.y == 2025) & (z.m <= 6)]
    bb = S("917899526")[lambda z: (z.y == 2026) & (z.m <= 6)]
    add("uz_h1_yoy", "uz", "STIR 917899526 sotuvchining bitimlari soni 2026 yilning birinchi yarmida 2025 yilning birinchi yarmiga nisbatan necha foizga o‘zgargan?",
        [near((len(bb) / len(a) - 1) * 100, 0.06)], note=f"{len(a)} -> {len(bb)}")
    x = v(S("906995753")[lambda z: z.y == 2025]).groupby("ИНН покупателя").uzs.sum().sort_values(ascending=False)
    add("uz_top_buyer", "uz", "STIR 906995753 sotuvchining 2025 yildagi eng yirik xaridori kim (summa bo‘yicha) va qancha xarid qilgan?",
        [name[x.index[0]], round(float(x.iloc[0]), 2)], note=f"{x.index[0]}; 2nd {name[x.index[1]]} {x.iloc[1]:.2f}")
    x = B("970300739")
    add("uz_partial_buyer", "uz", "STIR 970300739 xaridorning qisman bajarilgan bitimlari nechta (barcha yillar)?", [int((x.st == PART).sum())])
    x = S("980514566")[lambda z: z.y == 2023]
    add("uz_late_cnt", "uz", "STIR 980514566 sotuvchining 2023 yilda muddatidan kechikib yetkazilgan bitimlari nechta?", [int(x.late_p.sum())],
        note="due=pay+term", alt={"due_deal_date": int(x.late_d.sum())})

    # Uzbek Cyrillic
    x = S("913023166")[lambda z: z.y == 2026]
    add("cyr_ann", "uz_cyr", "СТИР 913023166 бўлган сотувчининг 2026 йилдаги битимларининг неча фоизи тўланмагани учун бекор қилинган?",
        [near(pct(x.ann.sum(), len(x)))], note=f"{x.ann.sum()}/{len(x)}")
    x = d[(d["Брокер продавца"] == "BK014") & (d.y == 2025)]
    add("cyr_broker_cnt", "uz_cyr", "BK014 брокер 2025 йилда сотувчи томонида нечта битим тузган?", [len(x)], note=f"annulled {x.ann.sum()}")
    x = B("909884005")[lambda z: z.y == 2024]
    add("cyr_buyer", "uz_cyr", "СТИР 909884005 харидор 2024 йилда нечта битим тузган ва бекор қилинганларсиз жами суммаси қанча?",
        [len(x), round(float(v(x).uzs.sum()), 2)], note=f"annulled {x.ann.sum()}", alt={"all_sum": round(float(x.uzs.sum()), 2)})

    # second wave: tie, a zero hidden in a normal-looking question, broker share of a product's turnover
    t = d[(d["ИНН продавца"] == "906453491") & (d.y == 2025)].groupby("ИНН покупателя").size().sort_values(ascending=False)
    add("tie_top3", "ru", "Назови трёх крупнейших покупателей продавца ИНН 906453491 в 2025 году по числу сделок.",
        [name[t.index[0]], name[t.index[1]], int(t.iloc[0]), int(t.iloc[1]), int(t.iloc[2])],
        note=f"tie at rank 3: {name[t.index[2]]} and {name[t.index[3]]} both {t.iloc[2]}; 5th {t.iloc[4]}; must not drop one silently",
        alt={"tied": [name[t.index[2]], name[t.index[3]]]})
    payd = (d.pay - d.date).dt.days
    x = d[(d["ИНН покупателя"] == "955014087") & (d.y == 2024) & d.pay.notna()]
    assert (payd[x.index] > x.pay_t).sum() == 0
    add("z_late_pay", "uz", "STIR 955014087 xaridor 2024 yilda nechta bitimni shartnomadagi to‘lov muddatidan kech to‘lagan?", [], kind="zero",
        note=f"no deal in the whole registry is paid after its term (unpaid past term = annulled); buyer had {len(x)} paid deals in 2024")
    ct = d[(d.tn == TN["cotton"]) & (d.y == 2025)]
    g = ct[~ct.ann].groupby("Брокер продавца").uzs.sum()
    ga = ct.groupby("Брокер продавца").uzs.sum()
    add("broker_cotton_share", "ru", "Какую долю оборота хлопка-волокна в 2025 году занимал брокер BK085 на стороне продавца? В процентах, без аннулированных.",
        [near(g["BK085"] / g.sum() * 100)], note=f"seller-side, valid; BK085 n={(ct['Брокер продавца'] == 'BK085').sum()}",
        alt={"all_status": round(float(ga["BK085"] / ga.sum() * 100), 4)})
    # no data / zero
    assert "912345678" not in set(p["ИНН"])
    add("r_fake_inn", "ru", "Какая доля сделок продавца с ИНН 912345678 за 2024 год аннулирована за неуплату? В процентах от числа сделок.", [], kind="refuse",
        note="INN not in participants")
    add("r_future", "uz", "STIR 917899526 sotuvchi 2026 yil dekabr oyida nechta bitim tuzgan?", [], kind="refuse", note="data ends 30.10.2026")
    add("r_profit", "ru", "Какая чистая прибыль продавца ИНН 980514566 по сделкам за 2025 год?", [], kind="refuse", note="no cost/profit field")
    add("r_reason", "ru", "По каким причинам покупатель ИНН 956224213 не оплатил свои аннулированные сделки в 2024 году?", [], kind="refuse",
        note="no reason field; mentioning the count of annulled deals is fine")
    add("r_cyr_future", "uz_cyr", "2027 йилнинг биринчи чорагида BK006 брокер нечта битим тузган?", [], kind="refuse", note="after data end")
    assert ((d["Брокер продавца"] == "BK004") & (d.y == 2025)).sum() == 0
    add("z_bk004_seller", "uz", "BK004 broker sotuvchi tomonida 2025 yilda nechta bitim tuzgan?", [], kind="zero",
        note=f"BK004 is buyer-side only; buyer-side 2025 = {int(((d['Брокер покупателя'] == 'BK004') & (d.y == 2025)).sum())}")
    assert ((d["ИНН продавца"] == "937876670") | (d["ИНН покупателя"] == "937876670")).sum() == 0
    add("z_idle", "ru", "Сколько сделок у участника с ИНН 937876670?", [], kind="zero", note=f"participant {name['937876670']} exists, no deals")
    return out


if __name__ == "__main__":
    data = {"cases": cases(), "sessions": []}
    with open(os.path.join(ROOT, "tests", "judge_cases.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    for c in data["cases"]:
        print(c["id"], c["lang"], c["kind"], c["expect"], "|", c["note"], "|", c["alt"])
    print(len(data["cases"]), "cases")
