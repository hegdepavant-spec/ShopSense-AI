from urllib.parse import quote_plus

from models.product import Product
from services.provider_utils import (
    collect_json_ld_products,
    fetch_soup,
    product_from_fields,
    text_one,
    attr_one,
    unique_products,
)

PLATFORM = "amazon"
BASE_URL = "https://www.amazon.in"


def search_amazon(query: str, limit: int = 12) -> list[Product]:
    url = f"{BASE_URL}/s?k={quote_plus(query)}"
    soup = fetch_soup(url, PLATFORM)
    if not soup:
        return []

    products = collect_json_ld_products(soup, PLATFORM, BASE_URL)
    cards = soup.select('[data-component-type="s-search-result"]')
    for card in cards:
        title = text_one(card, ["h2 span", "h2 a span", ".a-size-medium.a-color-base.a-text-normal"])
        link = attr_one(card, ["a.a-link-normal.s-no-outline", "h2 a.a-link-normal", "a.a-link-normal[href*='/dp/']"], "href")
        image = attr_one(card, ["img.s-image"], "src")
        price = text_one(card, [".a-price .a-offscreen", ".a-price-whole"])
        rating = text_one(card, ["span.a-icon-alt"])
        reviews = text_one(card, ["span.a-size-base.s-underline-text", "a[href*='customerReviews'] span"])
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
    return unique_products(products, limit)
