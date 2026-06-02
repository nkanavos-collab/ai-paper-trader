"""Options implied volatility via yfinance."""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 1800  # 30 min — IV changes frequently


def get_options_iv(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False}

    try:
        ticker   = yf.Ticker(symbol)
        exp_list = ticker.options

        if not exp_list:
            result["error"] = "No options listed for this symbol"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        # Current price
        hist = ticker.history(period="2d", interval="1d")
        if hist.empty:
            result["error"] = "No price data"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result
        current_price = float(hist["Close"].iloc[-1])

        # Use nearest expiration
        expiry = exp_list[0]
        chain  = ticker.option_chain(expiry)
        calls  = chain.calls.copy()
        puts   = chain.puts.copy()

        if calls.empty and puts.empty:
            result["error"] = "Empty option chain"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        def _atm_iv(df) -> float | None:
            if df.empty or "strike" not in df.columns or "impliedVolatility" not in df.columns:
                return None
            df = df.copy()
            df["_dist"] = (df["strike"] - current_price).abs()
            row = df.nsmallest(1, "_dist").iloc[0]
            iv = row.get("impliedVolatility")
            try:
                f = float(iv)
                return f if f == f and f > 0 else None
            except (TypeError, ValueError):
                return None

        iv_call = _atm_iv(calls)
        iv_put  = _atm_iv(puts)

        if iv_call is None and iv_put is None:
            result["error"] = "Could not extract IV from chain"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        iv = (((iv_call or 0) + (iv_put or 0)) / (int(iv_call is not None) + int(iv_put is not None)))

        # 20-day historical volatility (annualised)
        hist6m = ticker.history(period="6mo", interval="1d")
        hv20: float | None = None
        if len(hist6m) >= 21:
            rets = hist6m["Close"].pct_change().dropna()
            hv20 = float(rets.rolling(20).std().iloc[-1]) * (252 ** 0.5)

        iv_vs_hv = round(iv / hv20, 2) if hv20 and hv20 > 0 else None

        if iv_vs_hv is not None:
            if iv_vs_hv < 0.8:   signal = "cheap"
            elif iv_vs_hv > 1.3: signal = "expensive"
            else:                 signal = "fair"
        else:
            signal = "unknown"

        # Expected 1-week move
        expected_move_pct = round(iv / (52 ** 0.5) * 100, 1)

        result.update({
            "available":          True,
            "expiration":         expiry,
            "atm_iv_pct":         round(iv * 100, 1),
            "hv_20_pct":          round(hv20 * 100, 1) if hv20 else None,
            "iv_vs_hv":           iv_vs_hv,
            "signal":             signal,
            "expected_move_pct":  expected_move_pct,
        })
        log.info("%s options IV: %.1f%% ATM IV, HV20=%.1f%%, signal=%s",
                 symbol, iv * 100, (hv20 or 0) * 100, signal)

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s options_iv failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
