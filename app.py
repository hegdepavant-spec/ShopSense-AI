import logging
import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException


def _load_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        print("[env] .env loaded")


def create_app():
    _load_env()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from routes.search_routes import search_bp
    from services.database import init_db

    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    try:
        init_db()
    except Exception:
        logging.exception("database initialization failed")
        raise

    app.register_blueprint(search_bp)

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        app.logger.exception("unhandled backend error: %s", exc)
        return jsonify({"error": "Backend error. Check the Flask terminal logs for details."}), 500

    @app.route("/")
    def index():
        return send_from_directory("templates", "index.html")

    @app.route("/health")
    def health():
        return {
            "ok": True,
            "serpapi": bool(os.environ.get("SERPAPI_KEY", "").strip()),
            "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
            "openai": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "search_engine": "serpapi google_shopping",
        }

    serpapi = os.environ.get("SERPAPI_KEY", "")
    gemini = os.environ.get("GEMINI_API_KEY", "")
    print("\n" + "-" * 45)
    print(f"  {'OK' if serpapi and 'your_' not in serpapi else 'NO'}  SerpAPI  - {'active' if serpapi and 'your_' not in serpapi else 'NOT SET - add to .env'}")
    openai = os.environ.get("OPENAI_API_KEY", "")
    print(f"  {'OK' if gemini and 'your_' not in gemini else '--'}  Gemini   - {'active for vision/chat' if gemini and 'your_' not in gemini else 'not set'}")
    print(f"  {'OK' if openai and 'your_' not in openai else '--'}  OpenAI   - {'active for vision/chat fallback' if openai and 'your_' not in openai else 'not set'}")
    print(f"  DB       - {os.environ.get('SHOPPING_DB_PATH', 'shopping_cache.sqlite3')}")
    print("-" * 45 + "\n")

    return app


if __name__ == "__main__":
    app = create_app()
    print("ShopSense AI -> http://localhost:5000\n")
    app.run(port=5000, debug=True)
