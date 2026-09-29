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
}

ai_data = get_ai_data(client_data) # Get Ai output using function

# REQUIRED_AI_FIELDS = [
#     "total_wealth",
#     "liquidity",
#     "composition",
#     "velocity",
#     "counterparties",
#     "jurisdiction"
# ]

# To go through each required field. 
# If any field is missing, return False. 
# If everything is there, return True.

# def validate_ai_result(ai_data):
#     for field in REQUIRED_AI_FIELDS:
#         if field not in ai_data:
#             return False

#     return True

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


# if __name__ == "__main__":

   