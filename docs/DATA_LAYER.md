# Ma'lumot qatlami — jamoa uchun qo'llanma

## Qurish

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -I pipeline/build_db.py        # ~10 s → data/uzex.duckdb (~80 MB)
.venv/bin/python -I -m pytest tests -q          # oltin testlar (hammasi o'tishi shart)
.venv/bin/python -I pipeline/gen_dictionary.py  # docs/schema_for_llm.md (LLM prompt uchun)
```

Xom CSV `uzex_spot_data/` da, git ga kirmaydi. `data/` ham git ga kirmaydi — serverda qayta quriladi yoki `scp` qilinadi.

## Nima bor

| Joy | Nima |
|---|---|
| `catalog/*.csv` | Qo'lda tekshiriladigan lug'atlar: hududlar (14, 3 yozuv), holatlar (8, bayroqlar bilan), birliklar, savdo turlari, tovar oilalari (41, aliaslar bilan), tovarlar (44, regex qoidalari), tarjimalar |
| `pipeline/normalize.py` | Son/sana/nav/klass/kompaniya nomi normallashtirish — toza funksiyalar |
| `pipeline/build_db.py` | CSV + catalog → DuckDB. Qamrov 100% bo'lmasa yoki oltin son mos kelmasa to'xtaydi |
| `pipeline/gen_dictionary.py` | Bazadagi COMMENT lardan ikki tilli sxema hujjati |
| `app/db.py` | Read-only ulanish (`enable_external_access=false`), `run_sql()` |
| `app/resolve.py` | Foydalanuvchi so'zini bazaga bog'lash: `resolve_family/product/brand/company/broker/region/status` — har doim nomzodlar ro'yxati, `is_confident()` bilan tekshiriladi |
| `tests/` | 67 baza testi + resolver testlari + `golden_questions.json` (savol → SQL → kutilgan javob) |

## LLM ko'radigan jadvallar

`deals` (500 000 qator, hamma narsa qo'shilgan — asosiy jadval), `contracts`, `participants`, `brokers`, `usd_rates`, `meta`.
To'liq ustunlar va qoidalar: `docs/schema_for_llm.md`. Yashirin: `internal.*` (xom nusxalar, lug'atlar, xarita).

## Eng muhim ustunlar (deals)

- Tovar: `family_key` (paxta → `cotton`), `product_key` (`cotton_spot`), `grade_sort` ('highest','1','2','3'), `grade_class` (1–4), `brand` (АИ-92, ЦЕМ I 42,5 Н — yozuvlari birlashtirilgan), `product_raw` (reyestrdagi asl nom).
- Pul: `amount` (shartnoma valyutasida), `currency` (UZS/USD), `amount_uzs` (eksport bitim kunidagi kurs bilan), `amount_usd`, `unit_price`, `start_price`.
- Hajm: `volume` (birlikda), `volume_t` (tonna, faqat kg/tonna), `unit`, `lot_size`, `qty_lots`.
- Holat: `status_code`, `status_ru`, `status_uz`, `is_annulled`, `is_executed`, `is_partial`, `is_paid`, `is_open`.
- Muddat: `pay_due_date`, `delivery_due_date`, `days_to_pay`, `delivery_delay_days`, `is_delivery_late`.
- Tomonlar: `seller_name/inn`, `buyer_name/inn`, `buyer_activity_ru/uz`, `seller_broker(_name)`, `buyer_broker(_name)`, `region_id/uz/ru`.

## Birja ta'riflari (SQL da shunday yoziladi)

- Bitimlar soni: butun reyestr, `n_annulled` alohida.
- Summa, hajm, narx: asosiy raqam bekor qilinganlarsiz (`*_valid`), yonida hammasi (`*_all`). Sabab: README bo'yicha bekor qilingan bitim amalga oshmagan. Hakamlar qaysi birini kutishi noma'lum, shuning uchun ikkalasi ham ko'rsatiladi.
- O'rtacha narx: sum(amount)/sum(volume), DOIM `unit` va `currency` bo'yicha guruhlab
- Hajm tonnada: `sum(volume_t)`
- Kechikkan yetkazish: `is_delivery_late`; muddati o'tgan to'lov: yo'q (hammasi `is_annulled`)

## Resolver qoidasi

Aniq moslik yoki bitta kuchli nomzod (`is_confident`) bo'lsa — ishlatiladi va javobda "X deb tushundik" yoziladi.
Aks holda nomzodlar ro'yxati (STIR + hudud bilan) ko'rsatiladi. "Toshkent" doim ikkita (shahar va viloyat).
