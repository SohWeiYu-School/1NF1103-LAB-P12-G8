from ai_manager import get_ai_data

#Limits used to determine the PASS / FAIL for client data against ai data
LIMITS = {
    "total_wealth": 3,
    "liquidity": 2,
    "composition": 40,
    "velocity": 2,
    "counterparties": 1,
    "jurisdiction": 1
}

# Sample client information
client_data = {
    "client_id": "CID1001",
    "age": 28,
    "nationality": "Singaporean",
    "country_of_residence": "Singapore",
    "occupation": "Software Engineer",
    "career_start_year": 2020,
    "employment_years": 6,
    "industry": "Information Technology",
    "properties": "1 condominium",
    "investment": "Stocks and ETFs",
    "declared_net_worth": 200000,
    "expected_aum": None,
    "asset_composition": None,
    "source_of_wealth": "Salary savings and long-term investments",
    "pep_status": "None",
    "wealth_generation_country": "Singapore",
    "listed_equities": 11, #used for liquidity, composition
    "cash": 20, #used for liquidity, composition
    "property": 28, #used for composition
    "private_business": 41, #used for composition
    "biggest_wealth_jump": 15000000, #used for velocity
    "wealth_jump_years": 1.5, #used for velocity
}

# Temporary test values for Counterparties and Jurisdictions
expected_counterparties = [
    "employer",
    "CPF Board",
    "local bank",
    "HDB or developer"
]

declared_counterparties = [
    "friend",
    "BVI entity"
]

expected_countries = [
    "Singapore",
    "Malaysia",
    "Indonesia"
]

declared_countries = [
    "SG",
    "Cyprus"
]


ai_data = get_ai_data(client_data) # Get Ai output using function

def calculate_total_wealth(client_data, ai_data):

    declared = client_data["declared_net_worth"]
    expected = ai_data["expected_wealth"]

    result1 = declared / expected

    return result1

result1 = calculate_total_wealth(client_data, ai_data)
print("\n--- Total Wealth Check ---")
print("Declared Net Worth:", client_data["declared_net_worth"])
print("Expected Wealth:   ", ai_data["expected_wealth"])
print("Ratio:             ", round(result1, 2)) # Round to 2dp

if result1 <= LIMITS["total_wealth"]:
    print("Result:             PASS")
else:
    print("Result:             FAIL")

#=============================================================================#
def calculate_liquidity(client_data, ai_data):

    declared = client_data["listed_equities"] + client_data["cash"]
    expected = ai_data["expected_liquidity"]

    result2 = declared / expected

    return result2

result2 = calculate_liquidity(client_data, ai_data)
print("\n--- Liquidity Check ---")
print("Declared Liquidity:", client_data["listed_equities"] + client_data["cash"], "%")
print("Expected Liquidity:", ai_data["expected_liquidity"], "%")
print("Ratio:             ", round(result2, 2)) # Round to 2dp

if result2 <= LIMITS["liquidity"]:
    print("Result:             PASS")
else:
    print("Result:             FAIL")

#=============================================================================#

def calculate_composition(client_data, ai_data):

    # Property
    declared_property = client_data["property"]
    expected_property = ai_data["expected_property"]

    difference_property = abs(declared_property - expected_property) #abs - absolute value, so it removes the negative sign

    # Listed Equities
    declared_listed = client_data["listed_equities"]
    expected_listed = ai_data["expected_listed_equities"]

    difference_listed = abs(declared_listed - expected_listed)

    # Private Business
    declared_business = client_data["private_business"]
    expected_business = ai_data["expected_private_business"]

    difference_business = abs(declared_business - expected_business)

    # Cash
    declared_cash = client_data["cash"]
    expected_cash = ai_data["expected_cash"]

    difference_cash = abs(declared_cash - expected_cash)

    print("\n--- Asset Composition Check ---")
    print("Category           Declared    Expected    Difference")
    print("Property              ", declared_property, "%       ", expected_property, "%        ", difference_property, "%")
    print("Listed Equities       ", declared_listed, "%       ", expected_listed, "%        ", difference_listed, "%")
    print("Private Business      ", declared_business, "%       ", expected_business, "%        ", difference_business, "%")
    print("Cash                  ", declared_cash, "%       ", expected_cash, "%        ", difference_cash, "%")
    
    print("\n--- Composition Result ---")

    if difference_property <= LIMITS["composition"]:
        print("Property: PASS")
    else:
        print("Property: FAIL")

    if difference_listed <= LIMITS["composition"]:
        print("Listed Equities: PASS")
    else:
        print("Listed Equities: FAIL")

    if difference_business <= LIMITS["composition"]:
        print("Private Business: PASS")
    else:
        print("Private Business: FAIL")

    if difference_cash <= LIMITS["composition"]:
        print("Cash: PASS")
    else:
        print("Cash: FAIL")
    

# Run Asset Composition Check
calculate_composition(client_data, ai_data)

#=============================================================================#
def calculate_velocity(client_data, ai_data):

    actual = client_data["biggest_wealth_jump"]
    years = client_data["wealth_jump_years"]
    expected = ai_data["expected_velocity"]

    actual_velocity = actual / years
    result3 = actual_velocity / expected

    return result3
result3 = calculate_velocity(client_data, ai_data)

print("\n--- Velocity Check ---")
print("Actual Velocity:  ", round(client_data["biggest_wealth_jump"] / client_data["wealth_jump_years"], 2))
print("Expected Velocity:", ai_data["expected_velocity"])
print("Ratio:            ", round(result3, 2), "x")

if result3 <= LIMITS["velocity"]:
    print("Result:            PASS")
else:
    print("Result:            FAIL")

#=============================================================================#

def calculate_counterparties(declared, expected):
    unknown = []

    for item in declared:
        if item not in expected:
            unknown.append(item)

    return unknown

unknown_counterparties = calculate_counterparties(
    declared_counterparties,
    expected_counterparties
)

print("\n--- Counterparties Check ---")
print("Expected Counterparties:", expected_counterparties)
print("Declared Counterparties:", declared_counterparties)
print("Unknown Counterparties:", unknown_counterparties)
print("Number of Unknowns:", len(unknown_counterparties))

if len(unknown_counterparties) <= LIMITS["counterparties"]:
    print("Result: PASS")
else:
    print("Result: FAIL")

#=============================================================================#
def clean_country(country):
    if country == "SG":
        return "Singapore"

    return country

def calculate_jurisdiction(declared, expected):
    unknown = []

    for country in declared:
        country = clean_country(country)

        if country not in expected:
            unknown.append(country)

    return unknown
unknown_countries = calculate_jurisdiction(
    declared_countries,
    expected_countries
)
print("\n--- Jurisdiction Check ---")
print("Expected Countries:", expected_countries)
print("Declared Countries:", declared_countries)
print("Unknown Countries:", unknown_countries)
print("Number of Unknowns:", len(unknown_countries))

if len(unknown_countries) <= LIMITS["jurisdiction"]:
    print("Result: PASS")
else:
    print("Result: FAIL")

# if __name__ == "__main__":

   