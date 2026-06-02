"""Multi-quarter fundamental trends via yfinance."""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 4  # 4 hours


def get_quarterly_trends(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "quarters": []}

    try:
        ticker = yf.Ticker(symbol)

        df = None
        for attr in ("quarterly_income_stmt", "quarterly_financials"):
            try:
                candidate = getattr(ticker, attr, None)
                if candidate is not None and not candidate.empty:
                    df = candidate
                    break
            except Exception:
                pass

        if df is None or df.empty:
            result["error"] = "No quarterly data available"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        def _get(col, keys: list[str]) -> float | None:
            for k in keys:
                if k in df.index:
                    try:
                        v = df.loc[k, col]
                        if v is not None and str(v) not in ("nan", "None", "<NA>"):
                            return float(v)
                    except Exception:
                        pass
            return None

        quarters = []
        for col in list(df.columns)[:5]:
            q: dict = {"period": str(col)[:10]}
            q["revenue"]          = _get(col, ["Total Revenue", "Revenue"])
            q["net_income"]       = _get(col, ["Net Income", "Net Income Common Stockholders",
                                               "Net Income Applicable To Common Shares"])
            q["gross_profit"]     = _get(col, ["Gross Profit"])
            q["operating_income"] = _get(col, ["Operating Income", "EBIT"])

            if q["revenue"] and q["gross_profit"]:
                q["gross_margin_pct"] = round(q["gross_profit"] / q["revenue"] * 100, 1)
            if q["revenue"] and q["operating_income"]:
                q["operating_margin_pct"] = round(q["operating_income"] / q["revenue"] * 100, 1)

            quarters.append(q)

        if not quarters:
            result["error"] = "Could not parse quarterly data"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        result["available"] = True
        result["quarters"]  = quarters

        # YoY growth: compare most recent quarter (Q0) to same quarter one year ago (Q4)
        if len(quarters) >= 5:
            curr, yr_ago = quarters[0], quarters[4]
            if curr.get("revenue") and yr_ago.get("revenue") and yr_ago["revenue"] != 0:
                result["yoy_revenue_growth_pct"] = round(
                    (curr["revenue"] - yr_ago["revenue"]) / abs(yr_ago["revenue"]) * 100, 1
                )
            if curr.get("net_income") and yr_ago.get("net_income") and yr_ago["net_income"] != 0:
                result["yoy_earnings_growth_pct"] = round(
                    (curr["net_income"] - yr_ago["net_income"]) / abs(yr_ago["net_income"]) * 100, 1
                )

        # Sequential revenue trend across the last 3 quarters
        rev = [q["revenue"] for q in quarters[:4] if q.get("revenue")]
        if len(rev) >= 3:
            if   rev[0] > rev[1] > rev[2]: result["revenue_trend"] = "accelerating"
            elif rev[0] < rev[1] < rev[2]: result["revenue_trend"] = "decelerating"
            elif rev[0] > rev[2]:          result["revenue_trend"] = "growing"
            elif rev[0] < rev[2]:          result["revenue_trend"] = "declining"
            else:                           result["revenue_trend"] = "flat"

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s quarterly trends failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
