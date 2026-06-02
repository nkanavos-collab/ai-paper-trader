import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from app_templates import templates
from trading.portfolio import get_portfolio_summary
from database.db import get_transactions, get_realized_pnl
from config import STARTING_BALANCE_EUR, WATCHLIST

router = APIRouter()


# ── Chart data helpers ────────────────────────────────────────────────────────

def _portfolio_history_chart() -> str:
    """Last 50 transactions as a balance-over-time line chart dataset."""
    txs = list(reversed(get_transactions(limit=50)))
    labels = ["Start"] + [t["timestamp"][:10] for t in txs]
    values = [STARTING_BALANCE_EUR] + [round(t["balance_after_eur"], 2) for t in txs]
    return json.dumps({"labels": labels, "values": values})


def _equity_chart_safe() -> dict:
    try:
        from tracking.snapshots import get_equity_chart_data
        return get_equity_chart_data(days=60)
    except Exception:
        return {"has_data": False}


def _earnings_safe(held_symbols: list) -> dict:
    try:
        from trading.earnings_calendar import get_dashboard_earnings
        return get_dashboard_earnings(held_symbols, WATCHLIST)
    except Exception:
        return {"held": [], "watchlist": [], "total": 0}


def _latest_review_safe() -> dict | None:
    try:
        from trading.portfolio_review import get_latest_review
        return get_latest_review()
    except Exception:
        return None


def _allocation_chart(summary) -> str:
    """Current portfolio allocation as a donut chart dataset."""
    labels, values = [], []
    for p in summary.positions:
        labels.append(p.symbol)
        values.append(round(p.market_value_eur, 2))
    if summary.cash_eur > 0.01:
        labels.append("Cash €")
        values.append(round(summary.cash_eur, 2))
    return json.dumps({"labels": labels, "values": values})


def _auto_status_safe() -> dict:
    try:
        from trading.auto_trader import get_status
        return get_status()
    except Exception:
        return {
            "enabled": False, "paused": False, "active": False,
            "running": False, "last_run_at": "", "trades_today": 0,
            "recent_decisions": [],
        }


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/")
def dashboard(request: Request):
    try:
        summary      = get_portfolio_summary()
        recent_txs   = get_transactions(limit=6)
        realized_pnl = get_realized_pnl()
        auto_status  = _auto_status_safe()

        held_symbols = [p.symbol for p in summary.positions]
        equity_chart = _equity_chart_safe()
        earnings     = _earnings_safe(held_symbols)
        review       = _latest_review_safe()

        return templates.TemplateResponse(request, "dashboard.html", {
            "summary":            summary,
            "recent_txs":         recent_txs,
            "realized_pnl":       realized_pnl,
            "starting_balance":   STARTING_BALANCE_EUR,
            "now":                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "active":             "dashboard",
            "auto_status":        auto_status,
            "chart_portfolio":    _portfolio_history_chart(),
            "chart_allocation":   _allocation_chart(summary),
            "equity_chart":       equity_chart,
            "equity_chart_json":  json.dumps(equity_chart),
            "earnings":           earnings,
            "review":             review,
        })
    except Exception as exc:
        return templates.TemplateResponse(request, "dashboard.html", {
            "error":  str(exc),
            "active": "dashboard",
        })
