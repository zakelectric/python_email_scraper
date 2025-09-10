import re
import time
import os
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

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
y = 28 # Make sure to update this to 0 if starting from scratch. Used as Index of search_terms

search_terms = {
        0: 'data+programming+consulting+firm+san+francisco+-indeed+-linkedin+-glassdoor',
        1: 'data+programming+consulting+firm+oakland+-indeed+-linkedin+-glassdoor',
        2: 'data+programming+consulting+firm+san+jose+-indeed+-linkedin+-glassdoor',
        3: 'data+programming+consulting+firm+sacramento+-indeed+-linkedin+-glassdoor',
        4: 'data+programming+consulting+firm+berkeley+-indeed+-linkedin+-glassdoor',
        5: 'data+programming+consulting+firm+fremont+-indeed+-linkedin+-glassdoor',
        6: 'data+programming+consulting+firm+stockton+-indeed+-linkedin+-glassdoor',
        7: 'data+programming+consulting+firm+modesto+-indeed+-linkedin+-glassdoor',
        8: 'data+programming+consulting+firm+santa+rosa+-indeed+-linkedin+-glassdoor',
        9: 'data+programming+consulting+firm+hayward+-indeed+-linkedin+-glassdoor',
        10: 'data+programming+consulting+firm+sunnyvale+-indeed+-linkedin+-glassdoor',
        11: 'data+programming+consulting+firm+concord+-indeed+-linkedin+-glassdoor',
        12: 'data+programming+consulting+firm+vallejo+-indeed+-linkedin+-glassdoor',
        13: 'data+programming+consulting+firm+fairfield+-indeed+-linkedin+-glassdoor',
        14: 'data+programming+consulting+firm+richmond+-indeed+-linkedin+-glassdoor',
        15: 'data+programming+consulting+firm+antioch+-indeed+-linkedin+-glassdoor',
        16: 'data+programming+consulting+firm+san+mateo+-indeed+-linkedin+-glassdoor',
        17: 'data+programming+consulting+firm+daly+city+-indeed+-linkedin+-glassdoor',
        18: 'data+programming+consulting+firm+san+leandro+-indeed+-linkedin+-glassdoor',
        19: 'data+programming+consulting+firm+livermore+-indeed+-linkedin+-glassdoor',
        20: 'data+programming+consulting+firm+tracy+-indeed+-linkedin+-glassdoor',
        21: 'data+programming+consulting+firm+davis+-indeed+-linkedin+-glassdoor',
        22: 'data+programming+consulting+firm+napa+-indeed+-linkedin+-glassdoor',
        23: 'data+programming+consulting+firm+petaluma+-indeed+-linkedin+-glassdoor',
        24: 'data+programming+consulting+firm+redding+-indeed+-linkedin+-glassdoor',
        25: 'data+programming+consulting+firm+chico+-indeed+-linkedin+-glassdoor',
        26: 'data+programming+consulting+firm+yuba+city+-indeed+-linkedin+-glassdoor',
        27: 'data+programming+consulting+firm+elk+grove+-indeed+-linkedin+-glassdoor',
        28: 'data+programming+consulting+firm+roseville+-indeed+-linkedin+-glassdoor',
        29: 'data+programming+consulting+firm+rocklin+-indeed+-linkedin+-glassdoor',
        30: 'data+programming+consulting+firm+woodland+-indeed+-linkedin+-glassdoor',
        31: 'data+programming+consulting+firm+manteca+-indeed+-linkedin+-glassdoor',
        32: 'data+programming+consulting+firm+lodi+-indeed+-linkedin+-glassdoor',
        33: 'data+programming+consulting+firm+suisun+city+-indeed+-linkedin+-glassdoor',
        34: 'data+programming+consulting+firm+vacaville+-indeed+-linkedin+-glassdoor',
    }

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
driver.set_page_load_timeout(15)

SEEN_EMAILS_FILE = 'seen_emails.json'
SEEN_LINKS_FILE = 'seen_links.json'

def assure_proper_close():
    print(f"Skipping {modified_link} due to load failure.")
    try:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
        else:
            print("No extra windows to close. Resetting driver.")
            driver.quit()
    except Exception as e:
        print(f"Error during window close/switch: {e}")

def run_driver(link):
    print(f"\nSEARCHING LINK: {link}")
    try:
        driver.execute_script("window.open(arguments[0]);", link)
        driver.switch_to.window(driver.window_handles[-1])
        # Timeout for page load
        start_time = time.time()
        while not wait_for_page_load(driver):
            if time.time() - start_time > 30:  # 30 seconds max per link
                print("Timeout exceeded for this link.")
                break
            time.sleep(2)
        result = scrape_emails(link, 'results.txt')
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
        return result
    except Exception as e:
        print(f"Error during tab open/scrape: {e}")
        assure_proper_close()
        return None

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

# Write Emails to txt file
def write_emails_to_file(emails, filename):
    global email_added, email_skipped
    seen_emails = load_seen_emails()
    
    with open(filename, 'a') as file:
        for i, email in enumerate(emails):
            if email not in seen_emails:
                seen_emails.add(email)
                if i == len(emails) - 1:
                    file.write(email + ' ')
                else:
                    file.write(email + '\n')
                email_added += 1
            else:
                email_skipped += 1

    save_seen_emails(seen_emails) 

# Write phone numbers to text file
def write_phones_to_file(phones, filename):
    with open(filename, 'a') as file:
        for phone in phones:
            if phone not in seen_phones:
                seen_phones.add(phone)
                file.write(phone + ' ')

# Add a comma
def add_comma():
    with open('results.txt', 'a') as file:
        file.write(', ')

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
def scrape_emails(link, filename):
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    emails = find_emails(str(soup))
    phones = find_phone_numbers(str(soup))
    print(phones)
    if emails:
        print("New emails found:", emails)
        write_emails_to_file(emails, 'results.txt')
        add_comma()
    else:
        print("No emails found on this page.")
        return False
    if phones:
        print("New phone numbers found:", phones)
        write_phones_to_file(phones, 'results.txt')
        add_comma()
    else:
        print("No phone numbers found:")
        write_phones_to_file('###-###-####', 'results.txt')
        add_comma()
    return True

def get_filtered_links():
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    html = str(soup)
    pattern = r'http[s]?://[^\s"\'>]+?\.(com|net|org)\b'
    links = [m.group(0) for m in re.finditer(pattern, html)]
    # Filter out links containing unwanted domains
    filtered_links = {link for link in links if not any(domain in link for domain in unwanted_links)}
    return filtered_links

# START HERE
seen_emails = set()
seen_phones = set()
seen_filtered_links = set()

try:
    search_link = f"https://duckduckgo.com/?q={search_terms[y]}&t=h_&ia=web"
    y += 1
    driver.get(search_link)
    seen_filtered_links = load_seen_links()

    while True:
        if os.path.exists('pause.flag'):
            print("Paused... Type 'rm pause.flag' in another terminal to resume.")
            while os.path.exists('pause.flag'):
                time.sleep(5)
            print("Resuming...")

        print("\n----------------------------------------------------------------------------------------")
        print(f"Emails added: {email_added} | Emails skipped: {email_skipped} | Search term: {search_terms[y]}")
        print("----------------------------------------------------------------------------------------")
        filtered_links = get_filtered_links()
        main_window = driver.current_window_handle
        
        print("\nLINKS TO SEARCH:")
        for link in filtered_links:
            if link not in seen_filtered_links:
                print(link)

        for link in filtered_links:
            if link not in seen_filtered_links:

                modified_link = f"{link}/careers"
                result = run_driver(modified_link)
                if result == True:
                    seen_filtered_links.add(link)
                    continue

                modified_link = f"{link}/career"
                result = run_driver(modified_link)
                if result == True:
                    seen_filtered_links.add(link)
                    continue


                modified_link = f"{link}/contact"
                result = run_driver(modified_link)
                if result == True:
                    seen_filtered_links.add(link)
                    continue
            
                modified_link = f"{link}/contact-us"
                result = run_driver(modified_link)
                if result == True:
                    seen_filtered_links.add(link)
                    continue

                modified_link = link
                result = run_driver(modified_link)
                if result == True:
                    seen_filtered_links.add(link)
                    continue

                seen_filtered_links.add(link)

                # Avoid adding websites without email or phone
                if result is None:
                    continue

                with open('results.txt', 'a') as file:
                    file.write(link + '\n')
        
        save_seen_links(seen_filtered_links)

        try:
            more_results_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "more-results"))
            )
            more_results_button.click()
        except:
            print("FIRST ITERATION OR COULD NOT FIND MORE RESULTS BUTTON!")
            search_link = f"https://duckduckgo.com/?q={search_terms[y]}&t=h_&ia=web"
            driver.get(search_link)
            y += 1

finally:
    driver.quit()
