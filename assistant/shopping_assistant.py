import json
import logging
import os

import requests

LOG = logging.getLogger(__name__)
GEMINI_TEXT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def answer(message: str, products: list[dict], query: str = "", history: list[dict] | None = None) -> str:
    context = _compact_products(products)
    prompt = (
        "You are an AI shopping assistant. Use only the product data provided. "
        "Do not invent products, prices, ratings, stock, coupons, or links. "
        "Explain recommendations by value, price, rating, reviews, and seller trust. "
        f"Search query: {query}\nProducts JSON:\n{json.dumps(context, ensure_ascii=False)}\n"
        f"Conversation history:\n{json.dumps((history or [])[-8:], ensure_ascii=False)}\n"
        f"User question: {message}"
    )
    return _gemini_answer(prompt) or _openai_answer(prompt) or _local_answer(context)


def _compact_products(products: list[dict]) -> list[dict]:
    compact = []
    for idx, product in enumerate(products[:12], start=1):
        compact.append(
            {
                "rank": idx,
                "title": product.get("title"),
                "price": product.get("price"),
                "rating": product.get("rating"),
                "reviews_count": product.get("reviews_count") or product.get("reviews"),
                "source": product.get("source") or product.get("platform"),
                "link": product.get("product_link") or product.get("product_url"),
                "final_score": product.get("final_score"),
            }
        )
    return compact


def _gemini_answer(prompt: str) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or "your_" in key:
        return ""
    try:
        model = os.environ.get("GEMINI_TEXT_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.25, "maxOutputTokens": 420}}
        response = requests.post(GEMINI_TEXT_URL.format(model=model), params={"key": key}, json=payload, timeout=12)
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:
        LOG.warning("Gemini assistant failed: %s", exc)
        return ""


def _openai_answer(prompt: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or "your_" in key:
        return ""
    try:
        payload = {"model": os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini"), "messages": [{"role": "user", "content": prompt}], "temperature": 0.25, "max_tokens": 420}
        response = requests.post(OPENAI_CHAT_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload, timeout=12)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        LOG.warning("OpenAI assistant failed: %s", exc)
        return ""


def _local_answer(products: list[dict]) -> str:
    if not products:
        return "I do not have product results to compare yet. Run a real-time search first."
    best = products[0]
    cheapest = min(products, key=lambda p: float(p.get("price") or 10**12))
    rated = max(products, key=lambda p: float(p.get("rating") or 0))
    return (
        f"Best overall from the current live results is #{best['rank']}: {best['title']} from {best['source']} at Rs {float(best.get('price') or 0):,.0f}. "
        f"The lowest-price option is #{cheapest['rank']} at Rs {float(cheapest.get('price') or 0):,.0f}. "
        f"The highest-rated visible option is #{rated['rank']} with rating {rated.get('rating') or 'not available'}. "
        "I am using only the products returned by the live search results shown on this page."
    )
