import logging
import math
import re
from functools import lru_cache

from models.product import Product

LOG = logging.getLogger(__name__)

STOP_WORDS = {
    "the", "a", "an", "for", "and", "or", "with", "in", "of", "to", "at", "by",
    "from", "best", "buy", "online", "price", "new", "latest", "original",
}

CATEGORY_ALIASES = {
    "phone": {"phone", "smartphone", "mobile", "iphone", "android", "galaxy", "oneplus", "redmi", "realme"},
    "laptop": {"laptop", "notebook", "macbook", "thinkpad", "vivobook", "ideapad"},
    "headphones": {"headphone", "headphones", "earbuds", "earphones", "headset", "tws"},
    "shoes": {"shoe", "shoes", "sneaker", "sneakers", "trainer", "trainers", "footwear"},
    "shirt": {"shirt", "tshirt", "t-shirt", "tee", "polo"},
    "watch": {"watch", "smartwatch", "timepiece"},
    "camera": {"camera", "dslr", "mirrorless"},
    "tv": {"tv", "television", "smart tv"},
}

CATEGORY_NEGATIVES = {
    "phone": {"shirt", "tshirt", "t-shirt", "tee", "case", "cover", "skin", "sticker", "poster", "toy"},
    "laptop": {"bag", "sleeve", "skin", "sticker", "stand", "charger"},
    "headphones": {"case", "cover", "tips", "cable"},
    "shoes": {"shirt", "tshirt", "bag", "lace"},
    "watch": {"strap", "band", "charger", "case"},
}

TRUSTED_SOURCES = {
    "amazon": 0.92,
    "flipkart": 0.92,
    "myntra": 0.88,
    "ajio": 0.86,
    "nykaa": 0.84,
    "tata cliq": 0.86,
    "tatacliq": 0.86,
    "snapdeal": 0.72,
    "meesho": 0.70,
    "croma": 0.86,
    "reliance digital": 0.84,
    "nike": 0.90,
    "puma": 0.88,
    "official": 0.95,
}


def filter_and_rank(products: list[Product], query: str, limit: int = 10) -> list[Product]:
    if not products:
        return []

    semantic_scores = semantic_similarities(query, [product.title for product in products])
    scored = []
    for product in products:
        relevance = combined_relevance(query, product.title, semantic_scores.get(product.title))
        if not category_matches(query, product.title):
            continue
        if relevance < min_similarity(query):
            continue
        product.relevance_score = round(relevance, 4)
        scored.append(product)

    if not scored:
        return sorted(
            products,
            key=lambda p: p.relevance_score,
            reverse=True,
        )[:limit]

    prices = [p.price for p in scored if p.price and p.price > 0]
    min_price, max_price = min(prices), max(prices)
    max_reviews = max([p.reviews_count or 0 for p in scored] or [0])

    for product in scored:
        normalized_rating = max(0.0, min((product.rating or 3.5) / 5.0, 1.0))
        normalized_reviews = math.log((product.reviews_count or 0) + 1) / math.log(max_reviews + 1) if max_reviews > 0 else 0.35
        product.price_score = round(price_competitiveness(product.price, min_price, max_price), 4)
        product.trust_score = round(source_trust(product.source), 4)
        product.final_score = round(
            (product.relevance_score * 0.65)
            + (normalized_rating * 0.20)
            + (normalized_reviews * 0.10)
            + (product.price_score * 0.05),
            4,
        )

    return sorted(scored, key=lambda p: (p.final_score, p.trust_score, p.reviews_count or 0), reverse=True)[:limit]


def min_similarity(query: str) -> float:
    terms = meaningful_terms(query)

    if len(terms) <= 1:
        return 0.20

    if len(terms) <= 3:
        return 0.25

    return 0.30


def meaningful_terms(text: str) -> list[str]:
    return [
        w.lower()
        for w in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9+\-]*", text)
        if len(w) > 1 and w.lower() not in STOP_WORDS
    ]


def category_matches(query: str, title: str) -> bool:
    base_q_terms = set(meaningful_terms(query))
    q_terms = expand_aliases(base_q_terms)
    title_terms = set(meaningful_terms(title))

    if not base_q_terms:
        return True

    overlap = len(q_terms & title_terms)

    required = max(1, int(len(base_q_terms) * 0.4))

    return overlap >= required


def relevance_score(query: str, title: str) -> float:
    return combined_relevance(query, title, semantic_similarity(query, title))


def combined_relevance(query: str, title: str, semantic: float | None = None) -> float:
    semantic = lexical_similarity(query, title) if semantic is None else semantic
    lexical = lexical_similarity(query, title)
    return max(semantic, (semantic * 0.72) + (lexical * 0.28))


def lexical_similarity(query: str, title: str) -> float:
    base_q = set(meaningful_terms(query))
    q = expand_aliases(base_q)
    t = set(meaningful_terms(title))
    if not q or not t:
        return 0.0
    overlap = len(q & t) / max(1, len(base_q))
    jaccard = len(q & t) / len(q | t)
    phrase_bonus = 0.08 if " ".join(meaningful_terms(query)[:2]) in title.lower() else 0.0
    category_bonus = 0.28 if category_matches(query, title) and q & t else 0.0
    return min(1.0, (overlap * 0.66) + (jaccard * 0.18) + category_bonus + phrase_bonus)


def expand_aliases(terms: set[str]) -> set[str]:
    expanded = set(terms)
    for aliases in CATEGORY_ALIASES.values():
        if terms & aliases:
            expanded.update(alias for alias in aliases if " " not in alias)
    return expanded


def semantic_similarity(query: str, title: str) -> float:
    scores = semantic_similarities(query, [title])
    return scores.get(title, lexical_similarity(query, title))


def semantic_similarities(query: str, titles: list[str]) -> dict[str, float]:
    unique_titles = list(dict.fromkeys(titles))
    model = sentence_model()
    if not model:
        return {title: lexical_similarity(query, title) for title in unique_titles}
    try:
        from sentence_transformers import util

        texts = [query] + unique_titles
        embeddings = model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
        scores = util.cos_sim(embeddings[0], embeddings[1:]).cpu().tolist()[0]
        return {title: float(score) for title, score in zip(unique_titles, scores)}
    except Exception as exc:
        LOG.warning("semantic similarity failed, using lexical fallback: %s", exc)
        return {title: lexical_similarity(query, title) for title in unique_titles}


@lru_cache(maxsize=1)
def sentence_model():
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as exc:
        LOG.warning("sentence-transformers unavailable, using lexical fallback: %s", exc)
        return None


def price_competitiveness(price: float, min_price: float, max_price: float) -> float:
    if not price or min_price <= 0:
        return 0.0
    if max_price <= min_price:
        return 1.0
    return max(0.0, min(1.0, 1.0 - ((price - min_price) / (max_price - min_price))))


def source_trust(source: str) -> float:
    s = (source or "").lower()
    for name, score in TRUSTED_SOURCES.items():
        if name in s:
            return score
    return 0.62
