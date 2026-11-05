import pandas as pd, glob, re, numpy as np, unicodedata
pd.set_option("display.width",200); pd.set_option("display.max_columns",50); pd.set_option("display.max_colwidth",80)
D="uzex_spot_data/"
d = pd.concat([pd.read_csv(f, dtype=str) for f in sorted(glob.glob(D+"deals_*.csv"))], ignore_index=True)
c = pd.read_csv(D+"contracts.csv", dtype=str); p = pd.read_csv(D+"participants.csv", dtype=str)
b = pd.read_csv(D+"brokers.csv", dtype=str); r = pd.read_csv(D+"usd_rates.csv", dtype=str)

def num(s): return pd.to_numeric(s.str.replace(" ","",regex=False).str.replace(" ","",regex=False), errors="coerce")
print("== RAW NUMBER FORMAT SAMPLES")
for col in ["Сумма сделки","Стартовая цена","Количество","Лот","Поставлено"]:
    v=d[col].dropna(); bad=v[~v.str.fullmatch(r"[0-9][0-9 ]*(\.[0-9]+)?")]
    print(col, "non-std:", len(bad), bad.head(5).tolist(), "| examples:", v.sample(3, random_state=1).tolist())
d["sum"]=num(d["Сумма сделки"]); d["start"]=num(d["Стартовая цена"]); d["qty"]=num(d["Количество"]); d["lot"]=num(d["Лот"]); d["deliv"]=num(d["Поставлено"])
print("NaN after parse:", d[["sum","start","qty","lot"]].isna().sum().to_dict())
print("zeros/neg:", {k:int((d[k]<=0).sum()) for k in ["sum","start","qty","lot"]})
print("lot values:", d["lot"].value_counts().to_dict())
print("unit x lot combos:", d.groupby(["Единица измерения","lot"]).size().to_dict())

print("\n== DATES")
d["date"]=pd.to_datetime(d["Дата сделки"], format="%d.%m.%Y", errors="coerce")
d["pay"]=pd.to_datetime(d["Дата оплаты"], format="%d.%m.%Y", errors="coerce")
d["dlv"]=pd.to_datetime(d["Дата поставки"], format="%d.%m.%Y", errors="coerce")
print("date NaT:", d["date"].isna().sum(), "range", d["date"].min(), d["date"].max())
print("unparsed pay/dlv (non-empty but NaT):", ((d["Дата оплаты"].notna())&(d["pay"].isna())).sum(), ((d["Дата поставки"].notna())&(d["dlv"].isna())).sum())
print("weekday dist:", d["date"].dt.dayofweek.value_counts().sort_index().to_dict())
t=pd.to_datetime(d["Время сделки"], format="%H:%M:%S", errors="coerce"); print("time NaT", t.isna().sum(), "hours:", t.dt.hour.value_counts().sort_index().to_dict())
print("deals per year:", d["date"].dt.year.value_counts().sort_index().to_dict())
print("deals per month 2026:", d[d.date.dt.year==2026].date.dt.month.value_counts().sort_index().to_dict())
print("pay<date:", (d.pay<d.date).sum(), " dlv<pay:", (d.dlv<d.pay).sum(), " dlv<date:", (d.dlv<d.date).sum())
print("pay > 30.10.2026:", (d.pay>pd.Timestamp("2026-10-30")).sum(), "dlv > 30.10.2026:", (d.dlv>pd.Timestamp("2026-10-30")).sum())

print("\n== DEAL NUMBER")
d["no"]=num(d["Номер сделки"]); print("dup deal no:", d["no"].duplicated().sum(), "min/max", d.no.min(), d.no.max(), "missing count", int(d.no.max()-d.no.min()+1-len(d)))
srt=d.sort_values("no"); print("monotonic by date when sorted by no:", srt["date"].is_monotonic_increasing)
print("full dup rows:", d.duplicated().sum())

print("\n== STATUS LOGIC")
d["full"]= d.deliv==d.qty
g=d.groupby("Состояние сделки").agg(n=("no","size"), has_pay=("pay",lambda s:s.notna().mean()), has_dlv=("dlv",lambda s:s.notna().mean()), has_deliv=("deliv",lambda s:s.notna().mean()), full=("full","mean"))
print(g)
print("Частично with deliv>=qty:", ((d["Состояние сделки"]=="Исполнен(Частично)")&(d.deliv>=d.qty)).sum(), " deliv>qty anywhere:", (d.deliv>d.qty).sum(), " Исполнен deliv<qty:", ((d["Состояние сделки"]=="Исполнен")&(d.deliv<d.qty)).sum())
print("Новый dates:", d[d["Состояние сделки"]=="Новый"]["Дата сделки"].value_counts().to_dict())

print("\n== PRICE vs START (auction logic)")
d["unit"]=d["sum"]/(d.qty*d.lot)
for tt,gq in d.groupby("Тип торгов"):
    ratio=gq.unit/gq.start
    print(tt, "unit/start: min %.3f median %.3f max %.3f"%(ratio.min(), ratio.median(), ratio.max()), " below start:", (ratio<0.999).sum(), "above:", (ratio>1.001).sum(), "equal:", ((ratio>=0.999)&(ratio<=1.001)).sum())
print("sum exactly divisible check (unit price has >2 decimals):", (abs(d.unit*100-np.round(d.unit*100))>1e-6).sum())

print("\n== CONTRACT JOIN")
c["Срок оплаты_n"]=num(c["Срок оплаты"]); c["Срок поставки_n"]=num(c["Срок поставки"])
print("deal contracts not in contracts.csv:", (~d["Номер контракта"].isin(c["Номер контракта"])).sum(), " contracts w/o deals:", (~c["Номер контракта"].isin(d["Номер контракта"])).sum(), " dup contract no:", c["Номер контракта"].duplicated().sum())
m=d.merge(c, on="Номер контракта", how="left", suffixes=("","_c"))
for a,bb in [("Лот","Лот_c"),("Единица измерения","Единица измерения_c"),("Тип торгов","Тип торгов_c"),("Тип контракта","Тип контракта_c"),("Форма контракта","Форма контракта_c"),("ИНН продавца","ИНН продавца_c")]:
    print("mismatch", a, ":", (m[a]!=m[bb]).sum())
print("region deals vs contracts sample:", m[["Регион","Регион_c"]].drop_duplicates().head(16).values.tolist())
print("currency vs type:", m.groupby(["Тип контракта","Валюта"]).size().to_dict())
c["reg"]=pd.to_datetime(c["Дата регистрации"], format="%d.%m.%Y"); c["till"]=pd.to_datetime(c["Действует до"], format="%d.%m.%Y")
m2=d.merge(c[["Номер контракта","reg","till","Срок оплаты_n","Срок поставки_n","Базисная цена"]], on="Номер контракта")
print("deal before contract reg:", (m2.date<m2.reg).sum(), " deal after valid-till:", (m2.date>m2.till).sum())
m2["due"]=m2.date+pd.to_timedelta(m2["Срок оплаты_n"], unit="D")
late=(m2.pay>m2.due); print("paid late (pay>due):", late.sum(), "of paid", m2.pay.notna().sum())
over=(m2.pay.isna())&(m2.due<pd.Timestamp("2026-10-30"))&(~m2["Состояние сделки"].isin(["Аннулирован(Не оплачен)"]))
print("unpaid & due passed & not annulled:", over.sum(), m2[over]["Состояние сделки"].value_counts().to_dict())
m2["dlv_due"]=m2.pay+pd.to_timedelta(m2["Срок поставки_n"], unit="D")
print("delivered late:", (m2.dlv>m2.dlv_due).sum(), "of delivered", m2.dlv.notna().sum())
ann=m2[m2["Состояние сделки"]=="Аннулирован(Не оплачен)"]; print("annulled but due not passed:", (ann.due>=pd.Timestamp("2026-10-30")).sum())
wait=m2[m2["Состояние сделки"].isin(["Ждёт оплату","Заключен"])]; print("waiting but due passed:", (wait.due<pd.Timestamp("2026-10-30")).sum())
print("basis price vs unit price ratio:", (m2.unit/num(m2["Базисная цена"])).describe().round(3).to_dict())

print("\n== PRODUCT NAME: deals vs contract card")
def build(row):
    parts=[row["Наименование продукта"]]
    for lab,col in [("Марка","Марка товара"),("Размер","Размер товара"),("Сорт","Сорт товара"),("Тип","Тип товара"),("Класс","Класс товара")]:
        if isinstance(row[col],str) and row[col].strip(): parts.append(f"{lab} {row[col]}")
    return " ".join(parts)
c["built"]=c.apply(build, axis=1)
mm=d[["Номер контракта","Наименование товара"]].drop_duplicates().merge(c[["Номер контракта","built","Наименование продукта"]], on="Номер контракта")
print("deal name != built-from-card:", (mm["Наименование товара"]!=mm["built"]).sum(), "of", len(mm))
print(mm[mm["Наименование товара"]!=mm["built"]].head(8).to_string())
print("names per contract >1:", (d.groupby("Номер контракта")["Наименование товара"].nunique()>1).sum())
d.to_pickle("analysis/deals.pkl"); c.to_pickle("analysis/contracts.pkl")
