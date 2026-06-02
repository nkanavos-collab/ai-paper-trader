"""Portfolio correlation check via yfinance."""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 1800  # 30 min — positions change


def get_portfolio_correlation(symbol: str, position_symbols: list[str]) -> dict:
    """Compute 90-day return correlation between proposed stock and each held position."""
    symbol = symbol.upper()
    cache_key = f"{symbol}|{'|'.join(sorted(position_symbols))}"
    cached, ts = _cache.get(cache_key, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "correlations": {}}

    if not position_symbols:
        result["reason"] = "No current positions to compare against"
        result["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return result

    all_syms = [symbol] + [s.upper() for s in position_symbols]

    try:
        import pandas as pd

        data = yf.download(
            all_syms,
            period="3mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )

        if data.empty:
            result["error"] = "No price data returned"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[cache_key] = (result, time.time())
            return result

        # Extract close prices
        if len(all_syms) == 1:
            closes = data[["Close"]].rename(columns={"Close": symbol})
        elif "Close" in data.columns:
            closes = data["Close"]
        else:
            closes = pd.DataFrame({
                s: data[s]["Close"] for s in all_syms if s in data.columns
            })

        returns = closes.pct_change().dropna()

        if symbol not in returns.columns:
            result["error"] = f"No return data for {symbol}"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[cache_key] = (result, time.time())
            return result

        correlations: dict[str, float] = {}
        for s in position_symbols:
            su = s.upper()
            if su in returns.columns:
                corr = float(returns[symbol].corr(returns[su]))
                if corr == corr:  # NaN guard
                    correlations[su] = round(corr, 3)

        if not correlations:
            result["error"] = "Could not compute correlations"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[cache_key] = (result, time.time())
            return result

        high_corr = [(s, c) for s, c in correlations.items() if abs(c) >= 0.70]
        high_corr.sort(key=lambda x: abs(x[1]), reverse=True)

        result.update({
            "available":              True,
            "correlations":           correlations,
            "high_correlation_pairs": high_corr,
            "max_correlation":        max(abs(c) for c in correlations.values()),
            "warning":                bool(high_corr),
        })
        log.info("%s correlation: %d positions checked, %d high-corr",
                 symbol, len(correlations), len(high_corr))

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s correlation failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[cache_key] = (result, time.time())
    return result
