"""
Data source diagnostics endpoint.
GET /diagnostics/data?symbol=AAPL
Fetches all four collectors and returns structured status for the template.
"""

import logging
from fastapi import APIRouter, Request
from app_templates import templates
from research.sources.market import get_market_data
from research.sources.news import get_news
from research.sources.sentiment import get_sentiment
from research.sources.macro import get_macro_data

log = logging.getLogger(__name__)
router = APIRouter(prefix="/diagnostics")


@router.get("/data")
def diagnostics_data(request: Request, symbol: str = "AAPL"):
    symbol = symbol.upper().strip()
    log.info("Diagnostics requested for %s", symbol)

    market = get_market_data(symbol)
    company = market.get("name", symbol)
    news = get_news(symbol, company)
    sentiment = get_sentiment(symbol, company, news.get("avg_sentiment", 0.0))
    macro = get_macro_data()

    sources = [
        _market_diag(symbol, market),
        _news_diag(symbol, news),
        _sentiment_diag(symbol, sentiment),
        _macro_diag(macro),
    ]

    return templates.TemplateResponse(request, "diagnostics_data.html", {
        "symbol":  symbol,
        "sources": sources,
        "active":  "diagnostics",
    })


# ── Per-source diagnostic builders ───────────────────────────────────────────

def _market_diag(symbol: str, data: dict) -> dict:
    meta = data.get("_meta", {})
    status = "failed" if data.get("error") else meta.get("status", "ok")
    return {
        "name":        "Market Data",
        "icon":        "bi-bar-chart-line",
        "status":      status,
        "error":       data.get("error") or (", ".join(meta.get("errors", [])) or None),
        "warnings":    meta.get("warnings", []),
        "call":        meta.get("source", f"yfinance Ticker('{symbol}').history()"),
        "records":     meta.get("records", 0),
        "fetched_at":  meta.get("fetched_at"),
        "duration_ms": meta.get("duration_ms"),
        "details": {
            "price_usd":      data.get("price_usd"),
            "prev_close":     data.get("prev_close_usd"),
            "rsi_14":         data.get("rsi_14"),
            "sma_20":         data.get("sma_20"),
            "sma_50":         data.get("sma_50"),
            "sma_200":        data.get("sma_200"),
            "history_bars":   meta.get("history_bars"),
            "info_available": meta.get("info_available"),
            "fast_info_keys": meta.get("fast_info_keys", []),
        },
    }


def _news_diag(symbol: str, data: dict) -> dict:
    meta = data.get("_meta", {})
    src_list = meta.get("sources", [])
    return {
        "name":        "News",
        "icon":        "bi-newspaper",
        "status":      meta.get("status", "failed"),
        "error":       meta.get("reason") if not data.get("article_count") else None,
        "errors":      meta.get("errors", []),
        "warnings":    [],
        "call":        " → ".join(s.get("source", "") for s in src_list) if src_list
                       else "yfinance.news + Google News RSS",
        "records":     data.get("article_count", 0),
        "fetched_at":  meta.get("fetched_at"),
        "duration_ms": meta.get("duration_ms"),
        "details": {
            "sources_hit":      data.get("sources_hit", []),
            "avg_sentiment":    data.get("avg_sentiment"),
            "vader_available":  data.get("vader_available"),
            "source_breakdown": [
                {
                    "source": s.get("source", ""),
                    "status": s.get("status", ""),
                    "count":  s.get("count", 0),
                    "error":  s.get("error"),
                    "url":    s.get("url"),
                }
                for s in src_list
            ],
        },
    }


def _sentiment_diag(symbol: str, data: dict) -> dict:
    meta   = data.get("_meta", {})
    vader  = data.get("vader_available", False)
    return {
        "name":        "Sentiment",
        "icon":        "bi-chat-quote",
        "status":      "ok" if vader else "partial",
        "error":       None,
        "errors":      [],
        "warnings":    ["Reddit disabled — requires OAuth (news sentiment used instead)"],
        "call":        "VADER sentiment on news article titles",
        "records":     meta.get("records", 0),
        "fetched_at":  meta.get("fetched_at"),
        "duration_ms": meta.get("duration_ms"),
        "details": {
            "reddit_ok":       False,
            "news_fallback":   True,
            "overall_score":   data.get("overall_score"),
            "overall_label":   data.get("overall_label"),
            "vader_available": vader,
        },
    }


def _macro_diag(data: dict) -> dict:
    meta = data.get("_meta", {})
    return {
        "name":        "Macro Data",
        "icon":        "bi-globe-americas",
        "status":      meta.get("status", "failed"),
        "error":       data.get("error"),
        "errors":      list(meta.get("fetch_errors", {}).values()),
        "warnings":    [],
        "call":        "yfinance batch: ^VIX, ^GSPC, ^TNX, ^IRX, DX=F, GC=F, CL=F (+ FRED optional)",
        "records":     meta.get("records", 0),
        "fetched_at":  meta.get("fetched_at"),
        "duration_ms": meta.get("duration_ms"),
        "details": {
            "sources_status": meta.get("sources_status", {}),
            "tickers_tried":  meta.get("tickers_tried", {}),
            "fetch_errors":   meta.get("fetch_errors", {}),
            "vix":            data.get("vix"),
            "sp500":          data.get("sp500_price"),
            "market_regime":  data.get("market_regime"),
        },
    }
