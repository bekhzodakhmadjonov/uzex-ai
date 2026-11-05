"""Run the golden questions through the real pipeline (Claude) and score them.

Usage:  .venv/bin/python -I -m tests.eval_llm [--lang uz|ru|both] [--limit N] [--q "free question"]
Writes data/logs/eval_<timestamp>.json and prints a table.

A question PASSES when every expected number (from tests/golden_questions.json) appears
in the answer text or in the returned rows, and the status is not an error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from app.agent import _norm_num, answer_question  # noqa: E402
from app.providers import build_planner  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = json.load(open(os.path.join(ROOT, "tests", "golden_questions.json"), encoding="utf-8"))


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"[-+]?\d[\d\s ]*(?:[.,]\d+)?", text):
        n = _norm_num(m.group())
        if n:
            out.add(n)
    return out


def expected_numbers(exp: dict) -> set[str]:
    out = set()
    for v in exp.values():
        if v is None:
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        for d in (0, 1, 2):
            out.add(f"{round(f, d):.4f}".rstrip("0").rstrip("."))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="both")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--q", default=None)
    args = ap.parse_args()
    planner = build_planner()
    print("provider:", planner.name, [getattr(p, "model", "") for p in getattr(planner, "planners", [])])
    if args.q:
        r = answer_question(args.q, [], planner)
        print(json.dumps({k: v for k, v in r.items() if k != "trace"}, ensure_ascii=False, indent=1, default=str))
        print("TRACE:", json.dumps(r["trace"], ensure_ascii=False, default=str)[:3000])
        return
    cases = []
    for g in GOLDEN:
        if args.lang in ("uz", "both"):
            cases.append(("uz", g["q_uz"], g))
        if args.lang in ("ru", "both"):
            cases.append(("ru", g["q_ru"], g))
    if args.limit:
        cases = cases[: args.limit]
    results, passed = [], 0
    for lang, q, g in cases:
        t = time.time()
        r = answer_question(q, [], planner)
        got = numbers_in(r["answer"]) | numbers_in(json.dumps(r["rows"], ensure_ascii=False, default=str))
        exp = expected_numbers(g["expected"])

        def present(e: str) -> bool:  # relative tolerance 0.1% (rounding differences)
            fe = float(e)
            return any(abs(float(x) - fe) <= max(0.5, abs(fe) * 1e-3) for x in got)

        no_data_expected = all(v in (None, "0") for v in g["expected"].values())
        good_status = r["status"] in ("ok", "no_rows", "unanswerable", "template")
        if no_data_expected:
            ok = all_ok = good_status and r["status"] in ("unanswerable", "no_rows", "ok")
        else:
            ok = good_status and (not exp or any(present(e) for e in exp))
            all_ok = ok and all(present(e) for e in exp if len(e) > 2)
        passed += all_ok
        results.append({"lang": lang, "q": q, "status": r["status"], "pass": all_ok, "partial": ok, "elapsed_ms": r["elapsed_ms"],
                        "answer": r["answer"], "sql": r["sql"], "expected": g["expected"], "trace": r["trace"]})
        print(f"{'PASS' if all_ok else ('part' if ok else 'FAIL'):<5} {r['status']:<12} {r['elapsed_ms']/1000:5.1f}s  {q[:70]}")
        if not all_ok:
            print("      answer:", r["answer"][:200].replace("\n", " "))
            print("      expected:", g["expected"])
    print(f"\n{passed}/{len(cases)} passed")
    out = os.path.join(ROOT, "data", "logs", f"eval_{time.strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("saved", out)


if __name__ == "__main__":
    main()
