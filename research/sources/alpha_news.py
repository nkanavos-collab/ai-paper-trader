"""
Multi-source news aggregator.
Sources (each optional, graceful fallback if no key):
  1. Alpha Vantage News Sentiment API (free: 25 calls/day)
  2. NewsAPI.org (free: 1000 calls/day)
Both keys optional — set in .env: ALPHA_VANTAGE_KEY, NEWSAPI_KEY
"""
import logging
import time
from datetime import datetime, timezone
import requests

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 1800  # 30 min


def _av_news(symbol: str, api_key: str) -> list[dict]:
    """Alpha Vantage News Sentiment — returns Bloomberg/Reuters/Seeking Alpha etc."""
    try:
        url = "https://www.alphavantage.co/query"
        resp = requests.get(url, params={
            "function": "NEWS_SENTIMENT",
            "tickers":  symbol,
            "limit":    50,
            "sort":     "LATEST",
            "apikey":   api_key,
        }, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        feed = data.get("feed", [])
        articles = []
        for item in feed:
            ticker_sentiments = item.get("ticker_sentiment", [])
            relevance = next(
                (float(t.get("relevance_score", 0))
                 for t in ticker_sentiments if t.get("ticker") == symbol),
                0.0,
            )
            if relevance < 0.3:  # filter low relevance
                continue
            # AV sentiment: -1 to 1 (Bearish → Bullish)
            av_label = item.get("overall_sentiment_label", "")
            av_score = float(item.get("overall_sentiment_score", 0))
            articles.append({
                "title":     item.get("title", ""),
                "source":    item.get("source", ""),
                "url":       item.get("url", ""),
                "date":      str(item.get("time_published", ""))[:10],
                "sentiment": round(av_score, 3),
                "origin":    "alpha_vantage",
            })
        log.info("%s Alpha Vantage news: %d articles", symbol, len(articles))
        return articles
    except Exception as exc:
        log.warning("%s Alpha Vantage failed: %s", symbol, exc)
        return []


def _newsapi_news(symbol: str, company_name: str, api_key: str) -> list[dict]:
    """NewsAPI — Reuters, CNBC, MarketWatch, AP, FT, etc."""
    try:
        query = f'"{symbol}" OR "{company_name.split()[0]}"' if company_name else symbol
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":         query,
                "apiKey":    api_key,
                "sortBy":    "publishedAt",
                "pageSize":  30,
                "language":  "en",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        articles_raw = resp.json().get("articles", [])
        articles = []
        for item in articles_raw:
            title = item.get("title", "")
            if not title or title == "[Removed]":
                continue
            articles.append({
                "title":     title,
                "source":    item.get("source", {}).get("name", ""),
                "url":       item.get("url", ""),
                "date":      str(item.get("publishedAt", ""))[:10],
                "sentiment": 0.0,  # will be scored by caller
                "origin":    "newsapi",
            })
        log.info("%s NewsAPI: %d articles", symbol, len(articles))
        return articles
    except Exception as exc:
        log.warning("%s NewsAPI failed: %s", symbol, exc)
        return []


def get_extra_news(symbol: str, company_name: str = "") -> dict:
    """
    Fetch news from Alpha Vantage and/or NewsAPI if keys are configured.
    Returns empty result gracefully if neither key is set.
    """
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {
        "symbol":    symbol,
        "available": False,
        "articles":  [],
        "sources":   [],
    }

    from config import ALPHA_VANTAGE_KEY, NEWSAPI_KEY

    all_articles: list[dict] = []

    if ALPHA_VANTAGE_KEY:
        av = _av_news(symbol, ALPHA_VANTAGE_KEY)
        if av:
            all_articles.extend(av)
            result["sources"].append(f"Alpha Vantage ({len(av)})")

    if NEWSAPI_KEY:
        na = _newsapi_news(symbol, company_name, NEWSAPI_KEY)
        if na:
            all_articles.extend(na)
            result["sources"].append(f"NewsAPI ({len(na)})")

    if not all_articles:
        result["error"] = "No API keys configured (ALPHA_VANTAGE_KEY, NEWSAPI_KEY) or no results"
        _cache[symbol] = (result, time.time())
        return result

    # Deduplicate on first 60 chars of title
    seen: set[str] = set()
    unique: list[dict] = []
    for a in all_articles:
        key = a["title"][:60].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(a)

    result.update({
        "available":     True,
        "articles":      unique[:30],
        "article_count": len(unique),
    })
    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
