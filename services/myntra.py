import json
from urllib.parse import quote_plus

from models.product import Product
from services.provider_utils import (
    collect_json_ld_products,
    fetch_soup,
    product_from_fields,
    text_one,
    attr_one,
    absolute_url,
    unique_products,
)

PLATFORM = "myntra"
BASE_URL = "https://www.myntra.com"


def search_myntra(query: str, limit: int = 12) -> list[Product]:
    path = quote_plus(query).replace("+", "-")
    url = f"{BASE_URL}/{path}?rawQuery={quote_plus(query)}"
    soup = fetch_soup(url, PLATFORM)
    if not soup:
        return []

    products = _products_from_embedded_state(str(soup))
    if products:
        return unique_products(products, limit)

    products = collect_json_ld_products(soup, PLATFORM, BASE_URL)
    cards = soup.select("li.product-base, .product-base, [class*='product']")
    for card in cards:
        brand = text_one(card, [".product-brand", ".brand"])
        name = text_one(card, [".product-product", ".product-name", "h3", "h4"])
        title = f"{brand} {name}".strip() if brand and brand.lower() not in name.lower() else name
        link = attr_one(card, ["a[href]"], "href")
        image = attr_one(card, ["img"], "src") or attr_one(card, ["img"], "data-src")
        price = text_one(card, [".product-discountedPrice", ".product-price", "[class*='price']"])
        rating = text_one(card, [".product-ratingsContainer", "[class*='rating']"])
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


def _products_from_embedded_state(html: str) -> list[Product]:
    marker = "window.__myx = "
    start = html.find(marker)
    if start == -1:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start + len(marker):])
    except Exception:
        return []

    raw_products = (
        data.get("searchData", {})
        .get("results", {})
        .get("products", [])
    )
    products = []
    for item in raw_products:
        landing = item.get("landingPageUrl") or ""
        title = item.get("productName") or item.get("product") or item.get("additionalInfo")
        image = item.get("searchImage") or _image_from_list(item.get("images", []))
        available = any(info.get("available") for info in item.get("inventoryInfo", []) if isinstance(info, dict))
        product = Product(
            title=title or "",
            price=float(item.get("price") or item.get("mrp") or 0),
            rating=float(item.get("rating") or 0) or None,
            reviews_count=int(item.get("ratingCount") or 0) or None,
            source="Myntra",
            product_link=absolute_url(landing, BASE_URL),
            image_url=absolute_url(image, BASE_URL).replace("http://", "https://"),
            availability="in_stock" if available else "unknown",
        )
        if product.title and product.price and product.product_link.startswith(BASE_URL):
            products.append(product)
    return products


def _image_from_list(images: list[dict]) -> str:
    for image in images:
        if isinstance(image, dict) and image.get("src"):
            return image["src"]
    return ""
