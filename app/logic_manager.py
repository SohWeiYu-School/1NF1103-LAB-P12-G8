# from ai_manager import parse_ai_response

#Limits used to determine the PASS / FAIL for client data against ai data
LIMITS = {
    "total_wealth": 3,
    "liquidity": 2,
    "composition": 40,
    "velocity": 2,
    "counterparties": 1,
    "jurisdiction": 1
}

#data from ai_manager after running OpenAI (AI data might not always be same)
ai_data = {
    "total_wealth": 150000,
    "liquidity": 20,
    "composition": 70,
    "velocity": 15000,
    "counterparties": 2,
    "jurisdiction": 3
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
    "wealth_generation_country": "Singapore"
}

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
    expected = ai_data["total_wealth"]

    result1 = declared / expected

    return result1

result1 = calculate_total_wealth(client_data, ai_data)
print(result1)

if result1 <= 3:
    print("PASS")
else:
    print("FAIL")

# if __name__ == "__main__":

   