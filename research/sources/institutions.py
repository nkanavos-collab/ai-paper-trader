"""
Institutional ownership via yfinance.
Updated for yfinance 1.x API (column names changed significantly from 0.2.x).
"""
import logging
import time
from datetime import datetime, timezone, timedelta
import yfinance as yf

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 8


def _col(df, *names) -> str | None:
    """Return the first column name from df.columns that matches any of `names` (case-insensitive)."""
    if df is None:
        return None
    cols_lower = {c.lower(): c for c in df.columns}
    for n in names:
        if n.lower() in cols_lower:
            return cols_lower[n.lower()]
    return None


def get_institutional_data(symbol: str) -> dict:
    symbol = symbol.upper()
    cached, ts = _cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False}

    try:
        ticker = yf.Ticker(symbol)

        # ── Institutional % from major_holders ───────────────────────────────
        inst_pct: float | None = None
        insider_pct: float | None = None

        try:
            major = ticker.major_holders
            if major is not None and not major.empty:
                # yfinance 1.x: columns are "Value" and "Breakdown"
                # yfinance 0.2.x: positional — col 0 = pct, col 1 = description
                val_col  = _col(major, "Value", "value")
                desc_col = _col(major, "Breakdown", "Description", "description")

                for _, row in major.iterrows():
                    val  = row[val_col]  if val_col  else row.iloc[0]
                    desc = str(row[desc_col] if desc_col else row.iloc[1]).lower()

                    # Convert percentage string or float
                    try:
                        f = float(str(val).replace("%", "").strip())
                        # yfinance 1.x returns raw fraction (0.65) not percentage (65)
                        if f <= 1.01:
                            f = round(f * 100, 2)
                        if "institution" in desc:
                            inst_pct = round(f, 2)
                        elif "insider" in desc:
                            insider_pct = round(f, 2)
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("%s major_holders: %s", symbol, exc)

        # ── Top institutional holders ────────────────────────────────────────
        holders: list[dict] = []
        try:
            ih = ticker.institutional_holders
            if ih is not None and not ih.empty:
                # Detect column names robustly for yfinance 0.2.x and 1.x
                name_col  = _col(ih, "Holder", "holder", "Name", "name")
                share_col = _col(ih, "Shares", "shares", "sharesHeld", "Shares Held")
                pct_col   = _col(ih, "% Out", "pctHeld", "pctout", "% Held", "pct_held")
                date_col  = _col(ih, "Date Reported", "reportDate", "date", "Date")

                for _, row in ih.head(15).iterrows():
                    name = str(row[name_col]) if name_col else str(row.iloc[0])
                    if not name or name == "nan":
                        continue

                    shares = None
                    pct_out = None
                    date_str = ""

                    if share_col:
                        try:
                            shares = int(float(row[share_col]))
                        except Exception:
                            pass

                    if pct_col:
                        try:
                            v = float(row[pct_col])
                            # Normalise: yfinance 1.x returns fraction (0.05), 0.2.x returns pct (5.0)
                            pct_out = round(v * 100, 2) if v <= 1.01 else round(v, 2)
                        except Exception:
                            pass

                    if date_col:
                        try:
                            date_str = str(row[date_col])[:10]
                        except Exception:
                            pass

                    holders.append({
                        "name":    name[:40],
                        "shares":  shares,
                        "pct_out": pct_out,
                        "date":    date_str,
                    })
        except Exception as exc:
            log.debug("%s institutional_holders: %s", symbol, exc)

        if not holders and inst_pct is None:
            result["error"] = "No institutional data available from yfinance"
            result["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _cache[symbol] = (result, time.time())
            return result

        # ── Recency signal ───────────────────────────────────────────────────
        cutoff_recent = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent_filers = sum(1 for h in holders if h["date"] >= cutoff_recent)
        total_filers  = len(holders)
        accumulation_signal = (
            "active_filing_period"
            if total_filers > 0 and recent_filers >= total_filers * 0.6
            else "normal"
        )

        result.update({
            "available":           True,
            "inst_pct":            inst_pct,
            "insider_pct":         insider_pct,
            "holders":             holders,
            "holder_count":        len(holders),
            "recent_filers":       recent_filers,
            "accumulation_signal": accumulation_signal,
        })
        log.info("%s institutions: %s%% held, %d holders",
                 symbol, inst_pct, len(holders))

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s institutional data failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[symbol] = (result, time.time())
    return result
