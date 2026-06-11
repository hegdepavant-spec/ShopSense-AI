import logging
import os
import re
import time
from urllib.parse import urlparse

import requests

from models.product import Product
from ranking.deal_ranker import filter_dedupe_rank
from search.serpapi_shopping import search_google_shopping
from services.database import log_search
from services.scraper import clean_for_search, scrape_product_details
from vision.product_vision import identify_product

LOG = logging.getLogger(__name__)


def handle_query(query: str) -> tuple[list[Product], str, str, dict]:
    query = _clean_query(query)
    if not query:
        return [], "", "Empty search query.", {}
    products, meta = _search(query, source="query")
    return products, query, "", meta


def handle_image(image_bytes: bytes, mime_type: str) -> tuple[list[Product], str, str, dict]:
    vision = identify_product(image_bytes, mime_type)
    query = _clean_query(vision.get("query", ""))
    LOG.info("extracted image query=%r provider=%s", query, vision.get("provider"))
    if not query:
        details = " ".join(vision.get("errors") or [])
        message = "Could not identify a shopping product from this image."
        if details:
            message += f" Vision provider details: {details}"
        else:
            message += " Try a clearer product photo with the item centered."
        return [], "", message, {
            "vision": vision
        }
    products, meta = _search(query, source="image")
    meta["vision"] = vision
    return products, query, "", meta


def handle_url(url: str) -> tuple[list[Product], str, str, dict]:
    started = time.perf_counter()
    details = scrape_product_details(
        url,
        prefer_selenium=os.environ.get("URL_SCRAPE_WITH_SELENIUM", "0").lower() in {"1", "true", "yes"},
    )
    title = details.get("title")
    if not title:
        return [], "", (
            "Could not extract a product title from this page. The page may be blocked, private, or require login."
        ), {"elapsed_ms": int((time.perf_counter() - started) * 1000)}

    query = _clean_query(clean_for_search(title))
    if len(query) < 3:
        return [], "", "Product title found, but it was too vague to search.", {}

    original = _product_from_details(details, url)
    products, meta = _search(query, source="url", pinned=original)
    meta["original_product"] = original.to_dict()
    meta["url_extract_ms"] = int((time.perf_counter() - started) * 1000)
    return products, query, "", meta


def expand_short_url(url: str) -> str:
    try:
        response = requests.head(
            url,
            headers={"User-Agent": "Mozilla/5.0 Chrome/124.0 Safari/537.36"},
            allow_redirects=True,
            timeout=8,
        )
        return response.url or url
    except Exception as exc:
        LOG.warning("short URL expansion failed: %s", exc)
        return url


def _search(query: str, source: str, pinned: Product | None = None) -> tuple[list[Product], dict]:
    started = time.perf_counter()
    raw_products = search_google_shopping(query, limit=_env_int("SERPAPI_PAGE_SIZE", 40))
    ranked = filter_dedupe_rank(raw_products, query, limit=_env_int("RESULT_LIMIT", 12), pinned=pinned)
    elapsed = int((time.perf_counter() - started) * 1000)
    LOG.info(
        "cleaned query=%r api_count=%s filtered_ranked_count=%s response_time_ms=%s",
        query,
        len(raw_products),
        len(ranked),
        elapsed,
    )
    log_search(query, source, len(ranked), cache_hit=False)
    return ranked, {"cache_hit": False, "raw_count": len(raw_products), "filtered_count": len(ranked), "elapsed_ms": elapsed}


def _product_from_details(details: dict, requested_url: str) -> Product:
    final_url = details.get("final_url") or requested_url
    return Product(
        title=details.get("title") or "Original pasted product",
        price=float(details.get("price") or 0),
        rating=details.get("rating"),
        reviews_count=details.get("reviews_count"),
        source=details.get("source") or _source_from_url(final_url),
        product_link=final_url,
        image_url=details.get("image_url") or "",
        validated=True,
        availability="original pasted product",
    )


def _source_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").replace("www.", "")
    return host.split(".")[0] or "other"


def _clean_query(query: str) -> str:
    query = re.sub(r"\s+", " ", (query or "").strip())
    return query[:140]


def _product_from_dict(item: dict) -> Product:
    return Product(
        title=item.get("title", ""),
        price=float(item.get("price") or 0),
        source=item.get("source") or item.get("platform", "other"),
        product_link=item.get("product_link") or item.get("product_url", ""),
        image_url=item.get("image_url", ""),
        rating=item.get("rating"),
        reviews_count=item.get("reviews_count") or item.get("reviews"),
        relevance_score=item.get("relevance_score", 0.0),
        price_score=item.get("price_score", 0.0),
        trust_score=item.get("trust_score", 0.0),
        final_score=item.get("final_score", 0.0),
        validated=bool(item.get("validated")),
        availability=item.get("availability", "unknown"),
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
