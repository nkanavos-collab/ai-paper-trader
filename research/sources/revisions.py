"""Analyst upgrade/downgrade revision momentum via yfinance."""
import logging
import time
from datetime import datetime, timezone, timedelta
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 4

_BULLISH_GRADES = {"buy", "strong buy", "outperform", "overweight", "positive",
                   "accumulate", "add", "long-term buy"}
_BEARISH_GRADES = {"sell", "strong sell", "underperform", "underweight", "negative",
                   "reduce", "avoid"}


def _grade_direction(grade: str) -> str:
    g = grade.lower().strip()
    if g in _BULLISH_GRADES:
        return "bullish"
    if g in _BEARISH_GRADES:
        return "bearish"
    return "neutral"


def get_analyst_revisions(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "recent": []}

    try:
        ticker = yf.Ticker(symbol)
        df = getattr(ticker, "upgrades_downgrades", None)

        if df is None or df.empty:
            result["error"] = "No upgrades/downgrades data"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        # Normalise index to dates
        if hasattr(df.index, "date"):
            df = df.copy()
            df["_date"] = df.index.date
        else:
            df = df.copy()
            df["_date"] = None

        cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).date()
        cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).date()

        recent = []
        for _, row in df.head(30).iterrows():
            d = row.get("_date")
            firm    = str(row.get("Firm", "")).strip()
            to_g    = str(row.get("ToGrade", "")).strip()
            from_g  = str(row.get("FromGrade", "")).strip()
            action  = str(row.get("Action", "")).strip().lower()

            if not to_g:
                continue

            entry = {
                "date":     str(d)[:10] if d else "",
                "firm":     firm[:30],
                "to_grade": to_g,
                "from_grade": from_g,
                "action":   action,
                "direction": _grade_direction(to_g),
            }
            recent.append(entry)

        if not recent:
            result["error"] = "No parseable revision data"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        result["available"] = True
        result["recent"]    = recent[:15]

        # Counts within 30 and 90 days
        def _count(window_date, direction: str) -> int:
            return sum(1 for r in recent
                       if r["direction"] == direction
                       and r["date"] >= str(window_date))

        result["upgrades_30d"]   = _count(cutoff_30, "bullish")
        result["downgrades_30d"] = _count(cutoff_30, "bearish")
        result["upgrades_90d"]   = _count(cutoff_90, "bullish")
        result["downgrades_90d"] = _count(cutoff_90, "bearish")

        # Momentum signal
        u30, d30 = result["upgrades_30d"], result["downgrades_30d"]
        u90, d90 = result["upgrades_90d"], result["downgrades_90d"]
        if u30 > d30 and u90 > d90:
            result["momentum"] = "positive"
        elif d30 > u30 and d90 > u90:
            result["momentum"] = "negative"
        elif u30 > d30 or u90 > d90:
            result["momentum"] = "slightly positive"
        elif d30 > u30 or d90 > u90:
            result["momentum"] = "slightly negative"
        else:
            result["momentum"] = "neutral"

        log.info("%s revisions: %d up / %d down (30d), momentum=%s",
                 symbol, u30, d30, result["momentum"])

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s upgrades_downgrades failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
