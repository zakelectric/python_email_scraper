import re
import time
import os
import platform
import json
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

acceptable_area_codes = {'619', '858', '714', '818', '800', '949', '760', '951', '442', '213', '310', '323', '424', '562', '626', '661', '747', 
                         '657', '209', '530', '559', '707', '916', '925', '831', '650', '415', '510'}
unwanted_links = ['zillow', 'duck', 'w3', 'houzz', 'github', 'google', 'apple', 'nytimes', 'api.you', 'yelp', 'yahoo', 'reddit', 'uniontribune']
unwanted_email = ['sentry', 'wix', 'godaddy']
email_added = 0
email_skipped = 0
skipped_links = 0
y = 1 # Make sure to update this to 0 if starting from scratch. Used as Index of search_terms
s = 0 # Represents state

search_terms = {
    0: 'automotive+parts+"manufacturer"',
}

state = {
    1: 'alabama',
    2: 'alaska',
    3: 'arizona',
    4: 'arkansas',
    5: 'california',
    6: 'colorado',
    7: 'connecticut',
    8: 'delaware',
    9: 'florida',
    10: 'georgia',
    11: 'hawaii',
    12: 'idaho',
    13: 'illinois',
    14: 'indiana',
    15: 'iowa',
    16: 'kansas',
    17: 'kentucky',
    18: 'louisiana',
    19: 'maine',
    20: 'maryland',
    21: 'massachusetts',
    22: 'michigan',
    23: 'minnesota',
    24: 'mississippi',
    25: 'missouri',
    26: 'montana',
    27: 'nebraska',
    28: 'nevada',
    29: 'new hampshire',
    30: 'new jersey',
    31: 'new mexico',
    32: 'new york',
    33: 'north carolina',
    34: 'north dakota',
    35: 'ohio',
    36: 'oklahoma',
    37: 'oregon',
    38: 'pennsylvania',
    39: 'rhode island',
    40: 'south carolina',
    41: 'south dakota',
    42: 'tennessee',
    43: 'texas',
    44: 'utah',
    45: 'vermont',
    46: 'virginia',
    47: 'washington',
    48: 'west virginia',
    49: 'wisconsin',
    50: 'wyoming'
}



driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
driver.set_page_load_timeout(15)

SEEN_EMAILS_FILE = 'seen_emails.json'
SEEN_LINKS_FILE = 'seen_links.json'

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

def run_driver(link, rows):

    print(f"\nSEARCHING LINK: {link}")
    try:
        if use_signal:      
            signal.alarm(20)
        driver.execute_script("window.open(arguments[0]);", link)
        driver.switch_to.window(driver.window_handles[-1])
        if not wait_for_page_load(driver):
            assure_proper_close()
            if use_signal:
                signal.alarm(0)
            return None
        result = scrape_emails(link, rows)
        driver.close()
        driver.switch_to.window(main_window)
        return result
    except TimeoutException:
        print("Tab timed out, skipping.")
        assure_proper_close()
        if use_signal:
            signal.alarm(0)
        return None
    except Exception as e:
        print(f"Error during tab open/scrape: {e}")
        assure_proper_close()
        if use_signal:
            signal.alarm(0)
        return None

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
            return set(json.load(f))
    return set()
# Save Emails to json file
def save_seen_emails(seen_emails):
    with open(SEEN_EMAILS_FILE, 'w') as f:
        json.dump(list(seen_emails), f, indent=2)

# Load Links from json file
def load_seen_links():
    if os.path.exists(SEEN_LINKS_FILE):
        with open(SEEN_LINKS_FILE, 'r') as f:
            return set(json.load(f))
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
def write_emails_to_file(emails, link, rows):
    global email_added, email_skipped
    seen_emails = load_seen_emails()
    
    for email in emails:
        if email not in seen_emails:
            seen_emails.add(email)

            row = {'email': email,
                    'link': link}
            rows.append(row)

            email_added += 1
        else:
            email_skipped += 1

    save_seen_emails(seen_emails) 

    return rows

# Write phone numbers to text file
def write_phones_to_file(phones, link, rows):
    for phone in phones:
        if phone not in seen_phones:
            seen_phones.add(phone)
            row = {
                'phone': phone,
                'link': link
            }
            rows.append(row)
    
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
    pattern = r'(\+?1[\s\-\.]?)?(\(?\d{3}\)?[\s\-\.]?)?\d{3}-\d{4}'
    matches = re.finditer(pattern, html)
    formatted_numbers = set()
    for m in matches:
        number = m.group(0)
        digits = re.sub(r'\D', '', number)
        if len(digits) == 10:
            if digits[:3] in acceptable_area_codes:
                formatted_numbers.add(number.strip())
    return formatted_numbers

# Drives scraping activities
def scrape_emails(link, rows):
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    emails = find_emails(str(soup))
    phones = find_phone_numbers(str(soup))
    print(phones)
    if emails:
        print("New emails found:", emails)
        rows = write_emails_to_file(emails, link, rows)
    else:
        print("No emails found on this page.")
        return None
    if phones:
        print("New phone numbers found:", phones)
        rows = write_phones_to_file(phones, link, rows)
    else:
        print("No phone numbers found:")
    return rows

def get_filtered_links():
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    html = str(soup)
    pattern = r'http[s]?://[^\s"\'>]+?\.(com|net|org)\b'
    links = [m.group(0) for m in re.finditer(pattern, html)]
    # Filter out links containing unwanted domains
    filtered_links = {link for link in links if not any(domain in link for domain in unwanted_links)}
    return filtered_links

########################### START HERE #######################################
seen_emails = set()
seen_phones = set()
seen_filtered_links = set()
result = None

try:
    search_link = f"https://duckduckgo.com/?q={search_terms[y]}+{state[s]}&t=h_&ia=web"
    s += 1
    driver.get(search_link)
    seen_filtered_links = load_seen_links()

    while True:
        if os.path.exists('results.csv'):
            df = pd.read_csv('results.csv')
            print(f"Loaded {len(df)} rows from results.csv")
        else:
            df = pd.DataFrame(columns=["email", "phone", "link"])
            print("results.csv not found, starting with empty DataFrame.")
        rows = []

        if use_signal:
            signal.alarm(0) 
        if os.path.exists('pause.flag'):
            print("Paused... Type 'rm pause.flag' in another terminal to resume.")
            while os.path.exists('pause.flag'):
                time.sleep(5)
            print("Resuming...")

        print("\n----------------------------------------------------------------------------------------")
        print(f"Emails added: {email_added} | Emails skipped: {email_skipped} | S: {s} Y: {y}")
        print("----------------------------------------------------------------------------------------")
        filtered_links = get_filtered_links()
        main_window = driver.current_window_handle
        
        print("\nLINKS TO SEARCH:")
        for link in filtered_links:
            if link not in seen_filtered_links:
                print(link)

        for link in filtered_links:
            if link not in seen_filtered_links:

                modified_link = f"{link}/contact"
                result = run_driver(modified_link, rows)
                if result is not None:
                    rows = result
                    seen_filtered_links.add(link)
                    result = None
                    continue

            
                modified_link = f"{link}/contact-us"
                result = run_driver(modified_link, rows)
                if result is not None:
                    rows = result
                    seen_filtered_links.add(link)
                    result = None
                    continue


                modified_link = link
                result = run_driver(modified_link, rows)
                if result is not None:
                    rows = result
                    seen_filtered_links.add(link)
                    result = None
                    continue

                seen_filtered_links.add(link)
        
        save_seen_links(seen_filtered_links)
        if rows:
            new_df = pd.DataFrame(rows)
            df = pd.concat([df, new_df], ignore_index=True)
            df.to_csv('results.csv', index=False)
            print(f"Saved {len(new_df)} new rows to results.csv.")
        else:
            print("No new rows to save this loop.")
        rows = []

        try:
            more_results_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "more-results"))
            )
            more_results_button.click()
        except:
            print("FIRST ITERATION OR COULD NOT FIND MORE RESULTS BUTTON!")
            if use_signal:
                signal.alarm(0)
            search_link = f"https://duckduckgo.com/?q={search_terms[y]}+{state[s]}&t=h_&ia=web"
            if s == len(state):
                y += 1
                s = 0
            try:
                driver.get(search_link)
            except:
                print("Driver failed. Restarting...")
                driver.quit()
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
                driver.set_page_load_timeout(15)
                driver.get(search_link)
            y += 1

finally:
    driver.quit()