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
# --- Image Processing Imports ---
from PIL import Image
import io
# -----------------------------

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
FILTERED_BG_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "filtered_backgrounds") # <-- New directory

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SEARCH_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(ITEM_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(ERROR_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(BLOCK_SCREENSHOT_DIR, exist_ok=True)
os.makedirs(PAGE_LOG_DIR, exist_ok=True)
os.makedirs(FILTERED_BG_SCREENSHOT_DIR, exist_ok=True) # <-- Create the new directory
# --- End Directory Setup ---

# --- File Paths ---
KNOWN_PRODUCTS_FILE = os.path.join(DATA_DIR, "mercari_known_products.json")
# --- End File Paths ---

# --- Global variables ---
JPY_TO_EUR_RATE = None
RATE_LAST_UPDATED = 0
# ---

# --- Define Dummy Bot Class (Moved Outside) ---
class DummyBot: # Fallback if real bot init fails
    """A dummy bot class that prints messages instead of sending them."""
    def send_message(self, chat_id, text, **kwargs):
        print(f"[Telegram Dummy] {text}")
    def send_photo(self, chat_id, photo, **kwargs):
        print(f"[Telegram Dummy Photo] {kwargs.get('caption', '')}")
    def get_me(self): # Add a dummy get_me method
        class DummyBotInfo:
            username = "DummyBot"
        return DummyBotInfo()

# --- Initialize Telegram Bot ---
try:
    # Attempt to initialize the real bot
    telegram_bot = telegram.Bot(token=CONFIG["TELEGRAM_TOKEN"])
    # Test connection by getting bot info
    bot_info = telegram_bot.get_me()
    print(f"Successfully connected to Telegram as bot: {bot_info.username}")
except Exception as e:
    # If real bot fails, print error and assign the DummyBot instance
    print(f"Error initializing Telegram bot: {e}")
    print("!!! Using DummyBot - Telegram messages will only be printed to console !!!")
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
            # Avoid infinite loops if the dummy bot is active
            if not isinstance(telegram_bot, DummyBot):
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
            return JPY_TO_EUR_RATE
    except Exception as e:
        log_message(f"Failed to fetch or parse currency rate: {e}", level="error")
        return JPY_TO_EUR_RATE

def jpy_to_euro(jpy_str):
    """Converts a JPY price string (e.g., '¥15,000') to a formatted EUR string."""
    rate = get_jpy_to_eur_rate()
    if rate is None:
        return "€N/A (Rate Error)"
    try:
        jpy_str_cleaned = re.sub(r'[^\d.]', '', jpy_str)
        if not jpy_str_cleaned:
            return "€N/A (Parse Error)"
        jpy = float(jpy_str_cleaned)
        euro = jpy * rate
        return f"€{euro:.2f}"
    except Exception as e:
        print(f"Error converting JPY string '{jpy_str}' to EUR: {e}")
        return "€N/A (Conv. Error)"

# --- Image Background Check Function (MODIFIED) ---
def is_background_white(image_url, product_id, border_margin=5, color_threshold=245, border_threshold=0.95):
    """
    Downloads an image, checks if its border pixels are predominantly white,
    and saves the image if filtered.
    Returns True if likely white background, False otherwise or on error.
    """
    if not image_url or not image_url.startswith('http'):
        log_message(f"Invalid or missing image URL for background check: {image_url}", level="debug")
        return False

    log_message(f"Analyzing background for image: ...{image_url[-50:]}", level="debug")
    img_data = None # Initialize img_data
    try:
        response = requests.get(image_url, stream=True, timeout=15)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if not content_type.startswith('image/'):
            log_message(f"Skipping background check, content-type not image: {content_type} for URL ...{image_url[-50:]}", level="debug")
            return False

        img_data = response.content # Store image data
        img = Image.open(io.BytesIO(img_data)).convert('RGB')
        width, height = img.size

        if width <= border_margin * 2 or height <= border_margin * 2:
            log_message(f"Image too small ({width}x{height}) for background check.", level="debug")
            return False

        border_pixels = []
        for x in range(width):
            for y in range(border_margin):
                border_pixels.append(img.getpixel((x, y)))
                border_pixels.append(img.getpixel((x, height - 1 - y)))
        for y in range(border_margin, height - border_margin):
             for x in range(border_margin):
                  border_pixels.append(img.getpixel((x, y)))
                  border_pixels.append(img.getpixel((width - 1 - x, y)))

        if not border_pixels:
            log_message("No border pixels collected.", level="warning")
            return False

        white_count = 0
        for r, g, b in border_pixels:
            if r >= color_threshold and g >= color_threshold and b >= color_threshold:
                white_count += 1

        white_percentage = white_count / len(border_pixels)
        is_white = white_percentage >= border_threshold
        log_message(f"Image ...{image_url[-50:]} white border %: {white_percentage:.2f} -> White BG Filter: {is_white}", level="debug")

        # --- Save Filtered Image ---
        if is_white and img_data: # Check if filter triggered and we have data
            try:
                # Use product_id for filename, sanitize it just in case
                safe_product_id = re.sub(r'[^\w\-]+', '_', product_id) # Replace non-alphanumeric/- with _
                # Add timestamp to prevent potential overwrites if script restarts quickly
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{safe_product_id}_{timestamp}.jpg" # Save as JPEG
                filepath = os.path.join(FILTERED_BG_SCREENSHOT_DIR, filename)

                # Save the image using Pillow
                img_to_save = Image.open(io.BytesIO(img_data))
                # Ensure it's RGB before saving as JPEG
                if img_to_save.mode in ("RGBA", "P"):
                    img_to_save = img_to_save.convert("RGB")
                img_to_save.save(filepath, "JPEG")
                log_message(f"Saved filtered image to: {filepath}", level="info")
            except Exception as save_e:
                log_message(f"Failed to save filtered image {product_id}: {save_e}", level="error")
        # --- End Save Filtered Image ---

        return is_white

    except requests.exceptions.Timeout:
        log_message(f"Timeout downloading image {image_url}", level="warning")
        return False
    except requests.exceptions.RequestException as e:
        log_message(f"Failed to download image {image_url}: {e}", level="warning")
        return False
    except Image.UnidentifiedImageError:
         log_message(f"Failed to identify image format for {image_url}", level="warning")
         return False
    except Exception as e:
        log_message(f"Failed to process image {image_url}: {e}", level="warning")
        return False

# --- Browser Setup ---
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
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7")
        options.add_argument("--disable-features=UserAgentClientHint")

        if CONFIG.get("HEADLESS", True):
            log_message("Running in HEADLESS mode.", level="debug")
            options.add_argument("--headless=new")
        else:
            log_message("Running in HEADED mode.", level="debug")

        # If you encounter version mismatch errors, uncomment and set your Chrome version:
        driver = uc.Chrome(options=options, version_main=135) # Force version 135
        # driver = uc.Chrome(options=options, version_main=None)
        driver.set_page_load_timeout(60)
        log_message("Browser setup complete.", level="debug")
        return driver
    except Exception as e:
        log_message(f"Error creating undetected-chromedriver: {e}", level="error")
        try:
            log_message("Attempting fallback to standard chromedriver...", level="warning")
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            options = webdriver.ChromeOptions()
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
    wait_time = 15
    log_message("Attempting to apply 'Sort by Newest' using <select> dropdown...", level="debug")
    try:
        select_element_css = "select[name='sortOrder']"
        log_message(f"Waiting for sort <select> element using CSS: {select_element_css}", level="debug")
        select_element = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, select_element_css))
        )
        log_message("Sort <select> element found. Selecting 'Newest' option...", level="debug")
        select_object = Select(select_element)
        value_for_newest = "created_time:desc"
        select_object.select_by_value(value_for_newest)
        log_message(f"Selected option with value '{value_for_newest}'. Waiting for results to reload...", level="debug")
        time.sleep(random.uniform(5, 7))
        log_message("Applied sort by 'Newest'.")
        return True
    except TimeoutException as e:
        error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_timeout_error_{int(time.time())}.png")
        err_msg = f"Timeout finding Mercari sort <select> element ({e}). Selector '{select_element_css}' might be wrong or page didn't load correctly."
        log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
        try: driver.save_screenshot(error_screenshot_path)
        except: pass
        return False
    except NoSuchElementException as e:
         error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_no_option_error_{int(time.time())}.png")
         err_msg = f"Could not find the option with value '{value_for_newest}' in the sort dropdown ({e})."
         log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
         try: driver.save_screenshot(error_screenshot_path)
         except: pass
         return False
    except Exception as e:
        error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"sort_select_general_error_{int(time.time())}.png")
        err_msg = f"General error applying Mercari sort via <select>: {e}"
        log_message(err_msg, level="error", photo_path=error_screenshot_path, caption=err_msg)
        try: driver.save_screenshot(error_screenshot_path)
        except: pass
        return False

def extract_products_mercari(driver, query):
    """Extracts product details from the visible elements on Mercari search results."""
    products = {}
    log_message(f"Extracting products for query '{query}'...", level="debug")
    try:
        container_selector = "#item-grid"
        item_card_selector = f"{container_selector} > ul > li[data-testid='item-cell']"

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
        stored_count = 0 # Keep track of items actually stored
        for i, item_element in enumerate(item_elements):
            product_id = None
            link = None
            title = "Title not found"
            price = "Price not found"
            item_image = ""
            screenshot_path = None
            is_white_bg = False # Default for this item

            try:
                # Scroll element into view gently
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", item_element)
                time.sleep(0.3) # Small pause after scroll

                # --- Link and ID ---
                try:
                    link_element_selector = "a[data-testid='thumbnail-link']"
                    link_element = item_element.find_element(By.CSS_SELECTOR, link_element_selector)
                    link = link_element.get_attribute('href')
                    if link:
                        product_id_match = re.search(r'/(m\d+)/?$', link)
                        if product_id_match: product_id = product_id_match.group(1)
                        else: product_id = f"hash_{hash(link)}_{i}"
                    else: raise NoSuchElementException("Link href was empty")
                except NoSuchElementException:
                    log_message(f"Could not find link/ID using selector '{link_element_selector}' for item {i}. Skipping.", level="warning")
                    continue

                # --- Title ---
                try:
                    title_selector = "span[data-testid='thumbnail-item-name']"
                    title_elem = item_element.find_element(By.CSS_SELECTOR, title_selector)
                    title = title_elem.text.strip()
                    if not title or len(title) < 2:
                        log_message(f"Found title element but text is short/empty for {product_id}. Text: '{title}'", level="debug")
                        try:
                            thumb_div = item_element.find_element(By.CSS_SELECTOR, "div.merItemThumbnail")
                            aria_label = thumb_div.get_attribute('aria-label')
                            if aria_label:
                                title_match = re.match(r'^(.*?)\s+\d{1,3}(?:,\d{3})*円', aria_label)
                                if title_match: title = title_match.group(1).strip()
                        except: pass
                except NoSuchElementException:
                    log_message(f"Could not find title using selector '{title_selector}' for {product_id}.", level="warning")

                # --- Price ---
                try:
                    thumb_div_selector = "div.merItemThumbnail" # Assuming this class is stable enough
                    thumb_div = item_element.find_element(By.CSS_SELECTOR, thumb_div_selector)
                    aria_label = thumb_div.get_attribute('aria-label')
                    if aria_label:
                        price_match = re.search(r'(\d{1,3}(?:,\d{3})*|\d+)\s*円', aria_label)
                        if price_match: price = f"¥{price_match.group(1)}"
                        else: log_message(f"Could not find Yen price pattern in aria-label for {product_id}. Label: '{aria_label}'", level="debug")
                    else: log_message(f"Aria-label empty for {product_id}.", level="debug")

                    if price == "Price not found":
                        log_message(f"Price not in aria-label for {product_id}. Trying visible element search...", level="debug")
                        price_selectors = [".merPrice", "span[class*='itemPrice']", "div[class*='itemPrice']", "span[class*='price']", "div[class*='price']"]
                        found_price = False
                        for selector in price_selectors:
                            try:
                                price_elems = item_element.find_elements(By.CSS_SELECTOR, selector)
                                for pe in price_elems:
                                    p_text = pe.text.strip()
                                    if '¥' in p_text: price = p_text; found_price = True; break
                                if found_price: break
                            except NoSuchElementException: continue
                        if not found_price: log_message(f"Could not find visible Yen price for {product_id} using selectors.", level="warning")
                except Exception as price_e: log_message(f"Error extracting price for {product_id}: {price_e}", level="warning")

                # --- Image ---
                try:
                    img_selector = "figure img"
                    img_elem = item_element.find_element(By.CSS_SELECTOR, img_selector)
                    item_image = img_elem.get_attribute("src") or img_elem.get_attribute("data-src")
                    if not (item_image and item_image.startswith('http')):
                        log_message(f"Found img tag but src is invalid for {product_id}. Src: '{item_image}'", level="warning")
                        item_image = ""
                except NoSuchElementException: log_message(f"Could not find image using selector '{img_selector}' for {product_id}.", level="warning")
                except Exception as img_e: log_message(f"Error extracting image for {product_id}: {img_e}", level="warning")

                # --- White Background Check ---
                # Perform check only if filter enabled AND we have an image URL
                if CONFIG.get("FILTER_WHITE_BACKGROUNDS", False) and item_image:
                    # Pass product_id to the check function
                    is_white_bg = is_background_white(
                        item_image,
                        product_id, # Pass the ID for filename
                        color_threshold=CONFIG.get("WHITE_BG_COLOR_THRESHOLD", 245),
                        border_threshold=CONFIG.get("WHITE_BG_BORDER_THRESHOLD", 0.95)
                    )
                    if is_white_bg:
                        log_message(f"Skipping item {product_id} due to detected white background.", level="info")
                        processed_count += 1 # Count as processed, but not stored
                        continue # Skip to the next item_element in the loop
                # --- End White Background Check ---

                # --- Screenshot ---
                screenshot_path = os.path.join(ITEM_SCREENSHOT_DIR, f"item_{product_id}.png")
                try: item_element.screenshot(screenshot_path)
                except Exception as screenshot_error:
                    log_message(f"Error taking item screenshot for {product_id}: {screenshot_error}", level="warning")
                    screenshot_path = None

                # --- Store Product ---
                # Only store if essential info found AND background filter didn't skip it
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
                    stored_count += 1 # Increment stored count
                    log_message(f"Extracted item {product_id}: {title[:30]}... - {price}", level="debug")
                else:
                    # Log why it wasn't stored (already logged if white bg)
                    if not is_white_bg: # Avoid double logging if skipped for white bg
                         log_message(f"Skipping storage for item {product_id or i} due to missing essential data (Title: '{title}', Price: '{price}').", level="warning")
                    processed_count += 1 # Still counts as processed

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

        log_message(f"Processed {processed_count} / {len(item_elements)} items. Stored {stored_count} items for query '{query}'.")

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
        encoded_query = requests.utils.quote(query)
        search_url = f"https://jp.mercari.com/search?keyword={encoded_query}&status=on_sale"
        log_message(f"Navigating to: {search_url}", level="debug")
        driver.get(search_url)
        time.sleep(random.uniform(3, 5))

        # --- Check for block/error indicators ---
        block_page_indicators_text = ["アクセスが集中しています", "Access Denied", "リクエストが一時的にブロックされました"]
        block_page_selectors = ["h1[class*='error']", "div#error-page"]
        page_title = driver.title.lower()
        page_source = driver.page_source

        if "access denied" in page_title or any(indicator in page_source for indicator in block_page_indicators_text):
            block_page_path = os.path.join(BLOCK_SCREENSHOT_DIR, f"block_page_{query.replace(' ', '_')}_{int(time.time())}.png")
            error_msg = f"Potential block page detected for query '{query}'. Title: {driver.title}"
            log_message(error_msg, level="error", photo_path=block_page_path, caption=error_msg)
            try: driver.save_screenshot(block_page_path)
            except: pass
            return {}

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
            except NoSuchElementException: pass
        # --- End block page check ---

        # --- Apply sorting - MANDATORY ---
        log_message(f"Attempting mandatory sort for '{query}'...")
        sort_applied = apply_sort_by_newest_mercari(driver)
        if not sort_applied:
            log_message(f"Sorting failed for query '{query}'. Skipping item extraction.", level="warning")
            return {}
        log_message(f"Sorting successful for '{query}'.")
        # --- End Sorting ---

        post_sort_delay = random.uniform(2, 4)
        log_message(f"Waiting {post_sort_delay:.1f}s after sort before extracting...", level="debug")
        time.sleep(post_sort_delay)

        # --- Basic CAPTCHA Check ---
        captcha_iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in captcha_iframes:
            src = frame.get_attribute('src') or ""
            if "captcha" in src or "recaptcha" in src or "hcaptcha" in src:
                captcha_path = os.path.join(ERROR_SCREENSHOT_DIR, f"captcha_detected_{query.replace(' ', '_')}_{int(time.time())}.png")
                error_msg = f"CAPTCHA detected for query '{query}'. Manual intervention likely required."
                log_message(error_msg, level="error", photo_path=captcha_path, caption=error_msg)
                try: driver.save_screenshot(captcha_path)
                except: pass
                return {}
        # --- End Basic CAPTCHA Check ---

        # --- Extract Products ---
        products = extract_products_mercari(driver, query)
        # --- End Extraction ---

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
        if "net::ERR_CONNECTION_REFUSED" in str(e) or "net::ERR_NAME_NOT_RESOLVED" in str(e): err_msg += " (Network/DNS issue?)"
        elif "session deleted because of page crash" in str(e) or "disconnected" in str(e): err_msg += " (Browser crashed or disconnected)"; raise e
        log_message(err_msg, level="error", photo_path=screenshot_path, caption=err_msg)
        try: driver.save_screenshot(screenshot_path)
        except: pass
        return {}
    except Exception as e:
        screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"unknown_error_search_{query.replace(' ', '_')}_{int(time.time())}.png")
        err_msg = f"Unexpected error during search for '{query}': {e}"
        log_message(err_msg, level="error", photo_path=screenshot_path, caption=err_msg)
        try: driver.save_screenshot(screenshot_path)
        except: pass
        return {}

# --- State Management ---
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
                  data_copy = item_data.copy()
                  data_copy.pop('screenshot_path', None)
                  products_to_save[query][item_id] = data_copy

        temp_file = KNOWN_PRODUCTS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(products_to_save, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, KNOWN_PRODUCTS_FILE)
        log_message(f"Saved {sum(len(v) for v in products_to_save.values())} known products.", level="debug")
    except Exception as e:
        log_message(f"Error saving known products: {e}", level="error")

# --- Alerting ---
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

        telegram_bot.send_message(
            chat_id=CONFIG["TELEGRAM_CHAT_ID"],
            text=message,
            disable_web_page_preview=False
        )

        if CONFIG.get("SEND_ITEM_SCREENSHOTS", False):
            screenshot_path = product.get("screenshot_path")
            if screenshot_path and os.path.exists(screenshot_path):
                log_message(f"Sending screenshot: {screenshot_path}", level="debug")
                try:
                    time.sleep(random.uniform(1, 2)) # Small delay before photo
                    with open(screenshot_path, "rb") as photo:
                        telegram_bot.send_photo(
                            chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                            photo=photo
                        )
                except Exception as photo_e:
                     if "Flood control exceeded" in str(photo_e): log_message(f"Flood control exceeded while sending photo for {product_id}.", level="warning")
                     else: log_message(f"Error sending item screenshot {screenshot_path}: {photo_e}", level="warning")
            elif product.get("image"):
                 log_message(f"Screenshot configured but not available/found for {product_id}. Sending image URL.", level="debug")
                 telegram_bot.send_message(
                    chat_id=CONFIG["TELEGRAM_CHAT_ID"],
                    text=f"Image URL: {product['image']}"
                )
        else:
            log_message(f"Screenshot sending disabled in config for {product_id}.", level="debug")

    except Exception as e:
         if "Flood control exceeded" in str(e): log_message(f"Flood control exceeded while sending text alert for {product_id}. Increase main alert_delay.", level="error")
         else: log_message(f"Error sending product alert for {product_id}: {e}", level="error")

# --- Main Loop ---
def main():
    # Send startup message only if bot initialized correctly
    if not isinstance(telegram_bot, DummyBot):
         try:
              telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text="🤖 Mercari product tracker starting...")
         except Exception as start_msg_e:
              print(f"Warning: Could not send startup message to Telegram: {start_msg_e}")
    else:
         log_message("🤖 Mercari product tracker starting... (Telegram connection failed, using dummy bot)")


    known_products = load_known_products()

    for query in SEARCH_QUERIES:
        if query not in known_products:
            known_products[query] = {}
            log_message(f"Initializing known products for new query: '{query}'", level="debug")

    driver = None
    run_count = 0
    try:
        driver = setup_browser()

        while True:
            run_count += 1
            log_message(f"--- Starting Check Cycle {run_count} ---", level="info")
            start_cycle_time = time.time()

            get_jpy_to_eur_rate() # Update currency rate

            new_items_found_this_cycle = 0

            for query in SEARCH_QUERIES:
                log_message(f"--- Checking Query: '{query}' ---", level="info")
                query_start_time = time.time()

                current_products = search_mercari(driver, query)

                if query not in known_products: known_products[query] = {}

                new_products_for_query = {id: product for id, product in current_products.items()
                                          if id not in known_products[query]}

                if new_products_for_query:
                    num_new = len(new_products_for_query)
                    new_items_found_this_cycle += num_new
                    log_message(f"Found {num_new} new items for '{query}'!", level="info")

                    alert_delay = 3 # Default delay between alerts
                    for product_id, product in new_products_for_query.items():
                        log_message(f"Pausing {alert_delay}s before sending alert for {product_id}", level="debug")
                        time.sleep(alert_delay)
                        send_product_alert(product, query, product_id)
                        known_products[query][product_id] = product
                else:
                    log_message(f"No new items found for '{query}'. {len(current_products)} items seen previously or extraction failed.", level="info")

                save_known_products(known_products) # Save after each query

                query_duration = time.time() - query_start_time
                log_message(f"--- Finished Query: '{query}' in {query_duration:.2f}s ---", level="info")

                if len(SEARCH_QUERIES) > 1:
                    inter_query_delay = random.uniform(5, 15)
                    log_message(f"Waiting {inter_query_delay:.1f} seconds before next query...", level="debug")
                    time.sleep(inter_query_delay)

            # --- End of Cycle ---
            cycle_duration = time.time() - start_cycle_time
            log_message(f"--- Check Cycle {run_count} Complete ({new_items_found_this_cycle} new items total) in {cycle_duration:.2f}s ---", level="info")

            check_interval = random.randint(CONFIG["CHECK_INTERVAL_MIN"], CONFIG["CHECK_INTERVAL_MAX"])
            sleep_time = check_interval - cycle_duration
            if sleep_time < 0:
                log_message(f"Warning: Check cycle ({cycle_duration:.1f}s) took longer than minimum interval ({check_interval}s). Sleeping for 10s.", level="warning")
                sleep_time = 10

            log_message(f"Sleeping for {sleep_time:.1f} seconds before next cycle.", level="info")
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        log_message("Bot stopped manually (Ctrl+C).", level="info")
        if not isinstance(telegram_bot, DummyBot): telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text="🤖 Mercari tracker stopped manually.")
    except Exception as e:
        log_message(f"CRITICAL ERROR in main loop: {e}", level="critical")
        if not isinstance(telegram_bot, DummyBot):
             try:
                  telegram_bot.send_message(chat_id=CONFIG["TELEGRAM_CHAT_ID"], text=f"🚨 CRITICAL ERROR: Mercari tracker stopped!\n{e}")
                  if driver:
                       error_screenshot_path = os.path.join(ERROR_SCREENSHOT_DIR, f"critical_error_{int(time.time())}.png")
                       driver.save_screenshot(error_screenshot_path)
                       with open(error_screenshot_path, "rb") as photo:
                            telegram_bot.send_photo(chat_id=CONFIG["TELEGRAM_CHAT_ID"], photo=photo, caption=f"Browser state at critical error: {e}")
             except Exception as report_e:
                  print(f"Failed to send critical error report to Telegram: {report_e}")
        save_known_products(known_products) # Attempt to save state on critical error
    finally:
        if driver:
            log_message("Closing browser...", level="debug")
            driver.quit()
        log_message("Bot has stopped.", level="info")

if __name__ == "__main__":
    main()
