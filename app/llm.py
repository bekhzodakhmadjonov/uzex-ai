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
2. Always include a row count (count(*) AS n_deals) in aggregate queries, and the period actually covered (min(deal_date), max(deal_date)) when the question is about a period. For "top N" questions, return N rows with the metric and the count per row.
3. Population: by default use the WHOLE registry (all statuses) — the registry counts every concluded deal, and the committee computes from the registry. ALWAYS add `count(*) FILTER (WHERE is_annulled) AS n_annulled`; for money/volume questions also add the valid-only figure next to the total, e.g. `sum(amount_uzs) FILTER (WHERE NOT is_annulled) AS amount_uzs_valid`, so the answer can say "of which N annulled (not paid)". Exclude annulled deals from the main figure ONLY when the user asks for executed / actual / completed deals ("amalga oshgan", "bajarilgan", "исполненных", "фактически", "состоявшихся") or says "bekor qilinganlarsiz" / "без аннулированных" — then say so in assumptions.
4. Average price: wavg_price(amount, volume) grouped by unit and currency, over the same population as rule 3. Never avg(unit_price). Never mix units.
5. Filters by entity: use the resolved keys from the hints (family_key, product_key, brand, region_id, status_code, inn, broker code). If the user names a product family (paxta/хлопок), filter family_key; add product_key / grade_sort / grade_class / brand only when the question specifies them. If no key was resolved, fall back to product_raw ILIKE '%...%' with the Russian stem.
6. Dates: data covers 2022-01-03..2026-10-30; 2026 is incomplete; status is as of 2026-10-30. "last month" = October 2026, "this year" = 2026 — state it in assumptions. Period outside the data → unanswerable=true with the reason (do not return an empty query).
7. Ambiguity: prefer answering with the most likely interpretation + assumption; use `clarify` only when the interpretations give completely different answers and the hints say the entity is ambiguous (e.g. several companies with the same core name). For 'Toshkent' alone, return BOTH regions (group by region_uz).
8. Questions about things not in the data (director names, phone numbers, real-world events, reasons/causes, data after 2026-10-30, forecasts) → unanswerable=true with a short reason.
9. Follow-up questions: reuse the filters of the previous question in the conversation unless the user changes them.
9b. Words like "tuzilgan", "tuzildi", "заключено", "заключённых", "совершено", "bo‘lgan" in a question mean "deals made / in the registry" — do NOT filter by status_code='concluded' (that status means a signed forward/futures awaiting payment) unless the user explicitly names the status ("holati Imzolangan", "статус Заключен").
10. Keep SQL readable: aliases in English snake_case; ORDER BY the metric; LIMIT ≤ 200. ILIKE, never LIKE. No regex \\b.
11. chart: 'line' when the result is a time series (x = year/month/deal_date, y = numeric), 'bar' for top-N / category breakdowns with ≤ 20 rows, else 'none'.
12. assumptions and filters_human must be written in the user's language ({lang}). Keep them short (max 6 items).
"""

_COMPOSE_RULES = """You write the final answer for an exchange analyst, in {lang}.
You are given the question, the SQL that was run, the assumptions, and the result rows (JSON, already formatted numbers as strings next to raw values).
Rules:
- Use ONLY numbers that appear in the result rows (or the row count). Copy them exactly as formatted in the `_fmt` fields. Do not compute, estimate, round differently, convert or extrapolate.
- Lead with the direct answer in one sentence. Then 1–3 short sentences of context: the period covered, units and currency, the number of deals the answer is based on, and — when the rows contain n_annulled or a *_valid figure — how many of them are annulled (not paid) and the valid-only figure.
- If the result has 0 rows, say clearly that the registry has no deals matching these filters, name the filters, and do not invent anything.
- Mention the assumptions briefly if they change the meaning (e.g. 'Toshkent' interpreted as the city).
- Use the exchange vocabulary of the language: uz: bitim, aylanma, o‘rtacha narx, bekor qilingan, so‘m; ru: сделка, оборот, средняя цена, аннулированные, сум. Write currency as words ("so‘m", "сум", "dollar", "долларов"), not codes like UZS/USD. In Uzbek Cyrillic answers use only Cyrillic letters (no Latin letters inside words).
- Plain text, no markdown tables, no headings. Max 120 words. Never mention SQL, columns or table names.
- Always end with the sentence that states the basis, e.g. uz: "Asos: N ta bitim, davr ..., 30.10.2026 holatiga ko‘ra." / ru: "Основание: N сделок, период ..., по состоянию на 30.10.2026."
"""


class Planner(Protocol):
    def plan(self, question: str, lang: str, hints_text: str, history: list[dict[str, str]], error: str | None = None) -> dict[str, Any]: ...
    def compose(self, question: str, lang: str, sql: str, assumptions: list[str], rows_json: str, n_rows: int, extra: str = "") -> str: ...


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
                f"RESULT ROWS ({n_rows} rows, showing up to 50):\n{rows_json}\n{extra}")
        resp = self.client.messages.create(
            model=self.model, max_tokens=1200, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": ANSWER_EFFORT},
        )
        if resp.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in resp.content if b.type == "text").strip()
