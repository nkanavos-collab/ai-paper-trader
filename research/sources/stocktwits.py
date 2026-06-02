"""
StockTwits real-time sentiment — free, no API key required.
Finance-specific platform where every post is explicitly tagged bullish/bearish.
Bull ratio and message velocity are leading indicators — they often precede price moves.
"""
import logging
import time
from datetime import datetime, timezone
import requests

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 900  # 15 min — sentiment changes fast

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def get_stocktwits(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False}

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        resp = requests.get(url, headers=_HEADERS, timeout=10,
                            params={"limit": 30})
        if resp.status_code == 429:
            result["error"] = "Rate limited"
            _cache[symbol] = (result, time.time())
            return result
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            _cache[symbol] = (result, time.time())
            return result

        data = resp.json()
        messages = data.get("messages", [])
        if not messages:
            result["error"] = "No messages found"
            _cache[symbol] = (result, time.time())
            return result

        bull = sum(1 for m in messages
                   if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
        bear = sum(1 for m in messages
                   if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
        total_with_sentiment = bull + bear

        bull_ratio  = round(bull / total_with_sentiment, 3) if total_with_sentiment else 0.5
        bear_ratio  = round(bear / total_with_sentiment, 3) if total_with_sentiment else 0.5

        # Message velocity: use timestamps to estimate msgs/hour
        timestamps = []
        for m in messages:
            ts_str = m.get("created_at", "")
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    timestamps.append(dt)
                except Exception:
                    pass

        msgs_per_hour: float | None = None
        if len(timestamps) >= 2:
            timestamps.sort(reverse=True)
            span_hours = (timestamps[0] - timestamps[-1]).total_seconds() / 3600
            if span_hours > 0:
                msgs_per_hour = round(len(timestamps) / span_hours, 1)

        # Signal
        if bull_ratio >= 0.70:   signal = "very_bullish"
        elif bull_ratio >= 0.60: signal = "bullish"
        elif bull_ratio <= 0.30: signal = "very_bearish"
        elif bull_ratio <= 0.40: signal = "bearish"
        else:                    signal = "neutral"

        # Sample top posts
        top_posts = []
        for m in messages[:6]:
            sent_val = (m.get("entities", {}).get("sentiment") or {}).get("basic", "")
            top_posts.append({
                "body":      m.get("body", "")[:120],
                "sentiment": sent_val,
                "date":      str(m.get("created_at", ""))[:10],
            })

        result.update({
            "available":      True,
            "bull_count":     bull,
            "bear_count":     bear,
            "total_messages": len(messages),
            "bull_ratio":     bull_ratio,
            "bear_ratio":     bear_ratio,
            "signal":         signal,
            "msgs_per_hour":  msgs_per_hour,
            "top_posts":      top_posts,
        })
        log.info("%s StockTwits: %d bull/%d bear (%.0f%% bullish), %d msgs",
                 symbol, bull, bear, bull_ratio * 100, len(messages))

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s StockTwits failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
