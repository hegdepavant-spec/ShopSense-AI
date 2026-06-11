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

PLATFORM = "ajio"
BASE_URL = "https://www.ajio.com"


def search_ajio(query: str, limit: int = 12) -> list[Product]:
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
    cards = soup.select(".item, .rilrtl-products-list__item, .product, [class*='product'], a[href*='/p/']")
    for card in cards:
        brand = text_one(card, [".brand", ".brand-name"])
        name = text_one(card, [".nameCls", ".prod-name", ".product-name", "a[title]"]) or attr_one(card, ["a[title]"], "title")
        title = f"{brand} {name}".strip() if brand and brand.lower() not in name.lower() else name
        link = attr_one(card, ["a[href*='/p/']", "a[href]"], "href")
        image = attr_one(card, ["img"], "src") or attr_one(card, ["img"], "data-src")
        price = text_one(card, [".price", ".prod-sp", ".discounted-price", "[class*='price']"])
        product = product_from_fields(
            title=title,
            price=price,
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
        f"{BASE_URL}/api/search"
        f"?fields=FULL&currentPage=0&pageSize=45&format=json"
        f"&query={quote_plus(query)}%3Arelevance&sortBy=relevance&text={quote_plus(query)}"
    )
    data = fetch_json(
        api_url,
        PLATFORM,
        headers={
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/search/?text={quote_plus(query)}",
        },
    )
    if not data:
        return []

    raw_products = data.get("products") or data.get("results") or _find_product_lists(data)
    products = []
    for item in raw_products if isinstance(raw_products, list) else []:
        title = item.get("name") or item.get("title") or item.get("productName")
        brand = item.get("brandName") or item.get("brand")
        if brand and title and brand.lower() not in title.lower():
            title = f"{brand} {title}"
        price = _price(item)
        image = item.get("images", [{}])[0].get("url") if isinstance(item.get("images"), list) and item.get("images") else item.get("imageUrl")
        link = item.get("url") or item.get("pdpUrl") or item.get("productUrl")
        product = product_from_fields(
            title=title,
            price=price,
            image_url=image,
            product_url=link,
            platform=PLATFORM,
            base_url=BASE_URL,
            availability="in_stock" if item.get("stock", {}).get("stockLevelStatus") == "inStock" else "unknown",
        )
        if product:
            products.append(product)
    return products


def _price(item: dict):
    price = item.get("price") or item.get("priceData") or {}
    if isinstance(price, dict):
        return price.get("value") or price.get("formattedValue") or price.get("sellingPrice")
    return price


def _find_product_lists(data) -> list[dict]:
    found = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if {"name", "url"} & set(item) and ("price" in item or "priceData" in item):
                found.append(item)
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return found
