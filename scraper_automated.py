import re
import time
import os
import platform
import json
from urllib.parse import unquote, urlparse, parse_qs
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pandas as pd

chrome_options = webdriver.ChromeOptions()
#chrome_options.add_argument('--headless')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')

LATAM_COUNTRY_CODES = r'52|57|55|54|56|51|58|593|591|595|598|507|506|502|504|503|505|53|509'
unwanted_links = ['zillow', 'duck', 'w3', 'houzz', 'github', 'google', 'apple', 'nytimes', 'api.you', 'yelp', 'yahoo', 'reddit', 'uniontribune']
unwanted_email = ['sentry', 'wix', 'godaddy']
email_added = 0
email_skipped = 0
skipped_links = 0

y = 0 # Make sure to update this to 0 if starting from scratch. Used as Index of search_terms
s = 0 # Represents state
current_link = "" # Last link searched

# with open('cities.json', 'r') as f:
#     cities = json.load(f)
with open('locations.json', 'r') as f:
    states = json.load(f)
with open('search_terms.json', 'r') as f:
    search_terms = json.load(f)

def create_driver():
    d = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    d.set_page_load_timeout(15)
    return d

driver = create_driver()

def reopen_scrape_tab():
    global scrape_tab, search_tab, main_window, driver
    try:
        driver.switch_to.window(scrape_tab)
        driver.close()
        driver.switch_to.window(search_tab)
        driver.execute_script("window.open('');")
        driver.switch_to.window(driver.window_handles[-1])
        scrape_tab = driver.current_window_handle
    except Exception as e:
        print(f"Error in reopen_scrape_tab: {e}")
        print("Restarting driver and tabs...")
        try:
            driver.quit()
        except Exception:
            pass
        driver = create_driver()
        driver.get(search_link)
        search_tab = driver.current_window_handle
        driver.execute_script("window.open('');")
        driver.switch_to.window(driver.window_handles[-1])
        scrape_tab = driver.current_window_handle
        main_window = search_tab


SEEN_EMAILS_FILE = 'seen_emails.json'
SEEN_LINKS_FILE = 'seen_links.json'
STATE_FILE = "scraper_state.json"


use_signal = os.name != 'nt' and platform.system() != 'Windows'

if use_signal:
    import signal
    class TimeoutException(Exception):
        pass
    def handler(signum, frame):
        raise TimeoutException()
    signal.signal(signal.SIGALRM, handler)
else:
    class TimeoutException(Exception):
        pass

def load_scraper_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return 0, 0, ""
            state = json.loads(content)
            return state.get("y", 0), state.get("s", 0), state.get("current link", "")
    return 0, 0, ""

def save_scraper_state(y, s, current_link):
    with open(STATE_FILE, "w") as f:
        json.dump({"y": y, "s": s, "current link": current_link}, f, indent=2)

def run_driver(link, rows):
    print(f"\nSEARCHING LINK: {link}")
    try:
        if use_signal:
            signal.alarm(20)
        driver.get(link)
        if not wait_for_page_load(driver):
            if use_signal:
                signal.alarm(0)
            return None
        result = scrape_emails(link, rows)
        if use_signal:
            signal.alarm(0)
        return result
    except TimeoutException:
        print("Tab timed out, skipping.")
        if use_signal:
            signal.alarm(0)
        return "Fail"
    except Exception as e:
        print(f"Error during tab open/scrape: {e}")
        return "Fail"

def assure_proper_close():
    print(f"Skipping {modified_link} due to load failure.")
    try:
        driver.close()
    except Exception as e:
        print(f"Window already closed or error closing: {e}")
    try:
        driver.switch_to.window(main_window)
    except Exception as e:
        print(f"Error switching to main window: {e}")

# Load Emails from json file
def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        with open(SEEN_EMAILS_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                return set()
            return set(json.loads(content))
    return set()
# Save Emails to json file
def save_seen_emails(seen_emails):
    with open(SEEN_EMAILS_FILE, 'w') as f:
        json.dump(list(seen_emails), f, indent=2)

# Load Links from json file
def load_seen_links():
    if os.path.exists(SEEN_LINKS_FILE):
        with open(SEEN_LINKS_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                return set()
            return set(json.loads(content))
    return set()
# Save Links to json file
def save_seen_links(seen_filtered_links):
    with open(SEEN_LINKS_FILE, 'w') as f:
        json.dump(list(seen_filtered_links), f, indent=2)

def wait_for_page_load(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        # Additional check: If page source is suspiciously short, treat as failed
        if len(driver.page_source) < 500:
            raise Exception("Page source too short, likely failed to load.")
    except Exception as e:
        print("Page did not load in time or failed:", e)
        return False
    return True

# Write Emails to Dataframe
def write_emails_to_file(emails, phones, link, rows):
    global email_added, email_skipped, seen_emails
    phone = next(iter(phones), None)

    for email in emails:
        if email not in seen_emails:
            seen_emails.add(email)

            row = {'email': email,
                   'phone': phone,
                   'link': link}
            rows.append(row)

            email_added += 1
        else:
            email_skipped += 1

    return rows

# Scan HTML for emails and return them as a set
def find_emails(html):
    emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', html)
    filtered_emails = [email for email in emails if len(email) <= 45]
    filtered_emails_0 = {email for email in filtered_emails if email.lower().endswith(('.com', '.net', '.org'))}
    filtered_emails_1 = {email for email in filtered_emails_0 if not any(unwanted in email.lower() for unwanted in unwanted_email)}
    return set(filtered_emails_1)

# Scan HTML for phone numbers and return them as a set
def find_phone_numbers(html):
    formatted_numbers = set()

    # US/Canada: optional +1, then (XXX) XXX-XXXX or similar separators
    us_pattern = r'(\+?1[\s\-\.]?)?(\(?\d{3}\)?[\s\-\.])\d{3}[\s\-\.]\d{4}'
    for m in re.finditer(us_pattern, html):
        number = m.group(0)
        digits = re.sub(r'\D', '', number)
        if len(digits) in (10, 11):
            formatted_numbers.add(number.strip())

    # Latin America: +CC followed by 6-12 digits with optional separators
    latam_pattern = r'\+(?:' + LATAM_COUNTRY_CODES + r')[\s\-\.\(\)]{0,2}\d[\d\s\-\.]{5,13}'
    for m in re.finditer(latam_pattern, html):
        formatted_numbers.add(m.group(0).strip())

    return formatted_numbers

# Drives scraping activities
def scrape_emails(link, rows):
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    emails = find_emails(str(soup))
    phones = find_phone_numbers(str(soup))
    print(phones)
    if emails:
        print("New emails found:", emails)
        if phones:
            print("New phone numbers found:", phones)
        rows = write_emails_to_file(emails, phones, link, rows)
    else:
        print("No emails found on this page.")
        return None
    return rows

def get_filtered_links():
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    found_links = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        # DuckDuckGo wraps results in redirect URLs — decode the real URL
        if 'uddg=' in href:
            params = parse_qs(urlparse(href).query)
            if 'uddg' in params:
                href = unquote(params['uddg'][0])
        if href.startswith('http') and not any(domain in href for domain in unwanted_links):
            found_links.add(href)
    return found_links

########################### START HERE #######################################
seen_emails = load_seen_emails()
seen_phones = set()
seen_filtered_links = set()
result = None

try:
    seen_filtered_links = load_seen_links()
    y, s, current_link = load_scraper_state()
    #search_link = f"https://duckduckgo.com/?q={search_terms[str(y)]}+{cities[str(s)]}&t=h_&ia=web"
    #search_link = f"https://duckduckgo.com/?q={cities[str(s)]}+texas+{search_terms[str(y)]}&t=h_&ia=web"
    search_link = f"https://duckduckgo.com/?q={search_terms[str(y)]}+{states[str(s)]}&t=h_&ia=web"
    driver.get(search_link)
    search_tab = driver.current_window_handle

    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[-1])
    scrape_tab = driver.current_window_handle
    main_window = search_tab

    MAX_PAGES_PER_LOCATION = 10
    page_count = 0

    while True:
        csv_path = f'results_{search_terms[str(y)]}.csv'
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            df = pd.read_csv(f'results_{search_terms[str(y)]}.csv')
            print(f"Loaded {len(df)} rows from results_{search_terms[str(y)]}.csv")
        else:
            df = pd.DataFrame(columns=["email", "phone", "link"])
            print(f"'results_{search_terms[str(y)]}.csv' not found, starting with empty DataFrame.")
        rows = []

        # if use_signal:
        #     signal.alarm(0) 
        if os.path.exists('pause.flag'):
            print("Paused... Type 'rm pause.flag' in another terminal to resume.")
            while os.path.exists('pause.flag'):
                time.sleep(5)
            print("Resuming...")
        
        if not os.path.exists('running.flag'):
            open('running.flag', 'a').close()


        print("\n----------------------------------------------------------------------------------------")
        print(f"Emails added: {email_added} | Emails skipped: {email_skipped} | S: {s} Y: {y} Link: {search_link}")
        print("----------------------------------------------------------------------------------------")
        filtered_links = get_filtered_links()
        
        print("\nLINKS TO SEARCH:")
        for link in filtered_links:
            if link not in seen_filtered_links:
                print(link)

        for link in filtered_links:
            if link not in seen_filtered_links and link not in current_link:

                save_scraper_state(y, s, link)

                driver.switch_to.window(scrape_tab)

                modified_link = f"{link}/contact"
                result = run_driver(modified_link, rows)
                if result == "Fail":
                    seen_filtered_links.add(link)
                    reopen_scrape_tab()
                    continue
                if result is not None:
                    seen_filtered_links.add(link)
                    result = None
                    continue

                modified_link = f"{link}/contact-us"
                result = run_driver(modified_link, rows)
                if result == "Fail":
                    seen_filtered_links.add(link)
                    reopen_scrape_tab()
                    continue
                if result is not None:
                    seen_filtered_links.add(link)
                    result = None
                    continue

                # modified_link = f"{link}/about-us"
                # result = run_driver(modified_link, rows)
                # if result == "Fail":
                #     seen_filtered_links.add(link)
                #     reopen_scrape_tab()
                #     continue
                # if result is not None:
                #     rows.extend(result)
                #     seen_filtered_links.add(link)
                #     result = None
                #     continue

                # modified_link = f"{link}/about"
                # result = run_driver(modified_link, rows)
                # if result == "Fail":
                #     seen_filtered_links.add(link)
                #     reopen_scrape_tab()
                #     continue
                # if result is not None:
                #     rows.extend(result)
                #     seen_filtered_links.add(link)
                #     result = None
                #     continue

                modified_link = link
                result = run_driver(modified_link, rows)
                if result == "Fail":
                    seen_filtered_links.add(link)
                    reopen_scrape_tab()
                    continue
                if result is not None:
                    seen_filtered_links.add(link)
                    result = None
                    continue

                seen_filtered_links.add(link)
        
        save_seen_links(seen_filtered_links)
        if rows:
            new_df = pd.DataFrame(rows)
            df = pd.concat([df, new_df], ignore_index=True)
            df = df.drop_duplicates(subset=['email'])
            df.to_csv(f'results_{search_terms[str(y)]}.csv', index=False)
            save_seen_emails(seen_emails)
            print(f"Saved {len(new_df)} new rows to results_{search_terms[str(y)]}.csv.")
        else:
            print("No new rows to save this loop.")
        rows = []

        page_count += 1
        advance = False

        if page_count >= MAX_PAGES_PER_LOCATION:
            print(f"Reached page limit ({MAX_PAGES_PER_LOCATION}) for this location, advancing...")
            advance = True
        else:
            try:
                driver.switch_to.window(search_tab)
                more_results_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "more-results"))
                )
                more_results_button.click()
            except:
                print("No more results found, advancing to next location...")
                advance = True

        if advance:
            page_count = 0
            s += 1
            if s >= len(states):
                y += 1
                s = 0

            search_link = f"https://duckduckgo.com/?q={search_terms[str(y)]}+{states[str(s)]}&t=h_&ia=web"
            save_scraper_state(y, s, "")

            try:
                driver.switch_to.window(search_tab)
                driver.get(search_link)
            except:
                print("Driver failed. Restarting...")
                driver.quit()
                create_driver()

finally:
    driver.quit()