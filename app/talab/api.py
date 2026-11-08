"""HTTP API for UZEX TALAB (/api/talab/*). No LLM on any path: free to run, same answer every time."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import lots as lots_mod
from . import market as market_mod
from . import settle
from .common import ROOT, families, money, regions, today
from .parse import parse
from .trust import trust

router = APIRouter(prefix="/api/talab")

# --- rate limit for the paid AI endpoints (a public link must not burn the API credit)
import os as _os  # noqa: E402
import time as _time  # noqa: E402
from collections import defaultdict as _dd, deque as _dq  # noqa: E402

_hits: dict = _dd(_dq)
AI_PER_MIN = int(_os.environ.get("UZEX_TALAB_AI_RATE", "12"))
AI_GLOBAL_PER_MIN = int(_os.environ.get("UZEX_TALAB_AI_RATE_GLOBAL", "60"))


def ai_limit(request: Request) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    now = _time.time()
    for key, lim in ((ip, AI_PER_MIN), ("*", AI_GLOBAL_PER_MIN)):
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= lim:
            raise HTTPException(429, "Juda ko'p so'rov. Bir daqiqadan keyin qayta urinib ko'ring.")
    for key in (ip, "*"):
        _hits[key].append(now)

SCENARIOS = [
    {"id": "uy", "title": "Uy quraman", "text": "Uy qurayapman, Samarqand. Smeta: Sement M400 — 8 tonna; Armatura d12 — 1,2 t; G'isht — 12 000 dona. Dekabrgacha kerak."},
    {"id": "sement", "title": "Faqat sement", "text": "Dekabrgacha Samarqandga 8 t sement kerak, tonnasi 950 mingdan oshmasin"},
    {"id": "novvoy", "title": "Novvoyxona", "text": "Novvoyxonamga 2 hafta ichida Toshkentga 5 tonna un va 1 tonna shakar kerak"},
    {"id": "transport", "title": "Transport", "text": "Qashqadaryoga dekabrgacha 20 tonna dizel yoqilg'isi kerak"},
]


class TextIn(BaseModel):
    text: str = Field(min_length=2, max_length=3000)
    session_id: str = "demo"


class ItemIn(BaseModel):
    item: dict[str, Any]
    session_id: str = "demo"
    contract_no: str | None = None


def _enrich(item: dict) -> dict:
    b = market_mod.band(item["family_key"], item.get("region_id"))
    item["band"] = {"p25": b["p25"], "p50": b["p50"], "p75": b["p75"], "n": b["n"], "why": b["why"]}
    if not item.get("price_max") and b["p75"]:
        step = 100 if b["p75"] > 1e4 else 1  # round DOWN so the suggested cap never lands above the typical range
        item["price_max"] = float(int(b["p75"] // step) * step)
        item["price_source"] = "market_p75"
    item["position"] = market_mod.position(item.get("price_max"), b)
    if item.get("qty") and item.get("price_max"):
        item["est_total"] = item["qty"] * item["price_max"]
    item["preview"] = _preview(item)
    return item


GROUP_RE = __import__("re").compile(r"jamoa|birga|birgalik|guruh|pul-?lot|kooperat|hamkor|вместе|совмест|групп|kichik hajm|ko'p miqdorda ola olmay", __import__("re").I)


def _preview(item: dict) -> dict:
    """What the market offers right now for this talabnoma: matching lots, cheapest reliable one, group buy."""
    fk = item["family_key"]
    lots = lots_mod.candidate_lots(fk, item.get("region_id"), limit=300)
    good = []
    for lot in lots:
        tr = trust(lot["seller_inn"], "seller")
        if tr.get("score") is not None and tr["score"] >= 60:
            good.append(lot)
    cap = item.get("price_max")
    fit = [x for x in good if not cap or x["expected_price"] <= cap]
    best = min(fit or good, key=lambda x: x["expected_price"]) if (fit or good) else None
    f = families()[fk]
    target = settle.typical_min_volume(fk, f["unit"])
    qty = item.get("qty") or 0
    pl = pool_state(fk) if fk in dict(POOL_FAMS) else None
    return {"n_lots": len(lots), "n_reliable": len(good), "n_fit": len(fit),
            "best": {"seller": best["seller_name"], "region_uz": best["region_uz"], "price": best["expected_price"],
                     "delivery_days": best["delivery_term_days"], "contract_no": best["contract_no"]} if best else None,
            "small": bool(qty and target and qty < target), "typical_min": target,
            "want_group": bool(GROUP_RE.search(item.get("text") or "")), "pool": pl}


@router.get("/meta")
def meta():
    return {"today": today().isoformat(), "scenarios": SCENARIOS,
            "regions": [{"id": k, "name_uz": v["name_uz"]} for k, v in regions().items()],
            "families": [{"key": k, "name_uz": v["name_uz"], "unit": v["unit"]} for k, v in families().items()]}


@router.post("/parse")
def api_parse(body: TextIn):
    r = parse(body.text, median_price=market_mod.median_prices())
    r["items"] = [_enrich(it) for it in r["items"]]
    total = sum(it.get("est_total") or 0 for it in r["items"])
    r["est_total"] = total
    if r.get("mode") == "chat":
        return r
    r["audit"] = settle.audit(body.session_id, "talab", {"summary": f"{len(r['items'])} ta talabnoma tuzildi", "text": body.text, "items": r["items"]})
    return r


@router.post("/market")
def api_market(body: ItemIn):
    it = body.item
    dl = date.fromisoformat(it["deadline"]) if it.get("deadline") else None
    return market_mod.market(it["family_key"], it.get("region_id"), it.get("price_max"), dl)


@router.post("/match")
def api_match(body: ItemIn):
    r = lots_mod.match(body.item)
    r["audit"] = settle.audit(body.session_id, "qoriqchi", {"summary": f"{r['n_accept']} ta lot mos, {r['n_reject']} ta rad etildi",
                                                            "verdicts": {x['lot']['contract_no']: x['guard']['verdict'] for x in r['results']}})
    return r


def _lot(item: dict, contract_no: str | None) -> dict:
    cands = lots_mod.candidate_lots(item["family_key"], item.get("region_id"), limit=50)
    for c in cands:
        if c["contract_no"] == contract_no:
            return c
    raise HTTPException(404, "Lot topilmadi")


@router.post("/pool")
def api_pool(body: ItemIn):
    return settle.pool(body.item, _lot(body.item, body.contract_no))


@router.post("/deal")
def api_deal(body: ItemIn):
    lot = _lot(body.item, body.contract_no)
    buyer = trust("__new__", "buyer")
    r = settle.deal(body.item, lot, buyer)
    r["pool"] = pool_for_deal(body.item, body.session_id.replace("preview-", ""))
    r["lot"] = lot
    r["buyer_trust"] = buyer
    r["audit"] = settle.audit(body.session_id, "imzo", {"summary": f"Talabnoma imzolandi: lot {lot['contract_no']}, {money(r['total'])}",
                                                        "contract_no": lot["contract_no"], "total": r["total"]})
    return r


@router.get("/arbiter")
def api_arbiter(deal_no: int | None = None, session_id: str = "demo"):
    r = settle.arbiter(deal_no)
    r["audit"] = settle.audit(session_id, "hakam", {"summary": f"Bitim {r.get('deal_no')} bo'yicha qaror loyihasi tayyorlandi"})
    return r


@router.get("/trust/{inn}")
def api_trust(inn: str, role: str = "seller"):
    return trust(inn, role)


@router.get("/audit/{session_id}")
def api_audit(session_id: str):
    return {"chain": settle.audit_log(session_id)}


@router.get("/evidence")
def api_evidence():
    p = ROOT / "data" / "talab_evidence.json"
    if not p.exists():
        raise HTTPException(404, "Isbot fayli hali hisoblanmagan: python -m tests.talab_evidence")
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ e-commerce catalog + AI endpoints
from datetime import timedelta  # noqa: E402
from functools import lru_cache  # noqa: E402

from . import ai  # noqa: E402
from .common import q1, unit_short  # noqa: E402

CATEGORIES = [
    {"id": "qurilish", "title": "Qurilish", "families": ["cement", "rebar", "brick", "lumber", "glass", "gypsum", "lime", "bitumen", "steel_sheet", "pe_pipe"]},
    {"id": "oziq", "title": "Oziq-ovqat", "families": ["flour", "sugar", "rice", "sunflower_oil", "cottonseed_oil", "salt", "wheat"]},
    {"id": "yoqilgi", "title": "Yoqilg'i", "families": ["diesel", "petrol", "lpg", "fuel_oil"]},
    {"id": "qishloq", "title": "Qishloq xo'jaligi", "families": ["urea", "ammonium_nitrate", "feed", "cottonseed_meal"]},
    {"id": "kimyo", "title": "Kimyo va polimer", "families": ["polyethylene", "polypropylene", "pvc", "sulfuric_acid"]},
]


@lru_cache(maxsize=64)
def _card(fk: str) -> dict:
    f = families()[fk]
    b = market_mod.band(fk)
    t = today()
    prev = q1("""select quantile_cont(unit_price,0.5) p from deals where family_key=? and unit=? and not is_annulled
                 and deal_date>? and deal_date<=?""", [fk, f["unit"], t - timedelta(weeks=16), t - timedelta(weeks=8)])
    lots = lots_mod.candidate_lots(fk, None, limit=200)
    sig = market_mod.signal(fk, t + timedelta(days=60))
    chg = (b["p50"] / prev["p"] - 1) * 100 if prev and prev["p"] and b["p50"] else None
    return {"key": fk, "name_uz": f["name_uz"], "unit": f["unit"], "unit_short": unit_short(f["unit"]), "p25": b["p25"], "p50": b["p50"], "p75": b["p75"],
            "n": b["n"], "change_pct": round(chg, 1) if chg is not None else None, "lots": len(lots),
            "sellers": len({x["seller_inn"] for x in lots}), "from_price": min((x["expected_price"] for x in lots), default=None),
            "signal": sig["action"], "signal_text": sig["text"]}


@router.get("/catalog")
def catalog():
    out = []
    for c in CATEGORIES:
        cards = [_card(fk) for fk in c["families"] if fk in families()]
        out.append({**c, "items": [x for x in cards if x["lots"] and x["p50"]]})
    return {"today": today().isoformat(), "categories": out, "ai_mode": ai.mode(), "ai_chain": ai.chain_name()}


@router.get("/product/{fk}")
def product(fk: str, region_id: str | None = None):
    if fk not in families():
        raise HTTPException(404, "Tovar topilmadi")
    m = market_mod.market(fk, region_id, None, None)
    lots = lots_mod.candidate_lots(fk, region_id, limit=12)
    for x in lots:
        tr = trust(x["seller_inn"], "seller")
        x["trust"] = {"score": tr["score"], "tier_uz": tr["tier_uz"]}
    return {"card": _card(fk), "market": m, "lots": lots, "pool_target": settle.typical_min_volume(fk, families()[fk]["unit"])}


SRC = {"ai": "AI tushundi, kod tekshirdi", "fake": "AI (demo), kod tekshirdi", "code": "dastur tahlilchisi"}
KIND = {"text": "matndan", "image": "rasmdan", "audio": "ovozdan"}


class AiIn(BaseModel):
    text: str | None = Field(default=None, max_length=3000)
    file_b64: str | None = Field(default=None, max_length=9_000_000)
    mime: str | None = None
    session_id: str = "demo"
    history: list[dict] | None = None


@router.post("/ai/understand", dependencies=[Depends(ai_limit)])
def ai_understand(body: AiIn):
    if not body.text and not body.file_b64:
        raise HTTPException(400, "Matn, rasm yoki ovoz kerak")
    r = ai.understand(body.text, body.file_b64, body.mime, market_mod.median_prices(), body.history)
    r["items"] = [_enrich(it) for it in r["items"]]
    if GROUP_RE.search(r.get("transcript") or body.text or ""):
        for it in r["items"]:
            it["preview"]["want_group"] = True
    r["est_total"] = sum(it.get("est_total") or 0 for it in r["items"])
    r["ai_mode"] = ai.mode()
    if r.get("mode") == "chat":
        return r
    r["audit"] = settle.audit(body.session_id, "talab", {"summary": f"{len(r['items'])} ta talabnoma tuzildi: {SRC.get(r.get('source'), r.get('source'))}, {KIND.get(r.get('kind'), r.get('kind'))}"})
    return r


class ExplainIn(BaseModel):
    topic: str = Field(max_length=200)
    facts: str = Field(max_length=3000)
    fallback: str = Field(max_length=2000)
    mode: str = "oddiy"


@router.post("/ai/explain", dependencies=[Depends(ai_limit)])
def ai_explain(body: ExplainIn):
    return ai.explain(body.topic, body.facts, body.fallback, body.mode)


@router.get("/arbiter_ai", dependencies=[Depends(ai_limit)])
def api_arbiter_ai(deal_no: int | None = None, session_id: str = "demo"):
    r = settle.arbiter(deal_no)
    r["ai"] = ai.arbiter_text(r)
    r["audit"] = settle.audit(session_id, "hakam", {"summary": f"Bitim {r.get('deal_no')} bo'yicha qaror loyihasi ({'AI' if r['ai']['source'] == 'ai' else 'shablon'})"})
    return r


# ------------------------------------------------------------------ group buying (pool-lots) shown in the shop
import hashlib  # noqa: E402

POOL_FAMS = [("cement", "SAM"), ("rebar", "SAM"), ("brick", "TAS"), ("flour", "TAS"), ("sugar", "FAR"), ("sunflower_oil", "AND"),
             ("rice", "NAM"), ("lumber", "TAO")]


@lru_cache(maxsize=64)
def _pool(fk: str, rid: str) -> dict | None:
    f = families().get(fk)
    if not f:
        return None
    target = settle.typical_min_volume(fk, f["unit"])
    if not target:
        return None
    best = None
    for lot in lots_mod.candidate_lots(fk, rid, limit=30):
        tr = trust(lot["seller_inn"], "seller")
        if tr.get("score") is not None and tr["score"] >= 60 and (best is None or lot["expected_price"] < best["expected_price"]):
            best = lot
    if not best:
        return None
    h = int(hashlib.sha256(fk.encode()).hexdigest(), 16)
    pct = 38 + h % 45
    us = unit_short(f["unit"])
    nd = -1 if us in ("dona", "kg") else 1
    filled = round(target * pct / 100, nd)
    b = market_mod.band(fk)
    return {"family_key": fk, "family_uz": f["name_uz"], "unit_short": us, "region_id": rid, "region_uz": regions()[rid]["name_uz"],
            "target": target, "filled": filled, "fill_pct": round(filled / target * 100), "members": 3 + h % 7,
            "closes": (today() + timedelta(days=2 + h % 6)).isoformat(), "unit_price": best["expected_price"],
            "vs_median_pct": round((best["expected_price"] / b["p50"] - 1) * 100, 1) if b["p50"] else None,
            "seller_name": best["seller_name"], "contract_no": best["contract_no"],
            "min_join": max(round(target * 0.1, nd), 1),
            "simulated": True, "note": "Boshqa ishtirokchilar demo uchun simulyatsiya qilingan; narx va sotuvchi real lotdan."}


@router.get("/pools")
def pools():
    return {"pools": [p for p in (_pool(fk, rid) for fk, rid in POOL_FAMS) if p]}


@router.get("/pool/{fk}")
def pool_for(fk: str):
    for f, rid in POOL_FAMS:
        if f == fk:
            return _pool(f, rid) or {}
    return {}


# ------------------------------------------------------------------ stateful group buying
from . import pools as pools_mod  # noqa: E402

_POOL_REGION = dict(POOL_FAMS)


def _seed(fk: str, region_id: str | None = None) -> dict | None:
    return _pool(fk, _POOL_REGION.get(fk) or region_id or "TAS")


def pool_state(fk: str, session: str | None = None, region_id: str | None = None) -> dict | None:
    p = pools_mod.ensure(fk, lambda f: _seed(f, region_id))
    return pools_mod.view(p, session) if p else None


def pool_for_deal(item: dict, session: str) -> dict:
    """The deal page shows the real pool: who is in, how much is filled, how much is still missing."""
    f = families()[item["family_key"]]
    target = settle.typical_min_volume(item["family_key"], f["unit"])
    qty = float(item.get("qty") or 0)
    if not item.get("pool") and qty >= target:
        return {"needed": False, "target": target, "text": f"Hajmingiz ({qty:g} {unit_short(f['unit'])}) odatiy minimal bitimdan katta: birga olish shart emas."}
    v = pool_state(item["family_key"], session, item.get("region_id"))
    if not v:
        return {"needed": False, "target": target, "text": "Bu tovar bo'yicha guruh ochib bo'lmadi."}
    joined = v["you"] is not None
    proj_filled = v["filled"] if joined else v["filled"] + qty
    return {**v, "needed": True, "joined": joined, "proj_filled": proj_filled, "proj_pct": min(100, round(proj_filled / v["target"] * 100)),
            "refund": "Muddatgacha to'lmasa, har bir ishtirokchining zakalati avtomatik qaytariladi.",
            "note": "A–J xaridorlar demo uchun simulyatsiya; “(real)” — ilovadan haqiqatan qo'shilganlar."}


class JoinIn(BaseModel):
    family_key: str
    qty: float = Field(gt=0)
    session_id: str = "demo"
    region_id: str | None = None


@router.get("/pools2")
def pools2(session_id: str | None = None):
    return {"pools": [pool_state(fk, session_id) for fk, _ in POOL_FAMS if pool_state(fk, session_id)]}


@router.get("/pool2/{fk}")
def pool2(fk: str, session_id: str | None = None):
    return pool_state(fk, session_id) or {}


@router.post("/pool/join")
def pool_join(body: JoinIn):
    try:
        v = pools_mod.join(body.family_key, body.session_id, body.qty, lambda f: _seed(f, body.region_id))
    except KeyError:
        raise HTTPException(404, "Bu tovar bo'yicha guruh yo'q")
    settle.audit(body.session_id, "guruh", {"summary": f"{v['family_uz']}: guruhga qo'shildi, {v['filled']:g}/{v['target']:g} {v['unit_short']}, {v['members_n']} ishtirokchi"})
    return v


class SimIn(BaseModel):
    family_key: str
    session_id: str = "demo"
    region_id: str | None = None


@router.post("/pool/simulate")
def pool_simulate(body: SimIn):
    return pools_mod.simulate_join(body.family_key, lambda f: _seed(f, body.region_id), body.session_id)


@router.post("/pools/reset")
def pools_reset():
    pools_mod.reset()
    return {"ok": True}
