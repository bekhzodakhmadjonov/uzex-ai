"""End-to-end pipeline with the FakePlanner (no API key needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db  # noqa: E402
from app.agent import answer_question, clean_note, fmt_value, is_empty_result, reason_is_safe, render_template, results_agree  # noqa: E402
from app.fake_llm import FakePlanner  # noqa: E402
from app.hints import detect_language, hints_for, hints_to_text  # noqa: E402


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
    assert r["basis"]["as_of"] == "2026-10-30" and r["basis"]["n_deals"] > 0 and "Asos:" in r["answer"]


def test_pipeline_unanswerable():
    r = answer_question("2026 yil noyabrda nechta bitim?", [], FakePlanner())
    assert r["status"] == "unanswerable" and r["sql"] == ""


def test_broker_code_does_not_crash():  # regression: KeyError('text') in hints_to_text
    text = hints_to_text(hints_for("BK111 brokerning telefon raqami qanday?"))
    assert "BK111" in text


def test_brand_hint_is_exact():
    h = hints_for("Сколько бензина АИ-95 продано?")
    assert [b["key"] for b in h["brands"]] == ["АИ-95"]


def test_without_annulled_is_not_annulled_status():
    assert not hints_for("2025 yilda paxta qancha sotilgan (bekor qilinganlarsiz)?")["statuses"]


def test_identifiers_not_formatted_as_amounts():
    assert fmt_value("year", 2025) == "2025" and fmt_value("deal_no", 247890) == "247890" and fmt_value("buyer_inn", "955082698") == "955082698"
    assert fmt_value("n_deals", 30894) == "30 894" and fmt_value("total", 2.5) == "2,50"
    assert fmt_value("total", 13641937568189.97) == "13 641 937 568 189,97 (13,64 trln)"


def _res():
    return db.run_sql("SELECT 12345 AS n, 'Real Co' AS name, DATE '2025-03-01' AS d")


def test_template_fills_values_from_rows():
    text, problems = render_template("{{0.name}}: {{0.n}} ta, {{0.d}}", _res(), "q", "uz")
    assert problems == [] and text == "Real Co: 12 345 ta, 01.03.2025"


def test_template_rejects_typed_numbers_names_and_unknown_columns():
    assert render_template("Jami 99 999 ta {{0.n}}", _res(), "q", "uz")[1]
    assert render_template("Shundan 17 tasi {{0.n}}", _res(), "q", "uz")[1]  # small numbers count too
    assert render_template('"Toshkent Kimyo" {{0.n}}', _res(), "q", "uz")[1]
    assert render_template("Toshkent Kimyo MChJ {{0.n}}", _res(), "q", "uz")[1]
    assert render_template("{{0.ghost}}", _res(), "q", "uz")[1]
    assert render_template("{{7.n}}", _res(), "q", "uz")[1]
    assert render_template("Jami ko‘p", _res(), "q", "uz")[1]


def test_template_allows_numbers_from_the_question():
    assert render_template("2025 yilda AI-92: {{0.n}}", _res(), "2025 yilda AI-92 narxi?", "uz")[1] == []


def test_refusal_text_cannot_carry_invented_figures():
    assert reason_is_safe("Ma’lumot 30.10.2026 gacha", "noyabr?")
    assert not reason_is_safe("Taxminan 15 000 so‘m bo‘ladi", "narx?")
    assert not reason_is_safe("", "narx?")


def test_zero_aggregate_counts_as_empty():
    assert is_empty_result(db.run_sql("SELECT 0 AS n_deals, 0 AS n_annulled, NULL AS s"))
    assert is_empty_result(db.run_sql("SELECT 1 AS a WHERE false"))
    assert not is_empty_result(db.run_sql("SELECT 5 AS n_deals"))
    assert not is_empty_result(db.run_sql("SELECT 'x' AS name, 0 AS n"))


def test_results_agree():
    a = db.run_sql("SELECT 1402 AS n, 121 AS ann, 10700.25 AS p")
    assert results_agree(a, db.run_sql("SELECT 1402 AS n_deals, 10700.25 AS price"))
    assert not results_agree(a, db.run_sql("SELECT 6590 AS n, 473 AS ann"))


def test_month_shown_as_name():
    assert fmt_value("month", 10, "ru") == "октябрь" and fmt_value("month", 10, "uz") == "oktabr"


def test_refusal_text_cannot_name_tables():
    assert not reason_is_safe("usd_rates jadvalida faqat dollar bor", "yevro?")


def test_notes_have_no_technical_text():
    assert clean_note("Товар: цемент (family_key = 'cement')") == "Товар: цемент"
    assert clean_note("Рейтинг без аннулированных (total_uzs_valid)") == "Рейтинг без аннулированных"
    assert clean_note("family_key = cement") == ""
    assert clean_note("Yil: 2026") == "Yil: 2026"


# ---- regressions from the 05.11 probe -------------------------------------------------------
def test_date_and_timestamp_in_one_row_do_not_crash():
    import datetime as dt
    from app.agent import basis_info
    res = db.QueryResult(["min_date", "max_date", "due"], [(dt.date(2023, 1, 1), dt.datetime(2023, 2, 1), dt.datetime(2023, 3, 1))], 0)
    assert basis_info(res)["period"] == (dt.date(2023, 1, 1), dt.date(2023, 2, 1))
    assert fmt_value("due", dt.datetime(2023, 10, 14)) == "14.10.2023"


def test_deal_date_and_due_date_are_not_a_period():
    import datetime as dt
    from app.agent import basis_info
    assert basis_info(db.QueryResult(["deal_date", "pay_due_date"], [(dt.date(2023, 1, 1), dt.date(2023, 2, 1))], 0))["period"] is None


def test_uzbek_cyrillic_without_special_letters():
    assert detect_language("Пахта нархи канча?") == "uz_cyr"
    assert detect_language("2025 йилда пахта нархи") == "uz_cyr"
    assert detect_language("Сколько сделок в Наманганской области?") == "ru"
    assert detect_language("Top 5 buyers") == "en"
    assert detect_language("top 5 xaridor") == "uz"


def test_cross_check_needs_the_same_answer():
    r = lambda cols, row: db.QueryResult(cols, [row], 0)  # noqa: E731
    assert not results_agree(r(["n_deals", "n_annulled", "total"], (1000, 50, 5e6)), r(["n_deals", "n_annulled", "total"], (1000, 50, 9e6)))
    assert not results_agree(r(["buyer_name", "total"], ("X MChJ", 5e9)), r(["buyer_name", "total"], ("Y MChJ", 5e9)))
    assert not results_agree(r(["n_deals"], (115763,)), r(["n_deals"], (115700,)))
    assert results_agree(r(["buyer_name", "n_deals", "total"], ("X", 10, 5e9)), r(["buyer_name", "total"], ("X", 5e9)))
    assert results_agree(r(["avg_price"], (10700.2534,)), r(["avg_price"], (10700.25,)))


def test_invented_source_and_table_name_are_rejected():
    res = db.QueryResult(["rate"], [(12666.9,)], 0)
    assert render_template("Markaziy bank kursi {{0.rate}} so‘m.", res, "kurs qancha", "uz")[1]
    assert render_template("{{0.rate}} so‘m, usd_rates bo‘yicha.", res, "kurs qancha", "uz")[1]
    assert not render_template("Dollar kursi {{0.rate}} so‘m.", res, "kurs qancha", "uz")[1]


def test_units_and_currency_print_in_the_answer_language():
    assert fmt_value("unit", "тонна", "uz") == "tonna" and fmt_value("unit", "штука", "uz") == "dona" and fmt_value("unit", "тонна", "ru") == "тонна"
    assert fmt_value("currency", "USD", "uz") == "dollar"


def test_group_words_cover_all_their_families():
    assert hints_for("Сколько сделок по маслу?")["groups"][0]["members"] == ("cottonseed_oil", "sunflower_oil")
    assert not hints_for("Подсолнечное масло, сколько сделок?")["groups"]
    assert hints_for("O‘g‘itlar bo‘yicha jami summa")["groups"][0]["members"] == ("urea", "ammonium_nitrate")
    assert not hints_for("Dizel yoqilg‘isi narxi")["groups"]
    assert [f["key"] for f in hints_for("Dizel yoqilg‘isi narxi")["families"]] == ["diesel"]
    assert [f["key"] for f in hints_for("Paxta yog‘i narxi")["families"]] == ["cottonseed_oil"]


def test_company_found_in_any_case_and_script():
    for q in ("zoket invest nechta bitim", "ЗОКЕТ ИНВЕСТ", "Сколько сделок у Zoket Invest?"):
        assert hints_for(q)["companies"][0]["candidates"][0]["inn"] == "983531029", q
    assert hints_for("chulo‘ser impeks nechta bitim")["companies"][0]["confident"]


def test_ordinary_capitalised_words_are_not_companies():
    for q in ("Sement narxi 2025 yilda qancha?", "Top 5 sotuvchi", "Gaz bo‘yicha nechta bitim?", "Чему равен лот муки?", "Metall mahsulotlari"):
        assert not hints_for(q)["companies"] and not hints_for(q)["ambiguous"], q


def test_unknown_company_is_marked_as_no_exact_match():
    assert "NOT an exact match" in hints_to_text(hints_for("Oltin Invest MChJ nechta bitim tuzgan?"))


def test_false_entity_matches_are_gone():
    assert not hints_for("2024 йилда нечта битим тузилган?")["families"]  # битим is not битум
    assert not hints_for("Какой регион лидирует?")["regions"]
    assert [r["key"] for r in hints_for("Fargona")["regions"]] == ["FAR"] and not hints_for("Fargona")["families"]


def test_negated_status_is_not_that_status():
    for q in ("Bekor qilinmagan bitimlar soni", "Сколько сделок не аннулировано?"):
        h = hints_for(q)
        assert not h["statuses"] and h["notes"], q


def test_tashkent_city_province_or_both():
    assert [r["key"] for r in hints_for("Toshkent viloyatida nechta bitim")["regions"]] == ["TAO"]
    assert [r["key"] for r in hints_for("Toshkent shahrida nechta bitim")["regions"]] == ["TAS"]
    assert [r["key"] for r in hints_for("Ташкентская область сделки")["regions"]] == ["TAO"]
    h = hints_for("Toshkentda nechta bitim")
    assert {r["key"] for r in h["regions"]} == {"TAS", "TAO"} and h["ambiguous"]


def test_brands_found_in_any_script_and_separator():
    for q, b in (("ai92 narxi", "АИ-92"), ("АИ 92 цена", "АИ-92"), ("ДТ-Е-К5 narxi", "ДТ-Е-К5"), ("ЦЕМ II/В-К 32,5 Н", "ЦЕМ II/В-К 32,5 Н"), ("БНД 90/130", "БНД 90/130")):
        assert b in [x["key"] for x in hints_for(q)["brands"]], q


class _Stub:
    """A planner with scripted plans, to exercise how the two models are reconciled."""
    def __init__(self, sql="", refuse=False, choice="A"):
        self.sql, self.refuse, self.choice, self.asked = sql, refuse, choice, []

    def plan(self, question, lang, hints_text, history, error=None):
        if self.refuse:
            return {"sql": "", "assumptions": [], "filters_human": [], "unanswerable": True, "reason": "Reyestrda bunday ma’lumot yo‘q.", "clarify": "", "chart": {"type": "none", "x": "", "y": ""}}
        return {"sql": self.sql, "assumptions": [], "filters_human": [], "unanswerable": False, "reason": "", "clarify": "", "chart": {"type": "none", "x": "", "y": ""}}

    def compose(self, *a, **k):
        return "Jami {{0.n}}."

    def adjudicate(self, question, a, b):
        self.asked.append((a, b))
        return self.choice


COUNT = "SELECT count(*) AS n FROM deals WHERE year=2025"


def test_refusal_is_overruled_when_the_data_holds_the_answer():
    p = _Stub(refuse=True, choice="B")
    r = answer_question("savol", [], p, "uz", _Stub(COUNT))
    assert p.asked and r["status"] == "ok" and r["check"] == "adjudicated" and "115 763" in r["answer"]


def test_refusal_stands_when_the_primary_keeps_it():
    r = answer_question("savol", [], _Stub(refuse=True, choice="A"), "uz", _Stub(COUNT))
    assert r["status"] == "unanswerable" and r["check"] == "adjudicated" and not r["sql"]


def test_answer_can_be_replaced_by_the_second_models_refusal():
    r = answer_question("savol", [], _Stub(COUNT, choice="B"), "uz", _Stub(refuse=True))
    assert r["status"] == "unanswerable"


def test_both_refuse_needs_no_adjudication():
    p = _Stub(refuse=True)
    assert answer_question("savol", [], p, "uz", _Stub(refuse=True))["status"] == "unanswerable" and not p.asked


def test_failed_primary_sql_falls_back_to_second_model():
    r = answer_question("savol", [], _Stub("SELECT no_such_column FROM deals"), "uz", _Stub(COUNT))
    assert r["status"] == "ok" and "115 763" in r["answer"]


def test_hung_model_cannot_exceed_the_budget(monkeypatch):
    import threading, time
    import app.agent as A
    monkeypatch.setattr(A, "PLAN_WAIT_S", 0.3); monkeypatch.setattr(A, "VERIFY_WAIT_S", 0.3)
    release = threading.Event()
    class Hung(_Stub):
        def plan(self, *a, **k):
            release.wait(5)
            return super().plan(*a, **k)
    t = time.time()
    r = answer_question("savol", [], Hung(COUNT), "uz", Hung(COUNT))
    release.set()
    assert time.time() - t < 2 and r["status"] == "llm_error"


def test_leftover_placeholder_and_total_as_part_are_rejected():
    res = db.QueryResult(["x_valid", "x_all", "n_deals"], [(1.0, 2.0, 3)], 0)
    assert render_template("Оборот за {{2025}} год: {{0.x_valid}}", res, "2025", "ru")[1]
    assert render_template("{{0.x_valid}} dona, shundan {{0.x_all}} dona bekor qilingan", res, "q", "uz")[1]
    assert not render_template("{{0.x_valid}} dona, bekor qilinganlar bilan birga {{0.x_all}} dona, jami {{0.n_deals}}", res, "q", "uz")[1]


def test_polish_fixes_grammar_and_leaves_numbers_alone():
    from app.agent import polish
    assert polish("Цена 951 290,08 сум за тонна, в месяц: февраль, в октябрь", "ru") == "Цена 951 290,08 сум за тонну, в феврале, в октябре"
    assert polish("зарегистрировано 2 396 сделок и 964 сделок, 21 сделок, 111 сделок, 500 сделок", "ru") == "зарегистрировано 2 396 сделок и 964 сделки, 21 сделка, 111 сделок, 500 сделок"
    assert polish("LO‘HI (INN: 955082698)", "uz") == "LO‘HI (STIR: 955082698)"


def test_no_records_line_names_records_when_the_query_is_not_about_deals():
    r = answer_question("savol", [], _Stub("SELECT code FROM brokers WHERE code='BK999'"), "ru")
    assert r["status"] == "no_rows" and "записей" in r["answer"]
    r = answer_question("savol", [], _Stub("SELECT deal_no FROM deals WHERE deal_no=1"), "ru")
    assert "сделок" in r["answer"]


def test_filter_note_may_name_the_company_from_the_hints():
    assert reason_is_safe('Sotuvchi: "Mo‘jotat Klaster" MChJ', "Mo‘jotat Klaster nechta", 'company inn=937937230 "Mo‘jotat Klaster" MChJ')
    assert not reason_is_safe('Sotuvchi: "Boshqa" MChJ', "Mo‘jotat Klaster nechta")


def test_template_answer_hides_period_columns():
    import datetime as dt
    from app.agent import template_answer
    a = template_answer("ru", db.QueryResult(["n_deals", "min_deal_date"], [(5, dt.date(2022, 1, 3))], 0))
    assert "5" in a and "min deal date" not in a


def test_plain_count_cannot_be_called_excluding_annulled():
    res = db.QueryResult(["deals_2024", "n_valid"], [(8220, 7643)], 0)
    assert render_template("Без аннулированных: в 2024 году {{0.deals_2024}} сделок", res, "q", "ru")[1]
    assert not render_template("Без аннулированных: {{0.n_valid}} сделок", res, "q", "ru")[1]


def test_year_from_the_query_and_data_range_may_be_typed_but_nothing_else():
    res = db.QueryResult(["n"], [(5,)], 0)
    sql = "SELECT count(*) AS n FROM deals WHERE year=2025"
    assert not render_template("2025 году {{0.n}}, на 30.10.2026", res, "q", "ru", sql)[1]
    assert render_template("2024 году {{0.n}}", res, "q", "ru", sql)[1]
    assert render_template("{{0.n}} и ещё 77", res, "q", "ru", sql)[1]


def test_regions_print_in_the_answer_language():
    assert fmt_value("region_uz", "Toshkent shahri", "ru") == "г. Ташкент"
    assert fmt_value("region_ru", "Ферганская область", "uz") == "Farg‘ona viloyati"
    assert fmt_value("region_uz", "Buxoro viloyati", "uz") == "Buxoro viloyati"
    assert fmt_value("region_uz", "Buxoro viloyati", "uz_cyr") != "Buxoro viloyati"


def test_a_name_cannot_take_a_value_from_another_row():  # regression: {{1.name}} (..., {{0.region_uz}}) printed a wrong region
    res = db.QueryResult(["name", "region"], [("A", "x"), ("B", "y")], 0)
    assert render_template("{{0.name}} {{0.region}}\n{{1.name}} {{0.region}}", res, "q", "uz")[1]
    assert not render_template("{{0.name}} {{0.region}}\n{{1.name}} {{1.region}}", res, "q", "uz")[1]
    assert not render_template("{{0.name}} {{0.region}}, {{1.name}} {{1.region}}", res, "q", "uz")[1]
