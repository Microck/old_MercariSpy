import json
import time
import random
import os
import re
from datetime import datetime, timedelta
import requests
from selenium import webdriver
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains
import telegram
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

# --- Configuration Loading ---
try:
    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("ERROR: config.json not found. Please create it.")
    exit()
except json.JSONDecodeError:
    print("ERROR: config.json is not valid JSON.")
    exit()
# --- End Configuration Loading ---

# --- Load Search Queries ---
SEARCH_QUERIES_FILE = "search_queries.txt"
try:
    with open(SEARCH_QUERIES_FILE, "r", encoding="utf-8") as f:
        SEARCH_QUERIES = [line.strip() for line in f if line.strip()]
    if not SEARCH_QUERIES:
        print(f"ERROR: {SEARCH_QUERIES_FILE} is empty.")
        exit()
except FileNotFoundError:
    print(f"ERROR: {SEARCH_QUERIES_FILE} not found.")
    exit()
# --- End Load Search Queries ---

# --- Directory Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Subdirectories
SEARCH_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "search_results")
ITEM_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "items")
ERROR_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "errors")
BLOCK_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "block_pages")
PAGE_LOG_DIR = os.path.join(LOG_DIR, "pages")

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SEARCH_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(ITEM_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(ERROR_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(BLOCK_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(PAGE_LOG_DIR, exist_ok=True)
# --- End Directory Setup ---

# --- File Paths ---
KNOWN_PRODUCTS_FILE = os.path.join(DATA_DIR, "mercari_known_products.json") # Renamed
# --- End File Paths ---

# --- Global variables ---
JPY_TO_EUR_RATE = None
RATE_LAST_UPDATED = 0
# ---

# --- Initialize Telegram Bot ---
try:
    telegram_bot = telegram.Bot(token=CONFIG["TELEGRAM_TOKEN"])
except Exception as e:
    print(f"Error initializing Telegram bot: {e}")
    class DummyBot: # Fallback if init fails
        def send_message(self, chat_id, text, **kwargs): print(f"[Telegram Dummy] {text}")
        def send_photo(self, chat_id, photo, **kwargs): print(f"[Telegram Dummy Photo] {kwargs.get('caption', '')}")
    telegram_bot = DummyBot()
# --- End Bot Initialization ---

# --- Helper Function for Conditional Logging ---
def log_message(message, level="info", photo_path=None, caption=""):
    """Sends message/photo to Telegram only if debug messages are enabled."""
    print(f"[{level.upper()}] {message or caption}") # Always print to console
    if CONFIG.get("SEND_DEBUG_MESSAGES", True):
        try:
            if photo_path and os.path.exists(photo_path):
                 with open(photo_path, "rb") as photo_file:
                      telegram_bot.send_photo(
                          chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                          photo=photo_file,
                          caption=(caption or message)[:1024] # Telegram caption limit
                      )
            elif message:
                # Split long messages if necessary
                max_len = 4096
                for i in range(0, len(message), max_len):
                    telegram_bot.send_message(
                        chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                        text=message[i:i+max_len]
                    )
        except Exception as e:
            print(f"Error sending debug message/photo to Telegram: {e}")

# --- Currency Conversion ---
def get_jpy_to_eur_rate():
    """Fetches/caches the JPY to EUR conversion rate."""
    global JPY_TO_EUR_RATE, RATE_LAST_UPDATED
    now = time.time()
    if JPY_TO_EUR_RATE is not None and (now - RATE_LAST_UPDATED < CONFIG.get("CURRENCY_RATE_UPDATE_INTERVAL_SECONDS", 3600)):
        return JPY_TO_EUR_RATE

    log_message("Fetching latest JPY->EUR conversion rate...", level="debug")
    try:
        response = requests.get("https://api.frankfurter.app/latest?from=JPY&to=EUR", timeout=10)
        response.raise_for_status()
        data = response.json()
        rate = data.get("rates", {}).get("EUR")
        if rate:
            JPY_TO_EUR_RATE = float(rate)
            RATE_LAST_UPDATED = now
            log_message(f"Updated JPY->EUR rate: {JPY_TO_EUR_RATE}")
            return JPY_TO_EUR_RATE
        else:
            log_message("Could not find EUR rate in API response.", level="warning")
            return JPY_TO_EUR_RATE # Return old rate if available
    except Exception as e:
        log_message(f"Failed to fetch or parse currency rate: {e}", level="error")
        return JPY_TO_EUR_RATE # Return old rate if fetch fails

def jpy_to_euro(jpy_str):
    """Converts a JPY price string (e.g., '¥15,000') to a formatted EUR string."""
    rate = get_jpy_to_eur_rate()
    if rate is None:
        return "€N/A (Rate Error)"
    try:
        # More robust cleaning
        jpy_str_cleaned = re.sub(r'[^\d.]', '', jpy_str)
        if not jpy_str_cleaned:
            return "€N/A (Parse Error)"
        jpy = float(jpy_str_cleaned)
        euro = jpy * rate
        return f"€{euro:.2f}"
    except Exception as e:
        print(f"Error converting JPY string '{jpy_str}' to EUR: {e}")
        return "€N/A (Conv. Error)"

# --- Browser Setup (Adapted from XYSpy) ---
def setup_browser():
    """Sets up the undetected_chromedriver instance."""
    log_message("Setting up browser...", level="debug")
    try:
        options = uc.ChromeOptions()
        options.add_argument(f"user-agent={CONFIG['USER_AGENT']}")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        # options.add_argument("--disable-web-security") # Use with caution
        # options.add_argument("--allow-running-insecure-content") # Use with caution
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7") # Prioritize Japanese
        options.add_argument("--disable-features=UserAgentClientHint")

        if CONFIG.get("HEADLESS", True):
            log_message("Running in HEADLESS mode.", level="debug")
            options.add_argument("--headless=new")
            # options.add_argument("--disable-features=IsolateOrigins,site-per-process") # May cause issues
        else:
            log_message("Running in HEADED mode.", level="debug")

        # Let UC handle the version detection unless specified
        driver = uc.Chrome(options=options, version_main=135) # Specify version 135
        driver.set_page_load_timeout(60) # 60 second page load timeout
        log_message("Browser setup complete.", level="debug")
        return driver
    except Exception as e:
        log_message(f"Error creating undetected-chromedriver: {e}", level="error")
        # Fallback attempt (optional, requires webdriver-manager)
        try:
            log_message("Attempting fallback to standard chromedriver...", level="warning")
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            options = webdriver.ChromeOptions() # Standard options
            options.add_argument(f"user-agent={CONFIG['USER_AGENT']}")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            if CONFIG.get("HEADLESS", True): options.add_argument("--headless=new")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(60)
            log_message("Fallback browser setup complete.", level="debug")
            return driver
        except Exception as e2:
            log_message(f"Error creating standard chromedriver fallback: {e2}", level="error")
            raise Exception(f"Failed to initialize any browser: {e}, {e2}")

# --- Mercari Specific Actions ---

def apply_sort_by_newest_mercari(driver):
    """Attempts to sort Mercari results by Newest using the <select> dropdown."""
    wait_time = 15 # Wait time for the select element
    log_message("Attempting to apply 'Sort by Newest' using <select> dropdown...", level="debug")
    try:
        # --- NEW Selector Strategy for Mercari Sort Dropdown ---
        # Use the CSS selector provided by the user, targeting the <select> element
        # Note: IDs like 'search-result' might change, but the structure below it is more likely stable.
        # Using a more concise selector targeting the name attribute which seems relevant.
        # select_element_css = "#search-result > div > div > div > section.sc-fe141818-2.dZkxEP.mer-spacing-b-16 > div > div.sc-d4b82f4-9.sc-fe141818-1.iwsraC.fBfzLE > div.sc-d4b82f4-9.sc-e54b9a83-0.iwsraC.jNceSq > label > div.merSelect.sc-e54b9a83-2.dyMPmC > div > div.selectWrapper__da4764db > select" # User provided selector
        # --- Simpler Selector (Often more robust if name is stable) ---
        select_element_css = "select[name='sortOrder']" # Targets the select element by its name attribute

        log_message(f"Waiting for sort <select> element using CSS: {select_element_css}", level="debug")

        # Wait for the select element to be present
        select_element = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, select_element_css))
        )

        log_message("Sort <select> element found. Selecting 'Newest' option...", level="debug")

        # Use Selenium's Select class for easy option selection
        select_object = Select(select_element)

        # Select the option for "Newest" (新しい順)
        # From the screenshot, its value is "created_time:desc"
        value_for_newest = "created_time:desc"
        select_object.select_by_value(value_for_newest)

        log_message(f"Selected option with value '{value_for_newest}'. Waiting for results to reload...", level="debug")
        # Allow ample time for the page results to potentially update
        time.sleep(random.uniform(5, 7))
        log_message("Applied sort by 'Newest'.")
        return True

    except TimeoutException as e:
        error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_timeout_error_{int(time.time())}.png")
        err_msg = f"Timeout finding Mercari sort <select> element ({e}). Selector '{select_element_css}' might be wrong or page didn't load correctly."
        log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
        try: driver.save_screenshot(error_screenshot_path)
        except: pass
        return False # Sorting failed
    except NoSuchElementException as e:
         error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_no_option_error_{int(time.time())}.png")
         err_msg = f"Could not find the option with value '{value_for_newest}' in the sort dropdown ({e})."
         log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
         try: driver.save_screenshot(error_screenshot_path)
         except: pass
         return False # Sorting failed
    except Exception as e:
        error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_general_error_{int(time.time())}.png")
        err_msg = f"General error applying Mercari sort via <select>: {e}"
        log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
        try: driver.save_screenshot(error_screenshot_path)
        except: pass
        return False # Sorting failed

def extract_products_mercari(driver, query):
    """Extracts product details from the visible elements on Mercari search results."""
    products = {}
    log_message(f"Extracting products for query '{query}'...", level="debug")
    try:
        container_selector = "#item-grid"
        item_card_selector = f"{container_selector} > ul > li[data-testid='item-cell']" # Added data-testid for potentially more specific card selection

        log_message(f"Waiting for item container: '{container_selector}'", level="debug")
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
            )
            log_message("Item container found.", level="debug")
        except TimeoutException:
            no_results_xpath = "//*[contains(text(),'出品された商品がありません') or contains(text(),'該当する商品が見つかりません')]"
            try:
                driver.find_element(By.XPATH, no_results_xpath)
                log_message(f"Confirmed: No results found for query '{query}'.", level="info")
            except NoSuchElementException:
                log_message(f"Timeout waiting for item container '{container_selector}' AND no 'No Results' message found.", level="warning")
                page_source_path = os.path.join(PAGE_LOG_DIR, f"page_source_no_container_{query.replace(' ', '_')}_{int(time.time())}.html")
                screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"error_no_container_{query.replace(' ', '_')}_{int(time.time())}.png")
                try:
                    with open(page_source_path, "w", encoding="utf-8") as f: f.write(driver.page_source)
                    driver.save_screenshot(screenshot_path)
                    log_message(f"Saved page source and screenshot.", level="debug", photo_path=screenshot_path)
                except: pass
            return {}

        log_message(f"Finding item cards using selector: '{item_card_selector}'", level="debug")
        item_elements = driver.find_elements(By.CSS_SELECTOR, item_card_selector)
        log_message(f"Found {len(item_elements)} potential item elements.")

        if not item_elements:
             no_results_xpath = "//*[contains(text(),'出品された商品がありません') or contains(text(),'該当する商品が見つかりません')]"
             try:
                 driver.find_element(By.XPATH, no_results_xpath)
                 log_message(f"Container found, but no item elements and 'No Results' message present.", level="info")
             except NoSuchElementException:
                 log_message("Container found, but no item elements found using the card selector.", level="warning")
                 page_source_path = os.path.join(PAGE_LOG_DIR, f"page_source_no_items_{query.replace(' ', '_')}_{int(time.time())}.html")
                 screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"error_no_items_{query.replace(' ', '_')}_{int(time.time())}.png")
                 try:
                     with open(page_source_path, "w", encoding="utf-8") as f: f.write(driver.page_source)
                     driver.save_screenshot(screenshot_path)
                     log_message(f"Saved page source and screenshot.", level="debug", photo_path=screenshot_path)
                 except: pass
             return {}

        processed_count = 0
        for i, item_element in enumerate(item_elements):
            product_id = None
            link = None
            title = "Title not found"
            price = "Price not found"
            item_image = ""
            screenshot_path = None

            try:
                # Scroll element into view gently
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item_element)
                time.sleep(0.3)

                # --- Link and ID ---
                try:
                    # Use the data-testid for the link
                    link_element_selector = "a[data-testid='thumbnail-link']"
                    link_element = item_element.find_element(By.CSS_SELECTOR, link_element_selector)
                    link = link_element.get_attribute('href')
                    if link:
                        product_id_match = re.search(r'/(m\d+)/?$', link)
                        if product_id_match:
                            product_id = product_id_match.group(1)
                        else:
                            product_id = f"hash_{hash(link)}_{i}" # Fallback ID
                    else:
                        raise NoSuchElementException("Link href was empty")
                except NoSuchElementException:
                    log_message(f"Could not find link/ID using selector '{link_element_selector}' for item {i}. Skipping.", level="warning")
                    continue # Essential info missing

                # --- Title ---
                try:
                    # Use the data-testid for the title
                    title_selector = "span[data-testid='thumbnail-item-name']"
                    title_elem = item_element.find_element(By.CSS_SELECTOR, title_selector)
                    title = title_elem.text.strip()
                    if not title or len(title) < 2: # Check if title seems valid
                        log_message(f"Found title element but text is short/empty for {product_id}. Text: '{title}'", level="debug")
                        # Fallback: Try aria-label of the thumbnail div
                        try:
                            thumb_div = item_element.find_element(By.CSS_SELECTOR, "div.merItemThumbnail")
                            aria_label = thumb_div.get_attribute('aria-label')
                            if aria_label:
                                # Extract title part before price from aria-label
                                title_match = re.match(r'^(.*?)\s+\d{1,3}(?:,\d{3})*円', aria_label)
                                if title_match: title = title_match.group(1).strip()
                        except: pass # Ignore errors in fallback
                except NoSuchElementException:
                    log_message(f"Could not find title using selector '{title_selector}' for {product_id}.", level="warning")
                    # Add other fallbacks if needed

                # --- Price ---
                try:
                    # Primary strategy: Extract from aria-label of the thumbnail div
                    thumb_div_selector = "div.merItemThumbnail"
                    thumb_div = item_element.find_element(By.CSS_SELECTOR, thumb_div_selector)
                    aria_label = thumb_div.get_attribute('aria-label')
                    if aria_label:
                        # Regex to find Yen price (¥ optional, handles commas)
                        price_match = re.search(r'(\d{1,3}(?:,\d{3})*|\d+)\s*円', aria_label)
                        if price_match:
                            price = f"¥{price_match.group(1)}" # Re-add Yen symbol for consistency
                        else:
                            log_message(f"Could not find Yen price pattern in aria-label for {product_id}. Label: '{aria_label}'", level="debug")
                    else:
                        log_message(f"Aria-label empty for {product_id}.", level="debug")

                    # Fallback: Search for visible price elements if aria-label fails
                    if price == "Price not found":
                        log_message(f"Price not in aria-label for {product_id}. Trying visible element search...", level="debug")
                        price_selectors = [
                            ".merPrice", # From the snippet, might contain EUR or JPY
                            "span[class*='itemPrice']",
                            "div[class*='itemPrice']",
                            "span[class*='price']",
                            "div[class*='price']",
                        ]
                        found_price = False
                        for selector in price_selectors:
                            try:
                                price_elems = item_element.find_elements(By.CSS_SELECTOR, selector)
                                for pe in price_elems:
                                    p_text = pe.text.strip()
                                    if '¥' in p_text: # Look specifically for Yen symbol
                                        price = p_text; found_price = True; break
                                if found_price: break
                            except NoSuchElementException: continue
                        if not found_price:
                             log_message(f"Could not find visible Yen price for {product_id} using selectors.", level="warning")

                except Exception as price_e:
                    log_message(f"Error extracting price for {product_id}: {price_e}", level="warning")

                # --- Image ---
                try:
                    # Target the img tag directly within the figure/picture structure
                    img_selector = "figure img"
                    img_elem = item_element.find_element(By.CSS_SELECTOR, img_selector)
                    item_image = img_elem.get_attribute("src") or img_elem.get_attribute("data-src")
                    if not (item_image and item_image.startswith('http')):
                        log_message(f"Found img tag but src is invalid for {product_id}. Src: '{item_image}'", level="warning")
                        item_image = "" # Reset if invalid
                except NoSuchElementException:
                    log_message(f"Could not find image using selector '{img_selector}' for {product_id}.", level="warning")
                except Exception as img_e:
                    log_message(f"Error extracting image for {product_id}: {img_e}", level="warning")

                # --- Screenshot ---
                screenshot_path = os.path.join(ITEM_SCREENSHOT_DIR, f"item_{product_id}.png")
                try:
                    item_element.screenshot(screenshot_path)
                except Exception as screenshot_error:
                    log_message(f"Error taking item screenshot for {product_id}: {screenshot_error}", level="warning")
                    screenshot_path = None

                # --- Store Product ---
                # Only store if essential info (ID, link, title, price) was found
                if product_id and link and title != "Title not found" and price != "Price not found":
                    euro_price = jpy_to_euro(price)
                    products[product_id] = {
                        "title": title,
                        "price_jpy": price,
                        "price_euro": euro_price,
                        "link": link,
                        "image": item_image,
                        "found_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "screenshot_path": screenshot_path
                    }
                    processed_count += 1
                    log_message(f"Extracted item {product_id}: {title[:30]}... - {price}", level="debug")
                else:
                    log_message(f"Skipping storage for item {product_id or i} due to missing essential data (Title: '{title}', Price: '{price}').", level="warning")


            except StaleElementReferenceException:
                log_message(f"Item element became stale during processing (item index {i}). Skipping.", level="warning")
                continue
            except Exception as e:
                log_message(f"Error processing item element {i} (ID: {product_id or 'unknown'}): {e}", level="error")
                try:
                    error_item_path = os.path.join(ERROR_SCREENSHOT_DIR, f"error_item_{product_id or f'index_{i}'}_{int(time.time())}.png")
                    item_element.screenshot(error_item_path)
                    log_message(f"Saved screenshot of problematic item.", level="debug", photo_path=error_item_path)
                except: pass
                continue

        log_message(f"Successfully processed and stored {processed_count} / {len(item_elements)} items for query '{query}'.")

    except Exception as e:
        log_message(f"Critical error during product extraction for '{query}': {e}", level="error")
        screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"error_extraction_{query.replace(' ', '_')}_{int(time.time())}.png")
        try: driver.save_screenshot(screenshot_path)
        except: pass
        log_message("Saved screenshot of extraction error state.", level="debug", photo_path=screenshot_path)

    return products


def search_mercari(driver, query):
    """Performs search, sorts, and extracts products from Mercari."""
    log_message(f"Starting search process for query: '{query}'")
    try:
        # Use the URL template from the previous script
        encoded_query = requests.utils.quote(query) # Use requests' quote for safety
        search_url = f"https://jp.mercari.com/search?keyword={encoded_query}&status=on_sale" # Base URL without sort initially
        log_message(f"Navigating to: {search_url}", level="debug")
        driver.get(search_url)
        time.sleep(random.uniform(3, 5)) # Wait for initial page load elements

        # --- Check for block/error indicators (Adapt from XYSpy) ---
        # Mercari might use different text or elements
        block_page_indicators_text = ["アクセスが集中しています", "Access Denied", "リクエストが一時的にブロックされました"] # Example Japanese/English indicators
        block_page_selectors = ["h1[class*='error']", "div#error-page"] # Example selectors
        page_title = driver.title.lower()
        page_source = driver.page_source

        if "access denied" in page_title or any(indicator in page_source for indicator in block_page_indicators_text):
            block_page_path = os.path.join(BLOCK_SCREENSHOT_DIR, f"block_page_{query.replace(' ', '_')}_{int(time.time())}.png")
            error_msg = f"Potential block page detected for query '{query}'. Title: {driver.title}"
            log_message(error_msg, level="error", photo_path=block_page_path, caption=error_msg)
            try: driver.save_screenshot(block_page_path)
            except: pass
            return {} # Skip this query for this cycle

        # Check using selectors
        for selector in block_page_selectors:
            try:
                block_element = driver.find_element(By.CSS_SELECTOR, selector)
                if block_element.is_displayed():
                    block_page_path = os.path.join(BLOCK_SCREENSHOT_DIR, f"block_page_selector_{query.replace(' ', '_')}_{int(time.time())}.png")
                    error_msg = f"Potential block page detected by selector '{selector}' for query '{query}'."
                    log_message(error_msg, level="error", photo_path=block_page_path, caption=error_msg)
                    try: driver.save_screenshot(block_page_path)
                    except: pass
                    return {}
            except NoSuchElementException:
                pass # Selector not found, continue checking
        # --- End block page check ---

        # --- Apply sorting - MANDATORY ---
        log_message(f"Attempting mandatory sort for '{query}'...")
        sort_applied = apply_sort_by_newest_mercari(driver)
        if not sort_applied:
            log_message(f"Sorting failed for query '{query}'. Skipping item extraction.", level="warning")
            # Screenshot is saved within the sort function on failure
            return {} # Return empty if sorting failed
        log_message(f"Sorting successful for '{query}'.")
        # --- End Sorting ---

        # Add delay *after* successful sort, *before* extracting products
        post_sort_delay = random.uniform(2, 4)
        log_message(f"Waiting {post_sort_delay:.1f}s after sort before extracting...", level="debug")
        time.sleep(post_sort_delay)

        # --- Handle potential CAPTCHAs (Basic Check - Add more if needed) ---
        # Mercari might use standard CAPTCHAs like reCAPTCHA/hCaptcha
        captcha_iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in captcha_iframes:
            src = frame.get_attribute('src') or ""
            if "captcha" in src or "recaptcha" in src or "hcaptcha" in src:
                captcha_path = os.path.join(ERROR_SCREENSHOT_DIR, f"captcha_detected_{query.replace(' ', '_')}_{int(time.time())}.png")
                error_msg = f"CAPTCHA detected for query '{query}'. Manual intervention likely required."
                log_message(error_msg, level="error", photo_path=captcha_path, caption=error_msg)
                try: driver.save_screenshot(captcha_path)
                except: pass
                # TODO: Implement manual CAPTCHA handling via Telegram if this occurs often
                return {} # Skip if CAPTCHA found
        # --- End Basic CAPTCHA Check ---

        # --- Extract Products from Visible Elements ---
        products = extract_products_mercari(driver, query)
        # --- End Extraction ---

        # Save final search results screenshot (after sort and extraction attempt)
        search_screenshot_path = os.path.join(SEARCH_SCREENSHOT_DIR, f"search_{query.replace(' ', '_')}_{int(time.time())}.png")
        try: driver.save_screenshot(search_screenshot_path)
        except: pass
        log_message(f"Search and extraction process complete for '{query}'. Found {len(products)} valid items.", photo_path=search_screenshot_path)

        return products

    except TimeoutException as e:
        screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"timeout_search_{query.replace(' ', '_')}_{int(time.time())}.png")
        err_msg = f"Timeout during search/navigation for '{query}': {e}"
        log_message(err_msg, level="error", photo_path=screenshot_path, caption=err_msg)
        try: driver.save_screenshot(screenshot_path)
        except: pass
        return {}
    except WebDriverException as e:
        screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"webdriver_error_search_{query.replace(' ', '_')}_{int(time.time())}.png")
        err_msg = f"Browser error during search for '{query}': {e}"
        # Check for common critical errors
        if "net::ERR_CONNECTION_REFUSED" in str(e) or "net::ERR_NAME_NOT_RESOLVED" in str(e):
             err_msg += " (Network/DNS issue?)"
        elif "session deleted because of page crash" in str(e) or "disconnected" in str(e):
             err_msg += " (Browser crashed or disconnected)"
             # Consider restarting the browser or exiting
             raise e # Re-raise critical browser errors to potentially restart
        log_message(err_msg, level="error", photo_path=screenshot_path, caption=err_msg)
        try: driver.save_screenshot(screenshot_path)
        except: pass
        return {} # Skip this query cycle
    except Exception as e:
        screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"unknown_error_search_{query.replace(' ', '_')}_{int(time.time())}.png")
        err_msg = f"Unexpected error during search for '{query}': {e}"
        log_message(err_msg, level="error", photo_path=screenshot_path, caption=err_msg)
        try: driver.save_screenshot(screenshot_path)
        except: pass
        return {}

# --- State Management (Adapted from XYSpy) ---
def load_known_products():
    """Loads known products from the JSON file."""
    if os.path.exists(KNOWN_PRODUCTS_FILE):
        try:
            with open(KNOWN_PRODUCTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
             log_message(f"Error decoding {KNOWN_PRODUCTS_FILE}. Starting fresh.", level="warning")
             return {}
        except Exception as e:
            log_message(f"Error loading known products: {e}", level="error")
            return {}
    return {}

def save_known_products(known_products):
    """Saves known products to the JSON file, excluding screenshot paths."""
    try:
        products_to_save = {}
        for query, items in known_products.items():
             products_to_save[query] = {}
             for item_id, item_data in items.items():
                  # Create a copy and remove the screenshot path before saving
                  data_copy = item_data.copy()
                  data_copy.pop('screenshot_path', None) # Remove path if it exists
                  products_to_save[query][item_id] = data_copy

        # Write to a temporary file first, then rename (atomic write)
        temp_file = KNOWN_PRODUCTS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(products_to_save, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, KNOWN_PRODUCTS_FILE) # Atomic rename/replace
        log_message(f"Saved {sum(len(v) for v in products_to_save.values())} known products.", level="debug")
    except Exception as e:
        log_message(f"Error saving known products: {e}", level="error")

# --- Alerting (Adapted from XYSpy) ---
def send_product_alert(product, query, product_id):
    """Sends the new product alert via Telegram."""
    log_message(f"Sending alert for new item {product_id} ('{query}')", level="info")
    try:
        message = f"✨ New Mercari Listing! ✨\n\n"
        message += f"🔍 Query: '{query}'\n"
        message += f"📝 {product.get('title', 'N/A')}\n"
        message += f"💰 {product.get('price_jpy', 'N/A')} / {product.get('price_euro', 'N/A')}\n"
        message += f"🔗 {product.get('link', 'N/A')}\n"
        message += f"⏰ Found: {product.get('found_time', 'N/A')}"

        # Send text message first
        telegram_bot.send_message(
            chat_id=CONFIG["TELEGRAM_CHAT_ID"],
            text=message,
            disable_web_page_preview=False
        )

        # --- Check config before sending screenshot ---
        if CONFIG.get("SEND_ITEM_SCREENSHOTS", False): # Default to False if missing
            screenshot_path = product.get("screenshot_path")
            if screenshot_path and os.path.exists(screenshot_path):
                log_message(f"Sending screenshot: {screenshot_path}", level="debug")
                try:
                    # Add a small delay specifically before sending photo
                    time.sleep(random.uniform(1, 2))
                    with open(screenshot_path, "rb") as photo:
                        telegram_bot.send_photo(
                            chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                            photo=photo
                        )
                except Exception as photo_e:
                     # Check for flood error specifically
                     if "Flood control exceeded" in str(photo_e):
                          log_message(f"Flood control exceeded while sending photo for {product_id}. Consider disabling screenshots or increasing delays.", level="warning")
                     else:
                          log_message(f"Error sending item screenshot {screenshot_path}: {photo_e}", level="warning")
            elif product.get("image"):
                 log_message(f"Screenshot configured but not available/found for {product_id}. Sending image URL.", level="debug")
                 # Add delay before sending URL too if needed
                 # time.sleep(random.uniform(0.5, 1))
                 telegram_bot.send_message(
                    chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                    text=f"Image URL: {product['image']}"
                )
        else:
            log_message(f"Screenshot sending disabled in config for {product_id}.", level="debug")


    except Exception as e:
         if "Flood control exceeded" in str(e):
              log_message(f"Flood control exceeded while sending text alert for {product_id}. Increase main alert_delay.", level="error")
         else:
              log_message(f"Error sending product alert for {product_id}: {e}", level="error")


# --- Main Loop (Adapted from XYSpy) ---
def main():
    log_message("🤖 Mercari product tracker starting...", level="info")
    known_products = load_known_products()

    # Ensure all queries from file exist in known_products dict
    for query in SEARCH_QUERIES:
        if query not in known_products:
            known_products[query] = {}
            log_message(f"Initializing known products for new query: '{query}'", level="debug")

    driver = None
    run_count = 0
    try:
        driver = setup_browser()
        # No cookie loading/login handling for Mercari initially

        while True:
            run_count += 1
            log_message(f"--- Starting Check Cycle {run_count} ---", level="info")
            start_cycle_time = time.time()

            # Update currency rate at the start of each cycle
            get_jpy_to_eur_rate()

            new_items_found_this_cycle = 0

            for query in SEARCH_QUERIES:
                log_message(f"--- Checking Query: '{query}' ---", level="info")
                query_start_time = time.time()

                # Perform the search, sort, and extraction
                current_products = search_mercari(driver, query)

                # Compare with known products
                if query not in known_products: known_products[query] = {} # Should not happen, but safety check

                new_products_for_query = {id: product for id, product in current_products.items()
                                          if id not in known_products[query]}

                if new_products_for_query:
                    num_new = len(new_products_for_query)
                    new_items_found_this_cycle += num_new
                    log_message(f"Found {num_new} new items for '{query}'!", level="info")
                    # Optional: Send summary message before individual alerts
                    # telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text=f"⚡ Found {num_new} new items for '{query}'!")

                    alert_delay = 3 # Seconds between alerts
                    for product_id, product in new_products_for_query.items():
                        log_message(f"Pausing {alert_delay}s before sending alert for {product_id}", level="debug")
                        time.sleep(alert_delay)
                        send_product_alert(product, query, product_id)
                        known_products[query][product_id] = product # Add to known list *after* sending alert
                else:
                    log_message(f"No new items found for '{query}'. {len(current_products)} items seen previously or extraction failed.", level="info")

                # Update known products for this query even if no new items, in case old ones disappeared
                # known_products[query].update(current_products) # Option: Overwrite vs only adding new

                # Save known products after each query (more robust)
                save_known_products(known_products)

                query_duration = time.time() - query_start_time
                log_message(f"--- Finished Query: '{query}' in {query_duration:.2f}s ---", level="info")

                # Delay between queries if multiple exist
                if len(SEARCH_QUERIES) > 1:
                    inter_query_delay = random.uniform(5, 15) # Shorter delay between queries
                    log_message(f"Waiting {inter_query_delay:.1f} seconds before next query...", level="debug")
                    time.sleep(inter_query_delay)

            # --- End of Cycle ---
            cycle_duration = time.time() - start_cycle_time
            log_message(f"--- Check Cycle {run_count} Complete ({new_items_found_this_cycle} new items total) in {cycle_duration:.2f}s ---", level="info")

            # Calculate sleep time for the main interval
            check_interval = random.randint(CONFIG["CHECK_INTERVAL_MIN"], CONFIG["CHECK_INTERVAL_MAX"])
            sleep_time = check_interval - cycle_duration
            if sleep_time < 0:
                log_message(f"Warning: Check cycle ({cycle_duration:.1f}s) took longer than minimum interval ({check_interval}s). Sleeping for 10s.", level="warning")
                sleep_time = 10 # Minimum sleep if cycle overruns

            log_message(f"Sleeping for {sleep_time:.1f} seconds before next cycle.", level="info")
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        log_message("Bot stopped manually (Ctrl+C).", level="info")
        telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text="🤖 Mercari tracker stopped manually.")
    except Exception as e:
        log_message(f"CRITICAL ERROR in main loop: {e}", level="critical")
        telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text=f"🚨 CRITICAL ERROR: Mercari tracker stopped!\n{e}")
        # Try to save final state and screenshot
        save_known_products(known_products)
        if driver:
            try:
                error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"critical_error_{int(time.time())}.png")
                driver.save_screenshot(error_screenshot_path)
                with open(error_screenshot_path, "rb") as photo:
                    telegram_bot.send_photo(chat_id=CONFIG["TELEGRAM_CHAT_ID"], photo=photo, caption=f"Browser state at critical error: {e}")
            except Exception as ss_error:
                log_message(f"Could not save screenshot on critical error: {ss_error}", level="error")
    finally:
        if driver:
            log_message("Closing browser...", level="debug")
            driver.quit()
        log_message("Bot has stopped.", level="info")
        # Final message only if not stopped by Ctrl+C
        # telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text="🤖 Mercari tracker has stopped.")

if __name__ == "__main__":
    main()
