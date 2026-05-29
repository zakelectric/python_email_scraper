import re
import time
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


driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def write_emails_to_file(emails, filename):
    with open(filename, 'a') as file:
        for email in emails:
            if email not in seen_emails:
                seen_emails.add(email)
                file.write(email + '\n')

def find_emails(html):
    emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', html)
    return set(emails)

def scrape_emails():
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    emails = find_emails(str(soup))
    if emails:
        print("\nNew emails found:", emails)
        write_emails_to_file(emails, 'emails.txt')

    else:
        print("\nNo emails found on this page.")


# START HERE
seconds = 0

seen_emails = set()  # Set to keep track of seen emails
try:
    initial_url = "https://duckduckgo.com"  # Start URL
    driver.get(initial_url)
    last_url = initial_url

    while True:
        current_url = driver.current_url
        if current_url != last_url or seconds == 10:
            #print(f"Navigation detected. New URL: {current_url}")
            scrape_emails()
            last_url = current_url
            print("\nEmail list:", seen_emails)
            counter = 0
            for item in seen_emails:
                counter += 1
            seconds = 0
            print(f"Total unique emails found: {counter}")
        time.sleep(1)
        seconds += 1
finally:
    driver.quit()
