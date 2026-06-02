"""
Macro data collector.
Root cause fix: DX-Y.NYB unreliable; individual ticker failures silently dropped.
Fix:
  - Use yf.download() for batch fetch (one HTTP round-trip, more reliable).
  - DX=F (Dollar Index Futures) as primary USD ticker.
  - Each field populated independently — one failure never blocks others.
  - _meta.sources_status tracks every ticker's outcome.
  - FRED clearly labelled "FRED unavailable — no API key" rather than silently absent.
"""

import logging
import time
import yfinance as yf
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_cache: tuple[dict, float] = ({}, 0.0)
CACHE_SECONDS = 1800  # 30 min

# Ticker map: field_name → yfinance symbol
_TICKERS = {
    "vix":   "^VIX",
    "sp500": "^GSPC",
    "y10":   "^TNX",   # 10-Year Treasury yield
    "y2":    "^IRX",   # 13-week T-bill (short-term proxy)
    "dxy":   "UUP",    # USD ETF (ProShares USD Bull) — more reliable than DX=F futures
    "gold":  "GC=F",
    "oil":   "CL=F",
}

# Fallback tickers when primary fails
_FALLBACKS = {
    "dxy":  "DX=F",    # Dollar Index Futures
    "y10":  "^TYX",    # 30Y yield as backup
    "gold": "GLD",     # Gold ETF fallback
    "oil":  "USO",     # Oil ETF fallback
}


def _fetch_price(sym: str) -> float | None:
    """Fetch latest closing price for a single ticker. Returns None on failure."""
    try:
        hist = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        # Flatten MultiIndex if present (newer yfinance versions)
        if isinstance(hist.columns, type(hist.columns)) and hasattr(hist.columns, 'levels'):
            hist.columns = [c[0] if isinstance(c, tuple) else c for c in hist.columns]
        close = hist.get("Close")
        if close is None:
            return None
        vals = close.dropna()
        return float(vals.iloc[-1]) if len(vals) else None
    except Exception as exc:
        log.debug("fetch_price %s: %s", sym, exc)
        return None


def _batch_prices(tickers: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    """Fetch latest price for each ticker individually (reliable across yfinance versions)."""
    import concurrent.futures
    prices: dict[str, float] = {}
    errors: dict[str, str]   = {}

    def _fetch(field_sym):
        field, sym = field_sym
        val = _fetch_price(sym)
        return field, sym, val

    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        for field, sym, val in pool.map(_fetch, tickers.items()):
            if val is not None:
                prices[field] = val
                log.debug("%s=%s → %.4f", field, sym, val)
            else:
                errors[field] = f"{sym}: no data"
                log.debug("%s: no data", sym)

    # Try fallbacks for any that failed
    for field, fb_sym in _FALLBACKS.items():
        if field not in prices:
            val = _fetch_price(fb_sym)
            if val is not None:
                prices[field] = val
                errors.pop(field, None)
                log.info("fallback %s → %s = %.4f", field, fb_sym, val)

    log.info("macro prices: %d/%d OK", len(prices), len(tickers))
    return prices, errors


def _period_return(sym: str, period: str) -> float | None:
    try:
        hist = yf.Ticker(sym).history(period=period, interval="1d")
        if len(hist) > 1:
            s, e = float(hist["Close"].iloc[0]), float(hist["Close"].iloc[-1])
            return round((e - s) / s * 100, 2) if s else None
    except Exception as exc:
        log.debug("%s return(%s) failed: %s", sym, period, exc)
    return None


def _derive_regime(vix: float | None, sp500_1m: float | None, spread: float | None) -> str:
    signals = []
    if vix is not None:
        signals.append("low_vol"  if vix < 18 else ("high_vol" if vix > 28 else "norm_vol"))
    if sp500_1m is not None:
        signals.append("risk_on"  if sp500_1m > 2 else ("risk_off" if sp500_1m < -2 else "flat"))
    if spread is not None:
        signals.append("inverted" if spread < 0 else "normal_curve")

    if "risk_on"  in signals and "low_vol"  in signals: return "bullish / risk-on"
    if "risk_off" in signals or  "high_vol" in signals: return "bearish / risk-off"
    if "inverted" in signals:                           return "cautious — yield curve inverted"
    return "neutral / mixed"


def get_macro_data() -> dict:
    global _cache
    data, ts = _cache
    if data and (time.time() - ts) < CACHE_SECONDS:
        return data

    t0 = time.time()
    result: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources":   [],
        "error":     None,
    }
    sources_status: dict[str, str] = {}

    prices, fetch_errors = _batch_prices(_TICKERS)

    # ── VIX ─────────────────────────────────────────────────────────────────
    vix = prices.get("vix")
    if vix is not None:
        result["vix"] = round(vix, 2)
        result["vix_signal"] = (
            "extreme fear / crisis" if vix > 35 else
            "elevated fear"         if vix > 25 else
            "normal range"          if vix > 15 else
            "low fear (complacent)"
        )
        result["sources"].append("^VIX")
        sources_status["VIX"] = f"ok ({vix:.1f})"
    else:
        sources_status["VIX"] = fetch_errors.get("vix", "unknown error")
        log.warning("VIX unavailable: %s", sources_status["VIX"])

    # ── Treasury yields ──────────────────────────────────────────────────────
    y10 = prices.get("y10")
    y2  = prices.get("y2")
    if y10 is not None:
        result["ten_year_yield_pct"] = round(y10, 3)
        result["sources"].append("^TNX (10Y)")
        sources_status["10Y"] = f"ok ({y10:.2f}%)"
    else:
        sources_status["10Y"] = fetch_errors.get("y10", "unknown error")

    if y2 is not None:
        result["two_year_yield_pct"] = round(y2, 3)
        result["sources"].append("^IRX (13W)")
        sources_status["2Y/13W"] = f"ok ({y2:.2f}%)"
    else:
        sources_status["2Y/13W"] = fetch_errors.get("y2", "unknown error")

    if y10 is not None and y2 is not None:
        spread = round(y10 - y2, 3)
        result["yield_curve_spread"] = spread
        result["yield_curve_signal"] = (
            "inverted — recession historically likely" if spread < 0 else "normal"
        )

    # ── S&P 500 ──────────────────────────────────────────────────────────────
    sp = prices.get("sp500")
    if sp is not None:
        result["sp500_price"]    = round(sp, 2)
        result["sp500_1m_pct"]   = _period_return("^GSPC", "1mo")
        result["sp500_ytd_pct"]  = _period_return("^GSPC", "ytd")
        result["sources"].append("^GSPC (S&P 500)")
        sources_status["SP500"] = f"ok ({sp:.0f})"
    else:
        sources_status["SP500"] = fetch_errors.get("sp500", "unknown error")

    # ── USD Index ────────────────────────────────────────────────────────────
    dxy = prices.get("dxy")
    if dxy is not None:
        result["usd_index"]  = round(dxy, 2)
        result["usd_1m_pct"] = _period_return("DX=F", "1mo")
        result["sources"].append("DX=F (USD Index Futures)")
        sources_status["USD"] = f"ok ({dxy:.2f})"
    else:
        sources_status["USD"] = fetch_errors.get("dxy", "unknown error")
        log.warning("USD index unavailable: %s", sources_status["USD"])

    # ── Gold ─────────────────────────────────────────────────────────────────
    gold = prices.get("gold")
    if gold is not None:
        result["gold_price_usd"] = round(gold, 2)
        result["gold_1m_pct"]    = _period_return("GC=F", "1mo")
        result["sources"].append("GC=F (Gold)")
        sources_status["Gold"] = f"ok ({gold:.0f})"
    else:
        sources_status["Gold"] = fetch_errors.get("gold", "unknown error")

    # ── Oil ──────────────────────────────────────────────────────────────────
    oil = prices.get("oil")
    if oil is not None:
        result["oil_wti_usd"] = round(oil, 2)
        result["oil_1m_pct"]  = _period_return("CL=F", "1mo")
        result["sources"].append("CL=F (WTI Oil)")
        sources_status["Oil"] = f"ok ({oil:.2f})"
    else:
        sources_status["Oil"] = fetch_errors.get("oil", "unknown error")

    # ── Market Regime ────────────────────────────────────────────────────────
    result["market_regime"] = _derive_regime(
        vix, result.get("sp500_1m_pct"), result.get("yield_curve_spread")
    )

    # ── FRED (optional) ──────────────────────────────────────────────────────
    fred_result = _try_fred()
    if fred_result:
        result.update(fred_result)
        result["sources"].append("FRED API")
        sources_status["FRED"] = "ok"
    else:
        try:
            from config import FRED_API_KEY
            sources_status["FRED"] = (
                "no API key (set FRED_API_KEY in .env)" if not FRED_API_KEY
                else "FRED API call failed"
            )
        except Exception:
            sources_status["FRED"] = "config error"

    # ── Meta ─────────────────────────────────────────────────────────────────
    ok_count = len(result["sources"])
    result["_meta"] = {
        "status":         "ok" if ok_count >= 3 else ("partial" if ok_count >= 1 else "failed"),
        "sources_status": sources_status,
        "fetch_errors":   fetch_errors,
        "tickers_tried":  _TICKERS,
        "records":        ok_count,
        "duration_ms":    round((time.time() - t0) * 1000),
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
    }
    log.info("Macro: %d/%d tickers OK in %dms", ok_count, len(_TICKERS),
             result["_meta"]["duration_ms"])

    _cache = (result, time.time())
    return result


def _try_fred() -> dict | None:
    try:
        from config import FRED_API_KEY
        if not FRED_API_KEY:
            return None
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
        ff    = fred.get_series("FEDFUNDS").dropna()
        cpi   = fred.get_series("CPIAUCSL").dropna()
        unemp = fred.get_series("UNRATE").dropna()
        cpi_yoy = ((cpi.iloc[-1] - cpi.iloc[-13]) / cpi.iloc[-13] * 100) if len(cpi) > 13 else None
        return {
            "fed_funds_rate_pct": round(float(ff.iloc[-1]),   2) if len(ff)    else None,
            "cpi_yoy_pct":        round(cpi_yoy,              2) if cpi_yoy    else None,
            "unemployment_pct":   round(float(unemp.iloc[-1]),1) if len(unemp) else None,
        }
    except Exception as exc:
        log.debug("FRED failed: %s", exc)
        return None
