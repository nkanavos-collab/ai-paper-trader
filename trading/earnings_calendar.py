"""
Earnings calendar — fetch upcoming earnings dates for held positions + watchlist.

Uses yfinance `calendar` data. Fetches in parallel; gracefully skips on failure.
"""

import logging
import concurrent.futures
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


def _fetch_earnings_date(symbol: str) -> dict | None:
    """Return {symbol, company, earnings_date, days_until} or None."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        cal = t.calendar  # dict with 'Earnings Date' key (list of timestamps)

        earnings_dates = []
        if cal is not None and isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            if hasattr(raw, "tolist"):
                raw = raw.tolist()
            if isinstance(raw, list):
                earnings_dates = raw

        if not earnings_dates:
            return None

        now = datetime.now(timezone.utc)
        # Find the nearest future date
        future_dates = []
        for d in earnings_dates:
            try:
                if hasattr(d, "to_pydatetime"):
                    dt = d.to_pydatetime()
                elif isinstance(d, datetime):
                    dt = d
                else:
                    dt = datetime.fromisoformat(str(d))

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                if dt >= now - timedelta(days=1):
                    future_dates.append(dt)
            except Exception:
                continue

        if not future_dates:
            return None

        next_date = min(future_dates)
        days_until = (next_date - now).days

        company = ""
        try:
            info = t.fast_info
            company = getattr(info, "company_name", "") or ""
        except Exception:
            pass

        return {
            "symbol":       symbol,
            "company":      company or symbol,
            "earnings_date": next_date.strftime("%Y-%m-%d"),
            "days_until":   days_until,
        }

    except Exception as exc:
        log.debug("[EARNINGS] %s: %s", symbol, exc)
        return None


def get_earnings_calendar(symbols: list[str], max_days_ahead: int = 30) -> list[dict]:
    """
    Fetch earnings dates for a list of symbols in parallel.
    Returns sorted list of upcoming events within max_days_ahead days.
    """
    if not symbols:
        return []

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_earnings_date, sym): sym for sym in symbols}
        for fut in concurrent.futures.as_completed(futures):
            try:
                r = fut.result()
                if r and 0 <= r["days_until"] <= max_days_ahead:
                    results.append(r)
            except Exception:
                pass

    return sorted(results, key=lambda x: x["days_until"])


def get_dashboard_earnings(held_symbols: list[str], watchlist: list[str]) -> dict:
    """
    Return earnings calendar split into held positions and watchlist.
    Limits to 14-day look-ahead for held, 30-day for watchlist.
    """
    all_symbols = list(set(held_symbols + watchlist))

    calendar = get_earnings_calendar(all_symbols, max_days_ahead=30)

    held_set = {s.upper() for s in held_symbols}

    held_events     = [e for e in calendar if e["symbol"] in held_set and e["days_until"] <= 14]
    watchlist_events = [e for e in calendar if e["symbol"] not in held_set]

    # Colour coding for urgency
    def _urgency(days: int) -> str:
        if days <= 2:  return "danger"
        if days <= 7:  return "warning"
        return "secondary"

    for e in held_events + watchlist_events:
        e["urgency"] = _urgency(e["days_until"])
        if e["days_until"] == 0:
            e["days_label"] = "Today"
        elif e["days_until"] == 1:
            e["days_label"] = "Tomorrow"
        else:
            e["days_label"] = f"in {e['days_until']}d"

    return {
        "held":      held_events,
        "watchlist": watchlist_events[:8],
        "total":     len(calendar),
    }
