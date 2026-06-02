"""Earnings surprise history via yfinance."""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 4


def get_earnings_history(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "quarters": []}

    try:
        ticker = yf.Ticker(symbol)

        df = None
        for attr in ("earnings_history", "get_earnings_history"):
            try:
                candidate = getattr(ticker, attr, None)
                if callable(candidate):
                    candidate = candidate()
                if candidate is not None and not candidate.empty:
                    df = candidate
                    break
            except Exception:
                pass

        if df is None or df.empty:
            result["error"] = "No earnings history available"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        def _safe(row, *keys) -> float | None:
            for k in keys:
                try:
                    v = row.get(k) if hasattr(row, "get") else getattr(row, k, None)
                    if v is not None and str(v) not in ("nan", "None", "<NA>"):
                        return float(v)
                except Exception:
                    pass
            return None

        quarters = []
        for idx, row in df.iterrows():
            period = str(idx)[:10] if idx is not None else ""
            actual   = _safe(row, "epsActual",    "Reported EPS")
            estimate = _safe(row, "epsEstimate",  "EPS Estimate")
            surprise = _safe(row, "surprisePercent", "Surprise(%)")

            if surprise is None and actual is not None and estimate is not None and estimate != 0:
                surprise = round((actual - estimate) / abs(estimate) * 100, 2)

            beat = None
            if actual is not None and estimate is not None:
                beat = actual >= estimate

            quarters.append({
                "period":          period,
                "eps_actual":      round(actual, 4)   if actual   is not None else None,
                "eps_estimate":    round(estimate, 4) if estimate is not None else None,
                "surprise_pct":    round(float(surprise) * (100 if abs(float(surprise)) < 5 else 1), 2)
                                   if surprise is not None else None,
                "beat":            beat,
            })

        # Keep only the 8 most recent, newest first
        quarters = [q for q in quarters if q["eps_actual"] is not None][:8]

        if not quarters:
            result["error"] = "No parseable earnings data"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        beats      = sum(1 for q in quarters if q["beat"] is True)
        misses     = sum(1 for q in quarters if q["beat"] is False)
        beat_rate  = round(beats / len(quarters) * 100, 1)
        surprises  = [q["surprise_pct"] for q in quarters if q["surprise_pct"] is not None]
        avg_surp   = round(sum(surprises) / len(surprises), 2) if surprises else None

        result.update({
            "available":       True,
            "quarters":        quarters,
            "beat_rate":       beat_rate,
            "beats":           beats,
            "misses":          misses,
            "avg_surprise_pct": avg_surp,
            "streak":          _streak(quarters),
        })
        log.info("%s earnings history: %d quarters, %.1f%% beat rate", symbol, len(quarters), beat_rate)

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s earnings_history failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result


def _streak(quarters: list[dict]) -> str:
    """Current beat/miss streak from most recent quarter."""
    if not quarters:
        return ""
    streak_type = "beat" if quarters[0].get("beat") else "miss"
    count = 0
    for q in quarters:
        if q.get("beat") == (streak_type == "beat"):
            count += 1
        else:
            break
    return f"{count} consecutive {'beats' if streak_type == 'beat' else 'misses'}"
