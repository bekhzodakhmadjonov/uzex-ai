"""Build catalog/product_map_draft.csv from the raw product strings.

Every distinct deal name (697) and contract product name (230) gets:
  base_core   - product name without grade/class/brand words, normalized
  group_key   - order-independent key (merges word-order + case variants)
  canonical_ru- most frequent raw spelling of the group's base (by deal count)
  grade_sort / grade_class / brand / size / ptype parsed attributes
  family_guess- first token of base_core (to be curated into families.csv)

A human reviews the draft and saves it as catalog/product_map.csv.
Run:  .venv/bin/python -I pipeline/catalog_draft.py
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd  # noqa: E402
from normalize import bag_key, norm_text, parse_class, parse_deal_name, parse_grade  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "uzex_spot_data")
OUT = os.path.join(ROOT, "catalog")

# words that describe grade/class and must not be part of the base product name
_GRADE_WORDS = re.compile(
    r"\b(?:сорта?|класса?|в/с|высш\w*|перв\w*|втор\w*|трет\w*|[0-9]+|i{1,3}|iv)\b", flags=re.I
)
# words that are attribute values embedded in contract names (kept in ptype instead)
_TYPE_WORDS = {
    "хлебопекарная", "обогащенная", "витаминно", "минеральной", "смесью", "рафинированное",
    "дезодорированное", "рафинированная", "гранулированный", "обрезные", "хвойных", "пород",
}


def base_core(base: str) -> str:
    s = norm_text(base)
    s = _GRADE_WORDS.sub(" ", s)
    toks = [t for t in s.split() if t not in _TYPE_WORDS]
    s = " ".join(toks)
    s = re.sub(r"\(\s*\)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    deals = pd.concat(
        [pd.read_csv(f, dtype=str, usecols=["Наименование товара"]) for f in sorted(glob.glob(os.path.join(DATA, "deals_*.csv")))]
    )
    deal_counts = Counter(deals["Наименование товара"])
    contracts = pd.read_csv(os.path.join(DATA, "contracts.csv"), dtype=str)

    rows = []
    for raw, n in deal_counts.items():
        p = parse_deal_name(raw)
        rows.append({
            "raw_name": raw, "source": "deal", "n_deals": n,
            "base_raw": p["base"] or raw, "brand": p["brand"], "size": p["size"],
            "ptype": p["type"],
            "grade_sort": parse_grade(raw), "grade_class": parse_class(raw),
        })
    for raw, grp in contracts.groupby("Наименование продукта"):
        # grade words may be embedded in the name itself when attribute columns are empty
        rows.append({
            "raw_name": raw, "source": "contract", "n_deals": 0,
            "base_raw": raw, "brand": None, "size": None, "ptype": None,
            "grade_sort": parse_grade(raw), "grade_class": parse_class(raw),
        })

    for r in rows:
        r["base_core"] = base_core(r["base_raw"])
        r["group_key"] = bag_key(r["base_core"])

    # canonical spelling per group = most frequent base_raw by deal count
    weight: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        weight[r["group_key"]][base_core_display(r["base_raw"])] += max(r["n_deals"], 1)
    canonical = {k: c.most_common(1)[0][0] for k, c in weight.items()}

    for r in rows:
        r["canonical_ru"] = canonical[r["group_key"]]
        r["family_guess"] = r["base_core"].split()[0] if r["base_core"] else ""

    cols = ["raw_name", "source", "n_deals", "group_key", "canonical_ru", "family_guess",
            "grade_sort", "grade_class", "brand", "size", "ptype", "base_core"]
    rows.sort(key=lambda r: (r["canonical_ru"], -r["n_deals"], r["raw_name"]))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "product_map_draft.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})

    groups = Counter()
    for r in rows:
        groups[(r["canonical_ru"], r["family_guess"])] += r["n_deals"]
    print(f"raw names: {len(rows)}  canonical products: {len(groups)}")
    for (c, fam), n in sorted(groups.items(), key=lambda x: -x[1]):
        print(f"{n:>7}  {fam:<18} {c}")


def base_core_display(base_raw: str) -> str:
    """Human-readable canonical spelling: strip grade words, keep original case
    unless the string is ALL CAPS (then capitalize first letter)."""
    s = re.sub(r"\s+", " ", base_raw).strip()
    s = re.sub(r"\s*(?:\d+|[IVX]+)[\s-]*(?:сорта?|класса?)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:в/с|высш\w*|перв\w*|втор\w*|трет\w*)\s+(?:сорта?)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s+(?:хлебопекарная|обогащенная.*|рафинированн\w+.*|гранулированный)$", "", s, flags=re.I)
    s = s.strip(" -")
    if s.isupper():
        s = s[:1] + s[1:].lower()
    return s


if __name__ == "__main__":
    main()
