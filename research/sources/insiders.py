"""Insider transactions via yfinance."""
import logging
import time
from datetime import datetime, timezone
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 6  # 6 hours


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if f != f else f  # NaN guard
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int:
    try:
        f = float(val)
        return int(f) if f == f else 0
    except (TypeError, ValueError):
        return 0


def get_insider_activity(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "transactions": []}

    try:
        ticker = yf.Ticker(symbol)

        df = None
        for attr in ("insider_transactions", "insider_purchases"):
            try:
                candidate = getattr(ticker, attr, None)
                if candidate is not None and not candidate.empty:
                    df = candidate
                    break
            except Exception:
                pass

        if df is None or df.empty:
            result["error"] = "No insider data available"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        transactions = []
        for _, row in df.head(15).iterrows():
            name = str(
                row.get("Insider Trading") or row.get("Name") or row.get("Insider") or "Unknown"
            ).strip()[:40]
            relation = str(
                row.get("Relationship") or row.get("Title") or row.get("Position") or ""
            ).strip()[:30]
            date_val = row.get("Date") or row.get("Start Date") or row.get("Transaction Date")
            txn_type = str(
                row.get("Transaction") or row.get("Type") or row.get("Action") or ""
            ).strip()

            if not txn_type:
                continue

            shares    = _safe_int(row.get("#Shares") or row.get("Shares") or row.get("Shares Traded") or 0)
            value_usd = _safe_float(row.get("Value") or row.get("Value ($)") or 0)

            transactions.append({
                "insider":     name,
                "relation":    relation,
                "date":        str(date_val)[:10] if date_val else "",
                "transaction": txn_type,
                "shares":      shares,
                "value_usd":   value_usd,
            })

        if not transactions:
            result["error"] = "Parsed 0 valid transactions"
            log.warning("%s insiders: no valid transactions parsed", symbol)
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        result["available"]     = True
        result["transactions"]  = transactions

        buys  = sum(1 for t in transactions
                    if any(k in t["transaction"].lower()
                           for k in ("purchase", "buy", "acquisition")))
        sells = sum(1 for t in transactions
                    if any(k in t["transaction"].lower()
                           for k in ("sale", "sell", "disposed")))
        result["buy_count"]  = buys
        result["sell_count"] = sells
        result["net_bias"]   = (
            "bullish" if buys > sells else
            "bearish" if sells > buys else "neutral"
        )
        log.info("%s insiders: %d buys, %d sells from %d transactions",
                 symbol, buys, sells, len(transactions))

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s insider_transactions failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
