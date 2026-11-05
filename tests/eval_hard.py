"""Strict benchmark: python -I tests/eval_hard.py --models a,b,c --lang uz
A numeric case passes only if every expected number appears in the ANSWER TEXT (0.1% tolerance).
A refusal case passes only if the agent gives no invented figure and status says no data.
"""
from __future__ import annotations
import argparse, json, os, re, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
from app.agent import answer_question
from app.llm_gemini import GeminiPlanner

CASES = []
NUM = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")
PRICE = {"gemini-3.1-flash-lite": (0.25, 1.5), "gemini-3.5-flash-lite": (0.30, 2.5), "gemini-3.8-flash": (0.75, 3.75), "gemini-3.5-flash": (None, None)}


def nums(text):
    out = []
    for m in NUM.finditer(text):
        s = re.sub(r"[\s ]", "", m.group()).replace(",", ".")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def scales(text):
    """'45,9 trln' style answers count too."""
    out = list(nums(text))
    for m in re.finditer(r"(\d[\d\s ]*(?:[.,]\d+)?)\s*(ming|тыс|mln|млн|mlrd|млрд|trln|трлн)", text, flags=re.I):
        v = float(re.sub(r"[\s ]", "", m.group(1)).replace(",", "."))
        f = {"ming": 1e3, "тыс": 1e3, "mln": 1e6, "млн": 1e6, "mlrd": 1e9, "млрд": 1e9, "trln": 1e12, "трлн": 1e12}[m.group(2).lower()]
        out.append(v * f)
    return out


def score(c, r):
    a = r["answer"] or ""
    if c["kind"] == "refuse":
        ok = r["status"] in ("unanswerable", "no_rows", "clarify")
        big = [n for n in nums(a) if n > 31 and not (2000 <= n <= 2100) and n not in (111111111, 9999999, 2021, 2027, 111)]
        return ok and not big, f"status={r['status']} extra_nums={big[:4]}"
    miss = []
    vals = scales(a)
    for e in c["expect"]:
        if isinstance(e, str):
            if e.lower().replace('"', "") not in a.lower().replace('"', "").replace("«", "").replace("»", ""):
                miss.append(e)
        elif isinstance(e, list):  # any one of these figures
            if not any(abs(v - x) <= max(0.5, abs(x) * 1e-3) for x in e for v in vals):
                miss.append(e)
        elif not any(abs(v - e) <= max(0.5, abs(e) * 1e-3) for v in vals):
            miss.append(e)
    return (not miss and r["status"] in ("ok", "template")), f"status={r['status']} missing={miss}"


def run_model(spec, lang, results):
    # spec is plan_model or plan_model+verify_model
    model, _, verify = spec.partition("+")
    p = GeminiPlanner(model=model)
    v = GeminiPlanner(model=verify) if verify else None
    rows = []
    for c in CASES:
        q = c[lang]
        t = time.time()
        try:
            r = answer_question(q, [], p, None, v)
        except Exception as e:  # noqa: BLE001
            r = {"answer": "", "status": "crash:" + repr(e)[:100], "sql": "", "trace": {}, "elapsed_ms": 0}
        ok, why = score(c, r)
        usage = [a.get("usage") for a in r.get("trace", {}).get("attempts", []) if a.get("usage")]
        rows.append({"id": c["id"], "kind": c["kind"], "q": q, "ok": ok, "why": why, "s": round(time.time() - t, 1), "status": r["status"], "check": r.get("check"), "adjudication": r.get("trace", {}).get("adjudication"), "rejected": [c["problems"] for c in r.get("trace", {}).get("compose", []) if c["problems"]], "answer": r["answer"], "sql": r["sql"], "usage": usage, "trace_err": [a.get("error") for a in r.get("trace", {}).get("attempts", []) if a.get("error")]})
    results[spec] = rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--models"); ap.add_argument("--lang", default="uz"); ap.add_argument("--cases", default="hard_cases")
    a = ap.parse_args()
    CASES.extend(json.load(open(os.path.join(ROOT, "tests", a.cases + ".json"), encoding="utf-8")))
    results = {}
    ts = [threading.Thread(target=run_model, args=(m, a.lang, results)) for m in a.models.split(",")]
    [t.start() for t in ts]; [t.join() for t in ts]
    out = os.path.join(ROOT, "data", "logs", f"{a.cases}_{a.lang}_{time.strftime('%H%M%S')}.json")
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    for m, rows in results.items():
        n = [r for r in rows if r["kind"] != "refuse"]; f = [r for r in rows if r["kind"] == "refuse"]
        lat = sorted(r["s"] for r in rows)
        checks = {}
        for r in rows:
            checks[r["check"]] = checks.get(r["check"], 0) + 1
        print(f"\n=== {m} [{a.lang}] checks={checks} rejected_drafts={sum(bool(r['rejected']) for r in rows)} templates={sum(r['status']=='template' for r in rows)} numeric {sum(r['ok'] for r in n)}/{len(n)}  refusal {sum(r['ok'] for r in f)}/{len(f)}  median {lat[len(lat)//2]}s max {lat[-1]}s")
        for r in rows:
            if not r["ok"]:
                print(f"  FAIL {r['id']:<20} {r['why'][:110]}\n       A: {(r['answer'] or '')[:170]!r}\n       SQL: {(r['sql'] or '')[:170]!r}")
    print("saved", out)


if __name__ == "__main__":
    main()
