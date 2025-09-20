import pandas as pd
from email_validator import validate_email, EmailNotValidError
import json
from openai import OpenAI
import time
import re
import os
from dotenv import load_dotenv

load_dotenv()

df = pd.read_csv('results.csv')
df['email'] = df['email'].astype(str).str.strip().str.lower()
df = df.drop_duplicates(subset=['email'])

def is_valid(email):
    try:
        validate_email(email)
        print(f"VALID EMAIL: {email}")
        return True
    except EmailNotValidError:
        print(f"INVALID EMAIL: {email}")
        return False

df = df[df['email'].apply(is_valid)]

unwanted_domains = ['gmail.com', 'yahoo.com']
df = df[~df['email'].str.split('@').str[1].isin(unwanted_domains)]

df.to_csv('results_cleaned.csv', index=False)

df = pd.read_csv('results_cleaned.csv')

email_dict = {str(i+2): email for i, email in enumerate(df['email'])}

with open('emails_for_gpt.json', 'w') as f:
    json.dump(email_dict, f, indent=2)

api_key = os.getenv('OPENAI_API_KEY')
client = OpenAI(api_key=api_key)

with open('emails_for_gpt.json') as f:
    email_dict = json.load(f)
batch_size = 100
keys = list(email_dict.keys())

removals_list = []

for start in range(0, len(keys), batch_size):
    batch_keys = keys[start:start+batch_size]
    batch = {k: email_dict[k] for k in batch_keys}

    prompt = (
        "Here is a JSON object where each key is a number and each value is an email address.\n"
        "Send back a list of keys for any values that are OBVIOUSLY NOT in industry, manufacturing, etc..\n"
        "ONly send back a value if you are 100% sure it is not in industry, manufacturing, etc.."
        "Only send the numbers, no words or explanations.\n\n"
        f"{json.dumps(batch, indent=2)}"
    )

    print(f"Processing batch {start // batch_size + 1}")
    response = client.chat.completions.create(
        model= "gpt-4o", #"gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0
    )
    response_text = response.choices[0].message.content
    found = re.findall(r'\d+', response_text)
    removals_list.extend(found)
    print(f"To remove: {found}")

    time.sleep(2)

# Remove emails from df whose keys are in removals_list
emails_to_remove = [email_dict[k] for k in removals_list if k in email_dict]
print("Emails Removing:")
for email in emails_to_remove:
    print(email)
df = df[~df['email'].isin(emails_to_remove)]

df.to_csv('results_final.csv', index=False)