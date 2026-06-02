"""
Unusual options activity detector via yfinance options chain.
Flags when call/put volume is anomalously high relative to open interest,
which often signals informed institutional positioning.
"""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 900  # 15 min — options data changes during trading hours


def get_unusual_options(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False}

    try:
        ticker   = yf.Ticker(symbol)
        exp_list = ticker.options
        if not exp_list:
            result["error"] = "No options listed"
            _cache[symbol] = (result, time.time())
            return result

        # Fetch nearest two expirations for broader picture
        total_call_vol = 0
        total_put_vol  = 0
        total_call_oi  = 0
        total_put_oi   = 0
        unusual_calls: list[dict] = []
        unusual_puts:  list[dict] = []

        for expiry in exp_list[:2]:
            try:
                chain = ticker.option_chain(expiry)
                calls = chain.calls
                puts  = chain.puts

                if not calls.empty:
                    total_call_vol += int(calls["volume"].fillna(0).sum())
                    total_call_oi  += int(calls["openInterest"].fillna(0).sum())
                    # Flag individual strikes with volume > 2x their OI
                    for _, row in calls.iterrows():
                        vol = int(row.get("volume", 0) or 0)
                        oi  = int(row.get("openInterest", 1) or 1)
                        iv  = float(row.get("impliedVolatility", 0) or 0)
                        if vol >= 500 and vol > oi * 2:
                            unusual_calls.append({
                                "expiry":  expiry,
                                "strike":  float(row.get("strike", 0)),
                                "volume":  vol,
                                "oi":      oi,
                                "vol_oi":  round(vol / max(oi, 1), 1),
                                "iv_pct":  round(iv * 100, 1),
                            })

                if not puts.empty:
                    total_put_vol += int(puts["volume"].fillna(0).sum())
                    total_put_oi  += int(puts["openInterest"].fillna(0).sum())
                    for _, row in puts.iterrows():
                        vol = int(row.get("volume", 0) or 0)
                        oi  = int(row.get("openInterest", 1) or 1)
                        iv  = float(row.get("impliedVolatility", 0) or 0)
                        if vol >= 500 and vol > oi * 2:
                            unusual_puts.append({
                                "expiry":  expiry,
                                "strike":  float(row.get("strike", 0)),
                                "volume":  vol,
                                "oi":      oi,
                                "vol_oi":  round(vol / max(oi, 1), 1),
                                "iv_pct":  round(iv * 100, 1),
                            })
            except Exception as exc:
                log.debug("%s chain %s failed: %s", symbol, expiry, exc)
                continue

        if total_call_vol == 0 and total_put_vol == 0:
            result["error"] = "No options volume data"
            _cache[symbol] = (result, time.time())
            return result

        cp_ratio = round(total_call_vol / max(total_put_vol, 1), 2)

        # Unusual activity flag
        call_vol_oi = round(total_call_vol / max(total_call_oi, 1), 2)
        put_vol_oi  = round(total_put_vol  / max(total_put_oi, 1), 2)
        unusual_call_flag = call_vol_oi > 0.5 or len(unusual_calls) >= 3
        unusual_put_flag  = put_vol_oi  > 0.5 or len(unusual_puts) >= 3

        # Determine signal
        if unusual_call_flag and cp_ratio > 1.5:
            signal = "unusual_calls"
            signal_label = "Unusual call buying — potential bullish smart-money positioning"
        elif unusual_put_flag and cp_ratio < 0.7:
            signal = "unusual_puts"
            signal_label = "Unusual put buying — potential bearish hedge or directional bet"
        elif unusual_call_flag or unusual_put_flag:
            signal = "elevated"
            signal_label = "Elevated options activity vs open interest"
        else:
            signal = "normal"
            signal_label = "Normal options activity"

        # Sort unusual strikes by volume
        unusual_calls.sort(key=lambda x: x["volume"], reverse=True)
        unusual_puts.sort(key=lambda x: x["volume"], reverse=True)

        result.update({
            "available":          True,
            "total_call_vol":     total_call_vol,
            "total_put_vol":      total_put_vol,
            "total_call_oi":      total_call_oi,
            "total_put_oi":       total_put_oi,
            "call_put_ratio":     cp_ratio,
            "call_vol_oi_ratio":  call_vol_oi,
            "put_vol_oi_ratio":   put_vol_oi,
            "unusual_calls":      unusual_calls[:5],
            "unusual_puts":       unusual_puts[:5],
            "signal":             signal,
            "signal_label":       signal_label,
            "is_unusual":         signal in ("unusual_calls", "unusual_puts"),
        })
        log.info("%s options: C/P=%.2f, call_vol/OI=%.2f, signal=%s",
                 symbol, cp_ratio, call_vol_oi, signal)

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s unusual_options failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
