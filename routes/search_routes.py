import base64
import logging
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from assistant.shopping_assistant import answer as assistant_answer
from services.database import connect, init_db
from services.pipeline import expand_short_url, handle_image, handle_query, handle_url

LOG = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)

PRODUCT_HOSTS = {
    "amazon.in": "amazon",
    "amazon.com": "amazon",
    "amzn.in": "amazon",
    "amzn.to": "amazon",
    "flipkart.com": "flipkart",
    "fkrt.it": "flipkart",
    "myntra.com": "myntra",
    "meesho.com": "meesho",
    "ajio.com": "ajio",
    "nykaa.com": "nykaa",
    "snapdeal.com": "snapdeal",
    "tatacliq.com": "tatacliq",
    "croma.com": "croma",
    "reliancedigital.in": "reliance digital",
    "nike.com": "nike",
    "puma.com": "puma",
}
SHORT_LINK_HOSTS = {"amzn.in", "amzn.to", "fkrt.it"}


def _check_url(value: str):
    """Return (is_product, platform, error)."""
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().replace("www.", "")
        for known_host, platform in PRODUCT_HOSTS.items():
            if known_host in host:
                if len(parsed.path) > 1:
                    return True, platform, None
                return False, None, f"Looks like a {platform} homepage. Paste a product link."
        if parsed.scheme in ("http", "https"):
            return False, None, (
                f"'{host}' is not a supported platform. "
                "Paste a link from a supported shopping site, or use the Search tab."
            )
    except Exception as exc:
        LOG.warning("URL validation failed: %s", exc)
    return False, None, None


@search_bp.route("/search", methods=["POST", "OPTIONS"])
def do_search():
    if request.method == "OPTIONS":
        return "", 204
    return _dispatch_search(request.get_json(silent=True) or {})


@search_bp.route("/url-search", methods=["POST", "OPTIONS"])
def url_search():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or body.get("product_url") or "").strip()
    if not url:
        return jsonify({"error": "Send { url }"}), 400
    return _dispatch_search({"url": url})


@search_bp.route("/image-search", methods=["POST", "OPTIONS"])
def image_search():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    image = (body.get("image") or body.get("image_base64") or "").strip()
    if not image:
        return jsonify({"error": "Send { image }"}), 400
    return _dispatch_search({"image": image, "mime_type": body.get("mime_type", "image/jpeg")})


def _dispatch_search(body: dict):
    products, query, source, err, meta = [], "", "query", "", {}

    try:
        return _dispatch_search_inner(body, products, query, source, err, meta)
    except RuntimeError as exc:
        LOG.warning("search failed: %s", exc)
        return jsonify({"error": str(exc)}), 422


def _dispatch_search_inner(body: dict, products, query, source, err, meta):
    if body.get("url", "").strip():
        raw = body["url"].strip()
        is_product, platform, url_err = _check_url(raw)
        if url_err:
            return jsonify({"error": url_err}), 400

        if is_product:
            host = (urlparse(raw).hostname or "").replace("www.", "")
            if host in SHORT_LINK_HOSTS:
                raw = expand_short_url(raw)
            products, query, err, meta = handle_url(raw)
            source = platform or "url"
            if err:
                return jsonify({
                    "error": err,
                    "tip": "Try copying the product name and using the Search tab.",
                }), 422
        else:
            products, query, err, meta = handle_query(raw)

    elif body.get("image", "").strip():
        try:
            payload = body["image"].split(",", 1)[-1]
            image_bytes = base64.b64decode(payload, validate=True)
            products, query, err, meta = handle_image(image_bytes, body.get("mime_type", "image/jpeg"))
            source = "image"
        except Exception as exc:
            return jsonify({"error": f"Invalid image: {exc}"}), 400

    elif body.get("query", "").strip():
        products, query, err, meta = handle_query(body["query"].strip())
        source = "query"

    else:
        return jsonify({"error": "Send { url }, { query }, or { image }"}), 400

    if err:
        return jsonify({"error": err}), 422

    return jsonify({
        "query": query,
        "search_query": query,
        "source": source,
        "total": len(products),
        "meta": meta,
        "results": [product.to_dict() for product in products],
    })


@search_bp.route("/assistant", methods=["POST", "OPTIONS"])
def chat_assistant():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Send { message }"}), 400
    products = body.get("products") or []
    query = body.get("query") or ""
    history_items = body.get("history") or []
    reply = assistant_answer(message, products, query=query, history=history_items)
    return jsonify({"reply": reply})


@search_bp.route("/history", methods=["GET"])
def history():
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT query, source, result_count, cache_hit, created_at
            FROM search_history
            ORDER BY created_at DESC
            LIMIT 25
            """
        ).fetchall()
    return jsonify({"history": [dict(row) for row in rows]})
