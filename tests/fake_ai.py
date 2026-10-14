"""
fake_ai.py - made-up AI replies for the combined assessment tests (no API key, no network).

forecast_replies(case) gives a saved reply for every forecast piece (Step 0 and Calls 1-4),
in the exact form the system saves real replies, so the forecast runs through its real
schema checks. research(), typologies() and benchmark() stand in for the crime scan's and
the benchmark's AI calls.

The made-up client story: an honest career that explains a modest amount of wealth, plus a
trade-based laundering story through his current job that would explain much more.
"""

import json

from app import utilities


def _r(low: float, typical: float, high: float) -> dict:
    return {"low": low, "typical": typical, "high": high}


def research() -> dict:
    url = "https://www.fatf-gafi.org/trade-based-ml"
    return {
        "reports": [{"report_id": "R1", "organisation": "FATF", "title": "Trade-Based Money Laundering",
                     "year": 2020, "url": url,
                     "excerpts": ["Over- and under-invoicing of freight services is used to move value.",
                                  "Payments from unrelated third parties are a red flag."],
                     "summary": ["Over- and under-invoicing", "Third-party payments"]}],
        "api_sources": [{"url": url, "title": "Trade-Based Money Laundering"}],
        "retrieved_at": "2026-10-01T00:00:00+00:00",
        "model": "fake",
    }


def typologies() -> dict:
    return {
        "reasoning": "The FATF report covers freight invoicing.",
        "sector_typologies": [{
            "typology_id": "trade_based_laundering",
            "name": "Trade-based laundering through freight invoicing",
            "crime_category": "TRADE_BASED_ML",
            "description": "Freight services are over- or under-invoiced to move value. Third parties pay.",
            "source_ids": ["R1"],
            "source_quotes": [{"report_id": "R1",
                               "quote": "Over- and under-invoicing of freight services is used to move value."}],
            "indicators": [{"indicator": "counterparty_unnamed", "weight": "core"},
                           {"indicator": "unvalued_source_present", "weight": "supporting"}],
            "typical_documents": ["Freight invoices", "Bills of lading"],
        }],
        "expected_jurisdictions": ["SG", "HK"],
        "max_accumulation_per_year_sgd": 400000,
    }


def benchmark(expected_wealth: float = 2_400_000) -> dict:
    return {"expected_wealth": expected_wealth, "expected_liquidity": 20, "expected_property": 60,
            "expected_listed_equities": 15, "expected_private_business": 10, "expected_cash": 15,
            "expected_velocity": 300_000, "expected_counterparties": ["Straits Harbour Logistics Pte Ltd"],
            "expected_jurisdiction": ["Singapore", "Hong Kong"]}


def forecast_replies(case: dict, pay_scale: float = 1.0, crime_money: float = 400_000) -> dict:
    """{piece_name: raw JSON text} for every forecast piece of this case."""
    start, end = utilities.career_years(case)
    future = utilities.future_years(case)
    jobs = utilities.career_positions(case)
    assets = case.get("investments", [])
    replies = {}

    replies["step0_declaration"] = {"claimed_sources": [
        {"source_id": "S1", "source_type": "employment", "description": "Salary as COO", "start_year": start,
         "end_year": None, "amount": None, "country": "Singapore", "linked_position_index": len(jobs) - 1,
         "linked_asset_index": None, "quote": "salary", "counterparty": "Straits Harbour Logistics Pte Ltd",
         "occupation_mismatch": False},
        {"source_id": "S2", "source_type": "advisory_fees", "description": "Advisory work for shipping clients",
         "start_year": 2018, "end_year": None, "amount": None, "country": "Hong Kong",
         "linked_position_index": None, "linked_asset_index": None, "quote": "some advisory work",
         "counterparty": None, "occupation_mismatch": False},
    ]}

    markets = [("ECO_SG", "Singapore economy", "Singapore", "real GDP"),
               ("IND_SHIP", "Container shipping", "World", "freight rate index"),
               ("PROP_SG", "Singapore property", "Singapore", "property price index"),
               ("FX_AUD", "Australian dollar", "Australia", "AUD in SGD")]
    replies["call1_markets.list"] = {
        "relevant_markets": [{"market_id": m, "name": n, "country": c, "indicator": i} for m, n, c, i in markets],
        "jurisdictions": [{"country": c, "listings": [], "summary": "No listings.", "source_name": "FATF"}
                          for c in ("Singapore", "Hong Kong", "Australia")],
        "scenario_chances": [{"year": y, "steady": 60, "boom": 20, "downturn": 20} for y in future],
        "data_gaps": [],
    }
    for market_id, *_ in markets:
        replies[f"call1_markets.series.{market_id}"] = {
            "market_id": market_id,
            "history": [{"year": y, "change_pct": 3.0, "source_name": "IMF"} for y in range(start, end + 1)],
            "outlook": [{"year": y, "steady": _r(1, 2, 3), "boom": _r(3, 5, 7), "downturn": _r(-4, -2, 0)}
                        for y in future],
            "data_gaps": [],
        }

    for index, job in enumerate(jobs):
        first, last = int(job["start_year"]), utilities.position_end(job, end)
        base = 60_000 * (1 + index) * pay_scale
        replies[f"call2_earnings.position.{index}"] = {"position_index": index, "yearly": [
            {"year": y, "base_pay": _r(base * 0.8, base, base * 1.2), "bonus": _r(0, base * 0.2, base * 0.4),
             "equity_or_profit_share": _r(0, 0, 0), "tax_rate_pct": _r(10, 12, 15),
             "living_costs": _r(30_000, 40_000, 50_000), "savings_return_pct": _r(1, 2, 3)}
            for y in range(first, last + 1)]}
    pay = {"base_pay": _r(250_000, 300_000, 350_000), "bonus": _r(0, 50_000, 100_000),
           "equity_or_profit_share": _r(0, 0, 0)}
    replies["call2_earnings.future_pay"] = {"career_outlook": "Steady.", "yearly": [
        {"year": y, "steady": pay, "boom": pay, "downturn": pay, "tax_rate_pct": _r(10, 12, 15),
         "living_costs": _r(30_000, 40_000, 50_000), "savings_return_pct": _r(1, 2, 3)} for y in future]}
    for index, asset in enumerate(assets):
        bought = int(asset["year_acquired"])
        replies[f"call2_earnings.asset.{index}"] = {
            "asset_index": index, "exposures": [{"market_id": "PROP_SG", "sensitivity": _r(0.8, 1.0, 1.2)}],
            "own_growth_pct": _r(0, 1, 2),
            "income_yield_by_year": [{"year": y, "yield_pct": _r(0, 1, 2)} for y in range(bought, end + 1)],
            "future_income_yield_pct": {"steady": _r(0, 1, 2), "boom": _r(1, 2, 3), "downturn": _r(0, 0.5, 1)},
            "valuation_basis": "Property index",
        }

    current = len(jobs) - 1
    current_start = int(jobs[current]["start_year"])
    replies["call3_types.list"] = {"person_types": [
        {"type_id": "T1", "category": "HONEST", "typology_id": None, "name": "As declared",
         "route": "Everything came from his declared jobs and assets.", "why_it_applies": "The declared story.",
         "enabled_by_position_index": None, "enabled_by_asset_index": None, "enabled_by_source_id": None,
         "open_from": start, "open_to": future[-1],
         "rule_out_documents": [{"document_code": "DOC_TAX_RETURNS", "also_rules_out": []}]},
        {"type_id": "T2", "category": "TRADE_BASED_ML", "typology_id": "trade_based_laundering",
         "name": "Trade-based laundering through his freight company",
         "route": "Freight invoices are inflated and the difference is paid to him.",
         "why_it_applies": "He runs operations at a freight company.",
         "enabled_by_position_index": current, "enabled_by_asset_index": None, "enabled_by_source_id": None,
         "open_from": current_start, "open_to": future[-1],
         "rule_out_documents": [{"document_code": "DOC_TRADE_INVOICES", "also_rules_out": []}]},
        {"type_id": "T3", "category": "TAX_EVASION", "typology_id": None, "name": "Undeclared advisory income",
         "route": "Advisory fees paid offshore and never declared.", "why_it_applies": "His declared advisory work.",
         "enabled_by_position_index": None, "enabled_by_asset_index": None, "enabled_by_source_id": "S2",
         "open_from": 2018, "open_to": future[-1],
         "rule_out_documents": [{"document_code": "DOC_CONSULTING_AGREEMENT", "also_rules_out": []}]},
    ]}
    for type_id, first, money in (("T1", start, 0), ("T2", current_start, crime_money), ("T3", 2018, 50_000)):
        replies[f"call3_types.behaviour.{type_id}"] = {
            "type_id": type_id, "route_open_from": first, "route_open_to": future[-1],
            "extra_money_history": [{"year": y, "amount": _r(money * 0.5, money, money * 1.5)}
                                    for y in range(first, end + 1)],
            "extra_money_forecast": [{"year": y, "steady": _r(money * 0.5, money, money * 1.5),
                                      "boom": _r(money, money, money * 2), "downturn": _r(0, money * 0.5, money)}
                                     for y in future],
            "signals": [{"signal_code": "SIG_SALARY_REGULAR", "first_year": first, "last_year": future[-1],
                         "count_per_year": _r(12, 12, 12), "amount_per_year": _r(200_000, 300_000, 400_000)}],
            "linked_market_ids": ["IND_SHIP"], "market_fall_response": _r(-1, 0, 1),
        }
    replies["call4_events.list"] = {"events": [
        {"event_id": f"E{n}", "name": f"Event {n}", "year": future[n - 1], "probability_pct": _r(10, 20, 30),
         "shocks": [{"market_id": "IND_SHIP", "change_pct": _r(-20, -10, -5)}]} for n in (1, 2, 3)]}
    for type_id in ("T1", "T2", "T3"):
        replies[f"call4_events.reactions.{type_id}"] = {"type_id": type_id, "reactions": [
            {"event_id": f"E{n}", "extra_money_change": _r(-10_000, 0, 10_000), "signals": []} for n in (1, 2, 3)]}
    return {name: json.dumps(data) for name, data in replies.items()}
