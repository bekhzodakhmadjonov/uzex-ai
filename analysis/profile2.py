import pandas as pd, re, numpy as np, unicodedata, collections
pd.set_option("display.width",220); pd.set_option("display.max_columns",50); pd.set_option("display.max_colwidth",70)
D="uzex_spot_data/"
d=pd.read_pickle("analysis/deals.pkl"); c=pd.read_pickle("analysis/contracts.pkl")
p=pd.read_csv(D+"participants.csv", dtype=str); b=pd.read_csv(D+"brokers.csv", dtype=str); r=pd.read_csv(D+"usd_rates.csv", dtype=str)

print("== SCRIPT MIXING (Latin letters inside Cyrillic words etc.)")
def scripts(s):
    st=set()
    for ch in s:
        if ch.isalpha():
            n=unicodedata.name(ch,""); st.add("CYR" if "CYRILLIC" in n else "LAT" if "LATIN" in n else "OTH")
    return st
for name,ser in [("deal product names", d["Наименование товара"].drop_duplicates()),("contract product",c["Наименование продукта"].drop_duplicates()),("participants",p["Наименование"]),("brokers",b["Наименование"])]:
    mixed=[s for s in ser if scripts(s)>={"CYR","LAT"}]
    print(name, "mixed-script:", len(mixed), "of", len(ser), "| ex:", mixed[:6])
# homoglyph words: Cyrillic word containing latin letters within same token
tok_mixed=collections.Counter()
for s in d["Наименование товара"].drop_duplicates():
    for tok in re.findall(r"\w+", s):
        sc=scripts(tok)
        if sc>={"CYR","LAT"}: tok_mixed[tok]+=1
print("mixed-script tokens in product names:", tok_mixed.most_common(15))
print("special chars present:", {ch:int(d["Наименование товара"].str.contains(re.escape(ch)).sum()) for ch in ["Ø","№","ё","Ё","‘","’","'","ʻ"," ","–","—","  "]})
print("apostrophes in regions:", collections.Counter(ch for s in pd.concat([d["Регион"],p["Регион"],b["Регион"]]).unique() for ch in s if ch in "‘’'ʻ`"))

print("\n== PRODUCT NAME VARIANTS")
def norm(s):
    s=s.lower().replace("ё","е"); s=re.sub(r"[-–—]"," ",s); s=re.sub(r"[^\w\s]"," ",s); s=re.sub(r"\s+"," ",s).strip(); return s
names=d["Наименование товара"].value_counts()
print("distinct raw:", len(names))
nd=collections.defaultdict(list)
for n in names.index: nd[norm(n)].append(n)
print("distinct after lower/punct norm:", len(nd), "| groups with >1 raw spelling:", sum(1 for v in nd.values() if len(v)>1))
for k,v in list((k,v) for k,v in nd.items() if len(v)>1)[:12]: print("  ", v)
cp=c["Наименование продукта"].value_counts()
cnd=collections.defaultdict(list)
for n in cp.index: cnd[norm(n)].append(n)
print("contract product names raw", len(cp), "normed", len(cnd))
for k,v in list((k,v) for k,v in cnd.items() if len(v)>1)[:15]: print("  ", v, [int(cp[x]) for x in v])
print("\n-- base product families (first word) with most spellings:")
fam=collections.defaultdict(set)
for n in cp.index: fam[norm(n).split()[0]].add(n)
for k,v in sorted(fam.items(), key=lambda kv:-len(kv[1]))[:12]: print(f"  {k}: {sorted(v)}")
print("\n-- attribute spelling variants (Сорт/Класс):")
for col in ["Сорт товара","Класс товара","Марка товара","Тип товара"]:
    vc=c[col].dropna().value_counts(); print(col, "distinct", len(vc), "| sample:", vc.head(12).index.tolist())
print("attrs embedded in name (empty attrs but name has 'Марка'/'Сорт'):", ((c[["Марка товара","Размер товара","Сорт товара","Тип товара","Класс товара"]].isna().all(axis=1)) & c["Наименование продукта"].str.contains("Марка|Сорт|Класс|Тип|Размер")).sum())
print("names with embedded attr sample:", c[(c[["Марка товара","Размер товара","Сорт товара","Тип товара","Класс товара"]].isna().all(axis=1)) & c["Наименование продукта"].str.contains("Марка|Сорт|Класс")]["Наименование продукта"].head(8).tolist())
print("leading/trailing spaces in names:", (d["Наименование товара"]!=d["Наименование товара"].str.strip()).sum(), " double spaces:", d["Наименование товара"].str.contains("  ").sum())

print("\n== UNITS per product family (same product sold in different units?)")
d["fam"]=d["Наименование товара"].map(lambda s: norm(s).split()[0])
u=d.groupby("fam")["Единица измерения"].nunique(); multi=u[u>1]
print("families with >1 unit:", len(multi))
for f in multi.index[:15]: print("  ",f, d[d.fam==f].groupby(["Единица измерения","lot"]).size().to_dict())
print("currency per family (export+internal):", d.groupby("fam")["Тип контракта"].nunique().gt(1).sum())
print("export products:", d[d["Тип контракта"]=="Экспортный"]["Наименование товара"].value_counts().head(10).to_dict())
print("export by region:", d[d["Тип контракта"]=="Экспортный"]["Регион"].value_counts().head(5).to_dict())

print("\n== USD RATES")
r["dt"]=pd.to_datetime(r["Дата"], format="%d.%m.%Y"); r["k"]=pd.to_numeric(r["Курс"])
full=pd.date_range(r.dt.min(), r.dt.max()); print("range", r.dt.min().date(), r.dt.max().date(), "rows", len(r), "missing days:", len(full.difference(r.dt)), "dups:", r.dt.duplicated().sum())
print("rate min/max", r.k.min(), r.k.max(), "| yearly mean", r.groupby(r.dt.dt.year).k.mean().round(0).to_dict())
print("deal dates without rate:", (~d["date"].isin(r.dt)).sum())

print("\n== PARTICIPANTS")
print("dup INN:", p["ИНН"].duplicated().sum(), " INN len!=9:", (p["ИНН"].str.len()!=9).sum(), " dup names:", p["Наименование"].duplicated().sum())
print("roles:", p["Роль"].value_counts().to_dict(), "| VAT:", p["Плательщик НДС"].value_counts().to_dict())
sellers=set(p[p["Роль"]=="Продавец"]["ИНН"]); buyers=set(p[p["Роль"]=="Покупатель"]["ИНН"])
print("deal seller INN not in participants:", (~d["ИНН продавца"].isin(p["ИНН"])).sum(), " buyer not in:", (~d["ИНН покупателя"].isin(p["ИНН"])).sum())
print("deal sellers with role Покупатель:", d["ИНН продавца"].isin(buyers).sum(), " deal buyers with role Продавец:", d["ИНН покупателя"].isin(sellers).sum())
print("seller==buyer same INN deals:", (d["ИНН продавца"]==d["ИНН покупателя"]).sum())
print("participants never in deals:", (~p["ИНН"].isin(set(d["ИНН продавца"])|set(d["ИНН покупателя"]))).sum())
print("distinct sellers in deals:", d["ИНН продавца"].nunique(), " distinct buyers:", d["ИНН покупателя"].nunique())
print("name forms sample:", p["Наименование"].sample(12, random_state=3).tolist())
print("name suffix stats:", {k:int(p["Наименование"].str.contains(k, regex=False).sum()) for k in ["МЧЖ","MCHJ","MChJ","ООО","АЖ","AJ","ХК","XK","ЯТТ","YaTT","Кластер","klaster","КЛАСТЕР","\"","«"]})
pr=d.merge(p[["ИНН","Регион"]].rename(columns={"ИНН":"ИНН продавца","Регион":"seller_reg"}), on="ИНН продавца", how="left")
print("deal region != seller participant region:", (pr["Регион"]!=pr["seller_reg"]).sum())
print("participant regions:", sorted(p["Регион"].unique()))
print("activity x role:", p.groupby(["Роль","Вид деятельности"]).size().to_dict())

print("\n== BROKERS")
print("codes", b["Код брокера"].nunique(), "dup names", b["Наименование"].duplicated().sum(), "dup INN", b["ИНН"].duplicated().sum())
print("deal broker codes not in brokers:", (~d["Брокер продавца"].isin(b["Код брокера"])).sum(), (~d["Брокер покупателя"].isin(b["Код брокера"])).sum())
print("same broker both sides:", (d["Брокер продавца"]==d["Брокер покупателя"]).sum())
print("broker INN overlapping participants INN:", b["ИНН"].isin(p["ИНН"]).sum())
print("broker names sample:", b["Наименование"].head(8).tolist())
print("brokers never used:", (~b["Код брокера"].isin(set(d["Брокер продавца"])|set(d["Брокер покупателя"]))).sum())

print("\n== CONTRACT FIELDS")
print("Сумма залога:", c["Сумма залога"].value_counts().to_dict())
print("Срок оплаты range", c["Срок оплаты_n"].min(), c["Срок оплаты_n"].max(), "| Срок поставки", c["Срок поставки_n"].min(), c["Срок поставки_n"].max())
print("ТН ВЭД len:", c["Код ТН ВЭД"].str.len().value_counts().to_dict(), "leading zero:", c["Код ТН ВЭД"].str.startswith("0").sum())
print("Базисная цена format sample:", c["Базисная цена"].sample(5, random_state=1).tolist())
print("Базис поставки:", c["Базис поставки"].value_counts().to_dict())
print("Упаковка distinct:", c["Упаковка"].nunique(), c["Упаковка"].value_counts().head(8).to_dict())
print("contracts per seller: mean %.1f max %d"%(c.groupby("ИНН продавца").size().mean(), c.groupby("ИНН продавца").size().max()))
print("same seller same product multiple contracts (price change over time):", (c.groupby(["ИНН продавца","Наименование продукта"]).size()>1).sum())
print("contract validity days:", (c.till-c.reg).dt.days.describe().round(0).to_dict())
print("warehouse address contains STIR/INN text:", c["Адрес склада"].str.contains("STIR|ИНН").sum())
print("ТН ВЭД per product name >1:", (c.groupby("Наименование продукта")["Код ТН ВЭД"].nunique()>1).sum(), "| category per name>1:", (c.groupby("Наименование продукта")["Категория товара"].nunique()>1).sum())
