# UZEX AI Hackathon 2026 — Saralash bosqichi. Yechim tavsifi / Описание решения

**Prototip:** https://<DOMAIN>  (veb-xizmat, o‘rnatish va ro‘yxatdan o‘tish talab qilinmaydi; telefon va kompyuterda ishlaydi)
**Jamoa:** <Team lead>, Abubakr <...>, Azimjon <...>

---

## O‘zbekcha

### Nima qiladi
Agent birja bitimlari reyestri (500 000 bitim, 03.01.2022–30.10.2026) bo‘yicha o‘zbek (lotin/kirill) va rus tillaridagi savollarga javob beradi. Har javobda: raqam, qamrab olingan davr, qo‘llanilgan filtrlar, nechta bitimga asoslangani, bekor qilingan bitimlar soni va bajarilgan SQL so‘rov ko‘rsatiladi. Ma’lumotda javob bo‘lmasa, agent buni aniq sabab bilan aytadi (masalan, "reyestr 30.10.2026 gacha, noyabr uchun bitimlar yo‘q").

### Arxitektura
1. **Ma’lumot qatlami (DuckDB).** CSV lar bir marta tozalanadi va yagona keng `deals` jadvaliga yig‘iladi: raqamlar DECIMAL, sanalar DATE, eksport summalari bitim kunidagi kurs bilan so‘mga o‘tkazilgan, hajm = miqdor × lot, birlik narxi, to‘lov/yetkazish muddatlari, kechikish kunlari, holat bayroqlari, sotuvchi/xaridor/broker nomlari. 927 xil tovar yozuvi qo‘lda tekshirilgan katalog orqali 44 kanonik tovar va 41 oilaga bog‘langan ("Топливо дизельное" = "Дизельное топливо", "1-сорта" = "I сорт" = "первый сорт"). 14 hudud uch yozuvda, 8 holat o‘zbekcha/ruscha.
2. **Ob’ekt resolverlari.** Savoldagi so‘zlar ("paxta", "пахта", "solyarka", "Фарғона", "Sehim Savdo", "BK111", "bekor qilingan") bazadagi kalitlarga bog‘lanadi. Noaniq bo‘lsa (masalan "Toshkent" — shahar yoki viloyat; bir xil nomli ikki kompaniya) agent taxmin qilmaydi, ikkala variantni ko‘rsatadi.
3. **LLM faqat SQL yozadi va matn tuzadi, hisoblamaydi.** Gemini modeli savolni bitta SELECT so‘rovga aylantiradi (strukturali JSON: SQL, taxminlar, filtrlar, javobsizlik sababi). So‘rov himoya qatlamidan o‘tadi: faqat SELECT, faqat ruxsat etilgan jadvallar, LIMIT, ILIKE, vaqt chegarasi, faqat o‘qish rejimidagi ulanish.
4. **Javobni tekshirish.** Javob matni faqat SQL natijasi qatorlaridan tuziladi. Dastur javobdagi har bir raqamni natija qatorlari bilan solishtiradi; natijada bo‘lmagan raqam topilsa, javob qayta yoziladi, yana bo‘lmasa deterministik shablon javob beriladi. Shu sababli agent raqam o‘ylab topa olmaydi.
5. **Birja ta’riflari.** O‘rtacha narx — hajmga tortilgan (Σsumma ÷ Σhajm), birlik va valyuta bo‘yicha alohida; standart populyatsiya — butun reyestr, bekor qilinganlar soni va "bekor qilinganlarsiz" ko‘rsatkich alohida beriladi; muddati o‘tgan to‘lov — bitim sanasi + to‘lov muddati; kechikkan yetkazish — to‘lov sanasi + yetkazish muddati.

### Tekshirish
- 181 avtomatik test: README dagi sonlar, mustaqil pandas hisob bilan taqqoslash, 100% katalog qamrovi, resolverlar, SQL himoyasi.
- 17 ta "oltin savol" ikki tilda (jami 34) LLM orqali o‘tkaziladi, kutilgan raqamlar bilan solishtiriladi; natijalar `data/logs` da saqlanadi.
- Har savol-javob loglanadi (savol, SQL, qatorlar soni, vaqt).

### Texnologiyalar
Python 3.12, DuckDB, FastAPI, Gemini API (google-genai), rapidfuzz, Docker + Caddy (HTTPS), Oracle Cloud.

### Chegaralar (halol)
Ma’lumotlar o‘ylab topilgan va 30.10.2026 holatiga; haqiqiy kompaniyalar, sabab-oqibat, prognoz, 2026 noyabrdan keyingi davr haqida javob yo‘q. Eksport faqat USD da (mis katod va ip); fyuchers faqat paxta.

---

## Русский

### Что делает
Агент отвечает на вопросы по реестру биржевых сделок (500 000 сделок, 03.01.2022–30.10.2026) на русском и узбекском (латиница/кириллица). Каждый ответ содержит: число, охваченный период, применённые фильтры, количество сделок в основе, число аннулированных сделок и выполненный SQL-запрос. Если в данных нет ответа, агент прямо говорит об этом с причиной.

### Архитектура
1. **Слой данных (DuckDB).** CSV очищаются один раз и собираются в широкую таблицу `deals`: суммы DECIMAL, даты DATE, экспорт пересчитан в сумы по курсу на дату сделки, объём = количество × лот, цена за единицу, сроки оплаты/поставки, дни просрочки, флаги состояний, наименования продавца/покупателя/брокеров. 927 написаний товаров сведены вручную проверенным каталогом к 44 каноническим товарам и 41 семейству; 14 регионов в трёх написаниях; 8 состояний на двух языках.
2. **Резолверы сущностей.** Слова вопроса ("хлопок", "paxta", "солярка", "Фарғона", "Sehim Savdo", "BK111", "аннулированные") привязываются к ключам базы. При неоднозначности (Ташкент — город или область; две компании с одинаковым названием) агент не угадывает, а показывает оба варианта.
3. **LLM только пишет SQL и формулирует текст, но не считает.** Модель Gemini превращает вопрос в один SELECT (структурированный JSON: SQL, допущения, фильтры, причина отказа). Запрос проходит защитный слой: только SELECT, только разрешённые таблицы, LIMIT, ILIKE, таймаут, соединение только для чтения.
4. **Проверка ответа.** Текст формируется только из строк результата. Программа сверяет каждое число в ответе со строками результата; если число не найдено — ответ переписывается, при повторной ошибке выдаётся детерминированный шаблон. Поэтому агент не может выдумать цифру.
5. **Биржевые определения.** Средняя цена — взвешенная по объёму (Σсумма ÷ Σобъём), отдельно по единице и валюте; базовая выборка — весь реестр, отдельно показываются число аннулированных и показатель "без аннулированных"; просрочка оплаты — дата сделки + срок оплаты; опоздание поставки — дата оплаты + срок поставки.

### Проверка
181 автотест (контрольные числа из README, независимый пересчёт в pandas, 100% покрытие каталога, резолверы, SQL-защита); 34 «золотых вопроса» на двух языках прогоняются через LLM и сверяются с эталонными числами; каждый вопрос-ответ логируется.

### Технологии
Python 3.12, DuckDB, FastAPI, Gemini API, rapidfuzz, Docker + Caddy (HTTPS), Oracle Cloud.

### Ограничения
Данные вымышлены и зафиксированы на 30.10.2026; нет ответов о реальных компаниях, причинах, прогнозах и периоде после 30.10.2026. Экспорт только в USD (медный катод, пряжа); фьючерсы только по хлопку.
