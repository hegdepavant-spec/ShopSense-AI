import json
import logging
import os
import random
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

HEADERS = {
    "User-Agent": random.choice(USER_AGENTS),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

BAD_TITLE_PATTERNS = [
    r"^sorry", r"^page not found", r"^404", r"^access denied",
    r"^robot check", r"^sign in", r"^just a moment", r"captcha",
    r"amazon\.in$", r"flipkart\.com$", r"myntra\.com$",
]

TITLE_SELECTORS = {
    "amazon": ["#productTitle", "span#productTitle", "h1#title span"],
    "flipkart": ["span.VU-ZEz", "h1.yhB1nd", "span.B_NuCI", "h1._9E25nV", "div._35KyD6"],
    "myntra": ["h1.pdp-title", "h1.pdp-name", ".pdp-product-description-content h1"],
    "meesho": ["h1", "p.sc-gEvEer"],
    "ajio": [".prod-name", "h1"],
    "nykaa": ["h1", ".css-1gc4x7i"],
}

PRICE_SELECTORS = {
    "amazon": [".a-price .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"],
    "flipkart": ["div.Nx9bqj", "div._30jeq3", "div.CxhGGd"],
    "myntra": [".pdp-price strong", ".pdp-discount-container"],
    "meesho": ["h4", "[class*=Price]"],
    "ajio": [".prod-sp", ".prod-price-section"],
}

RATING_SELECTORS = {
    "amazon": ["span.a-icon-alt", "#acrPopover"],
    "flipkart": ["div.XQDdHH", "div._3LWZlK"],
    "myntra": [".index-overallRating"],
}


def scrape_title(url: str) -> tuple[str | None, str | None]:
    details = scrape_product_details(url, prefer_selenium=True)
    title = details.get("title")
    final_url = details.get("final_url")
    return (title, final_url) if title else (None, None)


def scrape_product_details(url: str, prefer_selenium: bool = False) -> dict:
    LOG.info("validating product page: %s", url[:120])
    if prefer_selenium:
        details = _selenium_details(url)
        if details.get("title"):
            return details
        return _requests_details(url)

    details = _requests_details(url)
    if details.get("title"):
        return details
    if os.environ.get("VALIDATE_WITH_SELENIUM", "0").lower() in {"1", "true", "yes"}:
        return _selenium_details(url)
    return details


def _selenium_details(url: str) -> dict:
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except Exception as exc:
        LOG.warning("selenium dependencies unavailable: %s", exc)
        return {}

    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1366,900")
        if os.environ.get("SELENIUM_HEADLESS", "1").lower() not in {"0", "false", "no"}:
            options.add_argument("--headless=new")

        try:
            driver_path = ChromeDriverManager().install()
            driver = uc.Chrome(options=options, driver_executable_path=driver_path, use_subprocess=True)
        except Exception as exc:
            LOG.warning("webdriver-manager startup failed, trying uc fallback: %s", exc)
            driver = uc.Chrome(options=options, use_subprocess=True)

        driver.set_page_load_timeout(int(os.environ.get("SELENIUM_PAGE_TIMEOUT", "24")))
        driver.get(url)
        wait = WebDriverWait(driver, int(os.environ.get("SELENIUM_WAIT_SECONDS", "10")))
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _human_pause()
        _simulate_scroll(driver)

        html = driver.page_source
        final_url = driver.current_url
        details = _extract_from_html(html, final_url)
        details["final_url"] = final_url
        return details
    except Exception as exc:
        LOG.warning("selenium scrape failed: %s", exc)
        return {}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _requests_details(url: str) -> dict:
    try:
        response = requests.get(url, headers={**HEADERS, "User-Agent": random.choice(USER_AGENTS)}, timeout=8, allow_redirects=True)
        if response.status_code >= 400:
            LOG.warning("requests fallback returned HTTP %s", response.status_code)
            return {}
        details = _extract_from_html(response.text, response.url)
        details["final_url"] = response.url
        return details
    except Exception as exc:
        LOG.warning("requests fallback failed: %s", exc)
        return {}


def _extract_from_html(html: str, final_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    platform = _platform(final_url)
    title = _first_selector_text(soup, TITLE_SELECTORS.get(platform, []))
    if not title:
        title = _json_ld_value(soup, "name") or _meta_value(soup, ["og:title", "twitter:title"])
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = _validate(title or "")

    price_text = _first_selector_text(soup, PRICE_SELECTORS.get(platform, []))
    price = parse_price(price_text) or parse_price(_json_ld_value(soup, "price") or "")
    rating = parse_rating(_first_selector_text(soup, RATING_SELECTORS.get(platform, [])) or _json_ld_value(soup, "ratingValue") or "")
    reviews = parse_reviews(_json_ld_value(soup, "reviewCount") or soup.get_text(" ", strip=True)[:4000])
    image_url = _json_ld_value(soup, "image") or _meta_value(soup, ["og:image", "twitter:image"])
    if isinstance(image_url, list):
        image_url = image_url[0] if image_url else ""

    return {
        "title": title,
        "price": price,
        "rating": rating,
        "reviews_count": reviews,
        "image_url": image_url or "",
        "source": platform,
        "final_url": final_url,
    }


def _first_selector_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return None


def _json_ld_value(soup: BeautifulSoup, key: str):
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if key in item:
                    return item[key]
                offers = item.get("offers")
                aggregate = item.get("aggregateRating")
                if isinstance(offers, dict):
                    stack.append(offers)
                if isinstance(aggregate, dict):
                    stack.append(aggregate)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return None


def _meta_value(soup: BeautifulSoup, names: list[str]) -> str | None:
    for name in names:
        tag = soup.select_one(f'meta[property="{name}"], meta[name="{name}"]')
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _validate(title: str) -> str | None:
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(
        r"\s*[\|\-:]\s*(Amazon|Flipkart|Myntra|Meesho|Buy Online|Online Shopping|Best Price|India|Shop Online).*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    if len(title) < 5:
        return None
    for pattern in BAD_TITLE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return None
    return title


def _platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for name in ["amazon", "flipkart", "myntra", "meesho", "ajio", "nykaa", "snapdeal", "croma", "nike", "puma"]:
        if name in host:
            return name
    if "reliancedigital" in host:
        return "reliance digital"
    if "tatacliq" in host or "tata cliq" in host:
        return "tatacliq"
    return host.replace("www.", "").split(".")[0] or "other"


def _human_pause() -> None:
    time.sleep(random.uniform(0.8, 1.9))


def _simulate_scroll(driver) -> None:
    for pct in (0.25, 0.55, 0.85):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * arguments[0]);", pct)
        time.sleep(random.uniform(0.35, 0.9))
    driver.execute_script("window.scrollTo(0, 0);")


def parse_price(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "")
    match = re.search(r"[\d,.]+", text)
    if not match:
        return None
    try:
        price = float(match.group(0).replace(",", ""))
        return price if price > 0 else None
    except ValueError:
        return None


def parse_rating(value) -> float | None:
    match = re.search(r"([0-5](?:\.\d+)?)", str(value or ""))
    if not match:
        return None
    rating = float(match.group(1))
    return rating if 0 < rating <= 5 else None


def parse_reviews(value) -> int | None:
    text = str(value or "").replace(",", "")
    match = re.search(r"(\d+)\s*(?:ratings?|reviews?)", text, re.IGNORECASE) or re.search(r"\b(\d{2,})\b", text)
    return int(match.group(1)) if match else None


def clean_for_search(title: str) -> str:
    t = re.sub(r"[\(\[\{][^\)\]\}]{0,80}[\)\]\}]", " ", title)
    t = re.sub(
        r"\b(buy|online|best price|lowest price|free shipping|cod available|"
        r"shop now|order now|available|in india|check price|pack of \d+|combo of \d+|set of \d+)\b",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(size|uk|us|eu|ind)\s*[\d\.\-/]+\b"
        r"|\b(color|colour)\s*:?\s*\S+"
        r"|\b\d+\s*(ml|l|kg|g|mm|cm|inch|inches|ft)\b",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+", " ", t).strip(" |,-:")
    return " ".join(t.split()[:8])
