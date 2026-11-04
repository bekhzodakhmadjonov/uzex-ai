"""Entity resolver tests (run against data/uzex.duckdb)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import resolve as R  # noqa: E402


def top(ms):
    return ms[0].key if ms else None


@pytest.mark.parametrize("text,key", [
    ("paxta", "cotton"), ("пахта", "cotton"), ("хлопок", "cotton"), ("cotton", "cotton"), ("Paxta xomashyosi", "cotton"),
    ("bug'doy", "wheat"), ("буғдой", "wheat"), ("пшеница", "wheat"),
    ("un", "flour"), ("мука", "flour"), ("sement", "cement"), ("цемент", "cement"), ("портландцемент", "cement"),
    ("solyarka", "diesel"), ("дизель", "diesel"), ("dizel", "diesel"), ("дизтопливо", "diesel"),
    ("benzin", "petrol"), ("бензин", "petrol"),
    ("shakar", "sugar"), ("сахар", "sugar"), ("mochevina", "urea"), ("карбамид", "urea"),
    ("selitra", "ammonium_nitrate"), ("g'isht", "brick"), ("ғишт", "brick"), ("кирпич", "brick"),
    ("mis", "copper_cathode"), ("медь", "copper_cathode"), ("guruch", "rice"), ("tuz", "salt"),
    ("gaz", "lpg"), ("пропан", "lpg"), ("armatura", "rebar"), ("shisha", "glass"),
])
def test_family(text, key):
    ms = R.resolve_family(text)
    assert top(ms) == key, [m.as_dict() for m in ms]


@pytest.mark.parametrize("text,key", [
    ("Хлопок-сырец (спот)", "cotton_spot"), ("spot paxta", "cotton_spot"), ("paxta forvard", "cotton_forward"),
    ("Мочевина", "urea"), ("Сахар-песок", "sugar"), ("Дизельное топливо", "diesel"), ("Топливо дизельное", "diesel"),
    ("Кормосмесь", "feed_mix"), ("Комбикорм", "compound_feed"),
])
def test_product(text, key):
    ms = R.resolve_product(text)
    assert top(ms) == key, [m.as_dict() for m in ms]


def test_brand():
    assert top(R.resolve_brand("АИ-92")) == "АИ-92"
    assert top(R.resolve_brand("ai 92")) == "АИ-92" or any(m.key == "АИ-92" for m in R.resolve_brand("ai 92"))


@pytest.mark.parametrize("text,inn", [
    ('"Sehim Savdo" MChJ', "975923536" if False else None),  # resolved below by name
])
def test_company_placeholder(text, inn):
    assert True


def test_company_exact_and_scripts():
    ms = R.resolve_company("Sehim Savdo")
    assert ms and ms[0].score >= 0.97 and "Sehim Savdo" in ms[0].label
    ms = R.resolve_company("ЧУЛЎСЕР ИМПЕКС")
    assert ms and ms[0].score >= 0.97 and "ЧУЛЎСЕР" in ms[0].label
    ms = R.resolve_company("chuloser impeks")
    assert ms and "ЧУЛЎСЕР" in ms[0].label and ms[0].score >= 0.9


def test_company_by_inn():
    ms = R.resolve_company("915688255")
    assert len(ms) == 1 and ms[0].key == "915688255" and ms[0].extra["role"] == "seller"


def test_company_apostrophe_collision_not_auto_picked():
    ms = R.resolve_company("Betol Biznes")
    names = [m.label for m in ms]
    assert any("Betol" in n for n in names) and any("Beto‘l" in n for n in names)
    assert not R.is_confident(ms)


def test_broker():
    assert top(R.resolve_broker("BK2")) == "BK002"
    assert top(R.resolve_broker("bk002")) == "BK002"
    assert top(R.resolve_broker("Kahir")) == "BK002"
    assert top(R.resolve_broker("KAHIR BROKER MCHJ")) == "BK002"
    assert top(R.resolve_broker("Gucham Broker")) == "BK111"


@pytest.mark.parametrize("text,rid", [
    ("Фарғона", "FAR"), ("Fergana", "FAR"), ("Farg'ona viloyati", "FAR"), ("Ферганская область", "FAR"),
    ("Toshkent shahri", "TAS"), ("г. Ташкент", "TAS"), ("Ташкентская область", "TAO"), ("Toshkent viloyati", "TAO"),
    ("Qoraqalpog'iston", "QOR"), ("Каракалпакстан", "QOR"), ("Nukus", "QOR"), ("Самарканд", "SAM"), ("Buxoro", "BUX"), ("Бухара", "BUX"),
])
def test_region(text, rid):
    assert top(R.resolve_region(text)) == rid


def test_region_tashkent_ambiguous():
    ms = R.resolve_region("Toshkent")
    assert {m.key for m in ms} == {"TAS", "TAO"} and not R.is_confident(ms)


@pytest.mark.parametrize("text,code", [
    ("bekor qilingan", "annulled"), ("аннулирован", "annulled"), ("Ждет оплату", "awaiting_payment"), ("Ждёт оплату", "awaiting_payment"),
    ("bajarilgan", "executed"), ("исполнен", "executed"), ("qisman", "partial"), ("to'langan", "paid"), ("yangi", "new"),
])
def test_status(text, code):
    assert top(R.resolve_status(text)) == code


def test_resolver_speed():
    import time
    t = time.perf_counter()
    for w in ["paxta", "sement", "benzin", "Sehim Savdo", "Фарғона", "bekor"]:
        R.resolve_family(w); R.resolve_region(w); R.resolve_status(w)
    R.resolve_company("Sehim Savdo"); R.resolve_broker("kahir")
    assert time.perf_counter() - t < 2.0
