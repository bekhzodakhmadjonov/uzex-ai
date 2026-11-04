"""End-to-end pipeline with the FakePlanner (no API key needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db  # noqa: E402
from app.agent import allowed_numbers, answer_question, unverified_numbers  # noqa: E402
from app.fake_llm import FakePlanner  # noqa: E402
from app.hints import detect_language, hints_for  # noqa: E402


def test_language_detection():
    assert detect_language("2025 yilda paxta qancha sotilgan?") == "uz"
    assert detect_language("Сколько хлопка продано в 2025?") == "ru"
    assert detect_language("2025 йилда пахта қанча сотилган?") == "uz_cyr"
    assert detect_language("How many deals in 2025?") == "en"


def test_hints_resolve_entities():
    h = hints_for("2025 yilda Farg‘onada paxta qancha sotilgan?")
    assert any(f["key"] == "cotton" for f in h["families"])
    assert any(r["key"] == "FAR" for r in h["regions"])
    assert h["years"] == [2025]
    h = hints_for("Сколько аннулированных сделок у брокера BK111 в октябре 2026?")
    assert any(s["key"] == "annulled" for s in h["statuses"])
    assert any(b["key"] == "BK111" for b in h["brokers"])
    assert h["months"] == [10]
    h = hints_for("Toshkentda nechta bitim?")
    assert h["ambiguous"]


def test_pipeline_aggregate_uz():
    r = answer_question("2025 yilda paxta qancha sotilgan?", [], FakePlanner())
    assert r["status"] == "ok" and r["n_rows"] == 1 and "family_key='cotton'" in r["sql"]
    assert r["basis"]["as_of"] == "2026-10-30" and r["sql"].endswith("LIMIT 200")


def test_pipeline_unanswerable():
    r = answer_question("2026 yil noyabrda nechta bitim?", [], FakePlanner())
    assert r["status"] == "unanswerable" and r["sql"] == ""


def test_number_verification():
    res = db.run_sql("SELECT 12345678 AS a, 2.5 AS b, DATE '2025-03-01' AS d")
    allowed = allowed_numbers(res, "qancha 2025?")
    assert unverified_numbers("Jami 12 345 678 so‘m, 12,3 mln, 2,5 kg, 2025 yil 1 mart.", allowed) == []
    assert unverified_numbers("Jami 99 999 so‘m", allowed) == ["99 999"]
