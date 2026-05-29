import pandas as pd
from email_validator import validate_email, EmailNotValidError
from urllib.parse import urlparse
import json
from openai import OpenAI
import time
import re
import os
from dotenv import load_dotenv

load_dotenv()

DOC_NAME = input("Enter filename excluding file extension: ")

df = pd.read_csv(f'{DOC_NAME}.csv')
df['email'] = df['email'].astype(str).str.strip().str.lower()
df = df.drop_duplicates(subset=['email'])

def is_valid(email):
    try:
        validate_email(email, check_deliverability=True)
        print(f"VALID EMAIL: {email}")
        return True
    except EmailNotValidError:
        print(f"INVALID EMAIL: {email}")
        return False

# Validate emails
answer = input("\nPress Y to validate emails N to skip: ")
answer = answer.lower()
if answer == 'y':
    df = df[df['email'].apply(is_valid)]
    df.to_csv(f'{DOC_NAME}_validated.csv', index=False)

# Save a copy before filtering unwanted terms
df_before = df.copy()

# Remove emails containing unwanted terms
unwanted_terms = ["mortgage"]

answer = input("\nPress Y to drop unwanted terms, N to skip: ")
answer = answer.lower()
if answer == 'y':
    if unwanted_terms:
        pattern = '|'.join(unwanted_terms)
        df = df[~df['email'].str.contains(pattern, case=False, na=False)]

    # Find and print dropped emails
    dropped_emails = df_before.loc[~df_before['email'].isin(df['email']), 'email']
    print("\nALERT - Emails dropped due to unwanted terms:")
    for email in dropped_emails:
        print(email)
    
    # Save cleaned file
    df.to_csv(f'{DOC_NAME}_cleaned_unwanted_terms.csv', index=False)

# keyboard_input = input("\nALERT - Press S to shuffle or ENTER to proceed without shuffling. You can shuffle again after AI cleaning.")
# if keyboard_input == 's' or keyboard_input == 'S':
#     df = df.sample(frac=1).reset_index(drop=True)

# print(f"\nALERT - Initial cleaning complete. {DOC_NAME}_cleaned.csv saved. Press ENTER to continue cleaning with AI.")
# input()

generic_domains = {'gmail.com', 'yahoo.com', 'ymail.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'live.com', 'aol.com'}

def build_entry(row):
    email = row.email
    domain = email.split('@')[-1]
    link = getattr(row, 'link', None)
    if domain in generic_domains and link and str(link) not in ('', 'nan'):
        parsed = urlparse(str(link))
        short_link = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else str(link).split('/')[0]
        return f"{email} (source: {short_link})"
    return email

email_dict = {str(i+2): build_entry(row) for i, row in enumerate(df.itertuples(index=False))}

with open('emails_for_gpt.json', 'w') as f:
    json.dump(email_dict, f, indent=2)

api_key = os.getenv('OPENAI_API_KEY')
client = OpenAI(api_key=api_key)

with open('emails_for_gpt.json') as f:
    email_dict = json.load(f)
batch_size = 100
keys = list(email_dict.keys())

removals_list = []

answer = input("\nPress Y to filter role-based addresses (info@, admin@, etc.), N to skip: ")
answer = answer.lower()
if answer == 'y':
    role_prefixes = ['info', 'admin', 'noreply', 'no-reply', 'support', 'contact',
                     'sales', 'marketing', 'hello', 'help', 'team', 'office',
                     'accounts', 'billing', 'enquiries', 'enquiry', 'inquiry'
                     # Spanish role prefixes
                     'informacion', 'información', 'contacto', 'ventas', 'soporte',
                     'ayuda', 'equipo', 'oficina', 'administracion', 'administración',
                     'facturacion', 'facturación', 'consultas', 'atencion', 'atención',
                     'servicios', 'recepcion', 'recepción', 'gerencia', 'direccion',
                     'direccion', 'dirección', 'hola', 'bienvenido', 'bienvenidos',
                     'pagos', 'cobros', 'contabilidad', 'reclamos', 'reservas']
    df_before_roles = df.copy()
    role_pattern = r'^(' + '|'.join(re.escape(p) for p in role_prefixes) + r')@'
    df = df[~df['email'].str.match(role_pattern, case=False)]
    dropped_roles = df_before_roles.loc[~df_before_roles['email'].isin(df['email']), 'email']
    print("\nALERT - Emails dropped due to role-based prefix:")
    for email in dropped_roles:
        print(email)
    df.to_csv(f'{DOC_NAME}_no_roles.csv', index=False)

answer = input("\nPress Y to clean list with AI, N to skip: ")
answer = answer.lower()
if answer == 'y':

    for start in range(0, len(keys), batch_size):
        batch_keys = keys[start:start+batch_size]
        batch = {k: email_dict[k] for k in batch_keys}

        # Real estate investor prompt
        prompt = (
            "Here is a JSON object where each key is a number and each value is an email address.\n"
            "Send back a list of keys for any values that are NOT associated with hard money, private capital, or private lending.\n"
            "If the value is for a regular mortgage lender, include in the list of keys."
            "Only send back a value if you are 100% sure."
            "Only send the numbers, no words or explanations.\n\n"
            f"{json.dumps(batch, indent=2)}"
        )

        # prompt = (
        #     "Here is a JSON object where each key is a number and each value is an email address.\n"
        #     "Send back a list of keys for any values that are NOT associated with realty.\n"
        #     "Only send back a value if you are 100% sure."
        #     "Only send the numbers, no words or explanations.\n\n"
        #     f"{json.dumps(batch, indent=2)}"
        # )

        # prompt = (
        #     "Here is a JSON object where each key is a number and each value is an email address.\n"
        #     "These emails are from South American companies. Send back a list of keys for any values "
        #     "that are NOT associated with real estate, property investment, or related fields.\n"
        #     "Real estate includes: residential/commercial property sales, rentals, property management, "
        #     "land investment, construction for sale, and related finance (mortgages, real estate funds).\n"
        #     "In Spanish this includes: inmobiliaria, bienes raíces, propiedades, inversión inmobiliaria, "
        #     "arrendamiento, loteo, urbanización, constructora (when selling units), and similar.\n"
        #     "Only flag an email if you are 100% sure it is unrelated to these fields.\n"
        #     "Only send the numbers, no words or explanations.\n\n"
        #     f"{json.dumps(batch, indent=2)}"
        # )


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
    # Strip the " (source: ...)" suffix that may have been added for generic domains
    emails_to_remove = [email_dict[k].split(' (source:')[0] for k in removals_list if k in email_dict]
    print("Emails Removing:")
    for email in emails_to_remove:
        print(email)
    df = df[~df['email'].isin(emails_to_remove)]

    df.to_csv(f'{DOC_NAME}_AI_cleaned.csv', index=False)

answer = input("\nPress Y to shuffle entries, N to skip: ")
answer = answer.lower()
if answer == 'y':
    df = df.sample(frac=1).reset_index(drop=True)

df.to_csv(f'{DOC_NAME}_final.csv', index=False)
print(f"Final AI cleaned list {DOC_NAME}_final.csv saved.")

answer = input("\nPress Y to delete all intermediary spreadsheets, N to skip: ")
answer = answer.lower()
if answer == 'y':
    for filename in [f'{DOC_NAME}_cleaned_unwanted_terms.csv', f'{DOC_NAME}_cleaned.csv', f'{DOC_NAME}_validated.csv', f'{DOC_NAME}_AI_cleaned.csv', f'{DOC_NAME}_no_roles.csv']:
        if os.path.exists(filename):
            os.remove(filename)
            print(f"Deleted {filename}")
        else:
            print(f"Skipped {filename} (does not exist)")