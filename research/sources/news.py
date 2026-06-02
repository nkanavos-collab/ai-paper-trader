"""
News aggregation.
Sources: yfinance news → Google News RSS (via requests+feedparser).
Root cause fix: feedparser.parse(url) sends no User-Agent → Google blocks it.
Fix: fetch with requests (proper UA + timeout), then parse the response body.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import requests

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

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Sentiment helpers ───────────────────────────────────────────────────────��

def score(text: str) -> float:
    if not _VADER_OK or not text:
        return 0.0
    return round(_vader.polarity_scores(str(text))["compound"], 3)


def label(s: float) -> str:
    if s >= 0.35:  return "positive"
    if s >= 0.05:  return "slightly positive"
    if s <= -0.35: return "negative"
    if s <= -0.05: return "slightly negative"
    return "neutral"


# ── Source: yfinance ─────────────────────────────────────────────────────────

def _yf_news(symbol: str) -> tuple[list[dict], dict]:
    src_meta = {"source": f"yfinance Ticker('{symbol}').news",
                "status": "failed", "count": 0, "error": None}
    try:
        import yfinance as yf
        raw = yf.Ticker(symbol).news or []
        if not raw:
            src_meta["status"] = "empty"
            src_meta["error"] = "yfinance returned no news items"
            log.warning("%s yfinance.news returned empty list", symbol)
            return [], src_meta

        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        out = []
        for item in raw[:25]:
            # yfinance 0.2.x uses nested content dict; older versions use flat keys
            content = item.get("content") or {}
            title = (item.get("title")
                     or content.get("title")
                     or item.get("headline") or "").strip()
            if not title:
                continue

            # Date: old format = providerPublishTime (unix int), new = pubDate (ISO string)
            dt = None
            ts = item.get("providerPublishTime") or item.get("publish_time")
            if ts:
                try:
                    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                except Exception:
                    pass
            if dt is None:
                pub_str = content.get("pubDate") or item.get("pubDate", "")
                if pub_str:
                    try:
                        dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except Exception:
                        pass
            if dt and dt < cutoff:
                continue

            source = (item.get("publisher")
                      or item.get("source")
                      or content.get("provider", {}).get("displayName", "")
                      or "Yahoo Finance")
            url = (item.get("link") or item.get("url")
                   or content.get("canonicalUrl", {}).get("url", "")
                   or content.get("clickThroughUrl", {}).get("url", ""))
            out.append({
                "title":     title,
                "source":    source,
                "url":       url,
                "date":      dt.strftime("%Y-%m-%d") if dt else "",
                "sentiment": score(title),
            })

        src_meta["status"] = "ok" if out else "empty"
        src_meta["count"]  = len(out)
        log.info("%s yfinance news: %d articles", symbol, len(out))
        return out, src_meta

    except Exception as exc:
        src_meta["error"] = str(exc)
        log.warning("%s yfinance.news failed: %s", symbol, exc)
        return [], src_meta


# ── Source: Google News RSS via requests ─────────────────────────────────────

def _google_rss(query: str) -> tuple[list[dict], dict]:
    url = (
        f"https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    src_meta = {"source": f"Google News RSS ({query!r})",
                "url": url, "status": "failed", "count": 0, "error": None}
    try:
        import feedparser
        resp = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        if resp.status_code != 200:
            src_meta["error"] = f"HTTP {resp.status_code}"
            log.warning("Google News RSS HTTP %d for query %r", resp.status_code, query)
            return [], src_meta

        feed = feedparser.parse(resp.content)
        if not feed.entries:
            src_meta["status"] = "empty"
            src_meta["error"]  = "Feed parsed but returned 0 entries"
            log.warning("Google News RSS: 0 entries for query %r", query)
            return [], src_meta

        out = []
        for e in feed.entries[:15]:
            raw_title = e.get("title", "").strip()
            # Google News title format: "Headline - Source Name"
            if " - " in raw_title:
                title, source = raw_title.rsplit(" - ", 1)
            else:
                title, source = raw_title, "Google News"

            # Parse date from published_parsed (struct_time) or published (string)
            pub_date = ""
            if hasattr(e, "published_parsed") and e.published_parsed:
                try:
                    from time import mktime
                    pub_date = datetime.fromtimestamp(
                        mktime(e.published_parsed), tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                except Exception:
                    pass
            if not pub_date and e.get("published"):
                pub_date = str(e.get("published", ""))[:10]

            out.append({
                "title":     title.strip(),
                "source":    source.strip(),
                "url":       e.get("link", ""),
                "date":      pub_date,
                "sentiment": score(title),
            })

        src_meta["status"] = "ok"
        src_meta["count"]  = len(out)
        log.info("Google News RSS %r: %d entries", query, len(out))
        return out, src_meta

    except ImportError:
        src_meta["error"] = "feedparser not installed"
        log.error("feedparser not installed")
        return [], src_meta
    except Exception as exc:
        src_meta["error"] = str(exc)
        log.warning("Google News RSS failed for %r: %s", query, exc)
        return [], src_meta


# ── Public API ────────────────────────────────────────────────────────────────

def get_news(symbol: str, company_name: str = "") -> dict:
    symbol = symbol.upper()
    cache_key = f"{symbol}|{company_name[:20]}"
    cached, ts = _cache.get(cache_key, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    t0 = time.time()
    articles: list[dict] = []
    sources_hit: list[str] = []
    source_metas: list[dict] = []
    errors: list[str] = []

    # Source 1: yfinance
    yf_arts, yf_meta = _yf_news(symbol)
    source_metas.append(yf_meta)
    if yf_arts:
        articles.extend(yf_arts)
        sources_hit.append(f"Yahoo Finance ({len(yf_arts)})")
    elif yf_meta.get("error"):
        errors.append(f"Yahoo Finance: {yf_meta['error']}")

    # Source 2: Google News — ticker query
    g1_arts, g1_meta = _google_rss(f"{symbol} stock news")
    source_metas.append(g1_meta)
    if g1_arts:
        articles.extend(g1_arts)
        sources_hit.append(f"Google News ticker ({len(g1_arts)})")
    elif g1_meta.get("error"):
        errors.append(f"Google News ticker: {g1_meta['error']}")

    # Source 3: Google News — company name query
    if company_name and company_name.split()[0].upper() != symbol:
        g2_arts, g2_meta = _google_rss(f"{company_name} stock news")
        source_metas.append(g2_meta)
        if g2_arts:
            articles.extend(g2_arts)
            sources_hit.append(f"Google News company ({len(g2_arts)})")

    # Deduplicate on first 50 chars of title
    seen: set[str] = set()
    unique: list[dict] = []
    for a in articles:
        key = a["title"][:50].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(a)

    unique.sort(key=lambda x: abs(x["sentiment"]), reverse=True)

    scores = [a["sentiment"] for a in unique]
    avg    = round(sum(scores) / len(scores), 3) if scores else 0.0
    pos    = sum(1 for s in scores if s >= 0.05)
    neg    = sum(1 for s in scores if s <= -0.05)

    result = {
        "articles":        unique[:20],
        "article_count":   len(unique),
        "avg_sentiment":   avg,
        "sentiment_label": label(avg),
        "positive_count":  pos,
        "negative_count":  neg,
        "neutral_count":   len(scores) - pos - neg,
        "top_positive":    next((a["title"] for a in unique if a["sentiment"] >= 0.35), None),
        "top_negative":    next((a["title"] for a in unique if a["sentiment"] <= -0.35), None),
        "sources_hit":     sources_hit,
        "vader_available": _VADER_OK,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "_meta": {
            "status":       "ok" if unique else "failed",
            "errors":       errors,
            "records":      len(unique),
            "sources":      source_metas,
            "duration_ms":  round((time.time() - t0) * 1000),
            "fetched_at":   datetime.now(timezone.utc).isoformat(),
            "vader_ok":     _VADER_OK,
        },
    }

    if not unique:
        result["_meta"]["reason"] = (
            "No articles found from any source. "
            + (" | ".join(errors) if errors else "All sources returned empty.")
        )
        log.warning("%s news: 0 articles. Errors: %s", symbol, errors)

    _cache[cache_key] = (result, time.time())
    return result
