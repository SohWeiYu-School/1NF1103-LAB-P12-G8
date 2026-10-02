from openai import OpenAI
# from anthropic import Anthropic
from dotenv import load_dotenv
import os
import json

load_dotenv() #loads your secret/environment variables from the .env file

openAi_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
#claude_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Generate a prompt using the client's information
def generate_prompt(client_data):

    # Read the prompt from the text file
    with open("app/prompts/benchmark.txt", "r") as file:
        prompt = file.read()

    # Replace {client_data} with the actual client information
    prompt = prompt.replace("{client_data}", str(client_data))

    return prompt

# Send the prompt to OpenAI
def call_openai(prompt):

    # Send the prompt to the AI model
    try:
        response = openAi_client.responses.create(
            model="gpt-4.1-nano",
            input=prompt
        )
        
        # Return the AI's response
        return response.output_text

    except Exception as error:
        handle_ai_failure(error)
        return None

# Sends the prompt to Claude as a backup

# def call_claude(prompt):

#     response = claude_client.messages.create(
#         model="claude-3-5-haiku-latest",
#         max_tokens=1000,
#         messages=[
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ]
#     )

#     return response.content[0].text

# Handles AI request errors
def handle_ai_failure(error):

    #Handle AI request failure
    print("AI request failed.")
    print("Error:", error)

    return None

# Converts the AI response into Python data
def parse_ai_response(response):

    # Remove Markdown code block formatting
    response = response.replace("```json", "")
    response = response.replace("```", "")

    # Remove unnecessary spaces
    response = response.strip()

    # Convert the JSON response into a Python dictionary
    data = json.loads(response)

    return data

#Get the AI result, to be used in logic
def get_ai_data(client_data):
    prompt = generate_prompt(client_data)
    result = call_openai(prompt)

    if result is not None:
        return parse_ai_response(result)
    return None
    
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

    # # Generate the prompt
    # prompt = generate_prompt(client_data)

    # # Send the prompt to OpenAI
    # result = call_openai(prompt)

    # # Convert the AI response into a Python dictionary
    # ai_data = parse_ai_response(result)

    # # Display the AI data
    # print(result)

    ai_data = get_ai_data(client_data)
    print(json.dumps(ai_data, indent=4))