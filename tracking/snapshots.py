"""
Daily portfolio snapshot — records portfolio value + benchmark prices once per day.

Used by:
  - Equity curve chart (portfolio value over time)
  - Benchmark comparison (SPY / QQQ)
  - Risk metrics (max drawdown, rolling returns)
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


# ── Record a snapshot ─────────────────────────────────────────────────────────

def record_snapshot() -> bool:
    """
    Save today's portfolio value + SPY/QQQ prices.
    Idempotent — calling more than once per day is safe (UNIQUE on snapshot_date).
    Returns True if a new snapshot was written.
    """
    from trading.portfolio import get_portfolio_summary
    from database.db import get_conn, _now

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        summary = get_portfolio_summary()
    except Exception as exc:
        log.warning("[SNAPSHOT] Could not get portfolio summary: %s", exc)
        return False

    spy_price = _bench_price("SPY")
    qqq_price = _bench_price("QQQ")

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM portfolio_snapshots WHERE snapshot_date = ?", (today,)
        ).fetchone()

        if existing:
            # Update in-place so intra-day value is kept current
            conn.execute("""
                UPDATE portfolio_snapshots
                SET portfolio_eur = ?, cash_eur = ?, positions_eur = ?,
                    spy_price = ?, qqq_price = ?
                WHERE snapshot_date = ?
            """, (
                round(summary.total_portfolio_eur, 4),
                round(summary.cash_eur, 4),
                round(summary.total_market_value_eur, 4),
                spy_price, qqq_price, today,
            ))
            return False

        conn.execute("""
            INSERT INTO portfolio_snapshots
            (snapshot_date, portfolio_eur, cash_eur, positions_eur,
             spy_price, qqq_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            round(summary.total_portfolio_eur, 4),
            round(summary.cash_eur, 4),
            round(summary.total_market_value_eur, 4),
            spy_price, qqq_price, _now(),
        ))

    log.info("[SNAPSHOT] Saved %s — portfolio €%.2f", today, summary.total_portfolio_eur)
    return True


def _bench_price(symbol: str) -> float | None:
    try:
        from trading.market import get_quote
        q = get_quote(symbol)
        return q.get("price_usd") if "error" not in q else None
    except Exception:
        return None


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_snapshots(days: int = 90) -> list[dict]:
    """Return up to `days` days of snapshots, oldest first."""
    from database.db import get_conn
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT snapshot_date, portfolio_eur, cash_eur, positions_eur,
                   spy_price, qqq_price
            FROM portfolio_snapshots
            WHERE snapshot_date >= ?
            ORDER BY snapshot_date ASC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_equity_chart_data(days: int = 90) -> dict:
    """
    Return chart-ready dict with portfolio + normalised benchmark series.
    Both SPY and QQQ are normalised to the portfolio starting value so
    they overlay on the same axis.
    """
    from config import STARTING_BALANCE_EUR

    snaps = get_snapshots(days)
    if not snaps:
        return {"labels": [], "portfolio": [], "spy": [], "qqq": [], "has_data": False}

    labels    = [s["snapshot_date"] for s in snaps]
    portfolio = [s["portfolio_eur"] for s in snaps]

    # Normalise benchmarks to portfolio start value
    start_val = portfolio[0] if portfolio else STARTING_BALANCE_EUR

    spy_raw = [s["spy_price"] for s in snaps]
    qqq_raw = [s["qqq_price"] for s in snaps]

    spy_start = next((v for v in spy_raw if v), None)
    qqq_start = next((v for v in qqq_raw if v), None)

    spy_norm = [
        round(v / spy_start * start_val, 4) if (v and spy_start) else None
        for v in spy_raw
    ]
    qqq_norm = [
        round(v / qqq_start * start_val, 4) if (v and qqq_start) else None
        for v in qqq_raw
    ]

    # Returns vs start
    first = portfolio[0]
    last  = portfolio[-1]
    port_return_pct = round((last - first) / first * 100, 2) if first else 0

    spy_first = next((v for v in spy_norm if v), None)
    spy_last  = next((v for v in reversed(spy_norm) if v), None)
    spy_return_pct = round((spy_last - spy_first) / spy_first * 100, 2) if spy_first else None

    qqq_first = next((v for v in qqq_norm if v), None)
    qqq_last  = next((v for v in reversed(qqq_norm) if v), None)
    qqq_return_pct = round((qqq_last - qqq_first) / qqq_first * 100, 2) if qqq_first else None

    return {
        "has_data":         True,
        "labels":           labels,
        "portfolio":        portfolio,
        "spy":              spy_norm,
        "qqq":              qqq_norm,
        "port_return_pct":  port_return_pct,
        "spy_return_pct":   spy_return_pct,
        "qqq_return_pct":   qqq_return_pct,
        "days":             len(snaps),
    }
