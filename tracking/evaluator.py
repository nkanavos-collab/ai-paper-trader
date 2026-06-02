"""
Lazy evaluator — called on page load.

For each pending prediction_evaluation whose due_at has passed,
fetches the historical closing price from yfinance and records the result.
"""

from datetime import datetime, timezone, timedelta
from database.predictions import (
    get_pending_evaluations,
    save_evaluation,
    skip_evaluation,
)


def _closing_price_on_or_after(symbol: str, target_date) -> float | None:
    """Return the first available closing price on or after target_date (handles weekends)."""
    try:
        import yfinance as yf
        from datetime import date as _date

        if isinstance(target_date, str):
            target_date = datetime.fromisoformat(target_date).date()
        elif isinstance(target_date, datetime):
            target_date = target_date.date()

        # Fetch up to 7 calendar days forward to skip weekends/holidays
        end = target_date + timedelta(days=7)
        hist = yf.Ticker(symbol).history(
            start=target_date.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        if not hist.empty:
            return float(hist["Close"].iloc[0])

        # If the target date is today or in the future, use latest available price
        if target_date >= datetime.now(timezone.utc).date():
            hist2 = yf.Ticker(symbol).history(period="2d")
            if not hist2.empty:
                return float(hist2["Close"].iloc[-1])

    except Exception:
        pass
    return None


def _get_eur_usd() -> float:
    try:
        import yfinance as yf
        h = yf.Ticker("EURUSD=X").history(period="1d", interval="1m")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return 1.08


def evaluate_pending(max_evals: int = 30) -> int:
    """Evaluate up to max_evals pending predictions. Returns count evaluated."""
    pending = get_pending_evaluations()
    if not pending:
        return 0

    eur_usd = _get_eur_usd()
    count = 0

    for ev in pending[:max_evals]:
        try:
            symbol = ev["symbol"]
            rec_price_usd = ev["price_usd"]
            recommendation = ev["recommendation"]
            due_at = ev["due_at"]

            eval_price_usd = _closing_price_on_or_after(symbol, due_at)

            if eval_price_usd is None:
                # Price not yet available — leave as pending
                continue

            eval_price_eur = eval_price_usd / eur_usd
            return_pct = (eval_price_usd - rec_price_usd) / rec_price_usd * 100

            if recommendation == "BUY":
                direction_correct = 1 if eval_price_usd > rec_price_usd else 0
            elif recommendation == "SELL":
                direction_correct = 1 if eval_price_usd < rec_price_usd else 0
            else:  # HOLD — neutral
                direction_correct = None

            save_evaluation(
                ev["id"], eval_price_usd, eval_price_eur,
                round(return_pct, 4), direction_correct,
            )
            count += 1

        except Exception as exc:
            print(f"[evaluator] {ev.get('symbol')} eval {ev.get('id')} failed: {exc}")

    return count
