from urllib.parse import quote_plus

from models.product import Product
from services.provider_utils import (
    collect_json_ld_products,
    fetch_json,
    fetch_rendered_soup,
    fetch_soup,
    product_from_fields,
    text_one,
    attr_one,
    unique_products,
)

PLATFORM = "croma"
BASE_URL = "https://www.croma.com"


def search_croma(query: str, limit: int = 12) -> list[Product]:
    products = _api_products(query)
    if products:
        return unique_products(products, limit)

    url = f"{BASE_URL}/search/?text={quote_plus(query)}"
    soup = fetch_soup(url, PLATFORM)
    if not soup:
        soup = fetch_rendered_soup(url, PLATFORM)
    if not soup:
        return []

    products = collect_json_ld_products(soup, PLATFORM, BASE_URL)
    cards = soup.select(".product-item, .cp-product, .product-card, li[class*='product'], [data-testid*='product'], a[href*='/p/']")
    for card in cards:
        title = text_one(card, [".product-title", ".product-name", "h3", "h2", "a[title]"]) or attr_one(card, ["a[title]"], "title")
        link = attr_one(card, ["a[href*='/p/']", "a[href]"], "href")
        image = attr_one(card, ["img"], "src") or attr_one(card, ["img"], "data-src")
        price = text_one(card, [".amount", ".new-price", ".pdpPrice", ".price", "[class*='price']"])
        rating = text_one(card, [".rating-text", "[class*='rating']"])
        product = product_from_fields(
            title=title,
            price=price,
            rating=rating,
            image_url=image,
            product_url=link,
            platform=PLATFORM,
            base_url=BASE_URL,
        )
        if product:
            products.append(product)
    return unique_products(products, limit)


def _api_products(query: str) -> list[Product]:
    api_url = (
        "https://api.croma.com/searchservices/v1/search"
        f"?query={quote_plus(query)}%3Arelevance&channel=WEB&channelCode=400049&spellOpt=DEFAULT"
    )
    data = fetch_json(
        api_url,
        PLATFORM,
        headers={
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/search/?text={quote_plus(query)}",
            "Ocp-Apim-Subscription-Key": "6f127cd7c2f8469b94f05852c649b924",
        },
    )
    if not data:
        return []
    raw_products = data.get("products") if isinstance(data.get("products"), list) else _find_product_lists(data)
    products = []
    for item in raw_products:
        title = item.get("name") or item.get("title") or item.get("productName")
        price = (
            item.get("price", {}).get("value")
            if isinstance(item.get("price"), dict)
            else item.get("price") or item.get("sellingPrice") or item.get("mrp")
        )
        image = item.get("image") or item.get("imageUrl") or item.get("plpImage") or _first_url(item.get("images"))
        link = item.get("url") or item.get("pdpUrl") or item.get("productUrl")
        rating = item.get("averageRating") or item.get("rating")
        reviews = item.get("numberOfReviews") or item.get("reviews")
        product = product_from_fields(
            title=title,
            price=price,
            rating=rating,
            reviews=reviews,
            image_url=image,
            product_url=link,
            platform=PLATFORM,
            base_url=BASE_URL,
        )
        if product:
            products.append(product)
    return products


def _find_product_lists(data) -> list[dict]:
    found = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if {"name", "url"} & set(item) and {"price", "sellingPrice", "mrp"} & set(item):
                found.append(item)
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return found


def _first_url(value) -> str:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for key in ("url", "imageUrl", "src"):
                    if item.get(key):
                        return item[key]
            elif isinstance(item, str):
                return item
    return ""
