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

PLATFORM = "flipkart"
BASE_URL = "https://www.flipkart.com"


def search_flipkart(query: str, limit: int = 12) -> list[Product]:
    url = f"{BASE_URL}/search?q={quote_plus(query)}"
    soup = fetch_soup(url, PLATFORM)
    if not soup:
        return []

    products = collect_json_ld_products(soup, PLATFORM, BASE_URL)
    cards = soup.select("div[data-id], div._75nlfW, div.slAVV4, div._1sdMkc")
    for card in cards:
        title = (
            text_one(card, [".RG5Slk", ".KzDlHZ", "._4rR01T", ".syl9yP", ".wjcEIp", "a[title]"])
            or attr_one(card, ["a[title]"], "title")
            or attr_one(card, ["img[alt]"], "alt")
        )
        link = attr_one(card, ["a[href*='/p/']", "a[href*='/itm']", "a.k7wcnx", "a.CGtC98", "a.VJA3rP", "a.wjcEIp"], "href")
        image = attr_one(card, ["img"], "src") or attr_one(card, ["img"], "data-src")
        price = text_one(card, [".hZ3P6w", ".Nx9bqj", "._30jeq3", "._1_WHN1", ".oFEPlD"])
        rating = text_one(card, [".CjyrHS", ".XQDdHH", "._3LWZlK"])
        reviews = text_one(card, [".PvbNMB", ".Wphh3N", "._2_R_DZ", ".hGSR34"])
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
