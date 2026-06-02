"""
Autonomous paper trading router.
PAPER TRADING ONLY — no real money, no real broker.

IMPORTANT: trading.auto_trader is imported LAZILY inside each function.
This means this module can always be imported even if auto_trader has issues.
"""

import json
import threading
import logging
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from app_templates import templates
from database import db
from config import PAPER_TRADING_ONLY, STARTING_BALANCE_EUR

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-trading")


def _at():
    """Lazy import of trading.auto_trader — called inside request handlers only."""
    import trading.auto_trader as m
    return m


def _decision_chart(decisions: list) -> str:
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0, "SKIP": 0,
              "executed_buys": 0, "executed_sells": 0}
    for d in decisions:
        a = d.get("action", "SKIP")
        if a in counts:
            counts[a] += 1
        r = d.get("result") or ""
        if a == "BUY"  and "executed" in r: counts["executed_buys"]  += 1
        if a == "SELL" and "executed" in r: counts["executed_sells"] += 1
    return json.dumps(counts)


def _confidence_chart(decisions: list) -> str:
    b = {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0}
    for d in decisions:
        if d.get("action") != "BUY":
            continue
        c = d.get("confidence") or 0
        if c <= 3: b["1-3"] += 1
        elif c <= 6: b["4-6"] += 1
        elif c <= 8: b["7-8"] += 1
        else: b["9-10"] += 1
    return json.dumps(b)


@router.get("")
def auto_trading_page(request: Request, msg: str = ""):
    try:
        at       = _at()
        status   = at.get_status()
        decisions = db.get_auto_decisions(limit=150)
        positions = db.get_all_positions()

        flash = {
            "run_started": ("Run started in background — refresh in ~30 s.", "info"),
            "paused":      ("Autonomous trading paused.", "warning"),
            "resumed":     ("Autonomous trading resumed.", "success"),
            "sold":        ("Position force-closed.", "success"),
            "no_position": ("No open position found.", "danger"),
        }.get(msg)

        return templates.TemplateResponse(request, "auto_trading.html", {
            "status":           status,
            "decisions":        decisions,
            "positions":        positions,
            "active":           "auto-trading",
            "message":          flash[0] if flash else None,
            "msg_type":         flash[1] if flash else "info",
            "chart_decisions":  _decision_chart(decisions),
            "chart_confidence": _confidence_chart(decisions),
        })

    except Exception:
        tb = traceback.format_exc()
        log.error("auto_trading_page error:\n%s", tb)
        tb_html = tb.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Auto Trading Error</title></head>
<body style="background:#0b0e11;color:#eaecef;font-family:monospace;padding:2rem;max-width:900px;margin:0 auto">
  <h2 style="color:#f6465d">&#9888; Auto Trading — failed to load</h2>
  <p style="color:#848e9c;margin-bottom:1.5rem">
    Copy the traceback below and share it — it shows the exact line that failed.
  </p>
  <pre style="background:#1e2329;padding:1.5rem;border-radius:10px;color:#e6edf3;
              overflow:auto;font-size:.82rem;line-height:1.6;
              border:1px solid #2b3139">{tb_html}</pre>
  <a href="/" style="color:#3a85f7;text-decoration:none">← Back to Dashboard</a>
</body></html>""", status_code=500)


@router.post("/pause")
def pause_trading():
    _at().pause()
    return RedirectResponse("/auto-trading?msg=paused", status_code=303)


@router.post("/resume")
def resume_trading():
    _at().resume()
    return RedirectResponse("/auto-trading?msg=resumed", status_code=303)


@router.post("/run")
def manual_run():
    try:
        at = _at()
        thread = threading.Thread(
            target=at.run_once, daemon=True, name="auto-trader-manual"
        )
        thread.start()
        log.info("[AUTO] Manual run triggered via UI")
    except Exception:
        log.error("Manual run failed to start:\n%s", traceback.format_exc())
    return RedirectResponse("/auto-trading?msg=run_started", status_code=303)


@router.post("/force-sell/{symbol}")
def force_sell(symbol: str):
    from trading.market import get_quote
    from trading.engine import sell as engine_sell

    symbol = symbol.upper()
    pos = db.get_position(symbol)
    if not pos or pos["quantity"] <= 0:
        return RedirectResponse("/auto-trading?msg=no_position", status_code=303)

    quote     = get_quote(symbol)
    price_eur = quote.get("price_eur", pos["avg_cost_eur"])
    price_usd = quote.get("price_usd", 0.0)

    trade  = engine_sell(symbol, pos["quantity"],
                         notes="[MANUAL OVERRIDE] Force close via auto-trading page")
    run_id = "manual_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    db.save_auto_decision(
        run_id=run_id, action="SELL", symbol=symbol, confidence=0,
        price_usd=price_usd, price_eur=price_eur,
        position_size_eur=price_eur * pos["quantity"],
        reason="Manual override: force close via admin page",
        sources=[], result="executed" if trade.success else f"failed: {trade.message}",
    )
    return RedirectResponse("/auto-trading?msg=sold", status_code=303)
