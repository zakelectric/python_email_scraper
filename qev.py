import pandas as pd
import requests
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('QEV_API_KEY')
if not API_KEY:
    API_KEY = input("Enter your QuickEmailVerification API key: ").strip()

DOC_NAME = input("Enter filename excluding file extension: ")
df = pd.read_csv(f'{DOC_NAME}.csv')
df['email'] = df['email'].astype(str).str.strip().str.lower()
df = df.drop_duplicates(subset=['email'])
total = len(df)
print(f"Loaded {total} unique emails.\n")

BASE_URL = "https://api.quickemailverification.com/v1/verify"
remaining_credits = None

def verify_email(email: str) -> dict:
    global remaining_credits
    try:
        resp = requests.get(BASE_URL, params={'email': email, 'apikey': API_KEY}, timeout=15)
        remaining_credits = resp.headers.get('X-QEV-Remaining-Credits', remaining_credits)
        data = resp.json()
        result   = data.get('result', 'unknown')
        reason   = data.get('reason', '')
        safe     = data.get('safe_to_send', 'false')
        typo     = data.get('did_you_mean', '')
        credits  = remaining_credits

        label = result
        if typo:
            label += f" (did you mean {typo}?)"

        print(f"  {email:<45} {label:<12}  reason: {reason:<25}  safe: {safe}  credits left: {credits}", flush=True)
        return {'email': email, 'result': result, 'reason': reason, 'safe_to_send': safe, 'did_you_mean': typo}
    except Exception as e:
        print(f"  {email:<45} error: {e}", flush=True)
        return {'email': email, 'result': 'error', 'reason': str(e), 'safe_to_send': 'false', 'did_you_mean': ''}

print(f"Verifying {total} emails via QuickEmailVerification...\n")

results = []
emails = df['email'].tolist()

# Keep workers low — free tier is 100/day, no need to hammer the API
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {executor.submit(verify_email, email): email for email in emails}
    for future in as_completed(futures):
        try:
            results.append(future.result())
        except Exception as e:
            email = futures[future]
            results.append({'email': email, 'result': 'error', 'reason': str(e), 'safe_to_send': 'false', 'did_you_mean': ''})

results_df = pd.DataFrame(results)
df = df.merge(results_df, on='email', how='left')

# Save full report
report_path = f'{DOC_NAME}_qev_report.csv'
df.to_csv(report_path, index=False)

# Safe-to-send list
df_safe = df[df['safe_to_send'] == 'true'].copy()
df_safe.to_csv(f'{DOC_NAME}_qev_verified.csv', index=False)

# ── Summary ──────────────────────────────────────────────────────────────────
counts = df['result'].value_counts()
bar = '─' * 40
print(f"\n{bar}")
print(f"  Total checked:   {total}")
print(f"  Safe to send:    {len(df_safe)}")
print(bar)
for result, count in counts.sort_values(ascending=False).items():
    print(f"  {count:>6}  {result}")
if remaining_credits is not None:
    print(f"\n  Credits remaining: {remaining_credits}")
print(bar)
print(f"\nFull report : {report_path}")
print(f"Verified    : {DOC_NAME}_qev_verified.csv")
