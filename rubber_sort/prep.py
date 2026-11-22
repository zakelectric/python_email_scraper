"""Step 1: merge the rubber-related scrape CSVs, clean and de-duplicate emails,
and group them by company so each company is classified once."""
import json
import re
import sys
from pathlib import Path

import pandas as pd

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])

# Search term -> label. Marine uses the raw file (a superset of _final; _00/_01 are _final split in two).
FILES = {
    'results_rubber+extrusions.csv': 'Rubber extrusions',
    'results_construction+seal+and+gasket+manufacturer.csv': 'Construction seal & gasket manufacturers',
    'results_electrical+insulation+parts.csv': 'Electrical insulation parts',
    'results_marine+equipment+manufacturer.csv': 'Marine equipment manufacturers',
}

FREEMAIL = {'gmail.com', 'yahoo.com', 'ymail.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'live.com',
            'aol.com', 'comcast.net', 'msn.com', 'sbcglobal.net', 'att.net', 'verizon.net', 'me.com',
            '126.com', '163.com', 'qq.com', 'sina.com', 'yeah.net', 'foxmail.com', 'yandex.ru', 'mail.ru', 'naver.com', 'rediffmail.com', 'hotmail.co.uk', 'yahoo.co.uk', 'yahoo.com.cn', 'web.de', 'orange.fr', 'libero.it', 'btinternet.com', 'rocketmail.com', 'cox.net', 'bellsouth.net', 'charter.net', 'earthlink.net', 'mac.com', 'protonmail.com', 'gmx.com'}
PLACEHOLDER_DOMAINS = {'example.com', 'domain.com', 'yourdomain.com', 'my-domain.com', 'adomain.com', 'email.com',
                       'mysite.com', 'yoursite.com', 'company.com', 'website.com', 'sentry.io', 'wixpress.com',
                       'sentry-next.wixpress.com', 'test.com', 'site.com', 'yourcompany.com', 'mail.com'}
PLACEHOLDER_LOCAL = re.compile(r'^(john\.?doe|jane\.?doe|jdoe|jhondoe|your\.?(email|name)|email|name|user(name)?|'
                               r'first\.?last|firstname(\.lastname)?|test|abc|xyz|new\.email\d*|example|someone)$')
NOT_A_BUYER = re.compile(r'^(no-?reply|do-?not-?reply|donotreply|privacy|legal|abuse|postmaster|webmaster|hostmaster|'
                         r'careers?|jobs|hr|recruit(ing|ment)?|talent|resumes?|press|media|pr|news(letter)?|'
                         r'unsubscribe|dpo|gdpr|accessibility|ada|compliance|ethics|whistleblow\w*|investors?|ir|'
                         r'donations?|editor|letters|tips|newsroom|advertis\w*|subscriptions?|circulation|'
                         r'security|fraud|dmca|copyright)@')
FILE_EXT = re.compile(r'\.(png|jpe?g|gif|webp|svg|bmp|ico|css|js|pdf|mp4|woff2?)$')
VALID = re.compile(r'^[a-z0-9](?:[a-z0-9._%+\-]*[a-z0-9])?@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9\-]+)*\.[a-z]{2,24}$')


def fix(e):
    e = str(e).strip().lower().strip('.,;:<>()[]"\'')
    e = re.sub(r'^mailto:', '', e)
    e = re.sub(r'^(%[0-9a-f]{2})+', '', e)              # url-encoded junk glued to the front
    e = re.sub(r'^(20|22|3e|3c)(?=[a-z])', '', e)       # leftover from %20 / %22 / %3e
    e = re.sub(r'^(u00[0-9a-f]{2})+', '', e)            # escaped \u00a0 / \u003e glued to the front
    e = re.sub(r'^(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}', '', e)  # phone number glued to the front
    e = re.sub(r'^\d{3}[\s.\-]\d{4}(?=[a-z])', '', e)   # last 7 digits of a phone number
    e = re.sub(r'^\d{2,5}(?=(info|sales|contact|orders?|service|office|support|hello|admin)@)', '', e)
    e = e.split('?')[0]
    return e


rows = []
for name, cat in FILES.items():
    d = pd.read_csv(SRC / name, dtype=str)
    d['search'] = cat
    rows.append(d)
d = pd.concat(rows, ignore_index=True)
total_rows = len(d)
no_email = int(d.email.isna().sum())
d = d[d.email.notna()].copy()
d['raw_email'] = d.email
d['email'] = d.email.map(fix)
d['link'] = d.link.fillna('')

drop = {}
def why(e):
    if not VALID.match(e): return 'malformed'
    if FILE_EXT.search(e): return 'malformed'
    local, dom = e.split('@')
    if dom in PLACEHOLDER_DOMAINS or dom.endswith('.example.com') or dom.endswith('wixpress.com'): return 'placeholder'
    if PLACEHOLDER_LOCAL.match(local): return 'placeholder'
    if NOT_A_BUYER.match(e): return 'not a purchasing contact'
    if re.fullmatch(r'[0-9a-f]{20,}', local): return 'machine-generated'
    if dom.endswith('socalrubber.com') or dom.endswith('socalrubber.us'): return 'socalrubber itself'
    return ''
d['drop'] = d.email.map(why)
for k, v in d.loc[d['drop'] != '', 'drop'].value_counts().items():
    drop[k] = int(v)
d = d[d['drop'] == '']

d['email_domain'] = d.email.str.split('@').str[1]
d['link_domain'] = d.link.str.extract(r'//(?:www\.)?([^/:?#]+)', expand=False).fillna('').str.lower()
d['freemail'] = d.email_domain.isin(FREEMAIL)
# company key: the email's own domain, or for gmail-style addresses the site it was found on + the address
d['company_key'] = d.email_domain.where(~d.freemail, d.link_domain + '|' + d.email)

# de-duplicate: one row per email, remember every search it came from and the first page it was found on
order = {c: i for i, c in enumerate(FILES.values())}
d['o'] = d.search.map(order)
d = d.sort_values('o')
before = len(d)
agg = d.groupby('email', sort=False).agg(
    searches=('search', lambda s: ' / '.join(dict.fromkeys(s))),
    link=('link', 'first'), link_domain=('link_domain', 'first'), email_domain=('email_domain', 'first'),
    freemail=('freemail', 'first'), company_key=('company_key', 'first'),
    phone=('phone', lambda s: next((x for x in s if isinstance(x, str) and x.strip()), ''))).reset_index()
drop['duplicate'] = before - len(agg)

agg.to_csv(OUT / 'emails.csv', index=False)
comp = agg.groupby('company_key').agg(
    email_domain=('email_domain', 'first'), freemail=('freemail', 'first'),
    found_on=('link_domain', lambda s: ', '.join(list(dict.fromkeys(s))[:3])),
    searches=('searches', lambda s: ' / '.join(dict.fromkeys(' / '.join(s).split(' / ')))),
    sample=('email', lambda s: ', '.join(list(s)[:4])), n=('email', 'size')).reset_index()
comp.to_csv(OUT / 'companies.csv', index=False)
json.dump({'rows_in': total_rows, 'no_email': no_email, 'dropped': drop, 'emails_kept': len(agg), 'companies': len(comp)},
          open(OUT / 'prep_stats.json', 'w'), indent=2)
print(json.load(open(OUT / 'prep_stats.json')))
print(agg.searches.value_counts().head(10))
