import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("SHOPPING_DB_PATH", BASE_DIR / "shopping_cache.sqlite3"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "21600"))


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_cache (
                query_key TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_key TEXT NOT NULL,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                source TEXT,
                product_link TEXT NOT NULL,
                image_url TEXT,
                rating REAL,
                reviews_count INTEGER,
                relevance_score REAL,
                trust_score REAL,
                final_score REAL,
                validated INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                source TEXT NOT NULL,
                result_count INTEGER NOT NULL,
                cache_hit INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )


def cache_key(query: str, source: str = "query") -> str:
    version = os.environ.get("CACHE_VERSION", "shopping-v2")
    return f"{version}:{source}:{' '.join(query.lower().strip().split())}"


def get_cached_results(query: str, source: str = "query"):
    init_db()
    key = cache_key(query, source)
    cutoff = int(time.time()) - CACHE_TTL_SECONDS
    payload = None
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM search_cache WHERE query_key = ? AND created_at >= ?",
            (key, cutoff),
        ).fetchone()
        if row:
            payload = json.loads(row["payload"])
    if payload is not None:
        log_search(query, source, len(payload), cache_hit=True)
    return payload


def save_results(query: str, source: str, products: list[dict]) -> None:
    init_db()
    key = cache_key(query, source)
    now = int(time.time())
    payload = json.dumps(products, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO search_cache(query_key, query, source, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(query_key) DO UPDATE SET
                query = excluded.query,
                source = excluded.source,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (key, query, source, payload, now),
        )
        conn.execute("DELETE FROM products WHERE query_key = ?", (key,))
        for p in products:
            conn.execute(
                """
                INSERT INTO products(
                    query_key, title, price, source, product_link, image_url, rating,
                    reviews_count, relevance_score, trust_score, final_score,
                    validated, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    p.get("title", ""),
                    p.get("price", 0),
                    p.get("source", ""),
                    p.get("product_link", ""),
                    p.get("image_url", ""),
                    p.get("rating"),
                    p.get("reviews_count"),
                    p.get("relevance_score", 0),
                    p.get("trust_score", 0),
                    p.get("final_score", 0),
                    1 if p.get("validated") else 0,
                    now,
                ),
            )
    log_search(query, source, len(products), cache_hit=False)


def log_search(query: str, source: str, result_count: int, cache_hit: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO search_history(query, source, result_count, cache_hit, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (query, source, result_count, 1 if cache_hit else 0, int(time.time())),
        )
