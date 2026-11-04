"""Offline planner for tests and demos without an API key.

Maps a handful of question patterns to SQL; composes a template answer.
"""
from __future__ import annotations

import json
import re
from typing import Any


class FakePlanner:
    def plan(self, question: str, lang: str, hints_text: str, history, error=None) -> dict[str, Any]:
        q = question.lower()
        years = re.findall(r"20[12]\d", q)
        year = years[0] if years else None
        fam = re.search(r"family_key='(\w+)'", hints_text)
        reg = re.search(r"region_id='(\w+)'", hints_text)
        base = {"assumptions": [], "filters_human": [], "unanswerable": False, "reason": "", "clarify": "", "chart": {"type": "none", "x": "", "y": ""}}
        if "noyabr" in q or "ноябр" in q:
            return base | {"sql": "", "unanswerable": True, "reason": "Ma’lumotlar 30.10.2026 gacha; 2026-yil noyabr uchun bitimlar yo‘q."}
        where = ["NOT is_annulled"]
        if year:
            where.append(f"year={year}")
        if fam:
            where.append(f"family_key='{fam.group(1)}'")
        if reg:
            where.append(f"region_id='{reg.group(1)}'")
        if "narx" in q or "цена" in q:
            sql = f"SELECT unit, currency, count(*) AS n_deals, round(wavg_price(amount, volume), 2) AS avg_price FROM deals WHERE {' AND '.join(where)} GROUP BY ALL ORDER BY n_deals DESC"
        elif "top" in q or "eng katta" in q or "крупн" in q:
            sql = f"SELECT buyer_name, buyer_inn, count(*) AS n_deals, sum(amount_uzs) AS total_uzs FROM deals WHERE {' AND '.join(where)} GROUP BY ALL ORDER BY total_uzs DESC LIMIT 5"
            base["chart"] = {"type": "bar", "x": "buyer_name", "y": "total_uzs"}
        elif "oy" in q or "месяц" in q:
            sql = f"SELECT year, month, count(*) AS n_deals, sum(amount_uzs) AS total_uzs FROM deals WHERE {' AND '.join(where)} GROUP BY ALL ORDER BY year, month"
            base["chart"] = {"type": "line", "x": "month", "y": "n_deals"}
        else:
            sql = f"SELECT count(*) AS n_deals, sum(amount_uzs) AS total_uzs, sum(volume_t) AS tonnes, min(deal_date) AS from_date, max(deal_date) AS to_date FROM deals WHERE {' AND '.join(where)}"
        base["assumptions"] = ["Bekor qilingan bitimlar hisobga olinmadi"] if lang.startswith("uz") else ["Аннулированные сделки исключены"]
        base["filters_human"] = [w for w in where]
        return base | {"sql": sql}

    def compose(self, question, lang, sql, assumptions, rows_json, n_rows, extra="") -> str:
        rows = json.loads(rows_json)
        if not rows:
            return "Reyestrda mos bitim topilmadi." if lang.startswith("uz") else "В реестре нет подходящих сделок."
        r0 = rows[0]
        parts = [f"{k}: {v}" for k, v in r0.items() if k.endswith("_fmt") or not any(kk == k + "_fmt" for kk in r0)]
        return ("Natija: " if lang.startswith("uz") else "Результат: ") + "; ".join(parts) + f". Asos: {n_rows} qator."
