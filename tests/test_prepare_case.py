"""Tests for logic_manager.prepare_case_for_matching and normalize_declaration_jurisdictions."""

from app.logic_manager import normalize_declaration_jurisdictions, prepare_case_for_matching


def _sample_record(**overrides):
    base = {
        "client_ref": "ID-0001",
        "occupation": "COO",
        "industry": "Maritime",
        "age": 51,
        "career_start_year": 1998,
        "country": "SG",
        "country_of_residence": "SG",
    }
    base.update(overrides)
    return base


def _sample_declaration(sources):
    return {"sources": sources}


def _sample_source(jurisdiction):
    return {
        "source_type": "employment",
        "description": "Test",
        "amount_sgd": None,
        "start_year": 2000,
        "end_year": None,
        "jurisdiction": jurisdiction,
        "counterparty": None,
        "evidence_sentence": "Test sentence.",
    }


# ---------------------------------------------------------------------------
# prepare_case_for_matching — numeric coercion
# ---------------------------------------------------------------------------

def test_string_age_converted_to_int():
    record = _sample_record(age="51")
    out, warnings = prepare_case_for_matching(record)
    assert out["age"] == 51
    assert isinstance(out["age"], int)


def test_string_career_start_year_converted_to_int():
    record = _sample_record(career_start_year="1998")
    out, _ = prepare_case_for_matching(record)
    assert out["career_start_year"] == 1998
    assert isinstance(out["career_start_year"], int)


def test_int_age_unchanged():
    record = _sample_record(age=51)
    out, warnings = prepare_case_for_matching(record)
    assert out["age"] == 51
    assert not any("age" in w for w in warnings)


# ---------------------------------------------------------------------------
# prepare_case_for_matching — country ISO conversion
# ---------------------------------------------------------------------------

def test_country_name_to_iso():
    record = _sample_record(country="Singapore", country_of_residence="Singapore")
    out, _ = prepare_case_for_matching(record)
    assert out["country"] == "SG"
    assert out["country_of_residence"] == "SG"


def test_already_iso_country_unchanged():
    record = _sample_record(country="SG")
    out, warnings = prepare_case_for_matching(record)
    assert out["country"] == "SG"
    assert not any("could not convert" in w for w in warnings)


def test_unknown_country_kept_and_warned():
    record = _sample_record(country="Atlantis")
    out, warnings = prepare_case_for_matching(record)
    assert out["country"] == "Atlantis"
    assert any("Atlantis" in w and "could not convert" in w for w in warnings)


def test_null_country_handled():
    record = _sample_record(country=None, country_of_residence=None)
    out, warnings = prepare_case_for_matching(record)
    assert out["country"] is None
    assert out["country_of_residence"] is None
    assert not any("could not convert" in w for w in warnings)


# ---------------------------------------------------------------------------
# prepare_case_for_matching — edge cases
# ---------------------------------------------------------------------------

def test_empty_dict_no_crash():
    out, warnings = prepare_case_for_matching({})
    assert out["age"] is None
    assert out["career_start_year"] is None
    assert out["country"] is None
    assert out["all_jurisdictions"] == []


# ---------------------------------------------------------------------------
# prepare_case_for_matching — all_jurisdictions extraction
# ---------------------------------------------------------------------------

def test_all_jurisdictions_extracted():
    record = {
        "age": 51,
        "career_start_year": 1998,
        "country": "SG",
        "country_of_residence": "SG",
        "career_timeline": [
            {"country": "SG"},
            {"country": "HK"},
        ],
        "asset_timeline": [
            {"country": "AU"},
        ],
        "declarations": {
            "wealth_countries": ["SG", "HK", "AE"],
        },
    }
    out, _ = prepare_case_for_matching(record)
    assert out["all_jurisdictions"] == ["AE", "AU", "HK", "SG"]


def test_career_timeline_country_names_converted():
    record = _sample_record(
        career_timeline=[{"country": "Hong Kong"}, {"country": "SG"}]
    )
    out, _ = prepare_case_for_matching(record)
    assert "HK" in out["all_jurisdictions"]
    assert "SG" in out["all_jurisdictions"]


def test_original_record_not_mutated():
    record = _sample_record(age="51", country="Singapore")
    prepare_case_for_matching(record)
    assert record["age"] == "51"
    assert record["country"] == "Singapore"


# ---------------------------------------------------------------------------
# normalize_declaration_jurisdictions
# ---------------------------------------------------------------------------

def test_valid_iso_code_kept():
    decl = _sample_declaration([_sample_source("SG")])
    out, warnings = normalize_declaration_jurisdictions(decl)
    assert out["sources"][0]["jurisdiction"] == "SG"
    assert not warnings


def test_full_name_converted_to_iso():
    decl = _sample_declaration([_sample_source("Singapore")])
    out, warnings = normalize_declaration_jurisdictions(decl)
    assert out["sources"][0]["jurisdiction"] == "SG"
    assert any("converted" in w for w in warnings)


def test_unrecognizable_jurisdiction_set_to_null():
    decl = _sample_declaration([_sample_source("Mordor")])
    out, warnings = normalize_declaration_jurisdictions(decl)
    assert out["sources"][0]["jurisdiction"] is None
    assert any("unrecognizable" in w for w in warnings)


def test_null_jurisdiction_kept():
    decl = _sample_declaration([_sample_source(None)])
    out, warnings = normalize_declaration_jurisdictions(decl)
    assert out["sources"][0]["jurisdiction"] is None
    assert not warnings


def test_lowercase_iso_uppercased():
    decl = _sample_declaration([_sample_source("sg")])
    out, warnings = normalize_declaration_jurisdictions(decl)
    assert out["sources"][0]["jurisdiction"] == "SG"
    assert any("uppercased" in w for w in warnings)


# ---------------------------------------------------------------------------
# prepare_case_for_matching — declared_net_worth, reference_year, wealth_countries
# ---------------------------------------------------------------------------

def test_declared_net_worth_from_top_level_field():
    record = _sample_record(declared_net_worth_sgd=16500000)
    out, _ = prepare_case_for_matching(record)
    assert out["declared_net_worth_sgd"] == 16500000


def test_declared_net_worth_from_declarations_sub_dict():
    record = _sample_record()
    record["declarations"] = {"declared_net_worth": 5000000, "wealth_countries": []}
    out, _ = prepare_case_for_matching(record)
    assert out["declared_net_worth_sgd"] == 5000000


def test_reference_year_extracted_from_record():
    record = _sample_record(reference_year=2025)
    out, _ = prepare_case_for_matching(record)
    assert out["reference_year"] == 2025


def test_top_level_wealth_countries_added_to_all_jurisdictions():
    record = _sample_record(country="SG", country_of_residence="SG",
                            wealth_countries=["SG", "AE", "HK"])
    out, _ = prepare_case_for_matching(record)
    assert "AE" in out["all_jurisdictions"]
    assert "HK" in out["all_jurisdictions"]
