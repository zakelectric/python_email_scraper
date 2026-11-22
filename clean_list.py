"""Clean a raw scraper CSV into a ranked send list, with a reason for every row.

usage: python clean_list.py results_mortgage+broker.csv --profile mortgage [--no-llm]

Steps: normalize + dedupe, junk (syntax, placeholders, platform domains, dead-end roles), suppression
(clients, lead sheet, suppress.txt), known segment domains (realtors, big lenders), MX check, homepage
evidence, Claude classification of whatever is left, then tiered ranking with each company spread evenly.

Writes <input stem>_clean/: clean.csv (feed this to qev.py), one csv per segment, rejects.csv, all.csv.
cache.json in the same folder holds DNS, homepage and Claude results, so a rerun only does new work.
"""
import argparse
import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse

import dns.resolver
import pandas as pd
import requests
from email_validator import validate_email, EmailNotValidError

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
CLIENTS_DIR = PROGRAMS / 'marketing' / 'ex0dus_seo_blog' / 'clients_ex0dus'
SUPPRESS_FILE = HERE / 'suppress.txt'  # one email or @domain per line: unsubscribes, bounces, "do not contact"
LLM_BATCH = 60
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'}

ap = argparse.ArgumentParser()
ap.add_argument('input')
ap.add_argument('--profile', default='mortgage')
ap.add_argument('--no-llm', action='store_true', help='stop before the Claude step (rows it would judge stay "pending")')
args = ap.parse_args()

rules = json.load(open(HERE / 'clean_rules.json'))
profile = rules['profiles'][args.profile]
src = Path(args.input).resolve()
out_dir = src.with_name(src.stem + '_clean')
out_dir.mkdir(exist_ok=True)
cache_path = out_dir / 'cache.json'
cache = json.load(open(cache_path)) if cache_path.exists() else {}
for k in ('mx', 'site', 'llm'):
    cache.setdefault(k, {})


def save_cache():
    tmp = cache_path.with_suffix('.tmp')
    json.dump(cache, open(tmp, 'w'))
    tmp.replace(cache_path)


def in_set(domain, domains):
    """True if domain or any parent domain is in the set (leads.leadrouter.com -> leadrouter.com)."""
    parts = domain.split('.')
    return any('.'.join(parts[i:]) in domains for i in range(len(parts) - 1))


def host(url):
    return urlparse(str(url)).netloc.lower().removeprefix('www.')


placeholder_locals = set(rules['placeholder_locals'])
placeholder_locals_on_free = set(rules['placeholder_locals_on_free'])  # john@, email@... only fake on free/placeholder domains
placeholder_domains = set(rules['placeholder_domains'])
dead_end = set(rules['dead_end_locals'])
role_locals = set(rules['role_locals'])
platform = set(rules['platform_domains'])
free = set(rules['free_mailboxes'])
segment_domains = {seg: set(ds) for seg, ds in profile['segment_domains'].items()}

# ── 1. Normalize + dedupe ────────────────────────────────────────────────────
raw = pd.read_csv(src, dtype=str).fillna('')
print(f'Loaded {len(raw)} rows from {src.name}')


def normalize(e):
    e = unquote(e).strip().lower()
    e = re.sub(r'^(mailto:|u003e|\\u003e|>)+', '', e)
    return e.strip(' .,;:<>()[]"\'')


raw['email'] = raw['email'].map(normalize)
raw['pages_found'] = raw.groupby('email')['link'].transform('nunique')
df = raw.drop_duplicates('email').reset_index(drop=True)
print(f'  {len(raw) - len(df)} duplicates after normalizing, {len(df)} unique')
df['local'] = df.email.str.split('@').str[0]
df['domain'] = df.email.str.split('@').str[-1]
df['source'] = df.link.map(host)
df['status'] = ''
df['reason'] = ''
df['category'] = ''
df['company'] = ''
df['evidence'] = ''


def mark(mask, status, reason):
    mask = mask & (df.status == '')
    df.loc[mask, 'status'] = status
    df.loc[mask, 'reason'] = reason
    return int(mask.sum())


def report(n, what):
    print(f'  {n:>5}  {what}')


# ── 2. Junk ──────────────────────────────────────────────────────────────────
print('\n[junk]')


def bad_syntax(e):
    try:
        validate_email(e, check_deliverability=False)
        return False
    except EmailNotValidError:
        return True


report(mark(df.email.map(bad_syntax), 'reject', 'bad syntax'), 'bad syntax')
report(mark(df.email.str.contains(r'\.(?:png|jpe?g|gif|webp|svg|js|css)$'), 'reject', 'file name, not an email'), 'file names')
fake_domain = df.domain.map(lambda d: in_set(d, placeholder_domains))
weak = df.local.isin(placeholder_locals_on_free) & df.domain.map(lambda d: in_set(d, free))
report(mark(df.local.isin(placeholder_locals) | fake_domain | weak, 'reject', 'placeholder address'), 'placeholders')
report(mark(df.domain.map(lambda d: in_set(d, platform)), 'reject', 'platform/directory/media address'), 'platform/directory domains')
dead = df.local.map(lambda l: l in dead_end or re.split(r'[._\-+]', l)[0] in dead_end)
report(mark(dead, 'reject', 'dead-end role address'), 'dead-end roles (noreply, compliance, careers...)')

# ── 3. Suppression ───────────────────────────────────────────────────────────
print('\n[suppression]')
EMAIL_RE = r'[\w.+%-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}'
client_domains, client_emails = set(), set()
for f in CLIENTS_DIR.glob('*.md'):
    if f.name != 'README.md':
        client_domains.add(f.stem.lower())
        client_emails |= {m.lower() for m in re.findall(EMAIL_RE, f.read_text(), re.I)}
report(mark(df.domain.map(lambda d: in_set(d, client_domains)) | df.email.isin(client_emails), 'suppressed', 'existing client'),
       f'existing clients ({len(client_domains)} client domains)')

lead_emails = set()
try:
    sys.path.insert(0, str(PROGRAMS / 'website_clone'))
    import gspread
    import ads_connector as ac
    sheet = gspread.authorize(ac.get_credentials()).open_by_key(ac.SPREADSHEET_ID)
    for ws in sheet.worksheets():
        lead_emails |= {m.lower() for row in ws.get_all_values() for c in row for m in re.findall(EMAIL_RE, c, re.I)}
except Exception as e:
    print(f'  WARNING: lead sheet not read ({type(e).__name__}: {e}); leads are not suppressed this run')
report(mark(df.email.isin(lead_emails), 'suppressed', 'already in lead sheet'), f'already in lead sheet ({len(lead_emails)} sheet emails)')

sup_emails, sup_domains = set(), set()
if SUPPRESS_FILE.exists():
    for line in SUPPRESS_FILE.read_text().splitlines():
        line = line.split('#')[0].strip().lower()
        if line.startswith('@'):
            sup_domains.add(line[1:])
        elif '@' in line:
            sup_emails.add(line)
report(mark(df.email.isin(sup_emails) | df.domain.map(lambda d: in_set(d, sup_domains)), 'suppressed', 'suppress.txt'), 'suppress.txt')

# ── 4. Known segment domains ─────────────────────────────────────────────────
print('\n[segments]')
for seg, domains in segment_domains.items():
    n = mark(df.domain.map(lambda d: in_set(d, domains)), seg, 'known domain')
    df.loc[(df.status == seg) & (df.category == ''), 'category'] = seg
    report(n, f'{seg} (known domains)')

# ── 5. MX check per domain ───────────────────────────────────────────────────
print('\n[mx]')
open_rows = df.status == ''
todo = sorted(set(df.loc[open_rows, 'domain']) - set(cache['mx']))


def mx_lookup(domain):
    for _ in range(2):
        try:
            dns.resolver.resolve(domain, 'MX', lifetime=6)
            return 'ok'
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return 'none'
        except Exception:
            continue
    return 'unknown'  # timeouts: keep the row, verification will catch it


with ThreadPoolExecutor(32) as ex:
    for d, res in zip(todo, ex.map(mx_lookup, todo)):
        cache['mx'][d] = res
save_cache()
mx = df.domain.map(cache['mx']).fillna('')
report(mark(open_rows & (mx == 'none'), 'reject', 'no mail server (MX) for domain'), 'no MX record')
print(f'  ({len(todo)} domains looked up, {int((open_rows & (mx == "unknown")).sum())} rows with DNS timeouts kept)')

# ── 6. Homepage evidence ─────────────────────────────────────────────────────
print('\n[evidence]')
open_rows = df.status == ''
is_free = df.domain.map(lambda d: in_set(d, free))
# company emails: the email domain's homepage. free mailboxes: the page the email was scraped from.
site_urls = set('https://' + df.loc[open_rows & ~is_free, 'domain']) | set(df.loc[open_rows & is_free, 'link'])
todo = sorted(site_urls - set(cache['site']))


def fetch(url):
    tries = [url, url.replace('https://', 'http://', 1)] if url.count('/') == 2 else [url]
    for u in tries:
        try:
            r = requests.get(u, headers=UA, timeout=10, allow_redirects=True)
            html = r.text[:300_000]
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
            desc = (re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I)
                    or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description', html, re.I))
            clean = lambda m: re.sub(r'\s+', ' ', unescape(m.group(1))).strip()[:200] if m else ''
            return {'title': clean(title), 'desc': clean(desc), 'final': host(r.url), 'status': r.status_code}
        except Exception as e:
            err = type(e).__name__
    return {'title': '', 'desc': '', 'final': '', 'status': err}


with ThreadPoolExecutor(32) as ex:
    futs = {ex.submit(fetch, u): u for u in todo}
    for n, fut in enumerate(as_completed(futs), 1):
        cache['site'][futs[fut]] = fut.result()
        if n % 200 == 0:
            save_cache()
            print(f'  {n}/{len(todo)} pages fetched', flush=True)
save_cache()
print(f'  {len(todo)} pages fetched')


def site_info(url):
    s = cache['site'].get(url, {})
    bits = [s.get('title', ''), s.get('desc', '')]
    if s.get('final') and s['final'] != host(url):
        bits.append(f"redirects to {s['final']}")
    if not s.get('title') and s.get('status'):
        bits.append(f"site unreachable ({s['status']})")
    return ' | '.join(b for b in bits if b)


df.loc[open_rows & ~is_free, 'evidence'] = ('https://' + df.loc[open_rows & ~is_free, 'domain']).map(site_info)
df.loc[open_rows & is_free, 'evidence'] = df.loc[open_rows & is_free, 'link'].map(site_info)

# ── 7. Claude classification ─────────────────────────────────────────────────
segments = profile['segments']
CATEGORIES = ['target', *segments, 'vendor', 'unrelated', 'unclear']
SYSTEM = f"""You sort scraped email leads for ex0dus, an affordable marketing service (SEO, blog posts, email,
social media, Google Business Profile, websites) sold to small businesses by Integrated Dynamics.

The target buyer for this list: {profile['target']}

For each item you get a key and either:
- a company email domain, how many addresses from it are in the list, a few sample local parts, the sites the
  addresses were scraped from, and the company's homepage title/description; or
- one free-mailbox address (gmail, yahoo...) with the page it was scraped from and that page's title.
Judge the business from that evidence and what you know about the company.

category:
- "target": matches the target buyer above. Combined broker+realty shops that do loans count as target.
{chr(10).join(f'- "{k}": {v}' for k, v in segments.items())}
- "vendor": sells services or software to these businesses (marketing, SEO, web design, lead gen, CRM, data,
  title/escrow/appraisal services, training). These are competitors or suppliers, not buyers.
- "unrelated": anything else (other industries, directories, media, government, schools, nonprofits).
- "unclear": not enough evidence to tell. For a free mailbox scraped from a page that lists mortgage brokers or
  loan officers, lean "target" if the local part looks like a person or a loan business.

company: the business name as best you can tell (title case, no Inc/LLC), "" if unknown.
note: at most 8 words on why.
Return one result per key, using the exact key."""

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['results'],
    'properties': {'results': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['key', 'category', 'company', 'note'],
        'properties': {'key': {'type': 'string'}, 'category': {'type': 'string', 'enum': CATEGORIES},
                       'company': {'type': 'string'}, 'note': {'type': 'string'}}}}}}

open_rows = df.status == ''
df['llm_key'] = ''
df.loc[open_rows & ~is_free, 'llm_key'] = 'd:' + df.loc[open_rows & ~is_free, 'domain']
df.loc[open_rows & is_free, 'llm_key'] = 'e:' + df.loc[open_rows & is_free, 'email']
domain_counts = df.domain.value_counts()

items = {}
for key, g in df[df.llm_key != ''].groupby('llm_key'):
    r = g.iloc[0]
    if key.startswith('d:'):
        items[key] = {'key': key, 'email_domain': r.domain, 'emails_in_list': int(domain_counts[r.domain]),
                      'sample_local_parts': g.local.head(6).tolist(), 'found_on': sorted(set(g.source))[:4],
                      'homepage': r.evidence}
    else:
        items[key] = {'key': key, 'email': r.email, 'found_on_page': r.link, 'page_info': r.evidence}
todo = [items[k] for k in items if k not in cache['llm']]


def classify(batch):
    p = subprocess.run(['claude', '-p', '--model', 'claude-opus-5-5', '--effort', 'low', '--tools', '',
                        '--no-session-persistence', '--system-prompt', SYSTEM, '--json-schema', json.dumps(SCHEMA),
                        '--output-format', 'json'],
                       input=json.dumps(batch, ensure_ascii=False), capture_output=True, text=True, timeout=900)
    out = json.loads(p.stdout)
    if out.get('is_error') or not out.get('structured_output'):
        raise RuntimeError(str(out.get('result'))[:300])
    want = {b['key'] for b in batch}
    return [r for r in out['structured_output']['results'] if r['key'] in want]


print(f'\n[claude] {len(items)} items to judge, {len(todo)} not cached')
if todo and not args.no_llm:
    batches = [todo[i:i + LLM_BATCH] for i in range(0, len(todo), LLM_BATCH)]
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(classify, b): i for i, b in enumerate(batches)}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                for r in fut.result():
                    cache['llm'][r['key']] = r
            except Exception as e:
                print(f'  batch failed: {type(e).__name__}: {e}')
            save_cache()
            print(f'  {n}/{len(batches)} batches done', flush=True)

for i, key in df.loc[df.llm_key != '', 'llm_key'].items():
    r = cache['llm'].get(key)
    if not r:
        df.at[i, 'status'] = 'pending'
        df.at[i, 'reason'] = 'not classified yet, rerun'
        continue
    cat = r['category']
    df.at[i, 'category'] = cat
    df.at[i, 'company'] = r['company']
    df.at[i, 'reason'] = r['note']
    df.at[i, 'status'] = ('keep' if cat in ('target', 'unclear') else cat if cat in segments else 'reject')

# Hand overrides: companies Claude files elsewhere but whose people buy their own marketing (e.g. broker platforms).
target_domains = set(profile.get('target_domains', []))
forced = df.domain.map(lambda d: in_set(d, target_domains)) & df.status.isin([*segments, 'reject']) & (df.reason != 'dead-end role address')
df.loc[forced, ['status', 'category', 'reason']] = ['keep', 'target', 'target_domains override']

# ── 8. Rank + spread ─────────────────────────────────────────────────────────
# Tier 1 own-domain person, 2 own-domain role address, 3 free mailbox, 4 unclear.
is_role = df.local.map(lambda l: l in role_locals or re.split(r'[._\-+]', l)[0] in role_locals)
df['tier'] = 0  # 0 = not in clean.csv
keep = df.status == 'keep'
df.loc[keep, 'tier'] = 1
df.loc[keep & is_role, 'tier'] = 2
df.loc[keep & is_free, 'tier'] = 3
df.loc[keep & (df.category == 'unclear'), 'tier'] = 4


def spread(part):
    """Order by tier, and within a tier space each domain's rows evenly instead of bunching them."""
    rng = random.Random(42)
    part = part.copy()
    pos = part.groupby(['tier', 'domain']).cumcount()
    size = part.groupby(['tier', 'domain'])['email'].transform('size')
    part['_pos'] = [(p + rng.random()) / s for p, s in zip(pos, size)]
    return part.sort_values(['tier', '_pos']).drop(columns='_pos')


cols = ['email', 'phone', 'link', 'company', 'category', 'tier', 'reason', 'evidence', 'pages_found']
spread(df[keep])[cols].to_csv(out_dir / 'clean.csv', index=False)
for seg in segments:
    df[df.status == seg].sort_values(['domain', 'email'])[cols].to_csv(out_dir / f'{seg}.csv', index=False)
df[df.status.isin(['reject', 'suppressed', 'pending'])].sort_values(['status', 'reason', 'email'])[
    ['email', 'status', 'reason', 'category', 'company', 'link', 'evidence']].to_csv(out_dir / 'rejects.csv', index=False)
df.drop(columns=['local', 'llm_key']).to_csv(out_dir / 'all.csv', index=False)

print('\n' + '─' * 50)
print(df.status.value_counts().to_string())
print('─' * 50)
print('clean.csv by tier:', df[keep].tier.value_counts().sort_index().to_dict())
if (df.status == 'pending').any():
    print(f"WARNING: {int((df.status == 'pending').sum())} rows still pending; rerun to classify them")
print(f'Output: {out_dir}/')
