# UZEX deals database — schema for the SQL agent (DuckDB)

Data as of **2026-10-30**. Deals from 2022-01-03 to 2026-10-30 (500,000 rows). **2026 is incomplete (Jan–Oct only). No data for Nov–Dec 2026 or before 2022.** All companies, INNs and amounts are fictional.

Tables visible to you: `deals` (use this for almost everything — all lookups are already joined), `contracts`, `participants`, `brokers`, `usd_rates`, `meta`.

## Hard rules (ru | uz)

- Only SELECT. One statement. Always add `count(*) AS n_deals` (or an equivalent row count) so the answer can show its basis.
- Never do arithmetic yourself — compute everything in SQL and read the numbers from the result.
- Text filters: use ILIKE or exact equality on the normalized keys (`product_key`, `family_key`, `region_id`, `status_code`). Never LIKE (case-sensitive) and never regex `\b` (does not work on Cyrillic).
- Average price = `wavg_price(amount, volume)` (= sum(amount)/sum(volume)), ALWAYS grouped by `unit` and `currency`. Never avg(unit_price), never mix litres with tonnes or USD with UZS.
- Population: the whole registry by default (all statuses). Always add `count(*) FILTER (WHERE is_annulled) AS n_annulled`; for money/volume also add the valid-only figure (`... FILTER (WHERE NOT is_annulled) AS *_valid`). Exclude annulled deals from the main figure only when the user asks for executed/actual deals or says 'without annulled'.
- Export deals are in USD (`currency='USD'`, only copper cathode and cotton yarn). `amount_uzs` already converts them at the deal-date rate; `amount_usd` converts domestic deals to USD at the deal-date rate.
- Physical volume = `volume` (in `unit`) or `volume_t` (tonnes, only for тонна/килограмм rows). Delivered volume = `delivered_volume`.
- Status is as of 2026-10-30. There are NO overdue unpaid deals: anything unpaid past its term is already `is_annulled`. Late deliveries exist: `is_delivery_late`.
- Status spelling in Russian uses ё: 'Ждёт оплату'. Prefer `status_code` in filters.
- 'Toshkent shahri' (city) and 'Toshkent viloyati' (region) are different regions.
- When the question is about a family ('paxta', 'un', 'sement'), filter `family_key`; when about a specific product/grade, add `product_key`, `grade_sort`, `grade_class`, `brand`.
- If a filter returns 0 rows, do NOT invent a number — report that the data has no such records and name the filter that failed.

## `deals`

Реестр биржевых сделок, 500 000 строк, 03.01.2022–30.10.2026. Одна строка = одна сделка. Все справочные поля уже присоединены. | Birja bitimlari reyestri, 500 000 qator, 03.01.2022–30.10.2026. Bir qator = bir bitim. Barcha ma’lumotnoma maydonlari qo‘shilgan.

| column | type | meaning (ru \| uz) |
|---|---|---|
| `deal_no` | BIGINT | Номер сделки (сквозной, некоторые номера пропущены) | Bitim raqami (ba’zi raqamlar tushib qolgan) |
| `deal_date` | DATE | Дата сделки | Bitim sanasi |
| `deal_time` | TIME | Время сделки (Ташкент) | Bitim vaqti (Toshkent) |
| `year` | BIGINT | Год сделки | Bitim yili |
| `quarter` | BIGINT | Квартал 1-4 | Chorak 1-4 |
| `month` | BIGINT | Месяц 1-12 | Oy 1-12 |
| `week` | BIGINT | ISO-неделя года | ISO hafta raqami |
| `dow` | BIGINT | День недели 1=пн..7=вс | Hafta kuni 1=du..7=ya |
| `hour` | BIGINT | Час сделки 10-16 | Bitim soati 10-16 |
| `trade_type` | VARCHAR | Тип торгов: Английский аукцион / Обратный аукцион / Двусторонний аукцион | Savdo turi (ruscha qiymatlar) |
| `trade_type_uz` | VARCHAR | Тип торгов по-узбекски | Savdo turi: Angliya auksioni / Teskari auksion / Ikki tomonlama auksion |
| `contract_no` | VARCHAR | Номер контракта → contracts.contract_no | Shartnoma raqami → contracts.contract_no |
| `contract_type` | VARCHAR | Внутренний или Экспортный | Ichki yoki Eksport (ruscha qiymat) |
| `is_export` | BOOLEAN | TRUE = экспортная сделка (валюта USD) | TRUE = eksport bitimi (valyuta USD) |
| `contract_form` | VARCHAR | Спот / Форвард / Фьючерс | Spot / Forvard / Fyuchers (ruscha qiymat) |
| `product_raw` | VARCHAR | Наименование товара как в реестре (с маркой/сортом/классом) | Tovar nomi reyestrdagi kabi (marka/nav/klass bilan) |
| `product_key` | VARCHAR | Ключ канонического товара (cotton_spot, wheat, diesel ...) | Kanonik tovar kaliti |
| `product_ru` | VARCHAR | Канонический товар по-русски (объединяет варианты написания) | Kanonik tovar ruscha |
| `product_uz` | VARCHAR | Канонический товар по-узбекски | Kanonik tovar o‘zbekcha |
| `family_key` | VARCHAR | Ключ семейства товаров (cotton, wheat, flour, cement ...) | Tovar oilasi kaliti |
| `family_ru` | VARCHAR | Семейство товаров по-русски | Tovar oilasi ruscha |
| `family_uz` | VARCHAR | Семейство по-узбекски | Tovar oilasi o‘zbekcha |
| `grade_sort` | VARCHAR | Сорт: highest / 1 / 2 / 3 или NULL | Nav: highest (oliy) / 1 / 2 / 3 yoki NULL |
| `grade_class` | INTEGER | Класс 1-4 или NULL | Klass 1-4 yoki NULL |
| `brand` | VARCHAR | Марка товара (АИ-92, ЦЕМ I 42,5 Н, Б ...) | Tovar markasi |
| `size` | VARCHAR | Размер товара | Tovar o‘lchami |
| `ptype` | VARCHAR | Тип товара (хлебопекарная, рафинированное ...) | Tovar tipi |
| `spec` | VARCHAR | Спецификация после названия товара (марка/размер/тип одной строкой) | Tovar nomidan keyingi spetsifikatsiya |
| `category_ru` | VARCHAR | Категория товара | Tovar kategoriyasi (ruscha) |
| `category_uz` | VARCHAR | Категория по-узбекски | Tovar kategoriyasi |
| `industry_ru` | VARCHAR | Отрасль промышленности | Sanoat tarmog‘i (ruscha) |
| `industry_uz` | VARCHAR | Отрасль по-узбекски | Sanoat tarmog‘i |
| `tnved` | VARCHAR | Код ТН ВЭД (6 знаков) | TN VED kodi (6 raqam) |
| `unit` | VARCHAR | Единица измерения: тонна/килограмм/литр/штука/куб.метр/кв.метр/метр | O‘lchov birligi (ruscha qiymat) |
| `unit_uz` | VARCHAR | Единица по-узбекски | O‘lchov birligi: tonna/kilogramm/litr/dona/kub metr/kvadrat metr/metr |
| `lot_size` | INTEGER | Единиц измерения в одном лоте (1, 12, 100, 1000) | Bir lotdagi birliklar soni |
| `qty_lots` | INTEGER | Количество купленных лотов | Sotib olingan lotlar soni |
| `volume` | BIGINT | Объём = qty_lots × lot_size, в единицах unit | Hajm = qty_lots × lot_size, unit birligida |
| `volume_t` | DOUBLE | Объём в тоннах (только для тонна/килограмм, иначе NULL) | Hajm tonnada (faqat tonna/kg, aks holda NULL) |
| `amount` | DECIMAL(20,2) | Сумма сделки в валюте контракта (currency) | Bitim summasi shartnoma valyutasida (currency) |
| `currency` | VARCHAR | UZS или USD | UZS yoki USD |
| `start_price` | DECIMAL(18,2) | Стартовая цена за единицу измерения, в валюте контракта | Boshlang‘ich narx 1 birlik uchun, shartnoma valyutasida |
| `unit_price` | DOUBLE | Цена за единицу = amount / volume, в валюте контракта | Birlik narxi = amount / volume, shartnoma valyutasida |
| `price_vs_start_pct` | DOUBLE | Изменение цены к стартовой, % (+ выше старта) | Narxning boshlang‘ichga nisbatan o‘zgarishi, % |
| `usd_rate` | DECIMAL(12,2) | Курс USD на дату сделки, сум | Bitim kunidagi USD kursi, so‘m |
| `amount_uzs` | DECIMAL(22,2) | Сумма в сумах (экспорт пересчитан по курсу даты сделки) | Summa so‘mda (eksport bitim kunidagi kurs bilan) |
| `amount_usd` | DECIMAL(18,2) | Сумма в долларах (внутренние пересчитаны по курсу даты сделки) | Summa dollarda (ichki bitimlar bitim kunidagi kurs bilan) |
| `region_id` | VARCHAR | Код региона склада продавца | Sotuvchi ombori hududi kodi |
| `region_uz` | VARCHAR | Регион по-узбекски (Toshkent shahri, Farg‘ona viloyati ...) | Hudud o‘zbekcha |
| `region_ru` | VARCHAR | Регион по-русски (г. Ташкент, Ферганская область ...) | Hudud ruscha |
| `seller_inn` | VARCHAR | ИНН продавца → participants.inn | Sotuvchi STIR → participants.inn |
| `seller_name` | VARCHAR | Наименование продавца | Sotuvchi nomi |
| `buyer_inn` | VARCHAR | ИНН покупателя → participants.inn | Xaridor STIR |
| `buyer_name` | VARCHAR | Наименование покупателя | Xaridor nomi |
| `buyer_activity_ru` | VARCHAR | Вид деятельности покупателя | Xaridor faoliyat turi (ruscha) |
| `buyer_activity_uz` | VARCHAR | Вид деятельности по-узбекски | Xaridor faoliyat turi |
| `buyer_is_vat` | BOOLEAN | Покупатель — плательщик НДС | Xaridor QQS to‘lovchi |
| `seller_broker` | VARCHAR | Код брокера продавца → brokers.code | Sotuvchi brokeri kodi |
| `seller_broker_name` | VARCHAR | Брокер продавца | Sotuvchi brokeri nomi |
| `buyer_broker` | VARCHAR | Код брокера покупателя → brokers.code | Xaridor brokeri kodi |
| `buyer_broker_name` | VARCHAR | Брокер покупателя | Xaridor brokeri nomi |
| `status_ru` | VARCHAR | Состояние сделки на 30.10.2026 (Исполнен, Аннулирован(Не оплачен), Ждёт оплату ...) | Bitim holati 30.10.2026 holatiga (ruscha) |
| `status_uz` | VARCHAR | Состояние по-узбекски | Bitim holati: Bajarilgan, Bekor qilingan (to‘lanmagan), To‘lov kutilmoqda ... |
| `status_code` | VARCHAR | executed/partial/annulled/paid/awaiting_payment/concluded/not_signed/new | Holat kodi |
| `is_annulled` | BOOLEAN | TRUE = аннулирована (не оплачена в срок), сделка не состоялась — исключать из оборота | TRUE = bekor qilingan — aylanmadan chiqariladi |
| `is_executed` | BOOLEAN | TRUE = исполнена полностью или частично | TRUE = to‘liq yoki qisman bajarilgan |
| `is_partial` | BOOLEAN | TRUE = исполнена частично | TRUE = qisman bajarilgan |
| `is_paid` | BOOLEAN | TRUE = оплачена (есть дата оплаты) | TRUE = to‘langan |
| `is_open` | BOOLEAN | TRUE = ещё в процессе (новый/не подписан/заключен/ждёт оплату/оплачен) | TRUE = hali jarayonda |
| `pay_date` | DATE | Дата оплаты, NULL если не оплачена | To‘lov sanasi, to‘lanmagan bo‘lsa NULL |
| `delivery_date` | DATE | Дата поставки, NULL если не поставлена | Yetkazish sanasi, yetkazilmagan bo‘lsa NULL |
| `delivered_lots` | INTEGER | Поставлено лотов | Yetkazilgan lotlar |
| `delivered_volume` | BIGINT | Поставлено в единицах unit | Yetkazilgan hajm, unit birligida |
| `delivered_share` | DOUBLE | Доля поставки 0..1 | Yetkazilgan ulush 0..1 |
| `pay_term_days` | INTEGER | Срок оплаты по контракту, календарных дней от даты сделки | To‘lov muddati, bitim sanasidan kalendar kun |
| `delivery_term_days` | INTEGER | Срок поставки по контракту, календарных дней от даты оплаты | Yetkazish muddati, to‘lov sanasidan kalendar kun |
| `pay_due_date` | TIMESTAMP | Крайний срок оплаты = deal_date + pay_term_days | To‘lov muddati sanasi = deal_date + pay_term_days |
| `delivery_due_date` | TIMESTAMP | Крайний срок поставки = pay_date + delivery_term_days | Yetkazish muddati sanasi = pay_date + delivery_term_days |
| `days_to_pay` | BIGINT | Фактически дней от сделки до оплаты | Bitimdan to‘lovgacha o‘tgan kunlar |
| `delivery_delay_days` | BIGINT | Просрочка поставки в днях (>0 = с опозданием), NULL если не поставлено | Yetkazish kechikishi kunlarda (>0 kech) |
| `is_delivery_late` | BOOLEAN | TRUE = поставлено позже срока | TRUE = muddatidan kech yetkazilgan |

## `contracts`

Карточки контрактов (6 616): товар, единица, лот, валюта, базисная цена, сроки оплаты/поставки, продавец. | Shartnoma kartochkalari (6 616): tovar, birlik, lot, valyuta, bazis narx, to‘lov/yetkazish muddatlari, sotuvchi.

| column | type | meaning (ru \| uz) |
|---|---|---|
| `contract_no` | VARCHAR | Номер контракта | Shartnoma raqami |
| `product_name` | VARCHAR | Наименование продукта как заполнил продавец | Sotuvchi yozgan mahsulot nomi |
| `product_key` | VARCHAR |  |
| `product_ru` | VARCHAR |  |
| `product_uz` | VARCHAR |  |
| `family_key` | VARCHAR |  |
| `family_ru` | VARCHAR |  |
| `family_uz` | VARCHAR |  |
| `brand` | VARCHAR |  |
| `size` | VARCHAR |  |
| `sort_raw` | VARCHAR |  |
| `ptype` | VARCHAR |  |
| `class_raw` | VARCHAR |  |
| `grade_sort` | VARCHAR |  |
| `grade_class` | INTEGER |  |
| `tnved` | VARCHAR |  |
| `group_ru` | VARCHAR |  |
| `group_uz` | VARCHAR |  |
| `category_ru` | VARCHAR |  |
| `category_uz` | VARCHAR |  |
| `industry_ru` | VARCHAR |  |
| `industry_uz` | VARCHAR |  |
| `unit` | VARCHAR |  |
| `unit_uz` | VARCHAR |  |
| `lot_size` | INTEGER |  |
| `currency` | VARCHAR |  |
| `basis_price` | DECIMAL(18,2) | Базисная цена за единицу на дату регистрации | Ro‘yxatdan o‘tgan kundagi bazis narx |
| `deposit_pct` | INTEGER | Залог, % от суммы сделки | Garov, bitim summasidan % |
| `delivery_basis` | VARCHAR | Базис поставки EXW/FCA/DAP/CPT | Yetkazish bazisi |
| `region_id` | VARCHAR |  |
| `region_uz` | VARCHAR |  |
| `region_ru` | VARCHAR |  |
| `warehouse_address` | VARCHAR | Адрес склада продавца | Sotuvchi ombori manzili |
| `trade_type` | VARCHAR |  |
| `contract_type` | VARCHAR |  |
| `is_export` | BOOLEAN |  |
| `contract_form` | VARCHAR |  |
| `pay_term_days` | INTEGER |  |
| `delivery_term_days` | INTEGER |  |
| `packaging` | VARCHAR | Упаковка | Qadoq |
| `seller_inn` | VARCHAR | ИНН продавца | Sotuvchi STIR |
| `reg_date` | DATE | Дата регистрации контракта | Shartnoma ro‘yxat sanasi |
| `valid_to` | DATE | Действует до | Amal qilish muddati |

## `participants`

Компании-участники (14 392): 392 продавца и 14 000 покупателей. Роль фиксирована. | Ishtirokchi kompaniyalar (14 392): 392 sotuvchi, 14 000 xaridor. Rol qat’iy.

| column | type | meaning (ru \| uz) |
|---|---|---|
| `inn` | VARCHAR | ИНН, 9 цифр, уникален | STIR, 9 raqam |
| `name` | VARCHAR | Наименование как в реестре | Nomi reyestrdagi kabi |
| `name_norm` | VARCHAR | Нормализованное имя для поиска (латиница, без кавычек) | Qidiruv uchun normallashgan nom |
| `name_core` | VARCHAR | Имя без организационно-правовой формы | Tashkiliy shaklsiz nom |
| `legal_form` | VARCHAR | ОПФ: mchj, aj, xk ... | Tashkiliy shakl |
| `region_id` | VARCHAR |  |
| `region_uz` | VARCHAR |  |
| `region_ru` | VARCHAR |  |
| `activity_ru` | VARCHAR | Вид деятельности | Faoliyat turi |
| `activity_uz` | VARCHAR |  |
| `role` | VARCHAR | seller или buyer (фиксировано) | seller yoki buyer |
| `role_ru` | VARCHAR |  |
| `role_uz` | VARCHAR |  |
| `is_vat` | BOOLEAN | Плательщик НДС | QQS to‘lovchi |

## `brokers`

Брокерские конторы (120), коды BK001–BK120. | Broker idoralari (120), kodlar BK001–BK120.

| column | type | meaning (ru \| uz) |
|---|---|---|
| `code` | VARCHAR | Код брокера BK001..BK120 | Broker kodi |
| `name` | VARCHAR | Наименование | Nomi |
| `name_norm` | VARCHAR |  |
| `name_core` | VARCHAR |  |
| `legal_form` | VARCHAR |  |
| `inn` | VARCHAR | ИНН брокера | Broker STIR |
| `region_id` | VARCHAR |  |
| `region_uz` | VARCHAR |  |
| `region_ru` | VARCHAR |  |

## `usd_rates`

Курс доллара на каждый календарный день 01.01.2022–31.10.2026, сум за 1 USD. | Har kun uchun dollar kursi, 1 USD uchun so‘m.

| column | type | meaning (ru \| uz) |
|---|---|---|
| `rate_date` | DATE | Дата | Sana |
| `rate` | DECIMAL(12,2) | Сум за 1 USD | 1 USD uchun so‘m |

## Value lists

**status_code | status_ru | status_uz**: executed | Исполнен | Bajarilgan  (431,061); annulled | Аннулирован(Не оплачен) | Bekor qilingan (to‘lanmagan)  (33,719); partial | Исполнен(Частично) | Qisman bajarilgan  (22,648); paid | Оплачен | To‘langan  (5,854); concluded | Заключен | Imzolangan (forvard/fyuchers)  (3,954); awaiting_payment | Ждёт оплату | To‘lov kutilmoqda  (1,714); new | Новый | Yangi  (964); not_signed | Не подписан | Imzolanmagan  (86)

**trade_type | trade_type_uz**: Английский аукцион | Angliya auksioni  (254,021); Двусторонний аукцион | Ikki tomonlama auksion  (235,504); Обратный аукцион | Teskari auksion  (10,475)

**contract_form**: Спот  (456,724); Форвард  (36,431); Фьючерс  (6,845)

**contract_type | is_export | currency**: Внутренний | false | UZS  (493,410); Экспортный | true | USD  (6,590)

**unit | unit_uz | lot_size**: тонна | tonna | 1  (261,629); килограмм | kilogramm | 1  (170,068); килограмм | kilogramm | 100  (33,823); литр | litr | 1000  (12,524); куб.метр | kub metr | 1  (5,509); штука | dona | 1000  (4,892); кв.метр | kvadrat metr | 1  (3,877); метр | metr | 1  (3,863); штука | dona | 12  (1,908); штука | dona | 1  (1,907)

**region_id | region_uz | region_ru**: TAS | Toshkent shahri | г. Ташкент  (98,435); NAM | Namangan viloyati | Наманганская область  (58,493); BUX | Buxoro viloyati | Бухарская область  (41,124); SAM | Samarqand viloyati | Самаркандская область  (40,118); SIR | Sirdaryo viloyati | Сырдарьинская область  (38,970); TAO | Toshkent viloyati | Ташкентская область  (35,914); QAS | Qashqadaryo viloyati | Кашкадарьинская область  (32,770); XOR | Xorazm viloyati | Хорезмская область  (28,362); AND | Andijon viloyati | Андижанская область  (24,713); FAR | Farg‘ona viloyati | Ферганская область  (24,211); SUR | Surxondaryo viloyati | Сурхандарьинская область  (21,723); NAV | Navoiy viloyati | Навоийская область  (18,904); QOR | Qoraqalpog‘iston Respublikasi | Республика Каракалпакстан  (18,895); JIZ | Jizzax viloyati | Джизакская область  (17,368)

**category_ru | category_uz**: Сельскохозяйственная продукция | Qishloq xo‘jaligi mahsulotlari  (189,213); Нефтепродукты | Neft mahsulotlari  (81,706); Продовольственные товары | Oziq-ovqat tovarlari  (71,169); Строительные материалы | Qurilish materiallari  (56,365); Полимерная продукция | Polimer mahsulotlari  (35,456); Химическая продукция | Kimyo mahsulotlari  (31,711); Металлопродукция | Metall mahsulotlari  (26,632); Промышленные товары | Sanoat tovarlari  (7,748)

**buyer_activity_ru | buyer_activity_uz**: Торговая компания | Savdo kompaniyasi  (249,742); Транспортная компания | Transport kompaniyasi  (87,210); Перерабатывающее предприятие | Qayta ishlash korxonasi  (80,747); Производственное предприятие | Ishlab chiqarish korxonasi  (69,540); Строительная компания | Qurilish kompaniyasi  (12,761)

**grade_sort**: 1  (77,310); 2  (61,426); 3  (34,162); NULL  (306,049); highest  (21,053)

**grade_class**: 1  (68,079); 2  (68,129); 3  (17,152); 4  (21,471); NULL  (325,169)

## Product catalog (family_key → product_key, with units and deal counts)

| family_key | family_ru | family_uz | product_key | product_ru | product_uz | units | deals | brands/specs (top) |
|---|---|---|---|---|---|---|---|---|
| cotton | Хлопок-сырец | Paxta xomashyosi | cotton_spot | Хлопок-сырец (спот) | Paxta xomashyosi (spot) | килограмм | 103912 |  |
| cement | Цемент | Sement | cement | Портландцемент | Portlandsement | тонна | 39146 | ЦЕМ I 42,5 Н, ЦЕМ II/А-Ш 32,5Н, ЦЕМ II/В-К 32,5 Н |
| wheat | Пшеница | Bug‘doy | wheat | Пшеница продовольственная | Oziq-ovqat bug‘doyi | тонна | 38623 |  |
| flour | Мука | Un | flour | Мука пшеничная | Bug‘doy uni | килограмм | 31906 | ХЛЕБОПЕКАРНАЯ, обогащенная витаминно-минеральной смесью |
| diesel | Дизельное топливо | Dizel yoqilg‘isi | diesel | Топливо дизельное | Dizel yoqilg‘isi | килограмм, тонна | 28747 | ДТ-Е-К5, ДТ-З-К5, ДТ-Л-К5 |
| petrol | Бензин | Benzin | petrol | Бензин автомобильный | Avtomobil benzini | тонна, литр | 25523 | АИ-80, АИ-92, АИ-95 |
| cotton | Хлопок-сырец | Paxta xomashyosi | cotton_forward | Хлопок-сырец (форвард) | Paxta xomashyosi (forvard) | килограмм | 25451 |  |
| polyethylene | Полиэтилен | Polietilen | polyethylene | Полиэтилен | Polietilen | тонна | 19480 | LL-0218, HD-6018, HD-5502, PE-3015, LD-1825 |
| lpg | Сжиженный газ | Suyultirilgan gaz | lpg | Сжиженный газ (пропан-бутан) | Suyultirilgan gaz (propan-butan) | тонна | 16059 | ПБА, ПБТ |
| rebar | Арматура | Armatura | rebar | Арматура стальная | Po‘lat armatura | тонна | 13073 |  |
| sugar | Сахар | Shakar | sugar | Сахар белый | Oq shakar | тонна | 12926 |  |
| cottonseed_oil | Хлопковое масло | Paxta yog‘i | cottonseed_oil | Масло хлопковое | Paxta yog‘i | тонна | 12492 | рафинированное дезодорированное |
| urea | Карбамид | Karbamid | urea | Карбамид | Karbamid (mochevina) | тонна | 11285 | Б |
| polypropylene | Полипропилен | Polipropilen | polypropylene | Полипропилен | Polipropilen | тонна | 9648 | PP-2310, PP-1102, PP-0350 |
| ammonium_nitrate | Аммиачная селитра | Ammiakli selitra | ammonium_nitrate | Аммиачная селитра | Ammiakli selitra | тонна | 9623 | Б |
| cottonseed_meal | Шрот хлопковый | Paxta shroti | cottonseed_meal | Шрот хлопковый | Paxta shroti (kunjara) | килограмм | 7840 | тостированный |
| cotton | Хлопок-сырец | Paxta xomashyosi | cotton_futures | Хлопок-сырец (фьючерс) | Paxta xomashyosi (fyuchers) | килограмм | 6845 |  |
| bitumen | Битум | Bitum | bitumen | Битум нефтяной | Neft bitumi | тонна | 6602 | БНД 60/90, БНД 90/130, БНД 70/100, БНД 90/130 дорожный, БНД 70/100 дорожный |
| pvc | Поливинилхлорид | Polivinilxlorid | pvc | Поливинилхлорид | Polivinilxlorid (PVX) | тонна | 6328 | SG-5, SG-7 |
| brick | Кирпич | G‘isht | brick | Кирпич керамический | Keramik g‘isht | штука | 4892 | М-100, М-125, М-150 |
| sulfuric_acid | Серная кислота | Sulfat kislota | sulfuric_acid | Кислота серная | Sulfat kislota | тонна | 4863 |  |
| sunflower_oil | Подсолнечное масло | Kungaboqar yog‘i | sunflower_oil | Масло подсолнечное | Kungaboqar yog‘i | тонна | 4827 | рафинированное |
| fuel_oil | Мазут | Mazut | fuel_oil | Мазут топочный | Mazut | тонна | 4775 | М-40, М-100 |
| copper_cathode | Медный катод | Mis katod | copper_cathode | Катод медный | Mis katod | тонна | 4750 | М0К, М00к |
| lumber | Пиломатериалы | Yog‘och materiallar | lumber | Пиломатериал обрезной | Taxta (yog‘och materiallar) | куб.метр | 3914 | обрезные хвойных пород |
| rice | Рис | Guruch | rice | Рис шлифованный | Guruch (oqlangan) | тонна | 3910 |  |
| glass | Стекло | Shisha | glass | Стекло листовое (флоат) | Tekis shisha (float) | кв.метр | 3877 |  |
| steel_sheet | Лист стальной | Po‘lat list | steel_sheet | Лист стальной горячекатаный | Issiq prokat po‘lat list | килограмм | 3815 | Ст3сп |
| yarn | Пряжа | Ip | yarn | Пряжа хлопчатобумажная | Paxta ip (kalava) | тонна | 3278 | Ne 24/1, Ne 20/1 |
| salt | Соль | Tuz | salt | Соль пищевая | Osh tuzi | тонна | 3200 |  |
| feed | Корма | Yem | feed_mix | Кормосмесь | Yem aralashmasi | тонна | 2669 | КС-65, КС-70 |
| gypsum | Гипс | Gips | gypsum | Гипс строительный | Qurilish gipsi | тонна | 2610 | Г-5, Г-6 |
| wire | Проволока | Sim | wire | Проволока арматурная | Armatura simi | килограмм | 2528 | ВР-1 |
| aluminium_profile | Алюминиевый профиль | Alyuminiy profil | aluminium_profile | Профиль алюминиевый | Alyuminiy profil | килограмм | 2466 |  |
| caustic_soda | Каустическая сода | Kaustik soda | caustic_soda | Сода каустическая | Kaustik soda | тонна | 2428 |  |
| cable | Кабель | Kabel | cable | Кабель силовой | Kuch kabeli | метр | 2005 | ВВГ 3х4, ВВГ 3х2,5 |
| enamel | Эмаль | Emal | enamel | Эмаль | Emal (bo‘yoq) | килограмм | 1978 | ПФ-115 |
| lime | Известь | Ohak | lime | Известь | Ohak | тонна | 1926 |  |
| nitrogen | Азот | Azot | nitrogen | Азот | Azot | килограмм | 1917 |  |
| water | Питьевая вода | Ichimlik suvi | water | Вода питьевая | Ichimlik suvi | штука | 1908 |  |
| bearing | Подшипник | Podshipnik | bearing | Подшипник | Podshipnik | штука | 1907 | 6210, 6205, 6312, 6305 |
| pe_pipe | Труба полиэтиленовая | Polietilen quvur | pe_pipe | Труба полиэтиленовая | Polietilen quvur | метр | 1858 |  |
| argon | Аргон | Argon | argon | Аргон газообразный | Argon gazi | куб.метр | 1595 | высший |
| feed | Корма | Yem | compound_feed | Комбикорм | Kombikorm (omuxta yem) | тонна | 595 | КС-70, КС-65 |

## Family aliases (user words → family_key)

- `aluminium_profile`: alyuminiy profil, алюминиевый профиль, профиль алюминиевый, профиль, profil, aluminium profile, aluminum profile
- `ammonium_nitrate`: selitra, селитра, ammiakli selitra, аммиачная селитра, ammoniy nitrat, ammonium nitrate, o‘g‘it, o'g'it, ўғит, удобрение, удобрения
- `argon`: argon, аргон
- `bearing`: podshipnik, подшипник, bearing
- `bitumen`: bitum, битум, neft bitumi, битум нефтяной, bitumen, бнд
- `brick`: g‘isht, g'isht, gisht, ғишт, кирпич, brick, keramik g‘isht
- `cable`: kabel, кабель, kuch kabeli, кабель силовой, cable, power cable
- `caustic_soda`: kaustik soda, каустическая сода, сода каустическая, сода, soda, caustic soda, natriy gidroksid
- `cement`: sement, цемент, портландцемент, portlandsement, cement, portland cement, sement portland
- `copper_cathode`: mis, мис, mis katod, медь, медный катод, катод медный, copper, copper cathode, м00к, м0к
- `cotton`: paxta, пахта, paxta xomashyosi, paxta xom ashyosi, хлопок, хлопок-сырец, хлопок сырец, cotton, raw cotton, chigitli paxta
- `cottonseed_meal`: shrot, шрот, kunjara, кунжара, paxta shroti, шрот хлопковый, шрот хлопковых семян, cottonseed meal, yem
- `cottonseed_oil`: paxta yog‘i, paxta yogi, paxta yog'i, пахта ёғи, хлопковое масло, масло хлопковое, cottonseed oil, yog‘, yog', ёғ, масло
- `diesel`: dizel, дизель, дизтопливо, дт, солярка, solyarka, dizel yoqilg‘isi, dizel yoqilg'isi, дизельное топливо, топливо дизельное, diesel, diesel fuel
- `enamel`: emal, эмаль, bo‘yoq, bo'yoq, бўёқ, краска, пф-115, enamel, paint
- `feed`: yem, ем, kormosmes, кормосмесь, комбикорм, kombikorm, omuxta yem, feed, корма
- `flour`: un, ун, bug‘doy uni, bug'doy uni, мука, мука пшеничная, flour, wheat flour
- `fuel_oil`: mazut, мазут, fuel oil, мазут топочный
- `glass`: shisha, шиша, стекло, oyna, стекло флоат, стекло листовое, glass, float glass
- `gypsum`: gips, гипс, qurilish gipsi, gypsum, alebastr, алебастр
- `lime`: ohak, оҳак, известь, lime, известь негашеная
- `lpg`: gaz, газ, suyultirilgan gaz, сжиженный газ, пропан, бутан, пропан-бутан, propan, butan, lpg, суг, pba, pbt, пба, пбт
- `lumber`: taxta, тахта, yog‘och, yog'och, yogoch, ёғоч, пиломатериал, пиломатериалы, доска, lumber, timber, sawn timber
- `nitrogen`: azot, азот, nitrogen
- `pe_pipe`: quvur, қувур, труба, трубы, polietilen quvur, труба пэ, трубы полиэтиленовые, pipe, pe pipe
- `petrol`: benzin, бензин, аи-80, аи-92, аи-95, ai-80, ai-92, ai-95, petrol, gasoline, yoqilg‘i, yoqilg'i
- `polyethylene`: polietilen, полиэтилен, пэ, polyethylene, pe, hd-6018, ll-0218, pe-3015
- `polypropylene`: polipropilen, полипропилен, пп, polypropylene, pp
- `pvc`: pvx, пвх, pvc, polivinilxlorid, поливинилхлорид, sg-5
- `rebar`: armatura, арматура, rebar, а500с, a500c
- `rice`: guruch, гуруч, рис, sholi, rice, рис шлифованный
- `salt`: tuz, туз, osh tuzi, соль, соль пищевая, соль поваренная, salt
- `steel_sheet`: po‘lat list, po'lat list, list, лист стальной, лист, металлопрокат, steel sheet, прокат
- `sugar`: shakar, шакар, сахар, сахар-песок, qand, sugar, white sugar
- `sulfuric_acid`: sulfat kislota, сульфат кислота, серная кислота, кислота серная, sulfuric acid, kislota, кислота
- `sunflower_oil`: kungaboqar yog‘i, kungaboqar yogi, kungaboqar yog'i, кунгабоқар ёғи, подсолнечное масло, масло подсолнечное, sunflower oil
- `urea`: karbamid, карбамид, мочевина, mochevina, urea, azotli o‘g‘it
- `water`: suv, сув, ichimlik suvi, вода, питьевая вода, вода питьевая, water
- `wheat`: bug‘doy, bug'doy, bugdoy, буғдой, бугдой, пшеница, wheat, don, galla, g‘alla, ғалла
- `wire`: sim, сим, проволока, armatura simi, проволока арматурная, wire, вр-1
- `yarn`: ip, ип, kalava, калава, пряжа, пряжа хлопковая, пряжа хлопчатобумажная, yarn, cotton yarn, ne 20/1, ne 24/1

## Macros

- `wavg_price(amount, volume)` = sum(amount)/sum(volume) — volume-weighted average unit price. Group by unit and currency.
- `fmt_num(x)` — formats a number with thousands separators.

## Example SQL

**Paxta 2025: bitimlar soni, hajm tonnada, o‘rtacha narx (butun reyestr + bekor qilinganlar alohida)**
```sql
SELECT count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,
       sum(volume_t) AS tonnes, sum(volume_t) FILTER (WHERE NOT is_annulled) AS tonnes_valid,
       wavg_price(amount, volume) AS price_per_kg_uzs, min(deal_date) AS from_date, max(deal_date) AS to_date
FROM deals WHERE family_key='cotton' AND year=2025
```
**Средняя цена АИ-92 в 2025 по единицам**
```sql
SELECT unit, currency, count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled, wavg_price(amount, volume) AS avg_price
FROM deals WHERE product_key='petrol' AND brand='АИ-92' AND year=2025 GROUP BY ALL
```
**Топ-5 покупателей цемента по сумме, 2024**
```sql
SELECT buyer_name, buyer_inn, count(*) AS n_deals, count(*) FILTER (WHERE is_annulled) AS n_annulled,
       sum(amount_uzs) AS total_uzs, sum(amount_uzs) FILTER (WHERE NOT is_annulled) AS total_uzs_valid
FROM deals WHERE product_key='cement' AND year=2024 GROUP BY ALL ORDER BY total_uzs DESC LIMIT 5
```
**Eng ko‘p bekor qilingan bitimlar qaysi brokerda (sotuvchi tomoni)**
```sql
SELECT seller_broker, seller_broker_name, count(*) AS n_deals
FROM deals WHERE is_annulled GROUP BY ALL ORDER BY n_deals DESC LIMIT 5
```
**Kechikkan yetkazishlar 2026 yil bo‘yicha oylarda**
```sql
SELECT month, count(*) AS n_deals, round(avg(delivery_delay_days),1) AS avg_days_late
FROM deals WHERE year=2026 AND is_delivery_late GROUP BY 1 ORDER BY 1
```