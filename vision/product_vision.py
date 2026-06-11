import base64
import json
import logging
import os
import re

import requests

LOG = logging.getLogger(__name__)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def identify_product(image_bytes: bytes, mime_type: str) -> dict:
    gemini = _gemini_identify(image_bytes, mime_type)
    if gemini.get("query"):
        LOG.info("extracted image query via gemini: %s", gemini["query"])
        return gemini
    openai = _openai_identify(image_bytes, mime_type)
    if openai.get("query"):
        LOG.info("extracted image query via openai: %s", openai["query"])
        return openai
    ocr_terms = _ocr_terms(image_bytes)
    if ocr_terms:
        LOG.info("OCR support terms found: %s", ", ".join(ocr_terms[:5]))
    return {
        "query": "",
        "category": "",
        "brand": "",
        "attributes": {},
        "ocr_terms": ocr_terms,
        "errors": [err for err in (gemini.get("error"), openai.get("error")) if err],
    }


def _gemini_identify(image_bytes: bytes, mime_type: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or "your_" in key:
        return {"error": "Gemini key is missing."}
    model = os.environ.get("GEMINI_VISION_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"
    optimized_bytes, optimized_mime = _optimize_image_for_vision(image_bytes)
    payload = {
        "contents": [{"parts": [{"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}, {"text": _vision_prompt()}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 220},
    }
    payload["contents"][0]["parts"][0]["inline_data"] = {
        "mime_type": optimized_mime,
        "data": base64.b64encode(optimized_bytes).decode("ascii"),
    }
    try:
        response = _post_with_retry(
            GEMINI_URL.format(model=model),
            params={"key": key},
            json=payload,
            timeout=_env_int("GEMINI_VISION_TIMEOUT", 28),
        )
        if response.status_code >= 400:
            return {"error": f"Gemini {model} HTTP {response.status_code}: {response.text[:220]}"}
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_jsonish(text, "gemini")
    except Exception as exc:
        LOG.warning("Gemini vision failed: %s", exc)
        return {"error": f"Gemini vision failed: {exc}"}


def _openai_identify(image_bytes: bytes, mime_type: str) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or "your_" in key:
        return {"error": "OpenAI key is missing."}
    image = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": [{"type": "text", "text": _vision_prompt()}, {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image}"}}]}],
        "temperature": 0,
        "max_tokens": 180,
    }
    try:
        response = requests.post(OPENAI_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload, timeout=14)
        if response.status_code >= 400:
            return {"error": f"OpenAI HTTP {response.status_code}: {response.text[:220]}"}
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return _parse_jsonish(text, "openai")
    except Exception as exc:
        LOG.warning("OpenAI vision failed: %s", exc)
        return {"error": f"OpenAI vision failed: {exc}"}


def _post_with_retry(url: str, **kwargs):
    last_exc = None
    for attempt in range(_env_int("GEMINI_VISION_RETRIES", 2)):
        try:
            return requests.post(url, **kwargs)
        except requests.Timeout as exc:
            last_exc = exc
            LOG.warning("Gemini vision timeout on attempt %s: %s", attempt + 1, exc)
    raise last_exc


def _optimize_image_for_vision(image_bytes: bytes) -> tuple[bytes, str]:
    try:
        from io import BytesIO
        from PIL import Image

        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((_env_int("VISION_MAX_IMAGE_EDGE", 1024), _env_int("VISION_MAX_IMAGE_EDGE", 1024)))
        out = BytesIO()
        image.save(out, format="JPEG", quality=_env_int("VISION_JPEG_QUALITY", 82), optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception as exc:
        LOG.info("image optimization skipped: %s", exc)
        return image_bytes, "image/jpeg"


def _ocr_terms(image_bytes: bytes) -> list[str]:
    try:
        import easyocr
        import numpy as np
        from PIL import Image
        from io import BytesIO

        image = np.array(Image.open(BytesIO(image_bytes)).convert("RGB"))
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        results = reader.readtext(image, detail=0, paragraph=False)
        return [term.strip() for term in results if len(term.strip()) > 1]
    except Exception as exc:
        LOG.info("OCR support unavailable or found nothing: %s", exc)
        return []


def _vision_prompt() -> str:
    return (
        "Visually identify the shopping product. OCR is only supporting evidence. "
        "Return strict JSON with keys: query, category, brand, color, product_type, model, style. "
        "The query must be a concise Google Shopping search query using visible brand/model/type/color/style. "
        "Do not invent a brand or model when not visible. Return raw JSON only, with no markdown fences."
    )


def _parse_jsonish(text: str, provider: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    raw = match.group(0) if match else text
    try:
        data = json.loads(raw)
    except Exception:
        data = {"query": text}
    query = re.sub(r"\s+", " ", str(data.get("query") or "")).strip(" \"'")
    if not query and isinstance(data, dict):
        pieces = [
            str(data.get(key) or "").strip()
            for key in ("brand", "model", "color", "product_type", "style", "category")
            if data.get(key)
        ]
        query = " ".join(dict.fromkeys(pieces))
    data["query"] = query[:120]
    if any(marker in data["query"] for marker in ("```", "{", "}")):
        data["query"] = ""
    data["provider"] = provider
    return data


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
