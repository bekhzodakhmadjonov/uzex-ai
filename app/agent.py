"""Question → answer pipeline with hallucination guards.

answer_question(question, session_history, planner) -> dict (see RESPONSE KEYS below)

Pipeline:
  1. language + entity hints (resolvers)
  2. LLM plans SQL (structured JSON)          [retry once on guard/SQL error]
  3. guard validates / rewrites SQL
  4. DuckDB executes (read-only, 8 s timeout)
  5. LLM composes answer from the rows ONLY
  6. number check: every number in the answer must exist in the rows; otherwise
     fall back to a deterministic template answer
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import re
import time
from typing import Any

from . import db
from .guard import GuardError, validate_sql
from .hints import detect_language, hints_for, hints_to_text

SQL_TIMEOUT_S = 8.0
MAX_ROWS_TO_LLM = 50

T = {
    "uz": {"no_rows": "Reyestrda bu shartlarga mos bitim topilmadi", "filters": "Qo‘llanilgan filtrlar", "basis": "Asos",
           "deals": "ta bitim", "period": "davr", "asof": "30.10.2026 holatiga ko‘ra", "unans": "Bu savolga mavjud ma’lumotlar asosida javob berib bo‘lmaydi",
           "err": "So‘rovni bajarishda xatolik yuz berdi, iltimos savolni boshqacha ifodalang", "result": "Natija"},
    "uz_cyr": {"no_rows": "Реестрда бу шартларга мос битим топилмади", "filters": "Қўлланилган филтрлар", "basis": "Асос",
               "deals": "та битим", "period": "давр", "asof": "30.10.2026 ҳолатига кўра", "unans": "Бу саволга мавжуд маълумотлар асосида жавоб бериб бўлмайди",
               "err": "Сўровни бажаришда хатолик юз берди, илтимос саволни бошқача ифодаланг", "result": "Натижа"},
    "ru": {"no_rows": "В реестре нет сделок, соответствующих этим условиям", "filters": "Применённые фильтры", "basis": "Основание",
           "deals": "сделок", "period": "период", "asof": "по состоянию на 30.10.2026", "unans": "На этот вопрос нельзя ответить на основе имеющихся данных",
           "err": "Не удалось выполнить запрос, попробуйте переформулировать вопрос", "result": "Результат"},
    "en": {"no_rows": "The registry has no deals matching these filters", "filters": "Filters applied", "basis": "Basis",
           "deals": "deals", "period": "period", "asof": "as of 2026-10-30", "unans": "This question cannot be answered from the available data",
           "err": "The query failed, please rephrase the question", "result": "Result"},
}


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def fmt_number(v: Any, lang: str = "uz") -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int,)) or (isinstance(v, decimal.Decimal) and v == v.to_integral_value()) or (isinstance(v, float) and v.is_integer()):
        return f"{int(v):,}".replace(",", " ")
    if isinstance(v, (float, decimal.Decimal)):
        s = f"{float(v):,.2f}".replace(",", " ")
        return s.replace(".", ",") if lang in ("uz", "uz_cyr", "ru") else s
    return str(v)


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.days
    return v


def rows_for_llm(res: db.QueryResult, lang: str) -> tuple[str, list[dict[str, Any]]]:
    out = []
    for r in res.rows[:MAX_ROWS_TO_LLM]:
        d: dict[str, Any] = {}
        for c, v in zip(res.columns, r):
            d[c] = _jsonable(v)
            if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool):
                d[c + "_fmt"] = fmt_number(v, lang)
        out.append(d)
    return json.dumps(out, ensure_ascii=False), out


# ---------------------------------------------------------------------------
# number verification
# ---------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w,.])[-+]?\d[\d\s ]*(?:[.,]\d+)?(?![\w])")


def _norm_num(s: str) -> str | None:
    s = s.replace(" ", " ").strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    # decimal comma vs thousands comma: with our formatting, thousands are spaces, decimals are , or .
    s = s.replace(",", ".")
    try:
        return f"{float(s):.4f}".rstrip("0").rstrip(".")
    except ValueError:
        return None


def allowed_numbers(res: db.QueryResult, question: str) -> set[str]:
    allowed: set[str] = set()

    def add(x: float):
        for d in (0, 1, 2, 3):
            allowed.add(f"{round(x, d):.4f}".rstrip("0").rstrip("."))
        for div, _name in ((1e3, "ming"), (1e6, "mln"), (1e9, "mlrd"), (1e12, "trln")):
            if abs(x) >= div:
                for d in (0, 1, 2):
                    allowed.add(f"{round(x / div, d):.4f}".rstrip("0").rstrip("."))

    for r in res.rows:
        for v in r:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float, decimal.Decimal)):
                add(float(v))
            elif isinstance(v, (dt.date, dt.datetime)):
                allowed.update({str(v.year), str(v.month), str(v.day), f"{v.day:02d}", f"{v.month:02d}"})
            elif isinstance(v, str):
                for m in _NUM.finditer(v):
                    n = _norm_num(m.group())
                    if n:
                        allowed.add(n)
    add(float(len(res.rows)))
    for m in _NUM.finditer(question):
        n = _norm_num(m.group())
        if n:
            allowed.add(n)
    allowed.update({str(i) for i in range(0, 32)})  # days, months, small ordinals
    allowed.update({"2022", "2023", "2024", "2025", "2026", "30.10.2026", "2026-10-30"})
    return allowed


def unverified_numbers(answer: str, allowed: set[str]) -> list[str]:
    bad = []
    text = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", " ", answer)  # dates dd.mm.yyyy are fine
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    for m in _NUM.finditer(text):
        raw = m.group()
        n = _norm_num(raw)
        if n is None:
            continue
        if n in allowed:
            continue
        # tolerate "12 345 678" split by a line wrap or percent sign variants
        bad.append(raw.strip())
    return bad


# ---------------------------------------------------------------------------
# deterministic fallback answer
# ---------------------------------------------------------------------------
def template_answer(lang: str, res: db.QueryResult, filters_human: list[str], assumptions: list[str]) -> str:
    t = T.get(lang, T["uz"])
    if not res.rows:
        s = t["no_rows"] + "."
        if filters_human:
            s += f" {t['filters']}: " + "; ".join(filters_human) + "."
        return s
    parts = []
    if len(res.rows) == 1:
        for c, v in zip(res.columns, res.rows[0]):
            parts.append(f"{c.replace('_', ' ')}: {fmt_number(_jsonable(v), lang) if isinstance(v, (int, float, decimal.Decimal)) else v}")
        s = f"{t['result']}: " + "; ".join(parts) + "."
    else:
        lines = []
        for r in res.rows[:10]:
            lines.append(", ".join(f"{c}={fmt_number(_jsonable(v), lang) if isinstance(v, (int, float, decimal.Decimal)) else v}" for c, v in zip(res.columns, r)))
        s = f"{t['result']} ({len(res.rows)}):\n" + "\n".join(lines)
    if filters_human:
        s += f"\n{t['filters']}: " + "; ".join(filters_human) + "."
    return s


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def _run_with_timeout(sql: str) -> db.QueryResult:
    import threading
    cur = db.connection().cursor()
    timer = threading.Timer(SQL_TIMEOUT_S, cur.interrupt)
    timer.start()
    t = time.perf_counter()
    try:
        r = cur.execute(sql)
        cols = [d[0] for d in r.description]
        rows = r.fetchmany(201)
    finally:
        timer.cancel()
        cur.close()
    return db.QueryResult(cols, rows[:200], (time.perf_counter() - t) * 1000, len(rows) > 200)


def answer_question(question: str, history: list[dict[str, str]], planner, lang_override: str | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    lang = lang_override or detect_language(question)
    t = T.get(lang, T["uz"])
    hints = hints_for(question)
    hints_text = hints_to_text(hints)
    trace: dict[str, Any] = {"hints": hints_text, "attempts": []}

    plan, res, sql_clean, last_err = None, None, "", None
    for attempt in range(2):
        try:
            plan = planner.plan(question, lang, hints_text, history, error=last_err)
        except Exception as e:  # noqa: BLE001
            trace["attempts"].append({"stage": "plan", "error": repr(e)})
            return _finish(lang, question, t0, trace, answer=t["err"], status="llm_error")
        trace["attempts"].append({"stage": "plan", "plan": {k: v for k, v in plan.items() if k != "_usage"}, "usage": plan.get("_usage")})
        if plan.get("unanswerable"):
            ans = plan.get("reason") or t["unans"]
            return _finish(lang, question, t0, trace, answer=ans, status="unanswerable", plan=plan)
        if plan.get("clarify") and not plan.get("sql"):
            return _finish(lang, question, t0, trace, answer=plan["clarify"], status="clarify", plan=plan)
        try:
            sql_clean = validate_sql(plan.get("sql", ""))
            res = _run_with_timeout(sql_clean)
            break
        except GuardError as e:
            last_err = f"guard: {e}"
        except Exception as e:  # noqa: BLE001
            last_err = f"duckdb: {str(e)[:400]}"
        trace["attempts"][-1]["error"] = last_err
    if res is None:
        return _finish(lang, question, t0, trace, answer=t["err"], status="sql_error", plan=plan, sql=sql_clean or (plan or {}).get("sql", ""))

    rows_json, rows_list = rows_for_llm(res, lang)
    assumptions = plan.get("assumptions", [])
    filters_human = plan.get("filters_human", [])
    allowed = allowed_numbers(res, question)
    answer, status = "", "ok"
    try:
        answer = planner.compose(question, lang, sql_clean, assumptions, rows_json, len(res.rows))
        bad = unverified_numbers(answer, allowed)
        if bad:
            trace["unverified_numbers_first"] = bad
            answer = planner.compose(question, lang, sql_clean, assumptions, rows_json, len(res.rows),
                                     extra=f"\nIMPORTANT: your previous draft contained numbers not present in the rows: {bad}. Use only numbers from the rows, copied exactly.")
            bad = unverified_numbers(answer, allowed)
            if bad:
                trace["unverified_numbers_second"] = bad
                answer, status = template_answer(lang, res, filters_human, assumptions), "template"
    except Exception as e:  # noqa: BLE001
        trace["compose_error"] = repr(e)
        answer, status = template_answer(lang, res, filters_human, assumptions), "template"
    if not answer:
        answer, status = template_answer(lang, res, filters_human, assumptions), "template"
    if not res.rows and status == "ok":
        status = "no_rows"

    return _finish(lang, question, t0, trace, answer=answer, status=status, plan=plan, sql=sql_clean, res=res, rows_list=rows_list)


def _finish(lang, question, t0, trace, answer, status, plan=None, sql="", res=None, rows_list=None) -> dict[str, Any]:
    meta = db.meta()
    out = {
        "question": question, "language": lang, "status": status, "answer": answer,
        "sql": sql, "assumptions": (plan or {}).get("assumptions", []), "filters": (plan or {}).get("filters_human", []),
        "chart": (plan or {}).get("chart", {"type": "none", "x": "", "y": ""}),
        "columns": res.columns if res else [], "rows": rows_list or [], "n_rows": len(res.rows) if res else 0,
        "truncated": res.truncated if res else False,
        "basis": {"as_of": meta.get("data_asof"), "data_range": [meta.get("min_deal_date"), meta.get("max_deal_date")],
                  "n_rows": len(res.rows) if res else 0, "sql_ms": round(res.elapsed_ms, 1) if res else None},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "trace": trace,
    }
    return out
