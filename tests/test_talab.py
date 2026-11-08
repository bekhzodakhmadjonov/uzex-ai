"""Offline tests for UZEX TALAB (no LLM, no network)."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402
from app.talab import lots, market, settle, trust  # noqa: E402
from app.talab.common import q1, today
from app.talab.parse import parse

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _item(**kw):
    r = parse("Dekabrgacha Samarqandga 8 t sement kerak, tonnasi 950 mingdan oshmasin", ref=date(2026, 10, 30))
    it = r["items"][0]
    it.update(kw)
    return it


def test_parser_dev_set_all_correct():
    sys.path.insert(0, str(ROOT / 'tests'))
    from talab_parse_eval import run
    res = run("dev")
    assert res["exact"] == res["n"], res["fails"]


def test_parser_asks_instead_of_guessing():
    r = parse("menga sement kerak", ref=date(2026, 10, 30))
    fields = {x["field"] for x in r["questions"]}
    assert {"qty", "deadline", "region"} <= fields


def test_band_matches_direct_sql():
    b = market.band("cement", "SAM")
    t = today()
    r = q1("""select count(*) n, quantile_cont(unit_price,0.5) p50 from deals where family_key='cement' and unit='тонна'
              and not is_annulled and deal_date>? and deal_date<=? and region_id='SAM'""", [t - timedelta(weeks=8), t])
    assert b["n"] == r["n"] and abs(b["p50"] - r["p50"]) < 1e-6


def _lot(**kw):
    base = lots.candidate_lots("cement", "SAM")[0]
    return {**base, **kw}


GOOD_TRUST = {"score": 90}


def test_guard_rejects_price_above_cap():
    g = lots.guard(_item(price_max=500_000), _lot(), GOOD_TRUST)
    assert g["verdict"] == "reject" and any(c["code"] == "price" and c["status"] == "fail" for c in g["checks"])


def test_guard_rejects_low_trust_seller():
    g = lots.guard(_item(price_max=5_000_000), _lot(), {"score": 30})
    assert g["verdict"] == "reject" and any(c["code"] == "trust" and c["status"] == "fail" for c in g["checks"])


def test_guard_rejects_too_slow_delivery():
    g = lots.guard(_item(price_max=5_000_000, deadline=(today() + timedelta(days=3)).isoformat()), _lot(), GOOD_TRUST)
    assert any(c["code"] == "deadline" and c["status"] == "fail" for c in g["checks"])


def test_injection_text_does_not_change_the_verdict():
    item = _item(price_max=5_000_000)
    clean = lots.guard(item, _lot(spec="Portlandsement M400"), GOOD_TRUST)
    dirty = lots.guard(item, _lot(spec=lots.INJECTION_SPEC), GOOD_TRUST)
    assert clean["verdict"] == dirty["verdict"]
    assert any(c["code"] == "text" for c in dirty["checks"])
    expensive = lots.guard(_item(price_max=500_000), _lot(spec=lots.INJECTION_SPEC), GOOD_TRUST)
    assert expensive["verdict"] == "reject"


def test_match_contains_rejected_test_lot():
    r = lots.match(_item())
    test = [x for x in r["results"] if x["lot"]["is_test"]]
    assert test and test[0]["guard"]["verdict"] == "reject"


def test_trust_reasons_add_up_and_new_buyer_has_no_score():
    inn = lots.candidate_lots("cement", "SAM")[0]["seller_inn"]
    t = trust.trust(inn, "seller")
    assert t["score"] == sum(r["points"] for r in t["reasons"]) and 0 <= t["score"] <= 100
    assert trust.trust("__new__", "buyer")["score"] is None


def test_deposit_tiers():
    assert trust.deposit_for(10, {"tier": "high"})["pct"] == 7
    assert trust.deposit_for(10, {"tier": "low"})["pct"] == 15
    assert trust.deposit_for(10, {"tier": "yangi"})["pct"] == 10


def test_pool_reaches_target():
    it = _item()
    p = settle.pool(it, lots.candidate_lots("cement", "SAM")[0])
    assert p["needed"] and p["filled"] >= p["target"] - 0.1 and p["members"][0]["name"] == "Siz"


def test_audit_chain_links():
    settle.audit("t-chain", "a", {"summary": "1"})
    settle.audit("t-chain", "b", {"summary": "2"})
    ch = settle.audit_log("t-chain")
    assert ch[1]["prev"] == ch[0]["hash"]


def test_arbiter_cites_clause_171_and_requires_human():
    a = settle.arbiter()
    assert any(r["clause"] == "171" for r in a["rules"]) and "inson" in a["human"]


@pytest.mark.parametrize("path", ["/", "/tahlil", "/api/talab/meta", "/api/talab/evidence", "/api/talab/arbiter"])
def test_pages_and_get_endpoints(path):
    assert client.get(path).status_code == 200


def test_full_flow_endpoints():
    r = client.post("/api/talab/parse", json={"text": "Dekabrgacha Samarqandga 8 t sement kerak, tonnasi 950 mingdan oshmasin", "session_id": "flow"}).json()
    it = r["items"][0]
    m = client.post("/api/talab/market", json={"item": it}).json()
    assert m["band"]["n"] > 0 and m["signal"]["action"] in ("buy", "wait", "none")
    mt = client.post("/api/talab/match", json={"item": it, "session_id": "flow"}).json()
    assert mt["best"]
    d = client.post("/api/talab/deal", json={"item": it, "contract_no": mt["best"], "session_id": "flow"}).json()
    assert d["total"] > d["amount"] > 0
    chain = client.get("/api/talab/audit/flow").json()["chain"]
    assert [c["event"] for c in chain] == ["talab", "qoriqchi", "imzo"]


def test_evidence_file_is_honest_about_prediction():
    ev = json.loads((ROOT / "data" / "talab_evidence.json").read_text(encoding="utf-8"))
    rates = [b["annul_2025_pct"] for b in ev["history_predicts"]["buckets"]]
    assert max(rates) - min(rates) < 1.0   # history does not separate buyers here; the UI must not claim it does


# ---------------------------------------------------------------- AI layer (no network)
def test_ai_off_falls_back_to_code(monkeypatch):
    from app.talab import ai
    monkeypatch.setenv("UZEX_TALAB_AI", "0")
    monkeypatch.delenv("UZEX_TALAB_FAKE_AI", raising=False)
    r = ai.understand("Dekabrgacha Samarqandga 8 t sement kerak")
    assert r["source"] == "code" and r["items"][0]["qty"] == 8 and "AI" in r["reason"]


def test_ai_fake_mode_merges_with_code(monkeypatch):
    from app.talab import ai
    monkeypatch.setenv("UZEX_TALAB_FAKE_AI", "1")
    r = ai.understand("Uy qurayapman, Samarqand. Sement 8 t, armatura 1,2 t, g'isht 12 000 dona. Dekabrgacha.")
    assert r["source"] == "fake" and [i["family_key"] for i in r["items"]] == ["cement", "rebar", "brick"]
    assert all(c["status"] == "ok" for c in r["checks"])


def test_ai_number_conflict_code_wins(monkeypatch):
    from app.talab import ai
    monkeypatch.setenv("UZEX_TALAB_FAKE_AI", "1")
    real = ai._fake

    def lying(text, kind):
        d = real(text, kind)
        d["items"][0]["qty"] = 80  # the model "hallucinates" 80 t instead of 8 t
        return d
    monkeypatch.setattr(ai, "_fake", lying)
    r = ai.understand("Dekabrgacha Samarqandga 8 t sement kerak")
    assert r["items"][0]["qty"] == 8 and any(c["status"] == "warn" for c in r["checks"])


def test_grounding_rejects_invented_numbers():
    from app.talab.ai import _grounded
    facts = "P25 946 654 so'm, mediana 988 664 so'm"
    assert _grounded("Odatiy narx 988 664 so'm atrofida.", facts)
    assert not _grounded("Narx 1 200 000 so'mga chiqadi.", facts)


def test_mask_personal_data():
    from app.talab.ai import mask
    assert "[STIR]" in mask("STIR 984967317") and "[telefon]" in mask("+998 90 123 45 67")


def test_chat_mode_for_greeting_and_price_question(monkeypatch):
    from app.talab import ai
    monkeypatch.setenv("UZEX_TALAB_AI", "0")
    monkeypatch.delenv("UZEX_TALAB_FAKE_AI", raising=False)
    g = ai.understand("salom")
    assert g["mode"] == "chat" and g["items"] == [] and g["suggestions"]
    p = ai.understand("sement narxi qancha?")
    assert p["mode"] == "chat" and p["facts"]["family_key"] == "cement" and p["facts"]["p25"] < p["facts"]["p75"]
    n = ai.understand("Dekabrgacha Samarqandga 8 t sement kerak")
    assert n.get("mode") != "chat" and n["items"][0]["qty"] == 8


def test_group_buy_lifecycle_is_real_state():
    client.post("/api/talab/pools/reset")
    before = client.get("/api/talab/pool2/flour?session_id=t-pool").json()
    j = client.post("/api/talab/pool/join", json={"family_key": "flour", "qty": 1000, "session_id": "t-pool"}).json()
    assert j["members_n"] == before["members_n"] + 1 and j["you"]["position"] == j["members_n"]
    assert abs(j["filled"] - (before["filled"] + 1000)) < 1 and abs(j["remaining"] - (j["target"] - j["filled"])) < 1
    again = client.post("/api/talab/pool/join", json={"family_key": "flour", "qty": 1200, "session_id": "t-pool"}).json()
    assert again["members_n"] == j["members_n"] and again["event"] == "updated"   # same session = same member
    for _ in range(10):
        s = client.post("/api/talab/pool/simulate", json={"family_key": "flour"}).json()
        if s["status"] == "full":
            break
    assert s["status"] == "full" and s["remaining"] == 0
    item = {"family_key": "flour", "qty": 1200, "unit": "килограмм", "unit_short": "kg", "region_id": "FAR", "pool": True,
            "pool_contract": s["contract_no"], "price_max": 9000, "deadline": "2026-12-25", "min_trust": 60}
    assert lots.match(item)["best"] == s["contract_no"]   # the agent buys the group's lot
    client.post("/api/talab/pools/reset")
