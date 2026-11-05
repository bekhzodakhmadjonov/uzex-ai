"""Second held-out set, written after the fixes that came from fullscale_cases.py and not used to make them.
Answer key: pandas on the raw CSVs only. Same format and scorer: python -I tests/eval_fullscale.py --cases fresh_cases
Once this set has been run and something is fixed because of it, it stops being held out.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fullscale_cases import ROOT, TN, class_is, load, sort_is  # noqa: E402


def cases():
    d, c, p, b, r = load()
    out = []

    def add(i, lang, q, expect, kind="num", note=""):
        out.append({"id": i, "lang": lang, "q": q, "expect": expect, "kind": kind, "note": note})

    def fam(key):
        return d[d.tn == TN[key]]

    def both(x, col="sum"):
        return [round(float(x[~x.ann][col].sum()), 2), round(float(x[col].sum()), 2)]

    def price(x):
        v = x[~x.ann]
        return [round(float(v["sum"].sum() / v.vol.sum()), 2), round(float(x["sum"].sum() / x.vol.sum()), 2)]

    def near(x, tol=0.06):
        return {"near": round(float(x), 2), "tol": tol}

    name = p.set_index("ИНН")["Наименование"]
    bname = b.set_index("Код брокера")["Наименование"]
    pact = p.set_index("ИНН")["Вид деятельности"]
    pvat = p.set_index("ИНН")["Плательщик НДС"]
    days_to_pay = (d.pay - d.date).dt.days

    # shares and comparisons
    y25 = d[(d.y == 2025) & ~d.ann]
    add("share_cotton", "ru", "Какая доля хлопка в обороте 2025 года без аннулированных сделок, в процентах?", [near(y25[y25.tn == TN["cotton"]].uzs.sum() / y25.uzs.sum() * 100)])
    add("share_export_cnt", "uz", "2024 yilda eksport bitimlari barcha bitimlarning necha foizini tashkil qilgan?", [near(d[d.y == 2024].exp.mean() * 100)])
    a92 = fam("petrol")
    a92_l = a92[(a92.unit == "литр") & (a92.y == 2025)]
    add("cmp_ai", "ru", "Сравни среднюю цену АИ-92 и АИ-95 за литр в 2025 году.", [price(a92_l[a92_l.nm.str.contains("аи-92")])[0], price(a92_l[a92_l.nm.str.contains("аи-95")])[0]])
    add("cmp_years", "uz", "2023 va 2024 yillarda shakar bo‘yicha nechtadan bitim bo‘lgan?", [int((fam("sugar").y == 2023).sum()), int((fam("sugar").y == 2024).sum())])
    add("cmp_regions", "ru", "Где больше сделок в 2025 году: в Самаркандской или Бухарской области? Назови обе цифры.", [int(((d["Регион"] == "Samarqand viloyati") & (d.y == 2025)).sum()), int(((d["Регион"] == "Buxoro viloyati") & (d.y == 2025)).sum())])
    # time series and peaks
    q24 = d[d.y == 2024].groupby("quart").size()
    add("ts_quarters", "uz", "2024 yilda choraklar bo‘yicha bitimlar soni qancha?", [int(v) for v in q24.values])
    add("ts_years", "ru", "Сколько сделок было по годам?", [int((d.y == y).sum()) for y in range(2022, 2027)])
    hr = d.groupby("hour").size()
    add("peak_hour", "uz", "Kunning qaysi soatida eng ko‘p bitim tuziladi va nechta?", [int(hr.max())], note=f"hour {hr.idxmax()}")
    wd = d[d.y == 2025].groupby("dow").size()
    add("peak_dow", "ru", "В какой день недели в 2025 году было больше всего сделок и сколько?", [int(wd.max())], note=f"dow {wd.idxmax()}")
    mm = d[(d.y == 2022) & ~d.ann].groupby("m").uzs.sum()
    add("peak_month_money", "uz", "2022 yilda aylanma eng kam bo‘lgan oy qaysi va qancha?", [round(float(mm.min()), 2)], note=f"month {mm.idxmin()}")
    # rankings with entities
    t = fam("sugar")
    t = t[(t.y == 2025) & ~t.ann].groupby("ИНН продавца").vol.sum().sort_values(ascending=False)
    add("top_sugar_seller", "ru", "Какой продавец продал больше всего сахара в 2025 году в тоннах?", [name[t.index[0]]])
    t = fam("wheat")
    t = t[(t.y == 2024) & ~t.ann].groupby("ИНН покупателя").vol.sum().sort_values(ascending=False)
    add("top3_wheat_buyers", "uz", "2024 yilda bug‘doyni eng ko‘p sotib olgan 3 ta xaridor kim (tonna bo‘yicha)?", [name[t.index[0]], name[t.index[1]], name[t.index[2]]])
    t = d[d.y == 2026].groupby("Регион").size().sort_values(ascending=False)
    add("top3_regions", "uz_cyr", "2026 йилда битимлар сони бўйича энг катта 3 та ҳудуд қайси?", [int(t.iloc[0]), int(t.iloc[1]), int(t.iloc[2])])
    t = d[~d.ann].groupby("Брокер продавца").uzs.sum().sort_values(ascending=False)
    add("top_broker_money", "ru", "Какой брокер продавца дал наибольший оборот за всё время?", [bname[t.index[0]]])
    t = c.groupby("ИНН продавца").size().sort_values(ascending=False)
    add("top_seller_contracts", "uz", "Eng ko‘p kontrakt ro‘yxatdan o‘tkazgan sotuvchi kim va nechta?", [name[t.index[0]], int(t.iloc[0])])
    t = b.groupby("Регион").size().sort_values(ascending=False)
    add("top_broker_region", "ru", "В каком регионе больше всего брокеров и сколько?", [int(t.iloc[0])], note=t.index[0])
    # seller-side and buyer-side attributes
    add("seller_activity", "uz", "2025 yilda savdo kompaniyalari sotgan bitimlar nechta?", [int(((d["ИНН продавца"].map(pact) == "Торговая компания") & (d.y == 2025)).sum())], note="seller's activity")
    add("seller_nonvat", "ru", "Сколько сделок заключили продавцы, не являющиеся плательщиками НДС?", [int((d["ИНН продавца"].map(pvat) == "Нет").sum())])
    add("buyer_acts", "uz", "Faoliyat turi bo‘yicha nechta xaridor bor? Ishlab chiqarish korxonalari nechta?", [int(((p["Роль"] == "Покупатель") & (p["Вид деятельности"] == "Производственное предприятие")).sum())])
    idle = sorted(set(p["ИНН"]) - set(d["ИНН продавца"]) - set(d["ИНН покупателя"]))
    add("idle_names", "ru", "Какие участники не заключили ни одной сделки? Назови их.", [name[i] for i in idle])
    add("same_region", "uz", "2025 yilda xaridor va sotuvchi bir hududda bo‘lgan bitimlar nechta?", [int(((d["ИНН покупателя"].map(p.set_index("ИНН")["Регион"]) == d["Регион"]) & (d.y == 2025)).sum())])
    # execution and timing
    add("pay_same_day", "ru", "Сколько сделок оплачено в день заключения?", [int((days_to_pay == 0).sum())])
    add("pay_fast", "uz", "Bitim kunidan keyin 3 kun ichida to‘langan bitimlar nechta (3 kun ham kiradi)?", [int((days_to_pay <= 3).sum())])
    add("wait_sum", "ru", "Какая сумма сделок, ожидающих оплату, в сумах?", [round(float(d[d.st == "Ждёт оплату"].uzs.sum()), 2)])
    add("new_sum", "uz", "Yangi holatdagi bitimlarning jami summasi so‘mda qancha?", [round(float(d[d.st == "Новый"].uzs.sum()), 2)])
    un = d[d.st == "Не подписан"]
    add("unsigned_first", "ru", "Когда была заключена самая ранняя неподписанная сделка?", [un.date.min().strftime("%d.%m.%Y")])
    fu = d[d["Форма контракта"] == "Фьючерс"]
    add("fut_concluded", "uz", "Fyuchers bitimlaridan nechtasi hali to‘lanmagan va holati Imzolangan?", [int((fu.st == "Заключен").sum())])
    ex = d[d.st == "Исполнен"]
    add("deliv_share_cotton", "ru", "Сколько тонн хлопка фактически поставлено в 2025 году?", [round(float((fam("cotton").pipe(lambda x: x[(x.y == 2025) & x.dl.notna()]).eval("dl * lot")).sum() / 1000), 2)])
    # lots and units
    add("lot12", "uz", "Lot hajmi 12 dona bo‘lgan bitimlar nechta?", [int((d.lot == 12).sum())])
    big = d.loc[d.qty.idxmax()]
    add("max_lots", "ru", "Какое наибольшее количество лотов в одной сделке?", [int(big.qty)])
    add("lots_cement", "uz", "2023 yilda sement bo‘yicha jami necha lot sotilgan, bekor qilinganlar bilan birga?", [int(fam("cement").pipe(lambda x: x[x.y == 2023]).qty.sum())])
    br = fam("brick")
    add("price_brick", "uz", "G‘ishtning bir dona uchun o‘rtacha narxi 2025 yilda qancha?", price(br[br.y == 2025]))
    wa = fam("water")
    add("price_water", "ru", "Какая средняя цена за штуку питьевой воды в 2024 году?", price(wa[wa.y == 2024]))
    add("price_lumber", "uz", "Taxtaning kub metri o‘rtacha necha pul turadi 2023 yilda?", price(fam("lumber").pipe(lambda x: x[x.y == 2023])))
    add("price_cotton_t", "ru", "Какая средняя цена хлопка за тонну в 2025 году?", [{"any": [round(float(x["sum"].sum() / x.vol.sum() * 1000), 2) for x in (fam("cotton").pipe(lambda z: z[(z.y == 2025) & ~z.ann]), fam("cotton").pipe(lambda z: z[z.y == 2025]))]}], note="data is per kg")
    # grades in other wording
    ct = fam("cotton")
    add("gr_cyr", "uz_cyr", "2-нав 2-класс пахта бўйича нечта битим бор?", [int((sort_is(ct.nm, 2) & class_is(ct.nm, 2)).sum())])
    add("gr_spot_roman", "ru", "Сколько спотовых сделок по хлопку I сорта II класса в 2025 году?", [int((sort_is(ct.nm, 1) & class_is(ct.nm, 2) & (ct["Форма контракта"] == "Спот") & (ct.y == 2025)).sum())])
    add("gr_wheat_3_ru", "ru", "Какой объём пшеницы 3 класса продан в 2024 году?", both(fam("wheat").pipe(lambda x: x[class_is(x.nm, 3) & (x.y == 2024)]), "vol"))
    # dollar rate joined to deals
    row = d.iloc[333333]
    add("deal_usd", "ru", f"Сколько в долларах составила сделка № {row['Номер сделки']} по курсу на дату сделки?", [{"near": round(float(row["usd"]), 2), "tol": 0.02}])
    add("deal_rate", "uz", f"{row['Номер сделки']}-raqamli bitim kunidagi dollar kursi qancha edi?", [float(row.k)])
    rd = r.sort_values("date")
    add("rate_change", "ru", "На сколько сумов вырос курс доллара с 1 января 2022 года по 31 октября 2026 года?", [round(float(rd.k.iloc[-1] - rd.k.iloc[0]), 2)])
    add("rate_avg_oct", "uz", "2025 yil oktabr oyida dollar kursining o‘rtacha qiymati qancha?", [near(r[(r.date.dt.year == 2025) & (r.date.dt.month == 10)].k.mean(), 0.02)])
    # contracts
    add("c_region", "ru", "Сколько контрактов зарегистрировано в Навоийской области?", [int((c["Регион"] == "Навоийская область").sum())])
    add("c_forward_cotton", "uz", "Forvard paxta kontraktlari nechta?", [int(((c["Код ТН ВЭД"] == TN["cotton"]) & (c["Форма контракта"] == "Форвард")).sum())])
    add("c_maxdep", "ru", "Какой самый большой залог в контрактах в процентах?", [20])
    add("c_reverse", "uz", "Teskari auksion turidagi kontraktlar nechta?", [int((c["Тип торгов"] == "Обратный аукцион").sum())])
    add("c_liquid", "ru", "Сколько контрактов в группе «Высоколиквидные товары»?", [int((c["Группа товара"] == "Высоколиквидные товары").sum())])
    # noise
    add("noise_mixed", "uz", "Skolko bitim 2024 yilda paxta boyicha?", [int((fam("cotton").y == 2024).sum())], note="mixed languages")
    add("noise_short", "ru", "шрот 2025 сколько", [int((fam("meal").y == 2025).sum())])
    add("noise_polite", "uz", "Assalomu alaykum! Iltimos, 2023 yilda guruch bo‘yicha nechta bitim bo‘lganini ayta olasizmi?", [int((fam("rice").y == 2023).sum())])
    add("noise_cyr_nolet", "uz_cyr", "2025 йилда шакар нархи канча", price(fam("sugar").pipe(lambda x: x[x.y == 2025])), note="no ў қ ғ ҳ")
    # must refuse or answer none
    for i, lang, q in [
        ("r_gas_price", "uz", "Hozir tabiiy gaz narxi qancha?"), ("r_buyer_age", "ru", "Сколько лет работает компания Zoket Invest?"), ("r_employees", "uz", "Gucham Broker kompaniyasida nechta xodim ishlaydi?"),
        ("r_profit", "ru", "Какая прибыль у компании Mo‘jotat Klaster?"), ("r_compare_world", "ru", "Цена хлопка на бирже ICE выше, чем на UZEX?"), ("r_next_year", "uz", "2027 yilda nechta bitim bo‘ladi?"),
        ("r_tax", "ru", "Какой размер комиссии биржи со сделки?"), ("r_fake_broker", "uz", "Golden Broker MChJ nechta bitim tuzgan?"), ("r_reason_late", "ru", "Почему поставки опаздывают?"),
    ]:
        add(i, lang, q, [], kind="refuse")
    add("z_export_cement", "uz", "Sement eksporti qancha?", [], kind="zero")
    add("z_futures_sugar", "ru", "Сколько фьючерсов по сахару?", [], kind="zero")
    add("z_sunday_rate_deal", "uz", "Yakshanba kuni tuzilgan bitimlar nechta?", [], kind="zero")
    return out


if __name__ == "__main__":
    data = {"cases": cases(), "sessions": []}
    with open(os.path.join(ROOT, "tests", "fresh_cases.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=float)
    print(len(data["cases"]), "cases")
