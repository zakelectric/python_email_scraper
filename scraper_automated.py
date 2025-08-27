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

acceptable_area_codes = {'619', '858', '714', '818', '800', '949', '760'}
unwanted = ['zillow', 'duck', 'w3', 'houzz', 'github', 'google', 'apple', 'nytimes', 'api.you', 'yelp', 'yahoo', 'reddit', 'uniontribune']

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

SEEN_EMAILS_FILE = 'seen_emails.json'

def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        with open(SEEN_EMAILS_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_seen_emails():
    with open(SEEN_EMAILS_FILE, 'w') as f:
        json.dump(list(seen_emails), f, indent=2)

seen_emails = load_seen_emails()

def write_emails_to_file(emails, filename):
    with open(filename, 'a') as file:
        for i, email in enumerate(emails):
            if email not in seen_emails:
                seen_emails.add(email)
                if i == len(emails) - 1:
                    file.write(email + ' ')
                else:
                    file.write(email + '\n')
    save_seen_emails() 

def write_phones_to_file(phones, filename):
    with open(filename, 'a') as file:
        for phone in phones:
            if phone not in seen_phones:
                seen_phones.add(phone)
                file.write(phone + ' ')

def add_comma():
    with open('results.txt', 'a') as file:
        file.write(', ')

def find_emails(html):
    emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', html)
    filtered_emails = [email for email in emails if len(email) <= 45]
    filtered_emails_0 = {email for email in emails if email.lower().endswith(('.com', '.net', '.org'))}
    return set(filtered_emails_0)

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

def get_filtered_links():
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    html = str(soup)
    pattern = r'http[s]?://[^\s"\'>]+?\.(com|net|org)\b'
    links = [m.group(0) for m in re.finditer(pattern, html)]
    # Filter out links containing unwanted domains
    filtered_links = {link for link in links if not any(domain in link for domain in unwanted)}
    return filtered_links

# START HERE
seconds = 0
seen_emails = set()  # Set to keep track of seen emails
seen_phones = set()
seen_filtered_links = set()
try:
    initial_url = "https://duckduckgo.com"  # Start URL
    driver.get(initial_url)
    print("\nIndicate once you have made your search query by pressing ENTER")
    input()

    while True:
        filtered_links = get_filtered_links()
        main_window = driver.current_window_handle
        
        print("\nLINKS TO SEARCH:")
        for link in filtered_links:
            if link not in seen_filtered_links:
                print(link)

        for link in filtered_links:
            if link not in seen_filtered_links:
                print(f"\nSEARCHING LINK: {link}")
                driver.execute_script("window.open(arguments[0]);", link)
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(5)
                result = scrape_emails(link, 'results.txt')
                driver.close()
                driver.switch_to.window(main_window)
                time.sleep(1)

                if result == False:
                    contact_link = f"{link}/contact"
                    driver.execute_script("window.open(arguments[0]);", contact_link)
                    driver.switch_to.window(driver.window_handles[-1])
                    time.sleep(5)
                    result = scrape_emails(link, 'results.txt')
                    driver.close()
                    driver.switch_to.window(main_window)
                    time.sleep(1)

                if result == False:
                    contact_link_0 = f"{link}/contact-us"
                    driver.execute_script("window.open(arguments[0]);", contact_link_0)
                    driver.switch_to.window(driver.window_handles[-1])
                    time.sleep(5)
                    result = scrape_emails(link, 'results.txt')
                    driver.close()
                    driver.switch_to.window(main_window)
                    time.sleep(1)

                
                seen_filtered_links.add(link)

                # Avoid adding websites without email or phone
                if result == False:
                    continue

                with open('results.txt', 'a') as file:
                    file.write(link + '\n')
        try:
            more_results_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "more-results"))
            )
            more_results_button.click()
        except:
            print("COULD NOT FIND MORE RESULTS BUTTON! - Press ENTER to continue...")
            input()

finally:
    driver.quit()
