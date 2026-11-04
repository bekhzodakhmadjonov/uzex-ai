import pandas as pd, re, collections, numpy as np
pd.set_option("display.width",220)
d=pd.read_pickle("analysis/deals.pkl"); c=pd.read_pickle("analysis/contracts.pkl")
def norm(s):
    s=s.lower().replace("ё","е"); s=re.sub(r"[-–—]"," ",s); s=re.sub(r"[^\w\s]"," ",s); return re.sub(r"\s+"," ",s).strip()
print("== WORD-ORDER / SYNONYM FAMILIES (bag of words on contract product name)")
bag=collections.defaultdict(set)
for n in c["Наименование продукта"].unique(): bag[frozenset(norm(n).split())].add(n)
print("bag-of-words groups:", len(bag), " groups with >1 spelling:", sum(1 for v in bag.values() if len(v)>1))
for k,v in bag.items():
    if len(v)>1 and len({norm(x) for x in v})>1: print("  word-order:", sorted(v))
pairs=[("Топливо дизельное","Дизельное топливо"),("Портландцемент","Цемент портландский"),("Масло хлопковое","Хлопковое масло"),("Шрот хлопковых семян","Шрот хлопковый"),("Пряжа хлопковая","Пряжа хлопчатобумажная"),("Катод медный","Медный катод"),("Мука","Мука пшеничная"),("Пшеница","Пшеница продовольственная"),("Бензин","Бензин автомобильный"),("Пиломатериалы","Пиломатериал обрезной"),("Кирпич","Кирпич керамический"),("Стекло флоат","Стекло листовое"),("Соль","Соль поваренная")]
for a,b2 in pairs:
    na=d["Наименование товара"].str.lower().str.startswith(a.lower()).sum(); nb=d["Наименование товара"].str.lower().str.startswith(b2.lower()).sum()
    print(f"  deals starting '{a}': {na:>7}   '{b2}': {nb:>7}")

print("\n== GRADE (сорт/класс) SPELLINGS in deal names")
gr=collections.Counter()
for n in d["Наименование товара"].unique():
    for m in re.findall(r"Сорт ([^ ]+(?: сорта?)?)|Класс ([^ ]+(?: класса?)?)", n, flags=re.I): gr[m[0] or m[1]]+=1
print(sorted(gr.items(), key=lambda x:-x[1])[:40])

print("\n== SIMPLE vs WEIGHTED AVERAGE PRICE (trap demo)")
for prod in ["Сахар белый","Карбамид Марка Б","БЕНЗИН АВТОМОБИЛЬНЫЙ Марка АИ-92"]:
    for yr in [2025]:
        x=d[(d["Наименование товара"].str.lower()==prod.lower())&(d.date.dt.year==yr)]
        xo=x[x["Состояние сделки"]!="Аннулирован(Не оплачен)"]
        simple=x.unit.mean(); w=x["sum"].sum()/(x.qty*x.lot).sum(); w2=xo["sum"].sum()/(xo.qty*xo.lot).sum()
        print(f"  {prod} {yr}: n={len(x)} simple={simple:,.0f} weighted={w:,.0f} weighted_excl_annulled={w2:,.0f} ({x['Единица измерения'].iloc[0]}, lot {x.lot.iloc[0]})  case-variants: {x['Наименование товара'].nunique()}")
x=d[d["Наименование товара"].str.lower().str.startswith("бензин автомобильный марка аи-92")&(d.date.dt.year==2025)]
print("  AI-92 2025 by unit:", x.groupby(["Единица измерения","lot"]).agg(n=("no","size"), unit=("unit","mean")).round(0).to_dict())

print("\n== MONTHLY DEAL COUNTS (seasonality / Sep-Oct spike)")
mc=d.groupby([d.date.dt.year, d.date.dt.month]).size().unstack(0).fillna(0).astype(int); print(mc.to_string())
print("Sep-Oct top products 2026:", d[(d.date.dt.year==2026)&(d.date.dt.month>=9)]["fam"].value_counts().head(5).to_dict() if "fam" in d else "")
d["fam"]=d["Наименование товара"].map(lambda s: norm(s).split()[0])
print("Sep-Oct 2026 top families:", d[(d.date.dt.year==2026)&(d.date.dt.month>=9)]["fam"].value_counts().head(5).to_dict())
print("Sep-Oct 2025 top families:", d[(d.date.dt.year==2025)&(d.date.dt.month>=9)]["fam"].value_counts().head(5).to_dict())

print("\n== COTTON: name '(спот)/(форвард)' vs Форма контракта")
cot=d[d["Наименование товара"].str.lower().str.contains("хлопок")]
cot=cot.assign(tag=cot["Наименование товара"].str.extract(r"\((спот|форвард|фьючерс)\)", flags=re.I)[0].str.lower())
print(cot.groupby(["tag","Форма контракта"]).size().to_dict())
print("cotton units:", cot.groupby(["Единица измерения","lot"]).size().to_dict())

print("\n== TURNOVER: annulled share, export conversion effect")
tot=d["sum"].where(d["Тип контракта"]=="Внутренний",0).sum(); ann=d[(d["Состояние сделки"]=="Аннулирован(Не оплачен)")&(d["Тип контракта"]=="Внутренний")]["sum"].sum()
print(f"  internal UZS total {tot:,.0f}; annulled {ann:,.0f} ({ann/tot:.1%})")
r=pd.read_csv("uzex_spot_data/usd_rates.csv", dtype=str); r["date"]=pd.to_datetime(r["Дата"], format="%d.%m.%Y"); r["k"]=pd.to_numeric(r["Курс"])
ex=d[d["Тип контракта"]=="Экспортный"].merge(r[["date","k"]], on="date")
print(f"  export USD total {ex['sum'].sum():,.0f} USD = {(ex['sum']*ex.k).sum():,.0f} UZS at deal-date rate; naive sum mixing currencies would add {ex['sum'].sum():,.0f} as if UZS")

print("\n== LATE DELIVERY / DUE DATES")
m=d.merge(c[["Номер контракта","Срок оплаты_n","Срок поставки_n"]], on="Номер контракта")
m["pay_due"]=m.date+pd.to_timedelta(m["Срок оплаты_n"],unit="D"); m["dlv_due"]=m.pay+pd.to_timedelta(m["Срок поставки_n"],unit="D")
late=m[m.dlv>m.dlv_due]; print("  late deliveries:", len(late), "| by status:", late["Состояние сделки"].value_counts().to_dict(), "| avg days late %.1f"%(late.dlv-late.dlv_due).dt.days.mean())
print("  partial deliveries delivered share mean: %.2f"%(m[m["Состояние сделки"]=="Исполнен(Частично)"].deliv/m[m["Состояние сделки"]=="Исполнен(Частично)"].qty).mean())
print("  payment days actual mean %.1f vs term mean %.1f"%((m.pay-m.date).dt.days.mean(), m["Срок оплаты_n"].mean()))
open_=m[m["Состояние сделки"].isin(["Ждёт оплату","Заключен","Новый","Не подписан"])]
print("  open deals with pay_due > 30.10.2026:", (open_.pay_due>pd.Timestamp("2026-10-30")).sum(), "of", len(open_))

print("\n== TOP ENTITIES (sanity for 'top N' questions)")
print("  top sellers by deals:", d["ИНН продавца"].value_counts().head(3).to_dict())
print("  top brokers (seller side):", d["Брокер продавца"].value_counts().head(3).to_dict(), " buyer side:", d["Брокер покупателя"].value_counts().head(3).to_dict())
print("  region deal share:", (d["Регион"].value_counts(normalize=True).head(5)*100).round(1).to_dict())
print("  reverse auction products:", d[d["Тип торгов"]=="Обратный аукцион"]["fam"].value_counts().head(5).to_dict())
print("  start==final share overall: %.1f%%"%((abs(d.unit-d.start)<1e-6).mean()*100))
