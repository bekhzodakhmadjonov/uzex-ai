"""Field accuracy of the deterministic talabnoma parser on hand-labelled phrases (no LLM).

    .venv/bin/python -m tests.talab_parse_eval            # dev set (used while fixing bugs)
    .venv/bin/python -m tests.talab_parse_eval --holdout  # holdout set, run once for the report
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.talab.market import median_prices  # noqa: E402
from app.talab.parse import parse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(1.0, abs(b) * 0.01)


def run(split: str) -> dict:
    data = json.loads((ROOT / "tests" / "talab_phrases.json").read_text(encoding="utf-8"))
    ref = date.fromisoformat(data["ref"])
    mp = median_prices()
    fields = {"family": 0, "qty": 0, "region": 0, "deadline": 0, "price": 0}
    exact, fails = 0, []
    for c in data[split]:
        r = parse(c["t"], ref=ref, median_price=mp)
        its = r["items"]
        got = {"family": [i["family_key"] for i in its] == c["f"],
               "qty": len(its) == len(c["q"]) and all(close(i["qty"], q) for i, q in zip(its, c["q"])),
               "region": (its[0]["region_id"] if its else None) == c["r"],
               "deadline": (its[0]["deadline"] if its else None) == c["d"],
               "price": close(its[0]["price_max"] if its else None, c["p"])}
        for k, v in got.items():
            fields[k] += v
        if all(got.values()):
            exact += 1
        else:
            fails.append({"t": c["t"], "wrong": [k for k, v in got.items() if not v],
                          "got": [{k: i[k] for k in ("family_key", "qty", "region_id", "deadline", "price_max")} for i in its]})
    n = len(data[split])
    return {"split": split, "n": n, "exact": exact, "exact_pct": round(exact / n * 100), "fields_pct": {k: round(v / n * 100) for k, v in fields.items()},
            "fails": fails}


if __name__ == "__main__":
    split = "holdout" if "--holdout" in sys.argv else "dev"
    res = run(split)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    out = ROOT / "tests" / "talab_parse_eval.json"
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    prev[split] = {k: v for k, v in res.items()}
    out.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
