"""Step 3: assemble the tabs and write them to a new Google Sheet owned by the integrated-dynamics token
(kalamazoo951@gmail.com). Pass --dry to only write local CSVs."""
import json
import re
import sys
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

OUT = Path(sys.argv[1])
DRY = '--dry' in sys.argv
MAX_PER_COMPANY = 10
TOKEN = '/home/web/.config/integrated-dynamics/token.json'

TYPES = ['EPDM', 'Neoprene', 'Nitrile (Buna-N)', 'Silicone', 'Viton (FKM)', 'SBR', 'Red Rubber',
         'Natural Gum Rubber', 'Rubber Extrusions', 'Custom Molded & Stamped Parts', 'PVC Strip Curtains & Facility Safety']
TAB = {'PVC Strip Curtains & Facility Safety': 'PVC Curtains & Safety', 'Custom Molded & Stamped Parts': 'Custom Molded Parts'}

emails = pd.read_csv(OUT / 'emails.csv', dtype=str).fillna('')
cls = pd.DataFrame([json.loads(l) for l in open(OUT / 'classified.jsonl')]).drop_duplicates('key')
missing = set(emails.company_key) - set(cls.key)
if missing:
    print(f'WARNING: {len(missing)} companies not classified yet')
d = emails.merge(cls, left_on='company_key', right_on='key', how='inner')

ROLE = re.compile(r'^(customer|field|info|sales|contact|office|admin|support|service|hello|help|team|orders?|customerservice|'
                  r'cs|general|inquir(y|ies)|enquir(y|ies)|mail|marketing|accounts?|billing|ap|ar|parts|quotes?|'
                  r'estimating|engineering|operations|ops|dispatch|safety|quality|tech|projects|rentals|service|shop|warehouse|shipping|reception|purchasing|procurement|buyer)')
BUYER = re.compile(r'^(purchas|procure|buyer|buying|supply|sourcing|materials)')
SALESISH = re.compile(r'^(sales|quotes?|orders?|estimating|parts)')
INFO = re.compile(r'^(info|contact|office|hello|general|inquir|enquir)')


def contact_type(r):
    local = r.email.split('@')[0]
    if BUYER.match(local): return 'Purchasing inbox'
    if ROLE.match(local): return 'General inbox'
    return 'Personal (free email)' if r.freemail == 'True' else 'Named person'


def rank(local):
    if BUYER.match(local): return 0
    if SALESISH.match(local): return 1
    if INFO.match(local): return 2
    if not ROLE.match(local): return 3
    return 4


d['Contact Type'] = d.apply(contact_type, axis=1)
d['_rank'] = d.email.str.split('@').str[0].map(rank)
d = d.sort_values(['company_key', '_rank', 'email'])
d['_n'] = d.groupby('company_key').cumcount()
capped = int((d._n >= MAX_PER_COMPANY).sum())
d = d[d._n < MAX_PER_COMPANY]

d['country'] = d.country.str.upper().str.strip()
d['Also Pitch'] = d.also_pitch.map(lambda xs: ', '.join(x for x in xs if x))
d['Found On'] = d.link
COLS = ['Email', 'Company', 'Industry', 'Contact Type', 'Also Pitch', 'Country', 'Phone', 'Found On',
        'Search', 'Email Status', 'Notes']


def frame(x, lead_col=None):
    out = pd.DataFrame({
        'Email': x.email, 'Company': x.company, 'Industry': x.industry, 'Contact Type': x['Contact Type'],
        'Also Pitch': x['Also Pitch'], 'Country': x.country, 'Phone': x.phone, 'Found On': x['Found On'],
        'Search': x.searches, 'Email Status': '', 'Notes': ''})
    if lead_col:
        out.insert(2, 'Lead With', x.primary_rubber)
    return out.sort_values(['Company', 'Email'], key=lambda s: s.str.lower())


pros = d[d.status == 'prospect']
us = pros.country.isin(['US', ''])
tabs = {}
counts = []
for t in TYPES:
    x = pros[(pros.primary_rubber == t) & us]
    tabs[TAB.get(t, t)] = frame(x)
    counts.append((t, len(x), x.company_key.nunique()))
intl = pros[~us]
tabs['International'] = frame(intl, lead_col=True)
comp = d[d.status == 'competitor']
tabs['Competitors & Resellers'] = frame(comp, lead_col=True)
nr = d[d.status == 'not_relevant']
tabs['Not Relevant'] = frame(nr)

prep = json.load(open(OUT / 'prep_stats.json'))
summary = [
    ['SoCal Rubber prospect list, sorted by the rubber line to lead with'],
    ['Built 2026-10-18 from the python_email_scraper rubber searches: rubber extrusions, construction seal & gasket '
     'manufacturers, electrical insulation parts, marine equipment manufacturers.'],
    [],
    ['Tab', 'Lead with', 'Emails', 'Companies'],
]
for t, n, c in counts:
    summary.append([TAB.get(t, t), t, n, c])
summary += [
    ['International', 'Prospects outside the US (Lead With column)', len(intl), intl.company_key.nunique()],
    ['Competitors & Resellers', 'Make or sell rubber themselves; possible resellers', len(comp), comp.company_key.nunique()],
    ['Not Relevant', 'News, chambers, directories, services, consumers. Kept here so nothing is lost.', len(nr), nr.company_key.nunique()],
    [],
    ['Cleanup'],
    ['Rows in the four source CSVs', prep['rows_in']],
    ['Exact duplicate emails merged (across and within files)', prep['dropped'].get('duplicate', 0)],
    ['Placeholder addresses removed (example.com, johndoe@, wix/sentry...)', prep['dropped'].get('placeholder', 0)],
    ['Non-buyer inboxes removed (noreply, privacy, careers, press, investors...)', prep['dropped'].get('not a purchasing contact', 0)],
    ['Malformed addresses removed', prep['dropped'].get('malformed', 0)],
    [f'Extra addresses beyond {MAX_PER_COMPANY} per company dropped (kept purchasing, sales, info first)', capped],
    ['Rows with no email (phone only) skipped', prep['no_email']],
    [],
    ['Columns'],
    ['Lead With / tab', 'The compound or product line most likely to fit what the company does'],
    ['Also Pitch', 'Up to two other lines worth mentioning'],
    ['Contact Type', 'Purchasing inbox, General inbox, Named person, or Personal (free email)'],
    ['Search', 'Which scrape search found it'],
    ['Email Status', 'Blank. Fill in with verification results before mailing.'],
    [],
    ['Company, industry and lead-with were judged by Claude from the domain names; spot-check before a big send.'],
]

for name, df in tabs.items():
    df.to_csv(OUT / f'tab_{re.sub(r"[^A-Za-z0-9]+", "_", name)}.csv', index=False)
print(pd.DataFrame(summary[4:4 + len(TYPES) + 3]).to_string(index=False, header=False))
print('capped', capped)
if DRY:
    sys.exit()

creds = Credentials.from_authorized_user_file(TOKEN)
if not creds.valid:
    creds.refresh(Request())  # in memory only; token.json is not rewritten
s = AuthorizedSession(creds)
API = 'https://sheets.googleapis.com/v4/spreadsheets'

order = ['Summary'] + list(tabs)
body = {'properties': {'title': 'SoCal Rubber Leads by Rubber Type (2026-10-18)'},
        'sheets': [{'properties': {'title': n, 'index': i,
                                   'gridProperties': {'frozenRowCount': 0 if n == 'Summary' else 1}}}
                   for i, n in enumerate(order)]}
r = s.post(API, json=body); r.raise_for_status()
sheet = r.json()
sid = sheet['spreadsheetId']
ids = {p['properties']['title']: p['properties']['sheetId'] for p in sheet['sheets']}


def q(name):
    return "'" + name.replace("'", "''") + "'"


data = [{'range': f'{q("Summary")}!A1', 'values': summary}]
for name, df in tabs.items():
    data.append({'range': f'{q(name)}!A1', 'values': [list(df.columns)] + df.astype(str).values.tolist()})
r = s.post(f'{API}/{sid}/values:batchUpdate', json={'valueInputOption': 'RAW', 'data': data}); r.raise_for_status()

reqs = [
    {'repeatCell': {'range': {'sheetId': ids['Summary'], 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {'userEnteredFormat': {'textFormat': {'bold': True, 'fontSize': 14}}},
                    'fields': 'userEnteredFormat.textFormat'}},
    {'updateDimensionProperties': {'range': {'sheetId': ids['Summary'], 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
                                   'properties': {'pixelSize': 520}, 'fields': 'pixelSize'}},
    {'updateDimensionProperties': {'range': {'sheetId': ids['Summary'], 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': 2},
                                   'properties': {'pixelSize': 420}, 'fields': 'pixelSize'}},
]
for i, row in enumerate(summary):
    if row and (row[0] in ('Tab', 'Cleanup', 'Columns')):
        reqs.append({'repeatCell': {'range': {'sheetId': ids['Summary'], 'startRowIndex': i, 'endRowIndex': i + 1},
                                    'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
                                    'fields': 'userEnteredFormat.textFormat'}})
for name, df in tabs.items():
    g = ids[name]
    ncol = len(df.columns)
    reqs += [
        {'repeatCell': {'range': {'sheetId': g, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True},
                                                       'backgroundColor': {'red': .9, 'green': .92, 'blue': .95}}},
                        'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
        {'setBasicFilter': {'filter': {'range': {'sheetId': g, 'startRowIndex': 0, 'endRowIndex': len(df) + 1,
                                                 'startColumnIndex': 0, 'endColumnIndex': ncol}}}},
        {'autoResizeDimensions': {'dimensions': {'sheetId': g, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': ncol}}},
    ]
    fo = list(df.columns).index('Found On')
    reqs.append({'updateDimensionProperties': {'range': {'sheetId': g, 'dimension': 'COLUMNS', 'startIndex': fo, 'endIndex': fo + 1},
                                               'properties': {'pixelSize': 280}, 'fields': 'pixelSize'}})
r = s.post(f'{API}/{sid}:batchUpdate', json={'requests': reqs}); r.raise_for_status()
print(f'https://docs.google.com/spreadsheets/d/{sid}/edit')
