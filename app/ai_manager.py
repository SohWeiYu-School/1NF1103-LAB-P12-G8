from openai import OpenAI
from dotenv import load_dotenv
import os
#import json

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Generate a prompt using the client's information
def generate_prompt(client_data):

    # Create the prompt for the AI ( f-string lets you put variables directly inside a string using {} )
    prompt = f"""  
You are analysing a client's financial information.

Client information:
{client_data}

Analyse the client information and provide expected benchmark
values for:

Return only JSON.

Use numerical values only.
Do not include explanations or units.

The JSON must contain:
- total_wealth: expected amount in dollars
- liquidity: expected percentage
- composition: expected percentage
- velocity: expected annual amount in dollars
- counterparties: expected number
- jurisdiction: expected number
"""

    # Return the completed prompt
    return prompt

# Send the prompt to OpenAI
def call_openai(prompt):

    # Send the prompt to the AI model
    response = client.responses.create(
        model="gpt-4.1-nano",
        input=prompt
    )

    # Return the AI's response
    return response.output_text

# def parse_ai_response(response):

#     # Remove Markdown code block formatting
#     response = response.replace("```json", "")
#     response = response.replace("```", "")

#     # Remove unnecessary spaces
#     response = response.strip()

#     # Convert the JSON response into a Python dictionary
#     data = json.loads(response)

#     return data


# Test the AI
if __name__ == "__main__":

    # Sample client information
    client_data = {
    "client_id": "CID1001",
    "age": 28,
    "nationality": "Singaporean",
    "country_of_residence": "Singapore",
    "occupation": "Software Engineer",
    "career_start_year": 2020,
    "employment_years": 6,
    "properties": "1 condominium",
    "investment": "Stocks and ETFs",
    "industry": "Information Technology"
}

    # Generate the prompt
    prompt = generate_prompt(client_data)

    # Send the prompt to OpenAI
    result = call_openai(prompt)

    # Convert the AI response into a Python dictionary
    # ai_data = parse_ai_response(result)

    # Display the AI data
    print(result)