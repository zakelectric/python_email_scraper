"""Places API version of scraper_automated.py: Google Maps listings instead of DuckDuckGo results.

Run from a campaign folder, same files as the Selenium scraper:
    cd campaigns/psychic_local && ../../places_scraper.py

    search_terms.json  {"0": "crystal+shop", ...}
    cities.json        {"0": "astoria+queens", ...}
    campaign.json      region, max_pages (20 places per page, 3 max), skip_domains, max_calls

Every term x area goes to Places Text Search; each listing's website homepage is fetched once and
searched for emails. Places responses are cached in places_raw.json, so a rerun only pays for
term x area pairs it has not searched yet.

Writes:
    results_<term>.csv  email, phone, link (+ name, address, ...) — same first columns as the
                        Selenium scraper, so clean_list.py takes it
    places_all.csv      every listing with a website, emails or not (for the broken-link pass)
"""
import concurrent.futures as cf
import csv
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests

PLACES = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.websiteUri,places.nationalPhoneNumber,"
          "places.formattedAddress,places.businessStatus,places.primaryType,places.userRatingCount,"
          "nextPageToken")
ENV_FILE = os.path.expanduser("~/.config/integrated-dynamics/.env")
RAW_FILE = "places_raw.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# Listings whose "website" is a profile page elsewhere: no site of their own to scrape
SOCIAL = ['facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'linkedin.com', 'youtube.com', 'tiktok.com',
          'yelp.com', 'linktr.ee', 'google.com', 'business.site', 'pinterest.com']
# Same filters as scraper_automated.find_emails, plus template placeholders
UNWANTED_EMAIL = ['sentry', 'wix', 'godaddy', 'example.', '@email.com', '@domain.com', 'mysite.com', 'yourname', 'yourdomain']
# A page listing more addresses than this is a staff directory, not a contact line
MAX_EMAILS_PER_SITE = 3
EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')


def load_key():
    if os.environ.get("GOOGLE_PLACES_API_KEY"):
        return os.environ["GOOGLE_PLACES_API_KEY"]
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("GOOGLE_PLACES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"\'')
    sys.exit(f"GOOGLE_PLACES_API_KEY not in the environment or {ENV_FILE}")


def search(key, query, max_pages):
    """All places for one query, following nextPageToken up to max_pages. Returns (places, calls)."""
    places, token, calls = [], None, 0
    for _ in range(max_pages):
        body = {"textQuery": query, "pageSize": 20}
        if token:
            body["pageToken"] = token
        r = requests.post(PLACES, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS},
                          json=body, timeout=30)
        calls += 1
        if r.status_code != 200:
            sys.exit(f"Places API {r.status_code} on {query!r}: {r.text[:300]}")
        d = r.json()
        places += d.get("places", [])
        token = d.get("nextPageToken")
        if not token:
            break
        time.sleep(1)  # the next page token takes a moment to become valid
    return places, calls


def decode_cfemail(hexstr):
    # Cloudflare's email protection: first byte is the XOR key for the rest
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def find_emails(html):
    found = set(EMAIL_RE.findall(html))
    found |= {decode_cfemail(h) for h in CFEMAIL_RE.findall(html)}
    return {e for e in found
            if len(e) <= 45
            and e.lower().endswith(('.com', '.net', '.org'))
            and not any(u in e.lower() for u in UNWANTED_EMAIL)}


def fetch_emails(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    except requests.RequestException as e:
        return {"status": None, "error": type(e).__name__, "emails": []}
    return {"status": r.status_code, "final_url": r.url,
            "emails": sorted(find_emails(r.text)) if r.status_code < 400 else []}


def main():
    with open("search_terms.json") as f:
        terms = json.load(f)
    with open("cities.json") as f:
        areas = json.load(f)
    campaign = {}
    if os.path.exists("campaign.json"):
        with open("campaign.json") as f:
            campaign = json.load(f)
    region = campaign.get("region", "")
    max_pages = min(campaign.get("max_pages", 3), 3)
    max_calls = campaign.get("max_calls", 400)
    skip = SOCIAL + campaign.get("skip_domains", [])

    raw = {"searches": {}, "sites": {}}
    if os.path.exists(RAW_FILE):
        with open(RAW_FILE) as f:
            raw = json.load(f)

    # 1. Places searches (cached per query)
    key, calls = None, 0
    for t in terms.values():
        for a in areas.values():
            query = " ".join(p for p in (t.replace("+", " "), "in", a.replace("+", " "), region) if p)
            if query in raw["searches"]:
                continue
            if calls >= max_calls:
                print(f"Stopped at max_calls={max_calls}; rerun to continue.")
                break
            key = key or load_key()
            places, n = search(key, query, max_pages)
            calls += n
            raw["searches"][query] = {"term": t, "area": a, "places": places}
            print(f"{query}: {len(places)} places ({n} calls)")
            with open(RAW_FILE, "w") as f:
                json.dump(raw, f)
    print(f"Places API calls this run: {calls}")

    # 2. One record per place, first search that found it wins
    listings = {}
    for s in raw["searches"].values():
        for p in s["places"]:
            site = p.get("websiteUri")
            if p["id"] in listings or not site or p.get("businessStatus") != "OPERATIONAL":
                continue
            host = (urlparse(site).hostname or "").lower()
            if not host or any(d in host for d in skip):
                continue
            listings[p["id"]] = {
                "name": p.get("displayName", {}).get("text", ""), "address": p.get("formattedAddress", ""),
                "phone": p.get("nationalPhoneNumber", ""), "link": site, "host": host.removeprefix("www."),
                "term": s["term"], "area": s["area"], "type": p.get("primaryType", ""),
                "reviews": p.get("userRatingCount") or 0, "place_id": p["id"]}

    # 3. Fetch each website once (chains share one site)
    todo = sorted({l["link"] for l in listings.values()} - set(raw["sites"]))
    print(f"{len(listings)} listings with a website, fetching {len(todo)} new sites")
    with cf.ThreadPoolExecutor(8) as ex:
        for i, (url, res) in enumerate(zip(todo, ex.map(fetch_emails, todo)), 1):
            raw["sites"][url] = res
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} sites")
                with open(RAW_FILE, "w") as f:
                    json.dump(raw, f)

    # 4. Write CSVs (rebuilt from the cache each run)
    cols = ["email", "phone", "link", "name", "address", "area", "type", "reviews", "place_id"]
    by_term, seen_emails = {}, set()
    for l in sorted(listings.values(), key=lambda l: -l["reviews"]):
        site = raw["sites"].get(l["link"], {})
        l["site_status"] = site.get("status") or site.get("error", "")
        # Re-filter cached emails so filter changes apply without refetching; the site's own domain first
        emails = sorted(find_emails(" ".join(site.get("emails", []))),
                        key=lambda e: (not e.lower().endswith("@" + l["host"]), e.lower()))
        if len(emails) > MAX_EMAILS_PER_SITE:
            emails = []
        l["emails"] = " ".join(emails)
        for e in emails:
            if e.lower() in seen_emails:
                continue
            seen_emails.add(e.lower())
            by_term.setdefault(l["term"], []).append({**l, "email": e})
    for t, rows in by_term.items():
        with open(f"results_{t}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    with open("places_all.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["name", "link", "host", "emails", "site_status", "phone", "address",
                               "term", "area", "type", "reviews", "place_id"], extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(listings.values(), key=lambda l: (l["term"], -l["reviews"])))

    with_email = sum(1 for l in listings.values() if l["emails"])
    print(f"\n{len(listings)} listings, {with_email} with an email on the homepage, {len(seen_emails)} unique emails")
    for t in terms.values():
        print(f"  results_{t}.csv: {len(by_term.get(t, []))} emails")


if __name__ == "__main__":
    main()
