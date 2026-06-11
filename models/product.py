from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class Product:
    title:         str
    price:         float
    source:        str          # "amazon", "flipkart", "myntra", etc.
    product_link:  str          # real, full URL
    image_url:     str          # real, full image URL
    rating:        Optional[float] = None
    reviews_count: Optional[int]   = None
    relevance_score: float = 0.0
    price_score:     float = 0.0
    trust_score:     float = 0.0
    final_score:     float = 0.0
    validated:       bool = False
    availability:    str = "unknown"

    def to_dict(self):
        data = asdict(self)
        data["platform"] = self.source
        data["product_url"] = self.product_link
        data["reviews"] = self.reviews_count
        return data
