"""Shared helpers for the UZEX TALAB demand-first exchange MVP.

Everything here is read-only SQL over data/uzex.duckdb. "Today" in the demo is the dataset's
as-of date (2026-10-30), so every window and deadline is computed against that date.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from normalize import translit_cyr_to_lat, unify_apostrophe  # noqa: E402

# Fees from 1 Oct 2026 (uzex.uz tariff page): buyer exchange fee 0.13 %, clearing 0.05 % per side.
FEE_EXCHANGE_BUYER = 0.0013
FEE_CLEARING = 0.0005

_lock = threading.Lock()


def q(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Run a fixed, parameterised read-only query on its own cursor."""
    cur = db.connection().cursor()
    try:
        res = cur.execute(sql, params or [])
        cols = [d[0] for d in res.description]
        return [{c: (float(v) if isinstance(v, Decimal) else v) for c, v in zip(cols, r)} for r in res.fetchall()]
    finally:
        cur.close()


def q1(sql: str, params: list | None = None) -> dict[str, Any] | None:
    rows = q(sql, params)
    return rows[0] if rows else None


@lru_cache(maxsize=1)
def today() -> date:
    m = q1("select value from meta where key='data_asof'")
    return date.fromisoformat(m["value"]) if m else date(2026, 10, 30)


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads((ROOT / "data" / "rules.json").read_text(encoding="utf-8"))


def rule(n: str) -> dict:
    r = rules().get(n, {})
    return {"clause": n, "uz": r.get("uz", ""), "ru": r.get("ru", "")}


def norm(s: str) -> str:
    """Lowercase Latin form with unified apostrophes; Cyrillic is transliterated."""
    s = unify_apostrophe(str(s)).lower()
    s = translit_cyr_to_lat(s)
    s = s.replace("ʻ", "'").replace("‘", "'").replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def families() -> dict[str, dict]:
    rows = q("select family_key, name_uz, name_ru, aliases from internal.dim_family")
    units = {r["family_key"]: r for r in q(
        """select family_key, arg_max(unit, n) unit, arg_max(unit_uz, n) unit_uz, arg_max(lot_size, n) lot_size
           from (select family_key, unit, unit_uz, lot_size, count(*) n from deals group by 1,2,3,4) group by 1""")}
    out = {}
    for r in rows:
        al = {norm(a) for a in (r["aliases"] or "").split("|") if a.strip()}
        al |= {norm(r["name_uz"]), norm(r["name_ru"])}
        u = units.get(r["family_key"], {})
        out[r["family_key"]] = {"key": r["family_key"], "name_uz": r["name_uz"], "name_ru": r["name_ru"],
                                "aliases": sorted(a for a in al if len(a) >= 2),
                                "unit": u.get("unit"), "unit_uz": u.get("unit_uz"), "lot_size": u.get("lot_size")}
    return out


@lru_cache(maxsize=1)
def regions() -> dict[str, dict]:
    rows = q("select region_id, name_uz, name_ru, aliases from internal.dim_region")
    out = {}
    for r in rows:
        al = {norm(a) for a in (r["aliases"] or "").split("|") if a.strip()}
        al |= {norm(r["name_uz"]), norm(r["name_uz"]).replace(" viloyati", "").replace(" shahri", "")}
        out[r["region_id"]] = {"id": r["region_id"], "name_uz": r["name_uz"], "name_ru": r["name_ru"],
                               "aliases": sorted(a for a in al if len(a) >= 3)}
    return out


UNIT_SHORT = {"тонна": "t", "штука": "dona", "литр": "l", "килограмм": "kg", "куб.метр": "m³", "кв.метр": "m²", "метр": "m"}


def unit_short(unit: str | None) -> str:
    return UNIT_SHORT.get(unit or "", unit or "")


def money(v: float | None) -> str:
    """Uzbek money formatting: 984 000 so'm, 1,23 mln, 4,5 mlrd."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e12:
        s = f"{v / 1e12:.2f} trln"
    elif a >= 1e9:
        s = f"{v / 1e9:.2f} mlrd"
    elif a >= 1e7:
        s = f"{v / 1e6:.1f} mln"
    else:
        return f"{v:,.0f}".replace(",", " ") + " so'm"
    return s.replace(".", ",") + " so'm"
