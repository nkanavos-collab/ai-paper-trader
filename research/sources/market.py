"""
Market data source.
Strategy:
  1. history() first — always available for valid symbols.
  2. fast_info second — lightweight current-price object.
  3. info last — slow, optional fundamentals.
Never return an all-N/A snapshot because info failed.
"""

import logging
import time
import yfinance as yf
from datetime import datetime, timezone, date as _date, timedelta
from config import MARKET_CACHE_SECONDS, EUR_USD_FALLBACK

_SECTOR_ETFS: dict[str, str] = {
    "Technology":             "XLK",
    "Healthcare":             "XLV",
    "Consumer Cyclical":      "XLY",
    "Financial Services":     "XLF",
    "Communication Services": "XLC",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Consumer Defensive":     "XLP",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
}

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
_eur_usd_cache: tuple[float, float] = (EUR_USD_FALLBACK, 0.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta(status: str, errors: list, warnings: list, source: str, records: int, t0: float) -> dict:
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "source": source,
        "records": records,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.time() - t0) * 1000),
    }


def _get_eur_usd() -> float:
    global _eur_usd_cache
    rate, ts = _eur_usd_cache
    if time.time() - ts < 300:
        return rate
    for sym in ("EURUSD=X", "EUR=X"):
        try:
            h = yf.Ticker(sym).history(period="5d", interval="1d")
            if not h.empty:
                rate = float(h["Close"].iloc[-1])
                _eur_usd_cache = (rate, time.time())
                log.debug("EUR/USD %.4f from %s", rate, sym)
                return rate
        except Exception as exc:
            log.debug("EUR/USD %s failed: %s", sym, exc)
    log.warning("EUR/USD fallback to %.4f", EUR_USD_FALLBACK)
    return EUR_USD_FALLBACK


def _rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100.0 if al < 1e-9 else round(100 - 100 / (1 + ag / al), 2)


def _sma(prices: list[float], period: int) -> float | None:
    return round(sum(prices[-period:]) / period, 4) if len(prices) >= period else None


# ── Main fetch ────────────────────────────────────────────────────────────────

def get_market_data(symbol: str) -> dict:
    t0 = time.time()
    symbol = symbol.upper()
    errors: list[str] = []
    warnings: list[str] = []

    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < MARKET_CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "error": None,
                    "timestamp": datetime.now(timezone.utc).isoformat()}

    # ── 1. Price history (always first) ─────────────────────────────────────
    ticker = yf.Ticker(symbol)
    hist = None
    hist_period_used = "none"
    for period in ("1y", "6mo", "3mo", "1mo"):
        try:
            h = ticker.history(period=period, interval="1d")
            if not h.empty:
                hist = h
                hist_period_used = period
                log.info("%s history(%s): %d bars", symbol, period, len(h))
                break
        except Exception as exc:
            msg = f"history({period}): {exc}"
            errors.append(msg)
            log.warning("%s %s", symbol, msg)

    if hist is None or hist.empty:
        result["error"] = f"No price history returned for {symbol}"
        result["_meta"] = _meta("failed", errors, warnings,
                                "yfinance history()", 0, t0)
        log.error("%s: all history periods failed", symbol)
        return result

    eur_usd = _get_eur_usd()
    closes  = [float(c) for c in hist["Close"]]
    volumes = [int(v) for v in hist["Volume"]]
    price      = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else price

    def _ret(n: int) -> float | None:
        return round((price - closes[-(n+1)]) / closes[-(n+1)] * 100, 2) \
            if len(closes) > n and closes[-(n+1)] else None

    sma20  = _sma(closes, 20)
    sma50  = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    rsi14  = _rsi(closes[-30:]) if len(closes) >= 15 else 50.0

    # Fallback 52w from history
    h52_hist = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    l52_hist = min(closes[-252:]) if len(closes) >= 252 else min(closes)

    avg_vol   = int(sum(volumes[-20:]) / min(20, len(volumes))) if volumes else None
    vol_today = volumes[-1] if volumes else None
    vol_ratio = round(vol_today / avg_vol, 2) if avg_vol and vol_today else None

    # ── 2. fast_info (lightweight, usually reliable) ──────────────────────
    fi_data: dict = {}
    try:
        fi = ticker.fast_info
        fi_data = {
            "lastPrice":       getattr(fi, "last_price",       None),
            "previousClose":   getattr(fi, "previous_close",   None),
            "marketCap":       getattr(fi, "market_cap",       None),
            "fiftyTwoWeekHigh": getattr(fi, "year_high",       None),
            "fiftyTwoWeekLow":  getattr(fi, "year_low",        None),
            "currency":        getattr(fi, "currency",         None),
        }
        fi_data = {k: v for k, v in fi_data.items() if v is not None}
        log.debug("%s fast_info: %s", symbol, list(fi_data.keys()))
    except Exception as exc:
        warnings.append(f"fast_info: {exc}")
        log.debug("%s fast_info failed: %s", symbol, exc)

    # ── 3. info (fundamentals, slow, optional) ────────────────────────────
    info: dict = {}
    info_ok = False
    try:
        raw_info = ticker.info
        if raw_info and isinstance(raw_info, dict) and len(raw_info) > 5:
            info = raw_info
            info_ok = True
            log.info("%s info: %d keys", symbol, len(info))
        else:
            warnings.append(f"ticker.info returned only {len(raw_info or {})} keys (empty/stub)")
            log.warning("%s ticker.info returned %d keys", symbol, len(raw_info or {}))
    except Exception as exc:
        warnings.append(f"ticker.info: {exc}")
        log.warning("%s ticker.info failed: %s", symbol, exc)

    # Merge fast_info into info for key fields if info is empty
    for k, v in fi_data.items():
        if not info.get(k):
            info[k] = v

    # Final 52w: info > fast_info > history
    h52 = float(info.get("fiftyTwoWeekHigh") or h52_hist)
    l52 = float(info.get("fiftyTwoWeekLow")  or l52_hist)

    target = info.get("targetMeanPrice")

    # ── Build result ──────────────────────────────────────────────────────
    result.update({
        "name":         info.get("longName") or info.get("shortName") or symbol,
        "short_name":   info.get("shortName") or symbol,
        "sector":       info.get("sector") or "N/A",
        "industry":     info.get("industry") or "N/A",
        "currency":     info.get("currency") or "USD",

        "price_usd":    round(price, 4),
        "price_eur":    round(price / eur_usd, 4),
        "eur_usd_rate": round(eur_usd, 4),
        "prev_close_usd": round(prev_close, 4),

        "change_pct":    round((price - prev_close) / prev_close * 100, 2),
        "change_1w_pct": _ret(5),
        "change_1m_pct": _ret(21),
        "change_3m_pct": _ret(63),
        "change_6m_pct": _ret(126),

        "high_52w": round(h52, 2),
        "low_52w":  round(l52, 2),
        "pct_from_52w_high": round((price - h52) / h52 * 100, 1) if h52 else None,
        "pct_from_52w_low":  round((price - l52) / l52 * 100, 1) if l52 else None,

        "avg_volume":   avg_vol,
        "volume_today": vol_today,
        "volume_ratio": vol_ratio,

        "sma_20":  sma20,
        "sma_50":  sma50,
        "sma_200": sma200,
        "rsi_14":  rsi14,
        "price_vs_sma20_pct":  round((price - sma20)  / sma20  * 100, 1) if sma20  else None,
        "price_vs_sma50_pct":  round((price - sma50)  / sma50  * 100, 1) if sma50  else None,
        "price_vs_sma200_pct": round((price - sma200) / sma200 * 100, 1) if sma200 else None,

        # Fundamentals (from info — may be None)
        "market_cap":      info.get("marketCap"),
        "pe_ratio":        info.get("trailingPE"),
        "forward_pe":      info.get("forwardPE"),
        "peg_ratio":       info.get("pegRatio"),
        "eps":             info.get("trailingEps"),
        "revenue_growth":  info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "gross_margin":    info.get("grossMargins"),
        "profit_margin":   info.get("profitMargins"),
        "roe":             info.get("returnOnEquity"),
        "debt_to_equity":  info.get("debtToEquity"),
        "current_ratio":   info.get("currentRatio"),
        "beta":            info.get("beta"),
        "dividend_yield":  info.get("dividendYield"),
        "payout_ratio":    info.get("payoutRatio"),

        "analyst_rating":    (info.get("recommendationKey") or "").replace("_", " ").title() or None,
        "analyst_count":     info.get("numberOfAnalystOpinions"),
        "target_mean_price": target,
        "target_upside_pct": round((target - price) / price * 100, 1) if target else None,

        "short_pct_float":   info.get("shortPercentOfFloat"),
        "short_ratio":       info.get("shortRatio"),
        "institutional_pct": info.get("institutionsPercentHeld"),
        "insider_pct":       info.get("insidersPercentHeld"),
    })

    # ── Listing age / recent IPO detection ───────────────────────────────────
    try:
        hist_all = ticker.history(period="max", interval="3mo")
        if not hist_all.empty:
            first_idx = hist_all.index[0]
            try:
                first_date = first_idx.date() if hasattr(first_idx, "date") else \
                             _date.fromisoformat(str(first_idx)[:10])
                today = _date.today()
                listing_age_days = (today - first_date).days
                result["listing_age_days"]  = listing_age_days
                result["listing_date_approx"] = str(first_date)
                result["is_recent_listing"] = listing_age_days <= 730  # < 2 years
            except Exception:
                pass
    except Exception as exc:
        warnings.append(f"listing_age: {exc}")

    # ── Earnings date ─────────────────────────────────────────────────────────
    try:
        cal = ticker.calendar
        if cal is not None and isinstance(cal, dict):
            ed_list = cal.get("Earnings Date", [])
            if not isinstance(ed_list, list):
                ed_list = [ed_list] if ed_list is not None else []
            today = _date.today()
            best: _date | None = None
            for ed in ed_list:
                try:
                    d = ed.date() if hasattr(ed, "date") else _date.fromisoformat(str(ed)[:10])
                    if d >= today - timedelta(days=14):
                        if best is None or d < best:
                            best = d
                except Exception:
                    pass
            if best:
                result["earnings_date"]        = str(best)
                result["days_until_earnings"]  = (best - today).days
    except Exception as exc:
        warnings.append(f"calendar: {exc}")

    # ── Sector relative strength vs ETF ───────────────────────────────────────
    sector_etf = _SECTOR_ETFS.get(result.get("sector", ""))
    if sector_etf:
        try:
            etf_h = yf.Ticker(sector_etf).history(period="3mo", interval="1d")
            if not etf_h.empty:
                etf_c   = [float(c) for c in etf_h["Close"]]
                etf_p   = etf_c[-1]
                etf_1m  = round((etf_p - etf_c[-22]) / etf_c[-22] * 100, 2) if len(etf_c) >= 22 else None
                etf_3m  = round((etf_p - etf_c[0])   / etf_c[0]   * 100, 2) if len(etf_c) >= 2  else None
                result["sector_etf"]        = sector_etf
                result["sector_etf_1m_pct"] = etf_1m
                result["sector_etf_3m_pct"] = etf_3m
                stk_1m = result.get("change_1m_pct")
                stk_3m = result.get("change_3m_pct")
                if etf_1m is not None and stk_1m is not None:
                    result["rel_strength_1m_pct"] = round(stk_1m - etf_1m, 2)
                if etf_3m is not None and stk_3m is not None:
                    result["rel_strength_3m_pct"] = round(stk_3m - etf_3m, 2)
        except Exception as exc:
            warnings.append(f"sector ETF {sector_etf}: {exc}")

    status = "ok" if (not errors and info_ok) else ("partial" if not errors else "partial")
    result["_meta"] = _meta(status, errors, warnings,
                            f"yfinance history({hist_period_used})", len(closes), t0)
    result["_meta"]["info_available"] = info_ok
    result["_meta"]["history_bars"]   = len(closes)
    result["_meta"]["fast_info_keys"] = list(fi_data.keys())

    _cache[symbol] = (result, time.time())
    return result
