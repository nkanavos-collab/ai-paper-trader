"""
News sentiment module.

Reddit API now requires OAuth authentication and blocks unauthenticated requests
with HTTP 403. Reddit has been removed as a data source. Sentiment is derived
entirely from news article titles via VADER.
"""

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_vader = None
_VADER_OK = False
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    _VADER_OK = True
except ImportError:
    log.warning("vaderSentiment not installed — sentiment scoring disabled")

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600


def score(text: str) -> float:
    if not _VADER_OK or not text:
        return 0.0
    return round(_vader.polarity_scores(str(text))["compound"], 3)


def label(s: float) -> str:
    if s >= 0.35:  return "bullish"
    if s >= 0.05:  return "slightly bullish"
    if s <= -0.35: return "bearish"
    if s <= -0.05: return "slightly bearish"
    return "neutral"


def get_sentiment(symbol: str, company_name: str = "", news_sentiment: float = 0.0) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    t0 = time.time()
    overall = news_sentiment

    result = {
        "reddit": {
            "available": False,
            "post_count": 0,
            "reason": "Reddit API requires OAuth authentication — disabled.",
        },
        "news_sentiment_score": round(news_sentiment, 3),
        "news_sentiment_label": label(news_sentiment),
        "overall_score":        round(overall, 3),
        "overall_label":        label(overall),
        "vader_available":      _VADER_OK,
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "_meta": {
            "status":        "ok" if _VADER_OK else "partial",
            "reddit_ok":     False,
            "news_fallback": True,
            "errors":        [],
            "records":       0,
            "duration_ms":   round((time.time() - t0) * 1000),
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
        },
    }

    _cache[symbol] = (result, time.time())
    return result
