# UZEX AI Hackathon 2026 — Saralash bosqichi. Yechim tavsifi / Описание решения

**Prototip:** https://<DOMAIN>  (veb-xizmat, o‘rnatish va ro‘yxatdan o‘tish talab qilinmaydi; telefon va kompyuterda ishlaydi)
**Jamoa:** <Team lead>, Abubakr <...>, Azimjon <...>

---

## O‘zbekcha

### Nima qiladi
Agent birja bitimlari reyestri (500 000 bitim, 03.01.2022–30.10.2026) bo‘yicha o‘zbek (lotin/kirill) va rus tillaridagi savollarga javob beradi. Har javobda: raqam, qamrab olingan davr, qo‘llanilgan filtrlar, nechta bitimga asoslangani, bekor qilingan bitimlar soni va bajarilgan SQL so‘rov ko‘rsatiladi. Ma’lumotda javob bo‘lmasa, agent buni aniq sabab bilan aytadi (masalan, "reyestr 30.10.2026 gacha, noyabr uchun bitimlar yo‘q").

### Interfeys
Sahifa ochilganda yuqorida so‘rov qatori, pastida **Bozor paneli**: davr (3/6/12 oy, 2026, hammasi yoki ixtiyoriy oylar), tovar, hudud, valyuta (so‘m / USD — har bitim o‘z kunidagi kurs bilan) va «bekor qilinganlarsiz» filtrlari; KPI plitkalar (aylanma, bitimlar, hajm, o‘rtacha bitim, ishtirokchilar) oldingi davrga nisbatan o‘zgarish bilan; oylik aylanma va bitimlar soni grafiklari; top-10 xaridor, sotuvchi, broker (bossang savol tayyorlanadi); hududlar va tovarlar (bossang filtr qo‘llanadi); bitim holatlari ulushi va kechikkan yetkazish; CSV yuklab olish. Panel hisoblari LLM siz, parametrli SQL bilan. Har javob kartasida «Panelda ko‘rish» tugmasi javobdagi tovar/hudud/davrni panelga uzatadi.

Har javob kartasi broker uchun bir qarashda to‘liq: asosiy raqamlar, matn, grafik, keyin **Kontekst** bloki — javobdagi tovar/hudud/davr bo‘yicha LLM siz hisoblangan KPI lar (aylanma, bitimlar, hajm, eksport ulushi, kechikkan yetkazish), oylik aylanma, mavsumiylik (rangli shkala), bitim holatlari, eksport/ichki bozor va savdo turlari, mahsulot turlari yoki tovarlar (kichik bozorlar belgilanadi), narx dinamikasi yoki hududlar. «Asosni ko‘rish» tugmasi natija jadvalini, asos qatorini, bajarilgan SQL ni ochadi va CSV yuklab olish tugmasini beradi. Kartaning oxirida natijaga yaqin qo‘shimcha savollar tavsiya qilinadi (bir bosishda yuboriladi).

Javoblar bo‘limi chat emas, reyestr stoli ko‘rinishida: so‘rov qatori yuqorida, har javob raqamlangan ma’lumotnoma kartasi sifatida chiqadi. Kartada: asosiy ko‘rsatkichlar yirik raqamlarda, javob matni, jadval va grafik (oylik dinamika — chiziq, reyting — gorizontal ustunlar), qo‘llangan filtrlar (yashil) va modelning taxminlari (sariq) alohida, reyestrning qaysi davri qamrab olingani ko‘rsatkich chizig‘ida, "Asos" qatori (nechta bitim, davr, holat sanasi, SQL vaqti) va bir tugma bilan ochiladigan SQL so‘rov. Ikki model mos kelsa, kartada "Ikki model mos keldi" muhri turadi; ma’lumot bo‘lmasa, karta sariq "Ma’lumot yo‘q" muhri va sabab bilan chiqadi. Uch til tugmasi (lotin, kirill, rus), klaviatura bilan ishlash (Enter, "/" — maydonga o‘tish, ↑ — oldingi savol), sessiya jurnali va "Yangi sessiya" tugmasi (kontekstni tozalash). Telefon va kompyuterda, och va qorong‘i rejimda ishlaydi; tashqi kutubxona — faqat lokal Chart.js.

### Arxitektura
1. **Ma’lumot qatlami (DuckDB).** CSV lar bir marta tozalanadi va yagona keng `deals` jadvaliga yig‘iladi: raqamlar DECIMAL, sanalar DATE, eksport summalari bitim kunidagi kurs bilan so‘mga o‘tkazilgan, hajm = miqdor × lot, birlik narxi, to‘lov/yetkazish muddatlari, kechikish kunlari, holat bayroqlari, sotuvchi/xaridor/broker nomlari. 927 xil tovar yozuvi qo‘lda tekshirilgan katalog orqali 44 kanonik tovar va 41 oilaga bog‘langan ("Топливо дизельное" = "Дизельное топливо", "1-сорта" = "I сорт" = "первый сорт"). 14 hudud uch yozuvda, 8 holat o‘zbekcha/ruscha.
2. **Ob’ekt resolverlari.** Savoldagi so‘zlar ("paxta", "пахта", "solyarka", "Фарғона", "Sehim Savdo", "BK111", "bekor qilingan") bazadagi kalitlarga bog‘lanadi. Noaniq bo‘lsa (masalan "Toshkent" — shahar yoki viloyat; bir xil nomli ikki kompaniya) agent taxmin qilmaydi, ikkala variantni ko‘rsatadi.
3. **LLM faqat SQL yozadi va matn shablonini tuzadi, hisoblamaydi.** Gemini modeli savolni bitta SELECT so‘rovga aylantiradi. So‘rov himoya qatlamidan o‘tadi: DuckDB parseri bilan faqat bitta SELECT tekshiriladi, faqat ruxsat etilgan jadvallar, vaqt chegarasi, faqat o‘qish rejimidagi ulanish, tashqi fayllarga kirish yopiq.
4. **Javobni tekshirish.** (a) Ikkinchi, boshqa model xuddi shu savolga mustaqil SQL yozadi; natijalar farq qilsa, asosiy model ikkalasini solishtirib tanlaydi. (b) Model javob matnida raqam yoki kompaniya nomini o‘zi yoza olmaydi: u faqat {{qator.ustun}} belgilarini qo‘yadi, qiymatlarni dastur SQL natijasidan to‘ldiradi. Model o‘zi yozgan raqam yoki nom topilsa, matn rad etiladi; ikkinchi urinishdan keyin javob to‘g‘ridan-to‘g‘ri natijadan tuziladi. (c) Rad etish matnlari ham tekshiriladi.
5. **Birja ta’riflari.** O‘rtacha narx hajmga tortilgan (Σsumma ÷ Σhajm), birlik va valyuta bo‘yicha alohida. Bitimlar soni butun reyestr bo‘yicha, bekor qilinganlar soni alohida. Summa, hajm va narx avval bekor qilinganlarsiz beriladi, chunki README bo‘yicha bunday bitim amalga oshmagan, yonida hammasi bilan birga ko‘rsatkich ham ko‘rsatiladi. Kechikkan yetkazish: to‘lov sanasi + yetkazish muddati.

### Tekshirish
- 200 avtomatik test: README dagi sonlar, mustaqil pandas hisob bilan taqqoslash, 100% katalog qamrovi, resolverlar, SQL himoyasi, javob shablonini tekshirish.
- 86 ta qiyin savol, kutilgan javoblari bazadan emas, xom CSV dan pandas bilan hisoblangan. Ulardan 44 tasi prompt sozlangandan keyin yozilgan, tizim ularni oldin ko‘rmagan. Oxirgi natija: 85/86. Noto‘g‘ri SQL qo‘yib o‘tkazilgan 10 ta sinovda solishtirish 10 tasida to‘g‘ri so‘rovni tanladi.
- Har savol-javob loglanadi (savol, SQL, qatorlar soni, vaqt).

### Texnologiyalar
Python 3.12, DuckDB, FastAPI, Gemini API (google-genai), rapidfuzz, Docker + Caddy (HTTPS), Oracle Cloud.

### Chegaralar (halol)
Ma’lumotlar o‘ylab topilgan va 30.10.2026 holatiga; haqiqiy kompaniyalar, sabab-oqibat, prognoz, 2026 noyabrdan keyingi davr haqida javob yo‘q. Eksport faqat USD da (mis katod va ip); fyuchers faqat paxta.

---

## Русский

### Что делает
Агент отвечает на вопросы по реестру биржевых сделок (500 000 сделок, 03.01.2022–30.10.2026) на русском и узбекском (латиница/кириллица). Каждый ответ содержит: число, охваченный период, применённые фильтры, количество сделок в основе, число аннулированных сделок и выполненный SQL-запрос. Если в данных нет ответа, агент прямо говорит об этом с причиной.

### Интерфейс
При открытии страницы сверху строка запроса, ниже **Панель рынка**: фильтры периода (3/6/12 мес, 2026, всё или произвольные месяцы), товара, региона, валюты (сум / USD — каждая сделка по курсу на свою дату) и «без аннулированных»; KPI-плитки (оборот, сделки, объём, средняя сделка, участники) с изменением к предыдущему периоду; графики оборота и числа сделок по месяцам; топ-10 покупателей, продавцов, брокеров (клик готовит вопрос); регионы и товары (клик применяет фильтр); доли состояний сделок и поставки с опозданием; выгрузка CSV. Расчёты панели без LLM, параметризованным SQL. На каждой карточке ответа кнопка «Открыть в панели» передаёт товар/регион/период ответа в панель.

Каждая карточка ответа полна для брокера с первого взгляда: ключевые числа, текст, график, затем блок **Контекст** — рассчитанные без LLM по товару/региону/периоду ответа KPI (оборот, сделки, объём, доля экспорта, поставки с опозданием), оборот по месяцам, сезонность (цветовая шкала), состояния сделок, экспорт/внутренний рынок и типы торгов, виды продукции или товары (малые рынки помечены), динамика цены или регионы. Кнопка «Показать основание» открывает таблицу результата, строку основания, выполненный SQL и кнопку выгрузки CSV. В конце карточки — рекомендуемые дополнительные вопросы (отправляются одним кликом).

Раздел ответов построен не как чат, а как рабочий стол реестра: строка запроса сверху, каждый ответ — пронумерованная карточка-справка. В карточке: ключевые показатели крупными числами, текст ответа, таблица и график (помесячная динамика — линия, рейтинг — горизонтальные столбцы), отдельно применённые фильтры (зелёная метка) и допущения модели (жёлтая), полоса охвата — какой период реестра лёг в основу, строка «Основание» (число сделок, период, дата состояния, время SQL) и SQL-запрос, открывающийся одной кнопкой. Если две модели сошлись, на карточке стоит штамп «Две модели сошлись»; если данных нет — жёлтый штамп «Нет данных» с причиной. Три языка интерфейса (латиница, кириллица, русский), работа с клавиатуры (Enter, «/» — к полю ввода, ↑ — предыдущий вопрос), журнал сессии и кнопка «Новая сессия» (сброс контекста). Работает на телефоне и компьютере, в светлой и тёмной теме; единственная внешняя библиотека — локальный Chart.js.

### Архитектура
1. **Слой данных (DuckDB).** CSV очищаются один раз и собираются в широкую таблицу `deals`: суммы DECIMAL, даты DATE, экспорт пересчитан в сумы по курсу на дату сделки, объём = количество × лот, цена за единицу, сроки оплаты/поставки, дни просрочки, флаги состояний, наименования продавца/покупателя/брокеров. 927 написаний товаров сведены вручную проверенным каталогом к 44 каноническим товарам и 41 семейству; 14 регионов в трёх написаниях; 8 состояний на двух языках.
2. **Резолверы сущностей.** Слова вопроса ("хлопок", "paxta", "солярка", "Фарғона", "Sehim Savdo", "BK111", "аннулированные") привязываются к ключам базы. При неоднозначности (Ташкент — город или область; две компании с одинаковым названием) агент не угадывает, а показывает оба варианта.
3. **LLM только пишет SQL и шаблон текста, но не считает.** Модель Gemini превращает вопрос в один SELECT. Запрос проходит защитный слой: парсер DuckDB проверяет, что это один SELECT, только разрешённые таблицы, таймаут, соединение только для чтения, доступ к внешним файлам закрыт.
4. **Проверка ответа.** (а) Вторая, другая модель независимо пишет SQL на тот же вопрос; если результаты расходятся, основная модель сравнивает их и выбирает. (б) Модель не может сама написать в ответе число или название компании: она ставит только метки {{строка.столбец}}, значения подставляет программа из результата SQL. Если модель написала число или название сама, текст отклоняется; после второй попытки ответ собирается напрямую из результата. (в) Тексты отказов тоже проверяются.
5. **Биржевые определения.** Средняя цена взвешена по объёму (Σсумма ÷ Σобъём), отдельно по единице и валюте. Число сделок считается по всему реестру, аннулированные показываются отдельно. Сумма, объём и цена сначала даются без аннулированных, так как по README такая сделка не состоялась, рядом показан показатель со всеми сделками. Опоздание поставки: дата оплаты + срок поставки.

### Проверка
200 автотестов (контрольные числа из README, независимый пересчёт в pandas, 100% покрытие каталога, резолверы, SQL-защита, проверка шаблона ответа). 86 сложных вопросов с эталонами, посчитанными из исходных CSV, а не из базы; 44 из них написаны после настройки промпта. Последний результат: 85/86. В 10 тестах с подставленным неверным SQL сверка 10 раз выбрала верный запрос.

### Технологии
Python 3.12, DuckDB, FastAPI, Gemini API, rapidfuzz, Docker + Caddy (HTTPS), Oracle Cloud.

### Ограничения
Данные вымышлены и зафиксированы на 30.10.2026; нет ответов о реальных компаниях, причинах, прогнозах и периоде после 30.10.2026. Экспорт только в USD (медный катод, пряжа); фьючерсы только по хлопку.
