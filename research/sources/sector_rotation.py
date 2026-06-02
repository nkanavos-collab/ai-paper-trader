"""
Sector rotation detector.
Measures relative money flow into each SPDR sector ETF over multiple timeframes.
Hot sectors = rising price + above-average volume = institutional accumulation.
Cold sectors = falling relative flow = distribution.
"""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: tuple[dict, float] = ({}, 0.0)
CACHE_SECONDS = 1800  # 30 min

_SECTOR_ETFS: dict[str, str] = {
    "XLK":  "Technology",
    "XLV":  "Healthcare",
    "XLY":  "Consumer Cyclical",
    "XLF":  "Financial Services",
    "XLC":  "Communication Services",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLP":  "Consumer Defensive",
    "XLB":  "Basic Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
}


def _flow_score(closes: list[float], volumes: list[int]) -> dict:
    """Compute relative money flow across multiple windows."""
    if len(closes) < 22:
        return {}

    price = closes[-1]

    def _ret(n: int) -> float | None:
        return round((price - closes[-(n + 1)]) / closes[-(n + 1)] * 100, 2) \
            if len(closes) > n and closes[-(n + 1)] else None

    avg_vol_60 = sum(volumes[-60:]) / min(60, len(volumes)) if volumes else 0
    avg_vol_5  = sum(volumes[-5:])  / 5 if len(volumes) >= 5 else 0
    vol_ratio  = round(avg_vol_5 / avg_vol_60, 2) if avg_vol_60 > 0 else 1.0

    # Flow score: combine price momentum + volume
    ret_5  = _ret(5)  or 0
    ret_20 = _ret(20) or 0
    flow = round((ret_5 * 0.6 + ret_20 * 0.4) * vol_ratio, 2)

    return {
        "ret_5d":    ret_5,
        "ret_20d":   ret_20,
        "vol_ratio": vol_ratio,
        "flow_score": flow,
    }


def get_sector_rotation() -> dict:
    global _cache
    data, ts = _cache
    if data and (time.time() - ts) < CACHE_SECONDS:
        return data

    result: dict = {"available": False, "sectors": {}}
    symbols = list(_SECTOR_ETFS.keys())

    try:
        raw = yf.download(
            symbols,
            period="3mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )

        if raw.empty:
            result["error"] = "No ETF data returned"
            _cache = (result, time.time())
            return result

        sectors: dict[str, dict] = {}
        for etf, sector_name in _SECTOR_ETFS.items():
            try:
                if len(symbols) == 1:
                    close_col = raw["Close"]
                    vol_col   = raw["Volume"]
                elif etf in raw.columns.get_level_values(1):
                    close_col = raw["Close"][etf]
                    vol_col   = raw["Volume"][etf]
                else:
                    continue

                closes  = [float(c) for c in close_col.dropna()]
                volumes = [int(v)   for v in vol_col.dropna()]
                if len(closes) < 10:
                    continue

                scores = _flow_score(closes, volumes)
                if scores:
                    sectors[etf] = {
                        "name":        sector_name,
                        "etf":         etf,
                        "price":       round(closes[-1], 2),
                        **scores,
                    }
            except Exception as exc:
                log.debug("Sector %s failed: %s", etf, exc)
                continue

        if not sectors:
            result["error"] = "Could not compute any sector flows"
            _cache = (result, time.time())
            return result

        # Rank sectors by flow score
        ranked = sorted(sectors.values(), key=lambda x: x.get("flow_score", 0), reverse=True)
        hot    = [s for s in ranked if s.get("flow_score", 0) > 0][:3]
        cold   = [s for s in ranked if s.get("flow_score", 0) < 0][-3:]

        result.update({
            "available":  True,
            "sectors":    sectors,
            "hot_sectors": [s["etf"] for s in hot],
            "cold_sectors": [s["etf"] for s in cold],
            "ranked":     [{"etf": s["etf"], "name": s["name"],
                             "flow_score": s["flow_score"],
                             "ret_5d": s["ret_5d"]} for s in ranked],
        })
        log.info("Sector rotation: hot=%s cold=%s",
                 [s["etf"] for s in hot], [s["etf"] for s in cold])

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("sector_rotation failed: %s", exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache = (result, time.time())
    return result
