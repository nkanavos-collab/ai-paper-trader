import time
import yfinance as yf
from config import EUR_USD_FALLBACK, MARKET_CACHE_SECONDS

_quote_cache: dict[str, tuple[dict, float]] = {}


def _cached(symbol: str, data: dict) -> dict:
    _quote_cache[symbol] = (data, time.time())
    return data


def get_eur_usd_rate() -> float:
    try:
        ticker = yf.Ticker("EURUSD=X")
        hist = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return EUR_USD_FALLBACK


def get_quote(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _quote_cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < MARKET_CACHE_SECONDS:
        return cached

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        hist = ticker.history(period="5d", interval="1d")

        if hist.empty:
            return _cached(symbol, {"error": f"No data found for {symbol}"})

        price = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

        eur_usd = get_eur_usd_rate()

        return _cached(symbol, {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "price_usd": price,
            "price_eur": price / eur_usd,
            "eur_usd_rate": eur_usd,
            "change_pct": change_pct,
            "prev_close_usd": prev_close,
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps": info.get("trailingEps"),
            "revenue_growth": info.get("revenueGrowth"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "short_name": info.get("shortName") or symbol,
            "currency": info.get("currency", "USD"),
        })
    except Exception as e:
        return {"error": str(e)}


def get_history(symbol: str, period: str = "3mo") -> list[dict]:
    try:
        ticker = yf.Ticker(symbol.upper())
        hist = ticker.history(period=period)
        eur_usd = get_eur_usd_rate()
        records = []
        for date, row in hist.iterrows():
            records.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]) / eur_usd, 4),
                "high": round(float(row["High"]) / eur_usd, 4),
                "low": round(float(row["Low"]) / eur_usd, 4),
                "close": round(float(row["Close"]) / eur_usd, 4),
                "volume": int(row["Volume"]),
            })
        return records
    except Exception:
        return []
