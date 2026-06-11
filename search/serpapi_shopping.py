import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from models.product import Product
from services.scraper import parse_price, parse_rating, parse_reviews

LOG = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search"
TRACKING_PARAMS = {"tag", "ref", "ref_", "ascsubtag", "qid", "sr", "sprefix", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def search_google_shopping(query: str, limit: int | None = None) -> list[Product]:
    api_key = os.environ.get("SERPAPI_KEY", "").strip()
    if not api_key or "your_" in api_key:
        raise RuntimeError("SERPAPI_KEY is required for real-time shopping results.")

    limit = limit or _env_int("SERPAPI_PAGE_SIZE", 40)
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": api_key,
        "gl": os.environ.get("SERPAPI_GL", "in"),
        "hl": os.environ.get("SERPAPI_HL", "en"),
        "num": str(max(10, min(limit, 100))),
    }
    started = time.perf_counter()
    response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=_env_int("SERPAPI_TIMEOUT", 18))
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"SerpAPI error: {payload['error']}")

    raw_items = payload.get("shopping_results", []) or []
    products = _parse_items(raw_items)
    products = [product for product in products if product]
    LOG.info("SerpAPI google_shopping query=%r raw=%s parsed=%s elapsed_ms=%s", query, len(raw_items), len(products), int((time.perf_counter() - started) * 1000))
    return products


def _parse_items(items: list[dict]) -> list[Product]:
    immersive_limit = _env_int("SERPAPI_IMMERSIVE_LIMIT", 4)
    workers = max(1, min(_env_int("SERPAPI_IMMERSIVE_WORKERS", 6), immersive_limit))
    products: list[Product] = []
    immersive_items = []

    for item in items:
        link = _first_url(item.get("link"), item.get("product_link"))
        if "google." in (urlparse(link).hostname or ""):
            if item.get("serpapi_immersive_product_api") and len(immersive_items) < immersive_limit:
                immersive_items.append(item)
        else:
            product = _parse_item(item)
            if product:
                products.append(product)

    if immersive_items:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_parse_immersive_item, item) for item in immersive_items]
            for future in as_completed(futures):
                try:
                    products.extend(future.result())
                except Exception as exc:
                    LOG.warning("SerpAPI immersive parse failed: %s", exc)

    return products


def _parse_immersive_item(item: dict) -> list[Product]:
    api_url = item.get("serpapi_immersive_product_api")
    api_key = os.environ.get("SERPAPI_KEY", "").strip()
    if not api_url or not api_key:
        return []
    response = requests.get(api_url, params={"api_key": api_key}, timeout=_env_int("SERPAPI_TIMEOUT", 18))
    response.raise_for_status()
    data = response.json()
    product_results = data.get("product_results") or {}
    stores = product_results.get("stores") or []
    image = _first_image(product_results, item)
    fallback_title = product_results.get("title") or item.get("title") or ""

    products = []
    for store in stores[: _env_int("SERPAPI_STORES_PER_PRODUCT", 3)]:
        link = _first_url(store.get("link"))
        price = parse_price(store.get("extracted_price") or store.get("price") or item.get("extracted_price") or item.get("price"))
        title = (store.get("title") or fallback_title).strip()
        if not link or "google." in (urlparse(link).hostname or "") or not title or not price or not image:
            continue
        products.append(
            Product(
                title=title,
                price=price,
                rating=parse_rating(store.get("rating") or item.get("rating")),
                reviews_count=parse_reviews(store.get("reviews") or item.get("reviews")),
                source=_normalize_source(store.get("name") or item.get("source") or link),
                product_link=_strip_tracking(link),
                image_url=image,
                availability=store.get("details", "unknown") or "unknown",
            )
        )
    return products


def _parse_item(item: dict) -> Product | None:
    title = (item.get("title") or "").strip()
    link = _first_url(item.get("link"), item.get("product_link"))
    image = (item.get("thumbnail") or item.get("image") or item.get("serpapi_thumbnail") or "").strip()
    price = parse_price(item.get("extracted_price") or item.get("price"))
    if not title or not link or not image or not price:
        return None
    return Product(
        title=title,
        price=price,
        rating=parse_rating(item.get("rating")),
        reviews_count=parse_reviews(item.get("reviews") or item.get("reviews_count") or item.get("rating_count")),
        source=_normalize_source(item.get("source") or item.get("merchant") or item.get("seller") or link),
        product_link=_strip_tracking(link),
        image_url=image,
        availability=item.get("availability", "unknown") or "unknown",
    )


def _first_image(product_results: dict, item: dict) -> str:
    thumbnails = product_results.get("thumbnails") or []
    if thumbnails:
        return str(thumbnails[0] or "").strip()
    return (item.get("thumbnail") or item.get("image") or item.get("serpapi_thumbnail") or "").strip()


def _first_url(*values: str | None) -> str:
    for value in values:
        url = str(value or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in TRACKING_PARAMS])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def _normalize_source(value: str) -> str:
    text = (value or "").strip().lower()
    host = (urlparse(text).hostname or "").replace("www.", "")
    source = host or text
    aliases = {
        "amazon": "amazon",
        "flipkart": "flipkart",
        "myntra": "myntra",
        "ajio": "ajio",
        "croma": "croma",
        "reliance": "reliance digital",
        "tatacliq": "tatacliq",
        "tata cliq": "tatacliq",
        "nykaa": "nykaa",
        "meesho": "meesho",
        "snapdeal": "snapdeal",
    }
    for needle, label in aliases.items():
        if needle in source:
            return label
    return source or "other"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
