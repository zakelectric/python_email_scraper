"""Step 2: classify each company with Claude: is it a SoCal Rubber prospect, and which compound to pitch.
Resumable: results are appended to classified.jsonl and already-done keys are skipped."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import subprocess

import anthropic
import pandas as pd

OUT = Path(sys.argv[1])
BATCH = 80

key = next(l.split('=', 1)[1].strip().strip('"\'') for l in open(os.path.expanduser('~/.config/integrated-dynamics/.env'))
           if l.startswith('ANTHROPIC_KEY='))
client = anthropic.Anthropic(api_key=key, max_retries=6)

TYPES = ['EPDM', 'Neoprene', 'Nitrile (Buna-N)', 'Silicone', 'Viton (FKM)', 'SBR', 'Red Rubber',
         'Natural Gum Rubber', 'Rubber Extrusions', 'Custom Molded & Stamped Parts', 'PVC Strip Curtains & Facility Safety']

SYSTEM = f"""You sort scraped B2B email leads for SoCal Rubber, an industrial rubber supplier in Anaheim, California.
SoCal Rubber sells sheet rubber and rolls cut to order (EPDM, neoprene, nitrile/Buna-N, silicone, Viton, SBR, red rubber,
natural gum rubber; 35-85 Shore A), rubber extrusions, custom molded and stamped parts (gaskets, O-rings, seals, hoses),
PVC strip curtains, wheel stops, speed bumps and gym flooring. Buyers: purchasing agents, maintenance managers,
engineers, fabricators, distributors and wholesalers, mainly Southern California and the US West Coast, but any US
business is fine.

For each company you get a key, the email domain, the sites the emails were found on, the search that found it and a
few sample addresses. Judge the business from the domain names (you may know many of these companies).

status:
- "prospect": a business that plausibly buys industrial rubber (manufacturers, OEMs, fabricators, contractors,
  marine/boat builders and yards, electrical/utility equipment makers, industrial distributors, plants, facilities).
- "competitor": a company that itself makes or sells rubber sheet, gaskets, seals, extrusions or molded rubber.
  They can still be resellers, but flag them.
- "not_relevant": news sites, magazines and trade publishers, chambers of commerce, economic development agencies,
  associations, government, schools, stock-photo/web/software/marketing/hiring services, directories, banks,
  restaurants, retail consumers, real estate, and anything with no plausible need for industrial rubber.

primary_rubber: the one product line to lead with for this company, from this list: {TYPES}.
Rules of thumb: marine/boat/dock/outdoor/weatherproof -> Neoprene or EPDM; roofing, windows, doors, glazing, HVAC,
construction, water/wastewater, outdoor seals -> EPDM; oil, fuel, hydraulics, pumps, engines, automotive/heavy equipment
-> Nitrile (Buna-N); high heat, electrical insulation, food & beverage, lighting, electronics -> Silicone; chemical
processing, aerospace, very high temperature or aggressive fluids -> Viton (FKM); general-purpose pads, wear strips,
matting -> SBR; steam/flanges/old-line plant gaskets -> Red Rubber; abrasion/chutes/mining/sandblasting -> Natural Gum
Rubber; door/window/panel profiles and edge trim -> Rubber Extrusions; OEMs needing specific gaskets, grommets, bumpers
or seals to a drawing -> Custom Molded & Stamped Parts; warehouses, loading docks, parking, cold storage -> PVC Strip
Curtains & Facility Safety. For not_relevant companies still fill the field with your best guess.

also_pitch: up to two other lines from the same list.
industry: 2-5 words. company: the business name as best you can tell (title case, no Inc/LLC needed).
country: two-letter country code if you can tell (from the TLD or what you know), else "".
Return one result per key, using the exact key."""

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['results'],
    'properties': {'results': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['key', 'status', 'company', 'industry', 'primary_rubber', 'also_pitch', 'country'],
        'properties': {
            'key': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['prospect', 'competitor', 'not_relevant']},
            'company': {'type': 'string'}, 'industry': {'type': 'string'},
            'primary_rubber': {'type': 'string', 'enum': TYPES},
            'also_pitch': {'type': 'array', 'items': {'type': 'string', 'enum': TYPES}},
            'country': {'type': 'string'}}}}}}

comp = pd.read_csv(OUT / 'companies.csv', dtype=str).fillna('')
done_path = OUT / 'classified.jsonl'
done = set()
if done_path.exists():
    done = {json.loads(l)['key'] for l in open(done_path)}
todo = comp[~comp.company_key.isin(done)]
print(f'{len(done)} done, {len(todo)} to classify')


USE_CLI = '--cli' in sys.argv  # bill the Claude Max login through headless Claude Code instead of the API key


def run_cli(items, want):
    p = subprocess.run(['claude', '-p', '--model', 'claude-opus-5-5', '--effort', 'low', '--tools', '',
                        '--no-session-persistence', '--system-prompt', SYSTEM, '--json-schema', json.dumps(SCHEMA),
                        '--output-format', 'json'],
                       input=json.dumps(items, ensure_ascii=False), capture_output=True, text=True, timeout=900)
    out = json.loads(p.stdout)
    if out.get('is_error') or not out.get('structured_output'):
        raise RuntimeError(str(out.get('result'))[:300])
    u = out.get('usage', {})
    return ([r for r in out['structured_output']['results'] if r['key'] in want],
            type('U', (), {'input_tokens': u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0)
                           + u.get('cache_creation_input_tokens', 0), 'output_tokens': u.get('output_tokens', 0)}))


def run(chunk):
    if USE_CLI:
        items = [{'key': r.company_key, 'email_domain': r.email_domain, 'found_on': r.found_on,
                  'search': r.searches, 'sample_emails': r.sample} for r in chunk.itertuples()]
        return run_cli(items, set(chunk.company_key))
    items = [{'key': r.company_key, 'email_domain': r.email_domain, 'found_on': r.found_on,
              'search': r.searches, 'sample_emails': r.sample} for r in chunk.itertuples()]
    with client.messages.stream(
        model='claude-opus-5-5', max_tokens=32000, system=SYSTEM,
        output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
        messages=[{'role': 'user', 'content': json.dumps(items, ensure_ascii=False)}],
    ) as s:
        msg = s.get_final_message()
    if msg.stop_reason != 'end_turn':
        raise RuntimeError(f'stop_reason={msg.stop_reason}')
    text = next(b.text for b in msg.content if b.type == 'text')
    res = json.loads(text)['results']
    want = set(chunk.company_key)
    return [r for r in res if r['key'] in want], msg.usage


chunks = [todo.iloc[i:i + BATCH] for i in range(0, len(todo), BATCH)]
tin = tout = 0
with ThreadPoolExecutor(6) as ex, open(done_path, 'a') as f:
    futs = {ex.submit(run, c): i for i, c in enumerate(chunks)}
    for n, fut in enumerate(as_completed(futs), 1):
        try:
            res, u = fut.result()
        except Exception as e:
            print('batch failed:', type(e).__name__, e)
            continue
        for r in res:
            f.write(json.dumps(r) + '\n')
        f.flush()
        tin += u.input_tokens; tout += u.output_tokens
        print(f'{n}/{len(chunks)} batches, {len(res)} results, tokens in={tin} out={tout}', flush=True)
