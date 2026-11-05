"""Full-scale eval over the real HTTP API, one fresh session per question (see fullscale_cases.py).

    .venv/bin/python -I tests/eval_fullscale.py --url http://127.0.0.1:8766 [--ids a,b] [--workers 4]

Scoring is exact and looks only at the answer text above the basis line:
- num: every expected item appears, and the answer is in the question's language
- refuse: status says no data, and no figure appears beyond those in the question and the data range
- zero: status says no data, or the answer states zero
Stops early if the model provider stops answering, so a billing outage is not reported as wrong answers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUM = re.compile(r"(?<![\d.,])\d[\d  ]*(?:[.,]\d+)?")
BASIS = re.compile(r"\n\n(?:Asos|Основание|Асос|Basis):.*$", flags=re.S)
NO_DATA = {"unanswerable", "no_rows", "clarify"}
INFRA = {"llm_error", "rate_limited", "http_error"}
RANGE_DIGITS = {"03.01.2022", "30.10.2026", "31.10.2026", "01.01.2022", "2022", "2026", "3", "30", "31", "1", "10", "01", "0"}  # 0: the 'no records (0)' line
SAYS_NONE = re.compile(r"ни одной|ни одна|нет сделок|не зафиксирован|hech (?:qanday|bir)|yo.q|йўқ|\bnone\b", re.I)
# answer text a reader must never see, whatever the figures say
LEAK = re.compile(r"\{\{|\}\}|\b[a-z]+_[a-z_]+\b|\b(?:min|max) deal date\b|\bfamily_key\b|\bsql\b|jadval|жадвал|таблиц")


def ask(url: str, q: str, sid: str) -> dict:
    req = urllib.request.Request(url + "/api/ask", data=json.dumps({"question": q, "session_id": sid}).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        r = {"status": "http_error", "answer": f"HTTP {e.code}", "sql": ""}
    except Exception as e:  # noqa: BLE001
        r = {"status": "http_error", "answer": repr(e)[:200], "sql": ""}
    r["seconds"] = round(time.time() - t, 1)
    return r


def numbers(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text):
        try:
            out.append(float(re.sub(r"[\s ]", "", m.group()).replace(",", ".")))
        except ValueError:
            pass
    return out


def has_number(vals: list[float], e: float, tol: float | None = None) -> bool:
    allow = tol if tol is not None else max(0.011, abs(e) * 2e-9)  # exact up to the printed cents and float error on trillions
    return any(abs(v - e) <= allow for v in vals)


def plain(s: str) -> str:
    return re.sub(r"[\"«»“”„]", "", str(s)).lower().replace("ё", "е")


def present(item, body: str, vals: list[float]) -> bool:
    if isinstance(item, dict):
        if "any" in item:
            return any(present(x, body, vals) for x in item["any"])
        return has_number(vals, item["near"], item["tol"])
    if isinstance(item, str):
        return plain(item) in plain(body)
    return has_number(vals, float(item))


def language_ok(lang: str, body: str) -> bool:
    body = body.split("\n")[0]  # company names are stored in either script, so judge the opening sentence
    cyr, lat = len(re.findall(r"[А-Яа-яЁёЎўҚқҒғҲҳ]", body)), len(re.findall(r"[A-Za-z]", body))
    if lang in ("ru", "uz_cyr"):
        return cyr > lat
    return lat > cyr or (lat + cyr) < 8


def score(kind: str, expect: list, lang: str | None, r: dict, q: str) -> tuple[str, str]:
    """Returns (verdict, why). verdict: pass | fail | infra"""
    st, ans = r.get("status", ""), r.get("answer") or ""
    if st in INFRA:
        return "infra", st
    body = BASIS.sub("", ans)
    vals = numbers(body)
    leak = LEAK.search(body)
    if leak:
        return "fail", f"leaked into the answer: {leak.group(0)}"
    if kind == "refuse":
        if st not in NO_DATA:
            return "fail", f"gave an answer (status {st}) instead of saying no data"
        allowed = set(re.findall(r"\d+(?:[.,:/-]\d+)*", q)) | RANGE_DIGITS
        extra = [d for d in re.findall(r"\d+(?:[.,:/-]\d+)*", body) if d not in allowed and not re.fullmatch(r"\d{9}", d)]  # a real INN is an identifier, not a figure
        return ("fail", f"figures in a refusal: {extra[:4]}") if extra else ("pass", "")
    if kind == "zero":
        if st in NO_DATA or any(v == 0 for v in vals) or SAYS_NONE.search(body):
            return "pass", ""
        return "fail", f"status {st}, expected no such records"
    if st in NO_DATA:
        return "fail", f"said no data (status {st})"
    if st == "sql_error":
        return "fail", "query failed"
    missing = [e for e in expect if not present(e, body, vals)]
    if missing:
        return "fail", f"missing {missing}"
    if lang and not language_ok(lang, body):
        return "fail", f"answered in the wrong language, expected {lang}"
    return "pass", ""


def run_case(url: str, c: dict) -> dict:
    r = ask(url, c["q"], str(uuid.uuid4()))
    v, why = score(c["kind"], c["expect"], c.get("lang"), r, c["q"])
    return {**c, "verdict": v, "why": why, "status": r.get("status"), "check": r.get("check"), "seconds": r["seconds"], "answer": r.get("answer"), "sql": r.get("sql"), "assumptions": r.get("assumptions")}


def run_session(url: str, s: dict) -> list[dict]:
    sid, rows = str(uuid.uuid4()), []
    for n, step in enumerate(s["steps"]):
        r = ask(url, step["q"], sid)
        v, why = score("num", step["expect"], step.get("lang"), r, step["q"])
        rows.append({"id": f"{s['id']}#{n + 1}", "kind": "session", "lang": step.get("lang"), "q": step["q"], "expect": step["expect"], "verdict": v, "why": why, "status": r.get("status"), "check": r.get("check"), "seconds": r["seconds"], "answer": r.get("answer"), "sql": r.get("sql")})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8766")
    ap.add_argument("--ids", default="")
    ap.add_argument("--cases", default="fullscale_cases")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-sessions", action="store_true")
    a = ap.parse_args()
    with open(os.path.join(ROOT, "tests", a.cases + ".json"), encoding="utf-8") as f:
        data = json.load(f)
    only = set(filter(None, a.ids.split(",")))
    cases = [c for c in data["cases"] if not only or c["id"] in only]
    results: list[dict] = []
    infra_streak = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for row in ex.map(lambda c: run_case(a.url, c), cases):
            results.append(row)
            infra_streak = infra_streak + 1 if row["verdict"] == "infra" else 0
            if infra_streak >= 8:
                print("provider is not answering, stopping:", row["answer"])
                ex.shutdown(wait=False, cancel_futures=True)
                break
    if not a.no_sessions and not only and infra_streak < 8:
        with ThreadPoolExecutor(a.workers) as ex:
            for rows in ex.map(lambda s: run_session(a.url, s), data["sessions"]):
                results.extend(rows)
    stamp = time.strftime("%H%M%S")
    path = os.path.join(ROOT, "data", "logs", f"{a.cases}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    n = {k: sum(r["verdict"] == k for r in results) for k in ("pass", "fail", "infra")}
    lat = sorted(r["seconds"] for r in results) or [0]
    print(f"\n{n['pass']}/{len(results)} passed, {n['fail']} failed, {n['infra']} not answered by the provider | median {lat[len(lat) // 2]} s, max {lat[-1]} s | over 90 s: {sum(x > 90 for x in lat)}")
    kinds: dict[str, list[int]] = {}
    for r in results:
        k = kinds.setdefault(r["kind"], [0, 0])
        k[0] += r["verdict"] == "pass"
        k[1] += 1
    print("by kind:", {k: f"{v[0]}/{v[1]}" for k, v in kinds.items()})
    print("checks:", {c: sum(r.get("check") == c for r in results) for c in {r.get("check") for r in results}})
    for r in results:
        if r["verdict"] != "pass":
            print(f"\n{r['verdict'].upper()} {r['id']} [{r.get('lang')}] {r['status']} check={r.get('check')} {r['seconds']}s  {r['why'][:170]}\n Q: {r['q']}\n A: {(r['answer'] or '')[:420]!r}\n SQL: {(r['sql'] or '')[:420]!r}")
    print("saved", path)


if __name__ == "__main__":
    main()
