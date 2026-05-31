import pandas as pd
import socket
import smtplib
import dns.resolver
import requests
import random
import string
import threading
from email_validator import validate_email, EmailNotValidError
from concurrent.futures import ThreadPoolExecutor, as_completed

DOC_NAME = input("Enter filename excluding file extension: ")
df = pd.read_csv(f'{DOC_NAME}.csv')
df['email'] = df['email'].astype(str).str.strip().str.lower()
df = df.drop_duplicates(subset=['email'])
total_start = len(df)
print(f"Loaded {total_start} unique emails.\n")

# ── 1. Syntax ────────────────────────────────────────────────────────────────
print("[1/4] Syntax check...")
def passes_syntax(email):
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False

before = len(df)
df = df[df['email'].apply(passes_syntax)]
print(f"  Dropped {before - len(df)} bad syntax. {len(df)} remaining.")

# ── 2. Disposable domains ────────────────────────────────────────────────────
print("\n[2/4] Disposable domain check...")
disposable_domains = set()
try:
    url = "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/master/disposable_email_blocklist.conf"
    r = requests.get(url, timeout=10)
    disposable_domains = set(r.text.strip().splitlines())
    print(f"  Loaded {len(disposable_domains)} known disposable domains.")
except Exception as e:
    print(f"  Warning: could not load list ({e}). Skipping disposable check.")

before = len(df)
df = df[~df['email'].apply(lambda e: e.split('@')[-1] in disposable_domains)]
print(f"  Dropped {before - len(df)} disposable emails. {len(df)} remaining.")

# ── 3. MX records ────────────────────────────────────────────────────────────
print("\n[3/4] MX record check...")
mx_cache: dict = {}
mx_lock = threading.Lock()

def get_mx(domain: str) -> list[str]:
    with mx_lock:
        if domain in mx_cache:
            return mx_cache[domain]
    try:
        records = dns.resolver.resolve(domain, 'MX', lifetime=5)
        hosts = [str(r.exchange).rstrip('.') for r in sorted(records, key=lambda r: r.preference)]
    except Exception:
        hosts = []
    with mx_lock:
        mx_cache[domain] = hosts
    return hosts

before = len(df)
df = df[df['email'].apply(lambda e: bool(get_mx(e.split('@')[-1])))]
print(f"  Dropped {before - len(df)} domains with no MX records. {len(df)} remaining.")

# ── 4. SMTP verification ─────────────────────────────────────────────────────
print(f"\n[4/4] SMTP verification ({len(df)} emails) — this may take a while...")
print("  Gmail/Yahoo/Outlook block SMTP probing — those will show as 'unverifiable'.")
print("  If your ISP blocks port 25, most results will be 'unverifiable'.\n")

catch_all_cache: dict = {}
catch_all_lock = threading.Lock()
TIMEOUT = 10
FROM_ADDR = 'check@verify.local'

def _rcpt_probe(mx: str, email: str) -> int:
    smtp = smtplib.SMTP(timeout=TIMEOUT)
    smtp.connect(mx, 25)
    smtp.ehlo('verify.local')
    smtp.mail(FROM_ADDR)
    code, _ = smtp.rcpt(email)
    try:
        smtp.quit()
    except Exception:
        pass
    return code

def _is_catch_all(domain: str) -> bool | None:
    with catch_all_lock:
        if domain in catch_all_cache:
            return catch_all_cache[domain]
    hosts = get_mx(domain)
    fake = ''.join(random.choices(string.ascii_lowercase, k=16)) + '@' + domain
    result = None
    for mx in hosts[:1]:
        try:
            result = (_rcpt_probe(mx, fake) == 250)
            break
        except Exception:
            pass
    with catch_all_lock:
        catch_all_cache[domain] = result
    return result

_SMTP_ERRORS = (
    socket.timeout, socket.gaierror, ConnectionRefusedError, OSError,
    smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, smtplib.SMTPException,
)

def verify_email(email: str) -> str:
    domain = email.split('@')[-1]
    hosts = get_mx(domain)
    if not hosts:
        return 'no_mx'
    if _is_catch_all(domain) is True:
        return 'catch_all'
    for mx in hosts[:2]:
        try:
            code = _rcpt_probe(mx, email)
            if code == 250:
                return 'valid'
            elif 500 <= code <= 559:
                return 'invalid'
            else:
                return 'unverifiable'
        except _SMTP_ERRORS:
            continue
        except Exception:
            continue
    return 'unverifiable'

emails = df['email'].tolist()
smtp_results: dict = {}
completed = 0

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(verify_email, email): email for email in emails}
    for future in as_completed(futures):
        email = futures[future]
        try:
            status = future.result()
        except Exception:
            status = 'error'
        smtp_results[email] = status
        completed += 1
        if completed % 25 == 0 or completed == len(emails):
            print(f"  {completed}/{len(emails)} checked...")

df['smtp_status'] = df['email'].map(smtp_results)

# ── Output ───────────────────────────────────────────────────────────────────
report_path = f'{DOC_NAME}_verification_report.csv'
df.to_csv(report_path, index=False)

# Drop only addresses SMTP confirmed don't exist; keep everything else
df_out = df[df['smtp_status'] != 'invalid'].copy()
df_out.to_csv(f'{DOC_NAME}_verified.csv', index=False)

# ── Summary ──────────────────────────────────────────────────────────────────
counts = df['smtp_status'].value_counts()
bar = '─' * 38
print(f"\n{bar}")
print(f"  Started with:    {total_start}")
print(f"  Output (kept):   {len(df_out)}")
print(f"  Dropped:         {total_start - len(df_out)}")
print(bar)
for status, count in counts.sort_values(ascending=False).items():
    label = {
        'valid':        'SMTP confirmed valid',
        'catch_all':    'catch-all domain',
        'unverifiable': 'server blocked check',
        'invalid':      'SMTP confirmed invalid',
        'no_mx':        'no MX records',
        'error':        'error',
    }.get(status, status)
    print(f"  {count:>6}  {label}")
print(bar)
print(f"\nFull report : {report_path}")
print(f"Verified    : {DOC_NAME}_verified.csv")
