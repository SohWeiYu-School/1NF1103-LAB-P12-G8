"""The command line (menu option 2) and the web app must turn the same documents into the same client.

Uses the specimen documents in tests/documents/marcus_lim/ and marcus_lim_expected_extract.json, which is
what a correct AI reading of them looks like (no AI calls are made here).
"""

import copy
import json
import os

from app import data_manager, io_manager, logic_manager, web_app

FOLDER = os.path.join(os.path.dirname(__file__), "documents", "marcus_lim")
TODAY = "2026-10-13"


def expected_reply() -> dict:
    with open(FOLDER + "_expected_extract.json", encoding="utf-8") as handle:
        return json.load(handle)


def cli_record(reply: dict) -> dict:
    """What menu option 2 saves: documents -> record, the officer types what's missing."""
    record, _ = logic_manager.record_from_documents(reply, "CL-MARCUS", TODAY)
    for field in logic_manager.missing_client_fields(record):
        assert field["path"] == ["investments", 2, "income_produced"], field   # the only gap
        record = logic_manager.set_path(record, field["path"], 0)
    return logic_manager.finish_document_record(record)


def web_record(reply: dict) -> dict:
    """What the web form saves: auto-fill an empty form, the officer saves it unchanged."""
    empty = web_app.values_from_record({"current_date": TODAY})
    values, _ = web_app.fill_blanks(empty, web_app.extracted_to_values(reply, TODAY))
    record, problems, _ = web_app.build_record(values, "CL-MARCUS")
    assert problems == []
    return record


def test_all_specimen_documents_are_read_as_text():
    paths = data_manager.list_documents_in_folder(FOLDER)
    read = [data_manager.read_document_for_extraction(path) for path in paths]

    assert len(read) == 9
    assert all(document["kind"] == "text" for document in read)
    kyc = next(d for d in read if d["file_name"].startswith("01_KYC"))
    assert "SGD 14,500,000" in kyc["text"] and "Sentosa Cove" in kyc["text"]


def test_cli_and_web_save_the_same_client():
    cli, web = cli_record(expected_reply()), web_record(expected_reply())

    for key in ("name", "nationality", "career", "career_history", "investments", "sow_declaration"):
        assert cli[key] == web[key], key
    for key in ("declared_net_worth", "expected_aum", "pep_status", "wealth_countries",
                "asset_composition", "biggest_wealth_jump", "declared_counterparties"):
        assert cli["claims"][key] == web["claims"][key], key


def test_the_record_holds_the_declared_figures():
    record = web_record(expected_reply())

    assert record["career"]["age"] == 47                      # born 22 April 1979
    assert record["career"]["career_start_year"] == 2001
    assert record["career"]["employer_name"] == "Lim Precision Trading Pte Ltd"
    assert [job["start_year"] for job in record["career_history"]] == [2001, 2003, 2011]
    assert record["investments"][1] == {
        "asset_type": "Property - Condo, The Coralis, Sentosa Cove (own residence)", "country": "Singapore",
        "year_acquired": 2023, "price_paid": 4200000, "current_declared_value": 4600000,
        "payment_method": "loan", "income_produced": 0}
    assert record["claims"]["asset_composition"] == {
        "property": 0.45, "listed_equities": 0.15, "private_business": 0.30, "cash": 0.10}
    assert record["claims"]["declared_net_worth"] == 14500000
    # evidence the client did not declare stays out of the declared fields
    assert "Arcadia Holdings Ltd" not in json.dumps(record)


def test_both_records_pass_the_forecasting_checks():
    for record in (cli_record(expected_reply()), web_record(expected_reply())):
        case = {key: value for key, value in record.items() if key != "supporting_documents"}
        assert io_manager.validate_case(case) == []


def test_the_ai_gets_the_same_inputs_from_both():
    cli, web = cli_record(expected_reply()), web_record(expected_reply())

    assert logic_manager.crime_scan_input(cli) == logic_manager.crime_scan_input(web)
    cli_bench, cli_missing = logic_manager.benchmark_input(cli, logic_manager.crime_scan_input(cli))
    web_bench, web_missing = logic_manager.benchmark_input(web, logic_manager.crime_scan_input(web))
    assert cli_bench == web_bench and cli_missing == web_missing == []
    # whole percents, as run_benchmark expects
    assert (cli_bench["property"], cli_bench["listed_equities"],
            cli_bench["private_business"], cli_bench["cash"]) == (45, 15, 30, 10)


def test_forecasting_case_uses_the_saved_values_without_asking():
    def no_questions(question):
        raise AssertionError(f"asked: {question}")

    for record in (cli_record(expected_reply()), web_record(expected_reply())):
        case = io_manager.case_from_client_record(record, no_questions)
        assert case["investments"][0]["current_declared_value"] == 1900000
        assert case["investments"][0]["income_produced"] == 54000


def test_the_split_matches_the_listed_assets():
    record = cli_record(expected_reply())
    assert logic_manager.check_document_record(record, TODAY) == []

    misread = logic_manager.set_path(record, ["investments", 0, "current_declared_value"], 19000000)
    warnings = logic_manager.check_document_record(misread, TODAY)
    assert any("property assets listed are worth" in w for w in warnings)


def test_old_command_line_records_are_upgraded_when_loaded():
    old = {"client_ref": "CL-OLD", "investments": [
        {"asset_type": "Property", "asset_description": "Condo", "current_value": 1500000,
         "yearly_income": 36000, "price_paid": 900000}]}

    asset = data_manager.standardise_record(copy.deepcopy(old))["investments"][0]

    assert asset == {"asset_type": "Property - Condo", "price_paid": 900000,
                     "current_declared_value": 1500000, "income_produced": 36000}


def test_assessment_is_always_dated_today():
    import datetime
    today = datetime.date.today().isoformat()

    case = io_manager.case_from_client_record(cli_record(expected_reply()),
                                              lambda question: (_ for _ in ()).throw(AssertionError(question)))
    assert case["current_date"] == today                       # command line: not asked

    assert web_record(expected_reply())["current_date"] == today   # web form: no date box
    page = web_app.app.test_client().get("/client/new").get_data(as_text=True)
    assert 'name="client.current_date"' not in page
