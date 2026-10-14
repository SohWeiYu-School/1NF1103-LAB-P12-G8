"""
Tests for the combined assessment: the documents, the crime scan, the forecast and the benchmark
working as ONE feature with ONE verdict, and the command line and the web app giving the same result.

No API key and no network: the forecast runs on made-up saved replies (tests/fake_ai.py)
through its real checks, and the crime scan's and benchmark's AI calls are replaced.
"""

import copy
import html
import json
import os
import time
from unittest.mock import patch

import pytest

import main
from app import ai_manager, data_manager, logic_manager, utilities
from tests import fake_ai

SAMPLE = os.path.join(data_manager.sample_dir(), "case_001.json")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Everything the run saves goes to a temporary folder."""
    monkeypatch.setattr(data_manager, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(data_manager, "LOCAL_DB_DIR", str(tmp_path / "db"))
    monkeypatch.setattr(data_manager, "LOCAL_CASES_JSON", str(tmp_path / "db" / "cases.json"))
    monkeypatch.setattr(data_manager, "LOCAL_ASSESSMENTS_JSON", str(tmp_path / "db" / "assessments.json"))
    monkeypatch.setattr(data_manager, "DOCUMENTS_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def client_record(**claims) -> dict:
    record = json.load(open(SAMPLE))
    record["claims"].update({"biggest_wealth_jump": 3_000_000, "wealth_jump_years": 2,
                             "declared_counterparties": ["Straits Harbour Logistics Pte Ltd"], **claims})
    return record


def save_fake_forecast(record: dict, **options) -> dict:
    case, missing = logic_manager.forecasting_case(record, main.today())
    assert missing == []
    data_manager.save_raw_replies(case["client_ref"],
                                  {k: {"raw": v} for k, v in fake_ai.forecast_replies(case, **options).items()})
    return case


def fake_ai_calls(benchmark=None, typologies=None):
    """Replace the crime scan's and the benchmark's AI calls."""
    return (patch("app.ai_manager.get_ai_research", return_value=(fake_ai.research(), [])),
            patch("app.ai_manager.get_typologies", return_value=(typologies or fake_ai.typologies(), [])),
            patch("app.ai_manager.get_ai_benchmark",
                  return_value=fake_ai.benchmark() if benchmark is None else benchmark))


def run(case: dict, benchmark=None, typologies=None) -> dict:
    research, typology, bench = fake_ai_calls(benchmark, typologies)
    with research, typology, bench:
        return main.run_combined_assessment(case)


def check(verdict: dict, feature: str, name: str) -> dict:
    return next(c for c in verdict[feature] if c["check"] == name)


# ---------------------------------------------------------------------------
# One verdict from all three features
# ---------------------------------------------------------------------------

def test_one_verdict_with_reasons_from_every_feature(storage):
    case = save_fake_forecast(client_record())
    result = run(case)
    verdict = result["verdict"]
    assert verdict["verdict"] in ("PASSED", "MANUAL_REVIEW", "RISKY", "SERIOUS_RISK")
    assert verdict["reasons"], "the verdict must say why"
    assert {c["feature"] for c in verdict["benchmark"]} == {"benchmark"}
    assert verdict["forecast"] and verdict["crime"]
    assert verdict["not_run"] == []
    titles = [s["title"] for s in result["report"]["sections"]]
    assert titles[0] == "Why"
    assert any(t.startswith("Benchmark") for t in titles) and any(t.startswith("Forecast") for t in titles)
    assert any(t.startswith("Crime") for t in titles)


def test_crime_type_is_built_from_the_researched_typology(storage):
    case = save_fake_forecast(client_record())
    verdict = run(case)["verdict"]
    t2 = next(t for t in verdict["types"] if t["type_id"] == "T2")
    assert t2["typology_id"] == "trade_based_laundering"
    assert t2["match"] == "high"                      # its warning signs fired in the declaration
    t3 = next(t for t in verdict["types"] if t["type_id"] == "T3")
    assert t3["typology_id"] is None                  # no researched pattern behind it
    assert any(c["check"] == "UNBACKED_TYPE" and c["label"].startswith("T3") for c in verdict["crime"])


def test_serious_risk_needs_the_honest_story_to_fail_and_a_second_feature(storage):
    # tiny honest pay, big crime money: only T2 explains him, and its warning signs fired
    case = save_fake_forecast(client_record(), pay_scale=0.2, crime_money=900_000)
    verdict = run(case)["verdict"]
    assert verdict["verdict"] == "SERIOUS_RISK"
    assert "another check agrees" in verdict["summary"]


def test_without_a_second_feature_it_stays_risky(storage):
    # same money, but the pattern's warning signs can't fire and the benchmark finds nothing unexplained
    case = save_fake_forecast(client_record(), pay_scale=0.2, crime_money=900_000)
    quiet = fake_ai.typologies()
    quiet["sector_typologies"][0]["indicators"] = [{"indicator": "related_party_transaction", "weight": "core"},
                                                   {"indicator": "gift_or_inheritance_unnamed", "weight": "supporting"}]
    peers = fake_ai.benchmark(expected_wealth=20_000_000)
    peers.update(expected_velocity=5_000_000, expected_counterparties=["Straits Harbour Logistics Pte Ltd"],
                 expected_jurisdiction=["Singapore", "Hong Kong", "United Arab Emirates"])
    verdict = run(case, benchmark=peers, typologies=quiet)["verdict"]
    assert verdict["verdict"] == "RISKY"
    assert "no other check agrees" in verdict["summary"]


def test_a_benchmark_failure_the_forecast_explains_does_not_count(storage):
    case = save_fake_forecast(client_record())
    verdict = run(case)["verdict"]                    # peers have ~2.4M, he declares 16.5M
    total = check(verdict, "benchmark", "TOTAL_WEALTH")
    assert total["status"] == "explained"            # ...but the honest forecast reaches it
    assert total["explanation"].startswith("Explained")
    assert all("total wealth" not in r["text"].lower() for r in verdict["reasons"])


def test_new_benchmark_checks_are_there(storage):
    case = save_fake_forecast(client_record())
    verdict = run(case)["verdict"]
    names = {c["check"] for c in verdict["benchmark"]}
    assert {"PEP_STATUS", "AUM_FUNDING", "UNEXPLAINED_GAP", "JUMP_VS_ECONOMY"} <= names
    assert {"WEALTH_JUMP", "LIQUID_WEALTH"} <= {c["check"] for c in verdict["forecast"]}


def test_a_pep_is_never_passed(storage):
    case = save_fake_forecast(client_record(pep_status="Foreign PEP"))
    verdict = run(case)["verdict"]
    assert check(verdict, "benchmark", "PEP_STATUS")["status"] == "failed"
    assert logic_manager.SEVERITY[verdict["verdict"]] >= logic_manager.SEVERITY["MANUAL_REVIEW"]
    assert any("PEP" in r["text"] for r in verdict["reasons"])


def test_money_for_the_bank_he_cant_fund_is_risky(storage):
    case = save_fake_forecast(client_record(expected_aum=30_000_000))
    verdict = run(case)["verdict"]
    assert check(verdict, "benchmark", "AUM_FUNDING")["status"] == "failed"
    assert logic_manager.SEVERITY[verdict["verdict"]] >= logic_manager.SEVERITY["RISKY"]


def test_a_part_that_cant_run_means_a_person_must_look(storage):
    case = save_fake_forecast(client_record())
    research, typology, _ = fake_ai_calls()
    with research, typology, patch("app.ai_manager.get_ai_benchmark", return_value=None):
        verdict = main.run_combined_assessment(case)["verdict"]
    assert "The peer benchmark" in verdict["not_run"]
    assert logic_manager.SEVERITY[verdict["verdict"]] >= logic_manager.SEVERITY["MANUAL_REVIEW"]
    # the checks that don't need the peer benchmark still run
    assert check(verdict, "benchmark", "UNEXPLAINED_GAP")["status"] in ("passed", "failed")


def test_same_inputs_give_the_same_verdict(storage):
    case = save_fake_forecast(client_record())
    assert run(case)["report"] == run(case)["report"]


# ---------------------------------------------------------------------------
# The command line and the web app give the same assessment
# ---------------------------------------------------------------------------

def test_cli_and_web_app_give_the_same_report(storage, capsys):
    from app import web_app

    record = client_record()
    save_fake_forecast(record)
    data_manager.save_case_record(copy.deepcopy(record))

    # the command line (menu option 4)
    research, typology, bench = fake_ai_calls()
    with research, typology, bench, patch("builtins.input", return_value="y") as answer:
        cli = main.assess_client_record(copy.deepcopy(record))
    printed = capsys.readouterr().out
    assert "COMBINED ASSESSMENT" in printed and cli["report"]["header"]["verdict_label"] in printed
    assert "Show the full evidence?" in answer.call_args.args[0]       # asked before the evidence
    assert printed.index("FULL EVIDENCE  ") > printed.index("WHAT CAME BACK CLEAN")

    # the web app (Run Assessment button)
    research, typology, bench = fake_ai_calls()
    with research, typology, bench, patch("app.ai_manager.has_api_key", return_value=True):
        browser = web_app.app.test_client()
        reply = browser.post(f"/client/{record['client_ref']}/assess")
        assert reply.status_code == 302
        job_id = reply.headers["Location"].rstrip("/").split("/")[-1]
        for _ in range(200):
            if web_app._assessment_jobs[job_id]["status"] != "running":
                break
            time.sleep(0.05)
        job = web_app._assessment_jobs[job_id]
        assert job["status"] == "done", job.get("error")
        page = browser.get(f"/assess/result/{job_id}").get_data(as_text=True)

    assert job["result"]["report"] == cli["report"]               # the same verdict, reasons and evidence
    saved = [a for a in data_manager.load_all_assessments() if a.get("report")]
    assert len(saved) == 2 and saved[0]["report"] == saved[1]["report"]
    for part in cli["report"]["sections"]:                         # and the page shows every line of it
        for item in part["items"]:
            if "Crime" in part["title"]:
                continue  # crime section renders as a card UI, not plain text items
            assert item["text"] in html.unescape(page), item["text"]
    assert cli["report"]["header"]["verdict_label"] in page


def test_web_page_shows_a_saved_assessment(storage):
    from app import web_app

    record = client_record()
    case = save_fake_forecast(record)
    data_manager.save_case_record(copy.deepcopy(record))
    result = run(case)
    assessment_id = main.save_combined_assessment(result, copy.deepcopy(record))
    page = web_app.app.test_client().get(f"/assessment/{assessment_id}").get_data(as_text=True)
    assert result["report"]["header"]["summary"] in html.unescape(page)


# ---------------------------------------------------------------------------
# The pieces that link the features
# ---------------------------------------------------------------------------

def test_cli_record_without_the_current_job_in_history_gives_a_case():
    record = client_record()
    history = record["career_history"]
    record["career_history"] = history[:-1]           # the command line keeps the current job in "career"
    case, missing = logic_manager.forecasting_case(record, "2026-10-14")
    assert missing == []
    assert case["career_history"][-1]["end_year"] is None
    assert case["career_history"][-1]["start_year"] == history[-2]["end_year"]
    assert case["current_date"] == "2026-10-14"


def test_incomplete_record_says_what_is_missing():
    record = client_record()
    record["claims"]["declared_net_worth"] = ""
    case, missing = logic_manager.forecasting_case(record, "2026-10-14")
    assert case is None and "declared net worth" in missing


def test_declaration_is_read_once_and_reused_by_the_crime_scan():
    declaration = json.loads(fake_ai.forecast_replies(client_record() | {"current_date": "2026-10-14"})
                             ["step0_declaration"])
    rows, warnings = logic_manager.crime_declaration(declaration)
    salary, advisory = rows["sources"]
    assert salary["amount_explained_by_forecast"] is True      # the forecast works out what the job paid
    assert advisory["counterparty"] is None and advisory["amount_explained_by_forecast"] is False
    assert warnings == []


def test_old_saved_declaration_without_counterparties_does_not_count_as_unnamed():
    old = {"claimed_sources": [{"source_id": "S1", "source_type": "other", "description": "x", "start_year": None,
                                "end_year": None, "amount": None, "country": "Singapore",
                                "linked_position_index": None, "linked_asset_index": None, "quote": "x"}]}
    rows, warnings = logic_manager.crime_declaration(old)
    assert rows["sources"][0]["counterparty"] == "(not read)"
    assert warnings


def test_person_types_are_linked_to_typologies_by_id_or_category():
    researched = [{"typology_id": "tbml", "crime_category": "TRADE_BASED_ML"},
                  {"typology_id": "bribes", "crime_category": "BRIBERY_CORRUPTION"}]
    data = {"person_types": [{"type_id": "T1", "category": "HONEST", "typology_id": None},
                             {"type_id": "T2", "category": "TRADE_BASED_ML", "typology_id": "made_up"},
                             {"type_id": "T3", "category": "TAX_EVASION", "typology_id": "bribes"}]}
    linked = ai_manager.link_typologies(data, researched)["person_types"]
    assert linked[1]["typology_id"] == "tbml"                  # unknown id -> linked by its category
    assert linked[2]["typology_id"] == "bribes"
    assert linked[2]["category"] == "BRIBERY_CORRUPTION"      # takes its typology's category


def test_person_types_request_gets_the_typologies():
    case = client_record() | {"current_date": "2026-10-14"}
    case, _ = logic_manager.forecasting_case(case, "2026-10-14")
    replies = fake_ai.forecast_replies(case)
    real = ai_manager.run_forecasting
    with patch("app.ai_manager.run_forecasting", side_effect=real) as spy:
        run_result = ai_manager.run_onboarding_forecast(case, replies, fake_ai.typologies()["sector_typologies"])
    assert run_result["ok"]
    sent = next(c.args[3] for c in spy.call_args_list if c.args[0] == "call3_types.list")
    assert sent["researched_typologies"][0]["typology_id"] == "trade_based_laundering"
    assert sent["researched_typologies"][0]["crime_category"] == "TRADE_BASED_ML"


def test_benchmark_sees_the_economy_but_never_his_figures():
    economy = {"markets": [{"name": "Container shipping", "country": "World", "indicator": "freight index",
                            "yearly_change_pct": {"2020": -12.0}}], "country_listings": []}
    data = {"occupation": "COO", "industry": "Shipping", "age": 51, "career_start_year": 1998,
            "country": "Singapore", "declared_net_worth": 16_500_000, "cash": 20}
    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager.cache_write"), \
         patch("app.ai_manager._ai_chat_completion_pipeline",
               return_value=json.dumps(fake_ai.benchmark())) as call:
        assert ai_manager.get_ai_benchmark(data, economy) is not None
    prompt = call.call_args.args[0]
    assert "Container shipping" in prompt and "-12.0" in prompt
    assert "16500000" not in prompt and "declared_net_worth" not in prompt
    assert "prompt_version" not in prompt                      # notes in the prompt file aren't sent


def test_economy_brief_has_no_client_figures():
    case, _ = logic_manager.forecasting_case(client_record(), "2026-10-14")
    replies = fake_ai.forecast_replies(case)
    markets = {**json.loads(replies["call1_markets.list"]), "series": {
        m: json.loads(replies[f"call1_markets.series.{m}"]) for m in ("ECO_SG", "IND_SHIP", "PROP_SG", "FX_AUD")}}
    brief = logic_manager.economy_brief(markets)
    text = json.dumps(brief)
    assert "Singapore economy" in text
    for figure in ("16500000", "5000000", "declared"):
        assert figure not in text


def test_typology_schema_requires_a_crime_category():
    schema = utilities.load_schema("typology_response.schema.json")
    assert "crime_category" in schema["properties"]["sector_typologies"]["items"]["required"]
    categories = ai_manager.typology_crime_categories()
    assert "HONEST" not in categories and "TRADE_BASED_ML" in categories


# ---------------------------------------------------------------------------
# The documents layer, pay from documents, and the report layout
# ---------------------------------------------------------------------------

MARCUS = os.path.join(os.path.dirname(__file__), "documents", "marcus_lim")


def marcus() -> tuple[dict, dict]:
    """The Marcus Lim specimen: (client record as the form saves it, the correct reading of his documents)."""
    reading = json.load(open(MARCUS + "_expected_extract.json", encoding="utf-8"))
    record, _ = logic_manager.record_from_documents(reading, "CL-MARCUS", main.today())
    record = logic_manager.set_path(record, ["investments", 2, "income_produced"], 0)
    record = logic_manager.finish_document_record(record)
    record["supporting_documents"] = [{"file_name": os.path.basename(p), "file_path": p}
                                      for p in data_manager.list_documents_in_folder(MARCUS)]
    return record, reading


def quiet_peers() -> tuple[dict, dict]:
    """A benchmark and typologies that find nothing, so only the documents can add evidence."""
    peers = fake_ai.benchmark(expected_wealth=14_000_000)
    peers.update(expected_velocity=5_000_000, expected_counterparties=["Straits Harbour Logistics Pte Ltd"],
                 expected_jurisdiction=["Singapore"])
    quiet = fake_ai.typologies()
    quiet["sector_typologies"][0]["indicators"] = [{"indicator": "related_party_transaction", "weight": "core"},
                                                   {"indicator": "gift_or_inheritance_unnamed", "weight": "supporting"}]
    return peers, quiet


def run_marcus(record: dict, reading: dict, benchmark=None, typologies=None, **options) -> dict:
    case = save_fake_forecast(record, **options)
    research, typology, bench = fake_ai_calls(benchmark, typologies)
    with research, typology, bench, \
            patch("app.ai_manager.extract_client_details", return_value=(reading, [])) as read:
        result = main.run_combined_assessment(case, record["supporting_documents"])
    sent = read.call_args.args[0]
    assert len(sent) == 9 and all(d["kind"] == "text" for d in sent)     # every document is read
    return result


def test_marcus_lim_documents_make_it_serious_risk(storage):
    record, reading = marcus()
    record["claims"]["expected_aum"] = 3_000_000     # fundable, so only the documents can add evidence
    peers, quiet = quiet_peers()
    verdict = run_marcus(record, reading, peers, quiet)["verdict"]
    assert verdict["verdict"] == "SERIOUS_RISK"
    assert any(r["level"] == "SERIOUS_RISK" and "documents contradict his declaration" in r["text"]
               for r in verdict["reasons"])
    kinds = {c["check"] for c in verdict["document_findings"] if c["status"] == "failed"}
    assert {"UNDECLARED_COMPANY", "UNDECLARED_INCOME", "FUNDING_CONFLICT", "CASH_STRUCTURING"} <= kinds
    needed = [d["document"] for d in verdict["documents"]]
    assert "Beneficial ownership declaration" in needed


def test_without_documents_the_same_client_is_not_serious(storage):
    record, reading = marcus()
    record["claims"]["expected_aum"] = 3_000_000
    case = save_fake_forecast(record)
    peers, quiet = quiet_peers()
    verdict = run(case, peers, quiet)["verdict"]
    assert verdict["verdict"] != "SERIOUS_RISK"
    assert verdict["document_findings"][0]["check"] == "NO_DOCUMENTS"


def test_unreadable_documents_mean_a_person_must_look(storage):
    record, reading = marcus()
    case = save_fake_forecast(record)
    research, typology, bench = fake_ai_calls()
    with research, typology, bench, patch("app.ai_manager.extract_client_details", return_value=(None, ["x"])):
        verdict = main.run_combined_assessment(case, record["supporting_documents"])["verdict"]
    assert "Reading the submitted documents" in verdict["not_run"]
    assert logic_manager.SEVERITY[verdict["verdict"]] >= logic_manager.SEVERITY["MANUAL_REVIEW"]


def test_pay_comes_from_his_tax_notices_not_the_ai_guess(storage):
    record, reading = marcus()
    result = run_marcus(record, reading, pay_scale=0.5)
    line = check(result["verdict"], "forecast", "PAY_FROM_DOCUMENTS")
    assert "S$186,000" in line["detail"]
    case = result["case"]
    forecast, adjusted = logic_manager.calibrate_pay(
        case, json.loads(json.dumps(result["forecast"])), result["evidence"]["documented_pay"], utilities.policy())
    assert adjusted == []                          # already calibrated: applying it again changes nothing
    current = next(p for p in result["forecast"]["earnings"]["positions"]
                   if p["position_index"] == len(case["career_history"]) - 1)
    row = next(r for r in current["yearly"] if r["year"] == 2025)
    assert logic_manager.pay_total(row, "typical") == pytest.approx(186_000, rel=0.05)


def test_document_reading_is_saved_and_reused(storage):
    documents = [data_manager.read_document_for_extraction(p) for p in data_manager.list_documents_in_folder(MARCUS)]
    reading = json.load(open(MARCUS + "_expected_extract.json", encoding="utf-8"))
    with patch("app.ai_manager.has_api_key", return_value=True), \
         patch("app.ai_manager._openai_document_response", return_value=json.dumps(reading)) as call:
        first, _ = ai_manager.extract_client_details(documents)
        second, _ = ai_manager.extract_client_details(documents)
    assert call.call_count == 1 and first == second == reading


def test_loan_financed_purchases_dont_push_cash_below_zero(storage):
    record, reading = marcus()
    verdict = run_marcus(record, reading)["verdict"]
    funding = check(verdict, "benchmark", "AUM_FUNDING")
    liquid = check(verdict, "forecast", "LIQUID_WEALTH")
    assert "-S$" not in liquid["detail"].split(" to ")[-1]          # the optimistic end is not negative
    assert funding["status"] in ("passed", "failed")


def test_banks_and_law_firms_are_not_unusual_counterparties(storage):
    record, reading = marcus()                    # declared: Meridian Harbour Bank, Tan & Rao LLP
    verdict = run_marcus(record, reading)["verdict"]
    counterparties = check(verdict, "benchmark", "COUNTERPARTIES")
    assert counterparties["status"] == "passed"
    assert "Meridian Harbour Bank" in counterparties["detail"] and "left out" in counterparties["detail"]


def test_benchmark_always_shows_its_9_original_checks(storage):
    case = save_fake_forecast(client_record())
    report = run(case)["report"]
    benchmark = next(s for s in report["sections"] if s["title"].startswith("Benchmark"))
    for item, rule in zip(benchmark["items"][:9], logic_manager.ORIGINAL_BENCHMARK):
        assert item["text"].startswith(logic_manager.BENCHMARK_LABELS[rule] + ":"), item["text"]
    assert any(item["mark"] == "pass" for item in benchmark["items"][:9])     # passed ones are shown too
    assert "Declared S$" in benchmark["items"][0]["text"]                      # with their numbers
    forecast = next(s for s in report["sections"] if s["title"].startswith("Forecast"))
    assert all(item["mark"] != "pass" or "Stability" in item["text"] for item in forecast["items"])
    titles = [s["title"] for s in report["sections"]]
    assert "Where the checks agree" not in titles                   # merged into Why
    assert "Documents - what his papers show" in titles
    assert report["evidence_titles"] and all(
        "converted to" not in line for part in report["evidence"] for line in part["lines"])


def test_the_reader_is_told_to_fill_gaps_from_any_document():
    prompt = utilities.load_prompt("document_extract.txt")
    assert "Read EVERY document for every field" in prompt
    assert "EVIDENCE" in prompt and "documented_pay" in prompt



def test_cli_and_web_app_read_the_documents_the_same_way(storage):
    from app import web_app

    record, reading = marcus()
    save_fake_forecast(record)
    data_manager.save_case_record(copy.deepcopy(record))
    research, typology, bench = fake_ai_calls()
    reader = patch("app.ai_manager.extract_client_details", return_value=(reading, []))
    with research, typology, bench, reader, patch("builtins.input", return_value="n"):
        cli = main.assess_client_record(copy.deepcopy(record))
    research, typology, bench = fake_ai_calls()
    reader = patch("app.ai_manager.extract_client_details", return_value=(reading, []))
    with research, typology, bench, reader, patch("app.ai_manager.has_api_key", return_value=True), \
            patch("app.utilities.check_rate_limit", return_value=(True, 0)):
        browser = web_app.app.test_client()
        job_id = browser.post(f"/client/{record['client_ref']}/assess").headers["Location"].rstrip("/").split("/")[-1]
        for _ in range(200):
            if web_app._assessment_jobs[job_id]["status"] != "running":
                break
            time.sleep(0.05)
        page = html.unescape(browser.get(f"/assess/result/{job_id}").get_data(as_text=True))
    assert web_app._assessment_jobs[job_id]["result"]["report"] == cli["report"]
    assert "Documents - what his papers show" in page and "Arcadia Holdings Ltd" in page


# ---------------------------------------------------------------------------
# One model, web search from .env, repairs and fail-safes
# ---------------------------------------------------------------------------

def test_every_ai_call_uses_the_one_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "one-model")
    monkeypatch.setenv("OPENAI_RESEARCH_MODEL", "other")      # old settings are ignored
    monkeypatch.setenv("OPENAI_FORECAST_MODEL", "other")
    monkeypatch.setenv("OPENAI_EXTRACT_MODEL", "other")
    assert {utilities.model(), utilities.research_model(), utilities.extract_model(),
            utilities.forecast_model()} == {"one-model"}
    with patch("app.ai_manager._openai_search_response", return_value=(None, None)) as search, \
         patch("app.ai_manager._gemini_search_response", return_value=(None, None)):
        ai_manager._ai_search_pipeline("prompt", {})
    assert search.call_args.args[1] == "one-model"


def test_web_search_is_switched_from_env(monkeypatch):
    monkeypatch.setenv("FORECAST_WEB_SEARCH", "on")
    assert utilities.forecast_settings()["web_search"] is True
    monkeypatch.setenv("FORECAST_WEB_SEARCH", "off")
    assert utilities.forecast_settings()["web_search"] is False
    monkeypatch.delenv("FORECAST_WEB_SEARCH")
    assert utilities.forecast_settings()["web_search"] == utilities.policy()["forecasting"]["web_search"]


def test_model_that_refuses_temperature_zero_is_asked_again():
    calls = []

    def create(**request):
        calls.append(request)
        if "temperature" in request:
            raise ValueError("Unsupported value: 'temperature' does not support 0 with this model.")
        return "reply"
    assert ai_manager._create_at_temperature_zero(create, model="m") == "reply"
    assert calls == [{"temperature": 0, "model": "m"}, {"model": "m"}]


def test_the_bishan_reply_from_the_log_is_repaired_not_rejected():
    markets = {"ECO_SG", "PROP_BISHAN", "PROP_SG", "FX_SGD", "IND_PARTS", "STOCK_SG"}
    reply = {"asset_index": 0, "exposures": [
        {"market_id": m, "sensitivity": {"low": 2, "typical": 5 - n * 0.5, "high": 10}} for n, m in enumerate(sorted(markets))],
        "own_growth_pct": {"low": 0, "typical": 1, "high": 2},
        "income_yield_by_year": [{"year": y, "yield_pct": {"low": 2, "typical": 3, "high": 4}} for y in range(2010, 2027)],
        "future_income_yield_pct": {s: {"low": 2, "typical": 3, "high": 4} for s in ("steady", "boom", "downturn")},
        "valuation_basis": "Bishan property index"}
    repaired = ai_manager.repair_asset_model(reply, markets)
    assert len(repaired["exposures"]) == utilities.forecast_settings()["max_asset_exposures"]
    assert all(v <= 3.0 for e in repaired["exposures"] for v in e["sensitivity"].values())
    with patch("app.ai_manager.forecast_settings", return_value={**utilities.forecast_settings()}):
        assert ai_manager.check_asset_model(repaired, 0, list(range(2010, 2027)), markets) == []


def test_one_asset_the_ai_cant_describe_doesnt_stop_the_forecast(storage):
    case = save_fake_forecast(client_record())
    replies = data_manager.load_raw_replies(case["client_ref"])
    replies["call2_earnings.asset.0"] = json.dumps({"asset_index": 0})          # unusable, and no API key
    data_manager.save_raw_replies(case["client_ref"], {k: {"raw": v} for k, v in replies.items()})
    result = run(case)
    assert result["forecast"] is not None                                          # the forecast finished
    item = check(result["verdict"], "forecast", "ASSET_NOT_MODELLED")
    assert "purchase price" in item["detail"]
    assert all(c["asset_index"] != 0 for c in result["forecast_assessment"]["asset_checks"])


def test_the_report_says_why_the_forecast_stopped(storage):
    case = save_fake_forecast(client_record())
    replies = data_manager.load_raw_replies(case["client_ref"])
    replies["call3_types.list"] = json.dumps({"person_types": []})                # unusable, and no API key
    data_manager.save_raw_replies(case["client_ref"], {k: {"raw": v} for k, v in replies.items()})
    verdict = run(case)["verdict"]
    item = check(verdict, "forecast", "FORECAST")
    assert "the list of person types" in item["detail"] and "API key" in item["detail"]
    assert "The forecast" in verdict["not_run"]


def test_benchmark_shows_its_9_checks_even_when_it_couldnt_run(storage):
    case = save_fake_forecast(client_record())
    research, typology, _ = fake_ai_calls()
    with research, typology, patch("app.ai_manager.get_ai_benchmark", return_value=None):
        report = main.run_combined_assessment(case)["report"]
    benchmark = next(s for s in report["sections"] if s["title"].startswith("Benchmark"))
    first_nine = benchmark["items"][:9]
    assert all(item["mark"] == "skip" and "couldn't run" in item["text"] for item in first_nine)


def test_growth_speed_is_shown_when_no_jump_was_recorded(storage):
    case = save_fake_forecast(client_record(biggest_wealth_jump=None, wealth_jump_years=None))
    report = run(case)["report"]
    benchmark = next(s for s in report["sections"] if s["title"].startswith("Benchmark"))
    speed = next(item for item in benchmark["items"] if item["text"].startswith("Wealth growth speed"))
    assert speed["mark"] == "skip" and "No biggest wealth jump" in speed["text"]



def test_one_ai_client_is_reused_so_none_is_closed_mid_log(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert utilities.client() is utilities.client()


def test_network_libraries_dont_flood_the_log():
    import logging
    utilities.quiet_library_logs()
    for name in ("httpx", "httpcore", "openai"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING