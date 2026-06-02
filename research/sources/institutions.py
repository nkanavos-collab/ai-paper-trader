"""
Institutional ownership via yfinance.
Shows top holders, % institutional ownership, and whether recent 13F filings
suggest accumulation or distribution (based on reporting dates and % changes).
"""
import logging
import time
from datetime import datetime, timezone, timedelta
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 8  # 8 hours — 13F data is quarterly


def get_institutional_data(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False}

    try:
        ticker = yf.Ticker(symbol)

        # ── Major holders summary ────────────────────────────────────────────
        major = None
        try:
            major = ticker.major_holders
        except Exception:
            pass

        inst_pct: float | None = None
        insider_pct: float | None = None

        if major is not None and not major.empty:
            try:
                # major_holders is a 2-col DataFrame: Value, Description
                for _, row in major.iterrows():
                    desc = str(row.iloc[1]).lower()
                    val  = row.iloc[0]
                    try:
                        f = float(str(val).replace("%", "").strip())
                        if "institution" in desc:
                            inst_pct = round(f, 2)
                        elif "insider" in desc:
                            insider_pct = round(f, 2)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── Top institutional holders ────────────────────────────────────────
        ih = None
        try:
            ih = ticker.institutional_holders
        except Exception:
            pass

        holders: list[dict] = []
        if ih is not None and not ih.empty:
            for _, row in ih.head(15).iterrows():
                holder_name = str(row.get("Holder") or row.iloc[0] if len(row) > 0 else "")
                shares = None
                pct_out = None
                date_str = ""
                for col in row.index:
                    col_l = str(col).lower()
                    if "share" in col_l:
                        try: shares = int(float(row[col]))
                        except Exception: pass
                    elif "%" in col_l or "out" in col_l:
                        try: pct_out = round(float(row[col]) * 100, 2)
                        except Exception: pass
                    elif "date" in col_l:
                        try: date_str = str(row[col])[:10]
                        except Exception: pass
                if holder_name and holder_name != "nan":
                    holders.append({
                        "name":    holder_name[:40],
                        "shares":  shares,
                        "pct_out": pct_out,
                        "date":    date_str,
                    })

        if not holders and inst_pct is None:
            result["error"] = "No institutional data available"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        # ── Recency signal ───────────────────────────────────────────────────
        # If most recent filing dates are within last 60 days → active accumulation window
        cutoff_recent = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent_filers = sum(1 for h in holders if h["date"] >= cutoff_recent)
        total_filers  = len(holders)

        if recent_filers >= total_filers * 0.6:
            accumulation_signal = "active_filing_period"
        else:
            accumulation_signal = "normal"

        result.update({
            "available":           True,
            "inst_pct":            inst_pct,
            "insider_pct":         insider_pct,
            "holders":             holders,
            "holder_count":        len(holders),
            "recent_filers":       recent_filers,
            "accumulation_signal": accumulation_signal,
        })
        log.info("%s institutions: %.1f%% held, %d holders, %d recent",
                 symbol, inst_pct or 0, len(holders), recent_filers)

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s institutional_holders failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
