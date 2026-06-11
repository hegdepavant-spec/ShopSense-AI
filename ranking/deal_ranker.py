import logging
import math
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from models.product import Product
from services.relevance import category_matches, lexical_similarity, meaningful_terms, source_trust

LOG = logging.getLogger(__name__)
SUSPICIOUS_TERMS = {"replica", "copy", "first copy", "refurbished", "renewed", "compatible", "skin", "cover", "case", "sticker"}
COLORS = {"black", "white", "red", "blue", "green", "yellow", "pink", "purple", "brown", "grey", "gray", "silver", "gold", "orange", "beige", "cream"}


def filter_dedupe_rank(products: list[Product], query: str, limit: int = 12, pinned: Product | None = None) -> list[Product]:
    before = len(products)
    filtered = [p for p in products if _is_candidate(p, query)]
    LOG.info("filtering query=%r before=%s after=%s", query, before, len(filtered))
    ranked = _rank(_dedupe(filtered), query)
    if pinned:
        pinned.relevance_score = 1.0
        pinned.trust_score = source_trust(pinned.source)
        pinned.final_score = 1.0
        pinned.validated = True
        ranked = [p for p in ranked if _canonical_url(p.product_link) != _canonical_url(pinned.product_link)]
        return [pinned] + ranked[: max(0, limit - 1)]
    return ranked[:limit]


def _is_candidate(product: Product, query: str) -> bool:
    if not product.title or not product.product_link or not product.image_url:
        return False
    if not product.price or product.price <= 0:
        return False
    lowered = product.title.lower()
    if any(term in lowered for term in SUSPICIOUS_TERMS) and not any(term in query.lower() for term in SUSPICIOUS_TERMS):
        return False
    if _color_conflict(query, product.title):
        return False
    score = lexical_similarity(query, product.title)
    return category_matches(query, product.title) and score >= _min_relevance(query)


def _rank(products: list[Product], query: str) -> list[Product]:
    if not products:
        return []
    prices = [p.price for p in products if p.price and p.price > 0]
    min_price, max_price = min(prices), max(prices)
    max_reviews = max([p.reviews_count or 0 for p in products] or [0])
    for product in products:
        relevance = lexical_similarity(query, product.title)
        rating = max(0.0, min((product.rating or 3.6) / 5.0, 1.0))
        reviews = math.log((product.reviews_count or 0) + 1) / math.log(max_reviews + 1) if max_reviews else 0.35
        price = _price_score(product.price, min_price, max_price)
        trust = source_trust(product.source)
        product.relevance_score = round(relevance, 4)
        product.price_score = round(price, 4)
        product.trust_score = round(trust, 4)
        product.final_score = round((relevance * 0.42) + (price * 0.24) + (rating * 0.16) + (reviews * 0.10) + (trust * 0.08), 4)
    return sorted(products, key=lambda p: (p.final_score, p.trust_score, p.rating or 0, p.reviews_count or 0), reverse=True)


def _dedupe(products: list[Product]) -> list[Product]:
    by_url: dict[str, Product] = {}
    for product in products:
        key = _canonical_url(product.product_link)
        existing = by_url.get(key)
        if not existing or _duplicate_score(product) > _duplicate_score(existing):
            by_url[key] = product
    groups: list[list[Product]] = []
    for product in by_url.values():
        title_key = " ".join(meaningful_terms(product.title)[:12])
        for group in groups:
            sample = " ".join(meaningful_terms(group[0].title)[:12])
            if _same_listing(title_key, sample):
                group.append(product)
                break
        else:
            groups.append([product])
    return [max(group, key=_duplicate_score) for group in groups]


def _duplicate_score(product: Product) -> float:
    return (product.rating or 3.5) + min(product.reviews_count or 0, 5000) / 5000 + source_trust(product.source)


def _same_listing(a: str, b: str) -> bool:
    a_terms = set(a.split())
    b_terms = set(b.split())
    if not a_terms or not b_terms:
        return False
    overlap = len(a_terms & b_terms) / min(len(a_terms), len(b_terms))
    return overlap >= 0.78 and SequenceMatcher(None, a, b).ratio() >= 0.86


def _color_conflict(query: str, title: str) -> bool:
    query_colors = set(meaningful_terms(query)) & COLORS
    if not query_colors:
        return False
    title_colors = set(meaningful_terms(title)) & COLORS
    return bool(title_colors and not (query_colors & title_colors))


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return re.sub(r"/+$", "", f"{parsed.scheme}://{parsed.netloc}{parsed.path}".lower())


def _price_score(price: float, min_price: float, max_price: float) -> float:
    if max_price <= min_price:
        return 1.0
    return max(0.0, min(1.0, 1.0 - ((price - min_price) / (max_price - min_price))))


def _min_relevance(query: str) -> float:
    terms = meaningful_terms(query)
    if len(terms) <= 2:
        return 0.18
    if len(terms) <= 4:
        return 0.23
    return 0.28
