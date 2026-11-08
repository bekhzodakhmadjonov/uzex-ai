"""LLM layer: Claude generates SQL (structured JSON) and phrases the answer.

Two calls per question:
  plan_sql(...)   -> {"sql", "assumptions", "filters_human", "unanswerable", "reason", "chart", "clarify"}
  compose(...)    -> answer text in the user's language, using ONLY numbers present in the result.
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol

import anthropic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DOC = os.path.join(ROOT, "docs", "schema_for_llm.md")
MODEL = os.environ.get("UZEX_MODEL", "claude-opus-5-5")
SQL_EFFORT = os.environ.get("UZEX_SQL_EFFORT", "medium")
ANSWER_EFFORT = os.environ.get("UZEX_ANSWER_EFFORT", "low")

LANG_NAME = {"uz": "Uzbek (Latin script)", "uz_cyr": "Uzbek (Cyrillic script)", "ru": "Russian", "en": "English"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "One DuckDB SELECT. Empty string if unanswerable or clarification needed."},
        "assumptions": {"type": "array", "items": {"type": "string"}, "description": "Interpretation choices made, in the user's language, short."},
        "filters_human": {"type": "array", "items": {"type": "string"}, "description": "Human-readable list of filters applied (period, product, region, status...), in the user's language."},
        "unanswerable": {"type": "boolean", "description": "True when the data cannot answer the question at all (field does not exist, period outside 2022-01-03..2026-10-30, fictional/real-world knowledge)."},
        "reason": {"type": "string", "description": "If unanswerable: why, in the user's language. Else empty."},
        "clarify": {"type": "string", "description": "A short clarifying question ONLY when ambiguity makes the answer meaningless; otherwise empty (prefer answering with a stated assumption)."},
        "chart": {"type": "object", "properties": {"type": {"type": "string", "enum": ["none", "line", "bar"]}, "x": {"type": "string"}, "y": {"type": "string"}},
                  "required": ["type", "x", "y"], "additionalProperties": False},
    },
    "required": ["sql", "assumptions", "filters_human", "unanswerable", "reason", "clarify", "chart"],
    "additionalProperties": False,
}

_SYSTEM_RULES = """You are the SQL planner of an assistant that answers questions about the UZEX commodity exchange deal registry.
You write ONE DuckDB SELECT per question. You never compute numbers yourself; SQL does.

Output JSON per the schema. Rules:
1. Use only tables deals, contracts, participants, brokers, usd_rates, meta. Prefer `deals` alone (all lookups are already joined).
2. Always include a row count (count(*) AS n_deals) in aggregate queries, and the period actually covered (min(deal_date), max(deal_date)) when the question is about a period. For "top N" questions, return the metric and the count per row and keep ties at the cut: `QUALIFY rank() OVER (ORDER BY metric DESC) <= N`, then ORDER BY the metric DESC and the name, so a tie for place N returns every tied row instead of dropping one at random.
3. Population. Counting deals ("how many deals"): count every registry row and add `count(*) FILTER (WHERE is_annulled) AS n_annulled`. A count of different companies (count(DISTINCT buyer_inn)) also covers every registry row, with no second figure: never add a count(DISTINCT ...) FILTER (WHERE NOT is_annulled) column unless the user asks to exclude annulled deals. Money, physical volume and average price: an annulled deal did not take place, so the main figure excludes annulled deals, and you ALWAYS return the all-status figure next to it, e.g. `sum(amount_uzs) FILTER (WHERE NOT is_annulled) AS total_uzs_valid, sum(amount_uzs) AS total_uzs_all`. Rank by the valid figure. If the user explicitly asks for only one population, return only that one and say so in assumptions. A share or change of deal COUNTS ("доля сделок", "от числа сделок", "bitimlar ulushi", growth in the number of deals) is a count: its main figure uses every registry row as the denominator/base, named *_pct_all or pct_all; a version over non-annulled deals only may follow as *_pct_valid. A change between periods returns both period values next to the change.
4. Average price, per unit and currency: `sum(amount) FILTER (WHERE NOT is_annulled) / sum(volume) FILTER (WHERE NOT is_annulled) AS avg_price_valid, wavg_price(amount, volume) AS avg_price_all`, GROUP BY unit, currency. Never avg(unit_price). Never mix units or currencies in one figure, and when a product trades in several units or currencies return one row for each.
4a. A ranking of goods ("qaysi tovar", "какой товар", "most traded product") groups by family_key with family_uz and family_ru: cotton sold spot, forward and as futures is one commodity. Split by product_key only when the question names a form or asks for the split.
4b. Pick the table the question is about. Contracts ("kontrakt", "контракт", contract terms) -> `contracts`, count contract_no there. Companies, VAT payers, roles ("ishtirokchi", "участник", "QQS", "НДС") -> `participants`, e.g. `count(*) FILTER (WHERE is_vat)`. Brokers as entities -> `brokers`. Dollar rate -> `usd_rates`. Deals -> `deals`. Do not answer a contract or participant question by counting deals.
5. Filters by entity: use the resolved keys from the hints (family_key, product_key, brand, region_id, status_code, inn, broker code). If the user names a product family (paxta/хлопок), filter family_key; add product_key / grade_sort / grade_class / brand only when the question specifies them. If no key was resolved, fall back to product_raw ILIKE '%...%' with the Russian stem.
5b. A company is found by inn: when a hint gives an exact company, filter seller_inn or buyer_inn by that inn, never by name. Without a hint, search `participants.name_norm` (Latin, lower case, no quotes), for example `buyer_inn IN (SELECT inn FROM participants WHERE name_norm ILIKE '%zoket invest%')`, because names are stored in either script and one name can sit inside another. A hint marked "NOT an exact match" lists different companies: never answer for them. Set unanswerable=true and in `reason` say the registry has no participant with that name.
5f. An inn hint marked "NOT a registered participant" means the registry has no company with that inn: set unanswerable=true and say so in `reason`; do not answer 0 deals.
5c. region_id, region_uz and region_ru are the SELLER'S warehouse region. For buyers from a region ("Farg‘ona xaridorlari", "покупатели из Ферганы") use buyer_region_id. A plain "deals in a region" is the seller's region.
5e. A price exactly at the start price: `is_at_start_price`. Never test `price_vs_start_pct = 0`, that column is rounded and also catches prices slightly off the start.
5d. Late delivery: is_delivery_late covers only deals already delivered. Deals paid but not delivered with the term expired are `status_code='paid' AND delivery_due_date < DATE '2026-10-30'`. A term ending on 2026-10-30 has not expired yet, as the registry treats it. If the user asks about all overdue deliveries, return both counts in separate columns. The delivery term runs from the payment date (delivery_due_date = pay_date + term); say so in assumptions whenever the answer is about late delivery.
6. Dates: data covers 2022-01-03..2026-10-30; 2026 is incomplete; status is as of 2026-10-30. "last month" = October 2026, "this year" = 2026 — state it in assumptions. Period outside the data → unanswerable=true with the reason (do not return an empty query).
7. Ambiguity: prefer answering with the most likely interpretation + assumption; use `clarify` only when the interpretations give completely different answers and the hints say the entity is ambiguous (e.g. several companies with the same core name). For 'Toshkent' alone, return BOTH regions (group by region_uz).
8. Set unanswerable=true for things the data does not hold: directors, owners, phones, addresses, real-world events, causes ("why"), forecasts, data after 2026-10-30, other assets or countries. Also for value judgements with no field behind them ("most reliable", "best", "trustworthy", "ishonchli", "надёжный"): do not invent a metric for them; in `reason` say the registry has no such rating and name what it does hold, such as deal counts and annulled deals.
8b. The `reason` text states only what this registry does or does not contain. Make no claim about the outside world (never say something "is not traded on the exchange"; say the registry has no such deals). Never name tables, columns or SQL in it. Never blame technical limits.
8c. A "why" question can rest on a false premise. If what it assumes cannot be in the registry (export deals exist only for copper cathode and cotton yarn; futures only for cotton), `reason` must first correct the premise, for example "the registry has no sugar export deals at all; export deals are only copper cathode and cotton yarn", and then say causes are not recorded.
9. Follow-up questions: reuse the filters of the previous question in the conversation unless the user changes them.
9b. Words like "tuzilgan", "tuzildi", "заключено", "заключённых", "совершено", "bo‘lgan" in a question mean "deals made / in the registry" — do NOT filter by status_code='concluded' (that status means a signed forward/futures awaiting payment) unless the user explicitly names the status ("holati Imzolangan", "статус Заключен").
10. Keep SQL readable: aliases in English snake_case; ORDER BY the metric; LIMIT ≤ 200. ILIKE, never LIKE. No regex \\b.
11. chart: 'line' when the result is a time series (x = year/month/deal_date, y = numeric), 'bar' for top-N / category breakdowns with ≤ 20 rows, else 'none'.
12. assumptions and filters_human must be written in the user's language ({lang}) for a business reader. Keep them short (max 6 items). Never put column names, keys, codes or SQL in them.
"""

_COMPOSE_RULES = """You write the answer for an exchange analyst, in {lang}, as a TEXT TEMPLATE.
The application fills in the values, so you never type them.
- Write every value from the rows as a placeholder {{row.column}}, e.g. {{0.n_deals}} or {{2.buyer_name}}: numbers, dates, company names, product names, regions, codes.
- Do not type any digit and do not type any company name yourself. The only exception is a number the user wrote in the question, such as a year or a grade.
- The RESULT ROWS show what each placeholder will print. Use only columns that exist there, and {{row.column}} is the only placeholder form. A value the rows do not hold but the user wrote, such as a brand or a year, is typed as plain text. Never compute, round or compare values yourself.
- A *_all figure is the total INCLUDING annulled deals. It is never the annulled amount and never part of another figure. n_annulled is a number of deals. Only when the rows hold both a *_valid and a *_all column, word the pair like this, naming what the figure is and with the unit it has (a verb that fits: sold, bought, turnover; money is not "sold"):
  uz: {{0.x_valid}} (bekor qilinganlarsiz), bekor qilinganlar bilan birga {{0.x_all}}. Jami {{0.n_deals}} ta bitim, shundan {{0.n_annulled}} tasi bekor qilingan.
  uz_cyr: {{0.x_valid}} (бекор қилинганларсиз), бекор қилинганлар билан бирга {{0.x_all}}. Жами {{0.n_deals}} та битим, шундан {{0.n_annulled}} таси бекор қилинган.
  ru: {{0.x_valid}} без аннулированных, {{0.x_all}} включая аннулированные. Всего сделок: {{0.n_deals}}, из них аннулировано: {{0.n_annulled}}.
  en: {{0.x_valid}} excluding annulled deals, {{0.x_all}} including them. Total deals: {{0.n_deals}}, of which annulled: {{0.n_annulled}}.
  A plain deal count (n_deals, deals_2024) includes annulled deals: never write "excluding annulled" next to it.
- Lead with the direct answer in one sentence. Do not repeat the date range or the period columns, the application adds them. Use correct grammar around placeholders, for example "за тонну", "tonna uchun". A placeholder prints in the nominative, so in Russian write around it ("месяц: {{0.month}}", not "в {{0.month}}"; "за тонну" as a word, not "за {{0.unit}}"). When the rows hold both a valid figure (*_valid) and an all-status figure (*_all), give the valid one first and then say what it is including annulled deals. For a count of deals or companies the whole-registry figure comes first. When the rows hold n_annulled above zero, say how many of the deals are annulled; say nothing about a zero.
- Name the subject of the question as the user wrote it (the inn, broker code or product, typed as plain text from the question). A placeholder that prints a count never stands where a name, inn or code belongs. Keep every qualifier of the filter in the sentence: "on both sides", "seller side", "export", the status.
- A percentage placeholder (column ending in pct, *_pct_all, *_pct_valid) already prints with "%": never add "процентов", "foiz" or a second "%" after it. For a share or change of deal counts, the figure over all deals (*_pct_all, pct_all) comes first.
- When the rows hold more rows than the N the user asked for because of a tie, still give every row its own line, and end the line of each tied row with the place it shares, e.g. "(делит 3-е место)", "(3-o‘rinni bo‘lishadi)".
- State only what the rows show. Add no source (central bank, official rate), cause, trend or comparison that is not a value in the rows.
- For several rows, give each row its own line, up to 10 lines.
- Name units and currency in words in the answer language (uz: so‘m, tonna, litr; ru: сум, тонна, литр; dollar, доллар). Never write codes like UZS or USD.
- Mention an assumption only if it changes the meaning, for example 'Toshkent' read as the city.
- Exchange vocabulary: uz: bitim, aylanma, o‘rtacha narx, bekor qilingan; ru: сделка, оборот, средняя цена, аннулированные. In Uzbek Cyrillic use only Cyrillic letters.
- Plain text, no markdown, no headings, at most 60 words. Never mention SQL, columns or tables. Do not write a basis or source line, the application adds it.
"""

_ADJUDICATE_RULES = """Two SQL queries were written independently for the same question about a commodity exchange deal registry, and their results differ.
Decide which one answers the question as asked. Check: the right table, the right population (a deal count includes all deals, money and volume exclude annulled deals unless asked), the right product and period filters, no mixing of units or currencies.
One option may refuse to answer instead of running a query. Choose the refusal when the question asks for something the registry does not record: causes, forecasts, ratings such as "most reliable", real-world facts, or a company or product that the other option only matched approximately or replaced with something else. Choose the query when the registry really holds the answer and the query computes what was asked. Never accept a refusal that blames technical limits.
Return JSON: {"choice": "A" or "B", "reason": "short"}.
"""

ADJUDICATE_SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["A", "B"]}, "reason": {"type": "string"}},
                     "required": ["choice", "reason"], "additionalProperties": False}


def adjudicate_prompt(question: str, a: dict, b: dict) -> str:
    return f"QUESTION: {question}\n\nA SQL:\n{a['sql']}\nA ROWS: {a['rows'][:3000]}\n\nB SQL:\n{b['sql']}\nB ROWS: {b['rows'][:3000]}"


class Planner(Protocol):
    def plan(self, question: str, lang: str, hints_text: str, history: list[dict[str, str]], error: str | None = None) -> dict[str, Any]: ...
    def compose(self, question: str, lang: str, sql: str, assumptions: list[str], rows_json: str, n_rows: int, extra: str = "") -> str: ...
    def adjudicate(self, question: str, a: dict, b: dict) -> str: ...


def _load_schema_doc() -> str:
    with open(SCHEMA_DOC, encoding="utf-8") as f:
        return f.read()


class ClaudePlanner:
    name = "claude"

    def __init__(self, model: str = MODEL):
        self.client = anthropic.Anthropic(timeout=60.0, max_retries=1)
        self.model = model
        self.schema_doc = _load_schema_doc()

    # ---- SQL planning ------------------------------------------------------
    def plan(self, question, lang, hints_text, history, error=None):
        system = [
            {"type": "text", "text": _SYSTEM_RULES.replace("{lang}", LANG_NAME.get(lang, lang))},
            {"type": "text", "text": "# DATABASE DOCUMENTATION\n\n" + self.schema_doc, "cache_control": {"type": "ephemeral"}},
        ]
        msgs: list[dict[str, Any]] = []
        for h in history[-3:]:
            msgs.append({"role": "user", "content": h["question"]})
            msgs.append({"role": "assistant", "content": json.dumps({"sql": h.get("sql", ""), "answer": h.get("answer", "")[:300]}, ensure_ascii=False)})
        user = f"QUESTION ({LANG_NAME.get(lang, lang)}): {question}\n\nRESOLVED ENTITY HINTS:\n{hints_text}"
        if error:
            user += f"\n\nYOUR PREVIOUS SQL FAILED OR WAS REJECTED. Error: {error}\nFix it and return corrected JSON."
        msgs.append({"role": "user", "content": user})
        resp = self.client.messages.create(
            model=self.model, max_tokens=4000, system=system, messages=msgs,
            output_config={"effort": SQL_EFFORT, "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            return {"sql": "", "assumptions": [], "filters_human": [], "unanswerable": True, "reason": "refused", "clarify": "", "chart": {"type": "none", "x": "", "y": ""}}
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        data["_usage"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
                          "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0)}
        return data

    # ---- answer composition ---------------------------------------------
    def compose(self, question, lang, sql, assumptions, rows_json, n_rows, extra=""):
        system = _COMPOSE_RULES.replace("{lang}", LANG_NAME.get(lang, lang))
        user = (f"QUESTION: {question}\n\nSQL RUN:\n{sql}\n\nASSUMPTIONS: {json.dumps(assumptions, ensure_ascii=False)}\n\n"
                f"RESULT ROWS ({n_rows} rows, showing up to 30):\n{rows_json}\n{extra}")
        resp = self.client.messages.create(
            model=self.model, max_tokens=1200, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": ANSWER_EFFORT},
        )
        if resp.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def adjudicate(self, question, a, b):
        resp = self.client.messages.create(
            model=self.model, max_tokens=1500, system=_ADJUDICATE_RULES,
            messages=[{"role": "user", "content": adjudicate_prompt(question, a, b)}],
            output_config={"effort": SQL_EFFORT, "format": {"type": "json_schema", "schema": ADJUDICATE_SCHEMA}},
        )
        text = next(b_.text for b_ in resp.content if b_.type == "text")
        return json.loads(text)["choice"]
