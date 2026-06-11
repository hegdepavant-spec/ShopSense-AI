# ShopSense AI

ShopSense AI is a Flask-powered visual shopping assistant: product image search, product URL alternatives, text search, real-time deal ranking, and an AI chat panel.

The product search path uses live SerpAPI Google Shopping data only. It does not generate fake products, fake prices, or placeholder links. When Google Shopping returns comparison URLs, the app uses SerpAPI's immersive product endpoint to extract real merchant store links.

## Features

- Image search with Gemini Vision first, OpenAI Vision fallback, and EasyOCR as secondary support
- Product URL flow that extracts the pasted product once and pins it as result #1
- Text query search through `engine=google_shopping`
- Filtering for listings with image, price, and direct merchant link
- Deduping and weighted ranking by relevance, price, rating, reviews, and seller trust
- AI assistant endpoint that answers only from the returned product data
- SQLite search history
- Responsive browser UI with cards, comparison table, and chat panel

## Structure

```text
shopping_assistant/
|-- app.py
|-- routes/
|   `-- search_routes.py
|-- services/
|   |-- pipeline.py
|   |-- scraper.py
|   `-- database.py
|-- vision/
|   `-- product_vision.py
|-- search/
|   `-- serpapi_shopping.py
|-- ranking/
|   `-- deal_ranker.py
|-- assistant/
|   `-- shopping_assistant.py
|-- models/
|   `-- product.py
`-- templates/
    `-- index.html
```

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set keys in `.env`:

```env
SERPAPI_KEY=your_serpapi_key_here
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

`SERPAPI_KEY` is required. At least one of `GEMINI_API_KEY` or `OPENAI_API_KEY` is needed for image understanding. OCR is optional support only.

## Run

```powershell
python app.py
```

Open `http://localhost:5000`.

## API

`POST /search`

```json
{"query": "wireless headphones"}
```

```json
{"url": "https://www.amazon.in/..."}
```

```json
{"image": "base64_image_payload", "mime_type": "image/jpeg"}
```

`POST /assistant`

```json
{
  "message": "Which is best value?",
  "query": "wireless headphones",
  "products": []
}
```

## Performance Notes

- Product search uses SerpAPI responses and SerpAPI immersive product data only.
- It does not scrape every result page or validate every product URL.
- Pasted URL mode scrapes only the one pasted page so the original product can appear first.
- Search cache is not used for active results; SQLite records history only.
