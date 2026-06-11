import json
import logging
import os
import random
import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from models.product import Product
from services.scraper import USER_AGENTS, parse_price, parse_rating, parse_reviews

LOG = logging.getLogger(__name__)

REQUEST_TIMEOUT = float(os.environ.get("PROVIDER_TIMEOUT_SECONDS", "4.5"))
MAX_PER_PROVIDER = int(os.environ.get("PROVIDER_RESULT_LIMIT", "12"))
PROVIDER_DEBUG = os.environ.get("PROVIDER_DEBUG", "0").lower() in {"1", "true", "yes"}
BROWSER_FALLBACK = os.environ.get("PROVIDER_BROWSER_FALLBACK", "1").lower() in {"1", "true", "yes"}

BLOCKED_HOST_PARTS = ("google.", "googleusercontent.", "gstatic.")
TRACKING_PARAMS = {
    "tag",
    "ascsubtag",
    "ref",
    "ref_",
    "pd_rd_w",
    "pd_rd_wg",
    "pd_rd_r",
    "pf_rd_p",
    "pf_rd_r",
    "qid",
    "lid",
    "marketplace",
    "q",
    "store",
    "srno",
    "otracker",
    "fm",
    "iid",
    "ppt",
    "ppn",
    "ssid",
    "qH",
    "ov_redirect",
    "sprefix",
    "sr",
    "th",
    "psc",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
}


def browser_headers(platform: str, accept: str = "text/html,application/xhtml+xml") -> dict:
    if platform.lower() == "flipkart":
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": accept,
            "Referer": "https://www.flipkart.com/",
        }
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept": accept,
        "Referer": provider_home(platform),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def provider_session(platform: str) -> requests.Session:
    session = requests.Session()
    if platform.lower() in {"flipkart", "amazon", "myntra"}:
        return session
    try:
        session.get(provider_home(platform), headers=browser_headers(platform), timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except Exception as exc:
        LOG.info("%s provider warmup failed: %s", platform, exc)
    return session


def fetch_soup(url: str, platform: str) -> BeautifulSoup | None:
    try:
        session = provider_session(platform)
        headers = browser_headers(platform)
        headers["User-Agent"] = random.choice(USER_AGENTS) if platform == "amazon" else headers["User-Agent"]
        response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        debug_provider_response(platform, response)
        if response.status_code >= 400:
            LOG.info("%s search returned HTTP %s", platform, response.status_code)
            return None
        return BeautifulSoup(response.text, "lxml")
    except Exception as exc:
        LOG.info("%s search failed: %s", platform, exc)
        return None


def fetch_json(url: str, platform: str, headers: dict | None = None) -> dict | None:
    try:
        session = provider_session(platform)
        request_headers = browser_headers(platform, "application/json, text/plain, */*")
        request_headers.update(headers or {})
        response = session.get(url, headers=request_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        debug_provider_response(platform, response)
        if response.status_code >= 400:
            return None
        return response.json()
    except Exception as exc:
        LOG.info("%s json fetch failed: %s", platform, exc)
        return None


def fetch_rendered_soup(url: str, platform: str, wait_seconds: float = 3.0) -> BeautifulSoup | None:
    if not BROWSER_FALLBACK:
        return None
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except Exception as exc:
        LOG.info("%s browser fallback unavailable: %s", platform, exc)
        return None

    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1366,900")
        options.add_argument(f"--user-agent={browser_headers(platform)['User-Agent']}")
        try:
            driver_path = ChromeDriverManager().install()
            driver = uc.Chrome(options=options, driver_executable_path=driver_path, use_subprocess=True)
        except Exception:
            driver = uc.Chrome(options=options, use_subprocess=True)
        driver.set_page_load_timeout(int(os.environ.get("PROVIDER_BROWSER_TIMEOUT", "12")))
        driver.get(url)
        WebDriverWait(driver, int(max(wait_seconds, 1))).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        for pct in (0.35, 0.75, 1.0):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * arguments[0]);", pct)
            time.sleep(0.35)
        html = driver.page_source
        debug_provider_html(platform, 200, driver.current_url, html)
        return BeautifulSoup(html, "lxml")
    except Exception as exc:
        LOG.info("%s browser fallback failed: %s", platform, exc)
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def debug_provider_response(platform: str, response: requests.Response) -> None:
    debug_provider_html(platform, response.status_code, response.url, response.text)


def debug_provider_html(platform: str, status_code: int, url: str, html: str) -> None:
    message = f"{platform} status={status_code} url={url} html={html[:500].replace(chr(10), ' ')!r}"
    LOG.info(message)
    if PROVIDER_DEBUG:
        print(message)


def provider_home(platform: str) -> str:
    homes = {
        "amazon": "https://www.amazon.in/",
        "flipkart": "https://www.flipkart.com/",
        "ajio": "https://www.ajio.com/",
        "myntra": "https://www.myntra.com/",
        "croma": "https://www.croma.com/",
    }
    return homes.get(platform.lower(), "https://www.google.com/")


def product_from_fields(
    *,
    title: str | None,
    price,
    platform: str,
    product_url: str | None,
    image_url: str | None = "",
    rating=None,
    reviews=None,
    availability: str = "unknown",
    base_url: str = "",
) -> Product | None:
    clean_title = normalize_text(title)
    clean_url = clean_product_url(product_url or "", platform, base_url)
    parsed_price = parse_price(price)
    if not clean_title or not parsed_price or not clean_url:
        return None
    return Product(
        title=clean_title,
        price=parsed_price,
        rating=parse_rating(rating),
        reviews_count=parse_reviews(reviews),
        source=display_platform(platform),
        product_link=clean_url,
        image_url=absolute_url(image_url or "", base_url),
        availability=availability or "unknown",
    )


def normalize_text(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\s*[\|\-:]\s*(Amazon|Flipkart|Myntra|Ajio|Croma|Buy Online|Online Shopping).*$", "", text, flags=re.I)
    return text if len(text) >= 5 else ""


def text_one(node, selectors: list[str]) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            text = found.get_text(" ", strip=True)
            if text:
                return text
    return ""


def attr_one(node, selectors: list[str], attr: str) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found and found.get(attr):
            return found.get(attr, "").strip()
    return ""


def absolute_url(url: str, base_url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base_url, url)


def clean_product_url(url: str, platform: str, base_url: str = "") -> str:
    full_url = absolute_url(url, base_url)
    if not full_url.startswith(("http://", "https://")):
        return ""

    parsed = urlparse(full_url)
    host = (parsed.hostname or "").lower().replace("www.", "")
    if any(blocked in host for blocked in BLOCKED_HOST_PARTS):
        return ""
    if not platform_host_matches(platform, host):
        return ""

    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in TRACKING_PARAMS])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def platform_host_matches(platform: str, host: str) -> bool:
    allowed = {
        "amazon": ("amazon.in", "amazon.com"),
        "flipkart": ("flipkart.com",),
        "ajio": ("ajio.com",),
        "myntra": ("myntra.com",),
        "croma": ("croma.com",),
    }
    return any(host == item or host.endswith("." + item) for item in allowed.get(platform.lower(), ()))


def display_platform(platform: str) -> str:
    names = {
        "amazon": "Amazon",
        "flipkart": "Flipkart",
        "ajio": "Ajio",
        "myntra": "Myntra",
        "croma": "Croma",
    }
    return names.get(platform.lower(), platform.title())


def collect_json_ld_products(soup: BeautifulSoup, platform: str, base_url: str) -> list[Product]:
    products = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(tag.string or "")
        except Exception:
            continue
        for item in _walk_json(payload):
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if isinstance(item_type, list):
                is_product = "Product" in item_type
            else:
                is_product = item_type == "Product"
            if not is_product:
                continue
            offers = item.get("offers") or {}
            aggregate = item.get("aggregateRating") or {}
            product = product_from_fields(
                title=item.get("name"),
                price=offers.get("price") if isinstance(offers, dict) else None,
                rating=aggregate.get("ratingValue") if isinstance(aggregate, dict) else None,
                reviews=aggregate.get("reviewCount") if isinstance(aggregate, dict) else None,
                image_url=_first(item.get("image")),
                product_url=item.get("url"),
                platform=platform,
                base_url=base_url,
                availability=str(offers.get("availability", "unknown")).rsplit("/", 1)[-1] if isinstance(offers, dict) else "unknown",
            )
            if product:
                products.append(product)
    return products


def _walk_json(value):
    stack = [value]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _first(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def unique_products(products: list[Product], limit: int = MAX_PER_PROVIDER) -> list[Product]:
    seen = set()
    unique = []
    for product in products:
        key = product.product_link.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(product)
        if len(unique) >= limit:
            break
    return unique
