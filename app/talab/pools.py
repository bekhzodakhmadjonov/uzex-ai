"""Group buying (pool-lots) with real state: members join, the pool fills, and closes when the target is reached.

State lives in data/talab_pools.json so it survives restarts and is shared by everyone who opens the app
(a judge joining from a phone is a real member). Seed members are clearly marked as simulated.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from typing import Any, Callable

from .common import ROOT

PATH = ROOT / "data" / "talab_pools.json"
_lock = threading.Lock()
_state: dict[str, dict] | None = None
SIM_NAMES = ["Xaridor A", "Xaridor B", "Xaridor C", "Xaridor D", "Xaridor E", "Xaridor F", "Xaridor G", "Xaridor H", "Xaridor I", "Xaridor J"]


def _load() -> dict[str, dict]:
    global _state
    if _state is None:
        try:
            _state = json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            _state = {}
    return _state


def _save() -> None:
    try:
        PATH.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _round(x: float, unit_short: str) -> float:
    return float(round(x, -1)) if unit_short in ("dona", "kg") else round(x, 1)


def seed(base: dict) -> dict:
    """Initial pool from the lot data (api._pool): spread the already-filled volume over simulated members."""
    fk, us = base["family_key"], base["unit_short"]
    n = base["members"]
    h = int(hashlib.sha256((fk + "w").encode()).hexdigest(), 16)
    weights = [1 + (h >> (i * 4)) % 5 for i in range(n)]
    tot = sum(weights)
    members, acc = [], 0.0
    for i, w in enumerate(weights):
        q = _round(base["filled"] * w / tot, us) if i < n - 1 else _round(base["filled"] - acc, us)
        acc += q
        members.append({"name": SIM_NAMES[i % len(SIM_NAMES)], "qty": q, "simulated": True, "session": None, "ts": 0})
    return {**{k: base[k] for k in ("family_key", "family_uz", "unit_short", "region_id", "region_uz", "target", "closes",
                                    "unit_price", "vs_median_pct", "seller_name", "contract_no", "min_join")},
            "members": members, "status": "open", "closed_at": None}


def view(p: dict, session: str | None = None) -> dict:
    filled = sum(m["qty"] for m in p["members"])
    remaining = max(0.0, p["target"] - filled)
    avg = filled / len(p["members"]) if p["members"] else p["min_join"]
    mine = next((m for m in p["members"] if session and m.get("session") == session), None)
    return {**{k: v for k, v in p.items() if k != "members"},
            "members_n": len(p["members"]), "members": [{"name": ("Siz" if mine is m else m["name"]), "qty": m["qty"], "simulated": m["simulated"],
                                                         "you": mine is m} for m in p["members"]],
            "filled": _round(filled, p["unit_short"]), "remaining": _round(remaining, p["unit_short"]),
            "fill_pct": min(100, round(filled / p["target"] * 100)), "more_members": math.ceil(remaining / avg) if remaining > 0 and avg else 0,
            "you": {"qty": mine["qty"], "position": p["members"].index(mine) + 1} if mine else None,
            "members_real": sum(1 for m in p["members"] if not m["simulated"])}


def ensure(fk: str, seed_fn: Callable[[str], dict | None]) -> dict | None:
    with _lock:
        st = _load()
        if fk not in st:
            base = seed_fn(fk)
            if not base:
                return None
            st[fk] = seed(base)
            _save()
        return st[fk]


def join(fk: str, session: str, qty: float, seed_fn: Callable[[str], dict | None]) -> dict:
    p = ensure(fk, seed_fn)
    if not p:
        raise KeyError(fk)
    with _lock:
        if p["status"] != "open":
            return view(p, session) | {"event": "closed"}
        qty = _round(max(qty, p["min_join"]), p["unit_short"])
        mine = next((m for m in p["members"] if m.get("session") == session), None)
        if mine:
            mine["qty"] = qty
            ev = "updated"
        else:
            n_real = sum(1 for m in p["members"] if not m["simulated"])
            p["members"].append({"name": f"Xaridor {n_real + 1} (real)", "qty": qty, "simulated": False, "session": session, "ts": time.time()})
            ev = "joined"
        _close_if_full(p)
        _save()
        return view(p, session) | {"event": "full" if p["status"] == "full" else ev}


def simulate_join(fk: str, seed_fn: Callable[[str], dict | None], session: str | None = None) -> dict:
    p = ensure(fk, seed_fn)
    if not p:
        raise KeyError(fk)
    with _lock:
        if p["status"] == "open":
            v = view(p)
            q = _round(min(v["remaining"], max(p["min_join"], v["filled"] / max(1, v["members_n"]))), p["unit_short"])
            used = {m["name"] for m in p["members"]}
            name = next((n for n in SIM_NAMES if n not in used), f"Xaridor {len(p['members']) + 1}")
            p["members"].append({"name": name, "qty": q, "simulated": True, "session": None, "ts": time.time()})
            _close_if_full(p)
            _save()
        return view(p, session) | {"event": "full" if p["status"] == "full" else "sim_joined"}


def _close_if_full(p: dict) -> None:
    if sum(m["qty"] for m in p["members"]) >= p["target"] - 1e-9 and p["status"] == "open":
        p["status"] = "full"
        p["closed_at"] = time.strftime("%Y-%m-%d %H:%M")


def reset() -> None:
    global _state
    with _lock:
        _state = {}
        _save()


def all_views(fks: list[str], seed_fn: Callable[[str], dict | None], session: str | None = None) -> list[dict]:
    out = []
    for fk in fks:
        p = ensure(fk, seed_fn)
        if p:
            out.append(view(p, session))
    return out
