"""
Autonomous paper trading engine.

AUTONOMOUS PAPER TRADING ONLY — NO REAL MONEY — NO REAL BROKER — NO REAL ORDERS.

The AI analyses the watchlist once per day, applies rule-based risk management,
and executes simulated buy/sell decisions without user confirmation.
Every decision is logged to the auto_decisions table.
"""

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from config import (
    AUTO_PAPER_TRADING, PAPER_TRADING_ONLY,
    WATCHLIST, SCANNER_UNIVERSE,
    AUTO_MAX_POSITION_PCT, AUTO_MAX_POSITIONS,
    AUTO_STOP_LOSS_PCT, AUTO_TAKE_PROFIT_PCT,
    AUTO_MIN_CONFIDENCE, AUTO_SELL_CONFIDENCE,
    AUTO_RUN_INTERVAL_HRS,
    SCANNER_RUN_INTERVAL_HRS,
)
from database import db
from trading.engine import buy as engine_buy, sell as engine_sell

log = logging.getLogger(__name__)

# ── Safety gate ───────────────────────────────────────────────────────────────
assert PAPER_TRADING_ONLY, (
    "FATAL: PAPER_TRADING_ONLY must be True. "
    "Autonomous trading refuses to run with real-money mode."
)

# ── Background scheduler state ────────────────────────────────────────────────
_run_lock         = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_active = False


# ── Public state API ──────────────────────────────────────────────────────────

def is_paused() -> bool:
    return db.get_auto_setting("paused") == "true"


def is_running() -> bool:
    return db.get_auto_setting("running") == "true"


def pause() -> None:
    db.set_auto_setting("paused", "true")
    log.info("[AUTO] Autonomous trading PAUSED by user")


def resume() -> None:
    db.set_auto_setting("paused", "false")
    log.info("[AUTO] Autonomous trading RESUMED by user")


def get_status() -> dict:
    last_run_at  = db.get_auto_setting("last_run_at") or ""
    last_run_id  = db.get_auto_setting("last_run_id") or ""
    paused       = is_paused()
    running      = is_running()
    active       = AUTO_PAPER_TRADING and not paused

    recent = db.get_auto_decisions(limit=10)

    return {
        "enabled":          AUTO_PAPER_TRADING,
        "paused":           paused,
        "running":          running,
        "active":           active,
        "last_run_at":      last_run_at,
        "last_run_id":      last_run_id,
        "trades_today":     db.count_auto_trades_today(),
        "recent_decisions": recent,
        "watchlist":        WATCHLIST,
        "risk_rules": {
            "max_position_pct":  AUTO_MAX_POSITION_PCT,
            "max_positions":     AUTO_MAX_POSITIONS,
            "stop_loss_pct":     AUTO_STOP_LOSS_PCT,
            "take_profit_pct":   AUTO_TAKE_PROFIT_PCT,
            "min_confidence":    AUTO_MIN_CONFIDENCE,
            "sell_confidence":   AUTO_SELL_CONFIDENCE,
            "run_interval_hrs":  AUTO_RUN_INTERVAL_HRS,
        },
    }


# ── Main run ──────────────────────────────────────────────────────────────────

def run_once() -> dict:
    """
    Execute one full autonomous trading cycle.
    Thread-safe — concurrent calls return immediately with {skipped}.
    PAPER TRADING ONLY.
    """
    if not AUTO_PAPER_TRADING:
        log.info("[AUTO] AUTO_PAPER_TRADING=False — run skipped")
        return {"skipped": True, "reason": "AUTO_PAPER_TRADING disabled in config"}

    if is_paused():
        log.info("[AUTO] Paused — run skipped")
        return {"skipped": True, "reason": "paused by user"}

    if not _run_lock.acquire(blocking=False):
        log.info("[AUTO] Run already in progress — skipped")
        return {"skipped": True, "reason": "run already in progress"}

    db.set_auto_setting("running", "true")
    run_id = _make_run_id()
    log.info("[AUTO] ── RUN START %s ──────────────────────────", run_id)

    decisions: list[dict] = []
    try:
        # Phase 1: exit checks on existing positions
        decisions.extend(_check_positions(run_id))

        # Phase 2: scan watchlist for new buys
        decisions.extend(_scan_and_buy(run_id))

        db.set_auto_setting("last_run_at", datetime.now(timezone.utc).isoformat())
        db.set_auto_setting("last_run_id", run_id)
        _record_snapshot_safe()

    except Exception as exc:
        log.error("[AUTO] Run error: %s", exc, exc_info=True)
    finally:
        db.set_auto_setting("running", "false")
        _run_lock.release()

    buys  = sum(1 for d in decisions if d["action"] == "BUY"  and "executed" in d["result"])
    sells = sum(1 for d in decisions if d["action"] == "SELL" and "executed" in d["result"])
    log.info("[AUTO] ── RUN END %s: %d buys, %d sells, %d total ──",
             run_id, buys, sells, len(decisions))

    return {"run_id": run_id, "decisions": decisions, "buys": buys, "sells": sells}


# ── Phase 1: position exit checks ────────────────────────────────────────────

def _check_positions(run_id: str) -> list[dict]:
    from trading.market import get_quote
    from research.analyst import analyse

    decisions = []
    for pos in db.get_all_positions():
        symbol   = pos["symbol"]
        avg_eur  = pos["avg_cost_eur"]
        qty      = pos["quantity"]

        quote = get_quote(symbol)
        if "error" in quote:
            log.warning("[AUTO] Skip position check %s — %s", symbol, quote["error"])
            continue

        cur_eur = quote["price_eur"]
        pnl_pct = (cur_eur - avg_eur) / avg_eur if avg_eur else 0.0

        sell_reason: str | None = None

        # Hard exits (price-based, no analysis needed)
        if pnl_pct <= -AUTO_STOP_LOSS_PCT:
            sell_reason = (
                f"STOP LOSS: {pnl_pct*100:+.1f}% ≤ −{AUTO_STOP_LOSS_PCT*100:.0f}% threshold"
            )
        elif pnl_pct >= AUTO_TAKE_PROFIT_PCT:
            sell_reason = (
                f"TAKE PROFIT: {pnl_pct*100:+.1f}% ≥ +{AUTO_TAKE_PROFIT_PCT*100:.0f}% threshold"
            )
        else:
            # Soft exit: check if AI signal has deteriorated (use cached research if recent)
            try:
                research  = analyse(symbol, force_refresh=False)
                conf      = int(research.get("confidence") or 5)
                rec       = research.get("recommendation", "HOLD")
                if rec == "SELL" and conf <= AUTO_SELL_CONFIDENCE:
                    sell_reason = (
                        f"AI SIGNAL SELL: confidence {conf}/10 ≤ {AUTO_SELL_CONFIDENCE}. "
                        f"{research.get('bear_thesis', '')[:150]}"
                    )
                    log.info("[AUTO] Signal sell %s: conf=%d, rec=%s", symbol, conf, rec)
            except Exception as exc:
                log.warning("[AUTO] Signal check error %s: %s", symbol, exc)

        if sell_reason:
            trade   = engine_sell(symbol, qty, notes=f"[AUTO] {sell_reason[:250]}")
            action  = "SELL"
            outcome = "executed" if trade.success else f"failed: {trade.message}"
            log.info("[AUTO] SELL %s → %s | %s", symbol, outcome, sell_reason[:60])
        else:
            action      = "HOLD"
            sell_reason = (
                f"P&L {pnl_pct*100:+.1f}% — within SL −{AUTO_STOP_LOSS_PCT*100:.0f}% "
                f"/ TP +{AUTO_TAKE_PROFIT_PCT*100:.0f}% bands"
            )
            outcome = "no action"

        dec = dict(
            action=action, symbol=symbol, confidence=0,
            price_usd=quote["price_usd"], price_eur=cur_eur,
            position_size_eur=cur_eur * qty,
            reason=sell_reason, sources=[], result=outcome,
        )
        db.save_auto_decision(run_id=run_id, **dec)
        decisions.append(dec)

    return decisions


# ── Phase 2: watchlist scan + buy ─────────────────────────────────────────────

def _scan_and_buy(run_id: str) -> list[dict]:
    from trading.market import get_quote
    from research.analyst import analyse

    decisions = []
    existing  = {p["symbol"] for p in db.get_all_positions()}
    n_open    = len(existing)

    if n_open >= AUTO_MAX_POSITIONS:
        log.info("[AUTO] Max positions (%d) reached — skipping buy scan", AUTO_MAX_POSITIONS)
        return decisions

    balance = db.get_balance()
    if balance < 5.0:
        log.info("[AUTO] Balance €%.2f too low — skipping buy scan", balance)
        return decisions

    candidates: list[dict] = []

    for symbol in WATCHLIST:
        if symbol in existing:
            log.debug("[AUTO] Skipping %s — already held", symbol)
            continue

        try:
            # Use cached research if < 12 hours old; otherwise force fresh
            force_fresh = _needs_fresh_analysis(symbol)
            research    = analyse(symbol, force_refresh=force_fresh)
            rec         = research.get("recommendation", "HOLD")
            conf        = int(research.get("confidence") or 5)
            sources     = research.get("sources_used", [])

            # Extract price from research raw_data (avoids an extra quote call)
            mkt       = _safe_mkt(research)
            price_usd = mkt.get("price_usd") or 0.0
            price_eur = mkt.get("price_eur") or 0.0

            log.info("[AUTO] Watchlist: %s → %s conf=%d/10", symbol, rec, conf)

            if rec == "BUY" and conf >= AUTO_MIN_CONFIDENCE:
                candidates.append({
                    "symbol": symbol, "conf": conf,
                    "bull":   research.get("bull_thesis", "")[:200],
                    "sources": sources,
                    "price_usd": price_usd, "price_eur": price_eur,
                })
            else:
                dec = dict(
                    action="SKIP", symbol=symbol, confidence=conf,
                    price_usd=price_usd, price_eur=price_eur, position_size_eur=0,
                    reason=(
                        f"Signal below threshold: {rec} conf={conf}/10 "
                        f"(need BUY conf≥{AUTO_MIN_CONFIDENCE})"
                    ),
                    sources=sources, result="skipped",
                )
                db.save_auto_decision(run_id=run_id, **dec)
                decisions.append(dec)

        except Exception as exc:
            log.error("[AUTO] Analysis error %s: %s", symbol, exc, exc_info=True)
            dec = dict(
                action="SKIP", symbol=symbol, confidence=0,
                price_usd=0, price_eur=0, position_size_eur=0,
                reason=f"Analysis error: {exc}",
                sources=[], result="error",
            )
            db.save_auto_decision(run_id=run_id, **dec)
            decisions.append(dec)

    if not candidates:
        log.info("[AUTO] No buy candidates from watchlist scan")
        return decisions

    # Rank by confidence (highest first), respect max positions
    candidates.sort(key=lambda c: c["conf"], reverse=True)
    slots = AUTO_MAX_POSITIONS - n_open

    for c in candidates[:slots]:
        balance = db.get_balance()
        if balance < 5.0:
            log.info("[AUTO] Balance €%.2f exhausted — stopping buys", balance)
            break

        symbol = c["symbol"]

        # Fresh execution price
        quote = get_quote(symbol)
        if "error" in quote:
            log.warning("[AUTO] Skip buy %s — quote error: %s", symbol, quote["error"])
            continue

        price_eur = quote["price_eur"]
        price_usd = quote["price_usd"]

        # Position sizing: 20% of portfolio, capped to 98% of remaining cash
        from trading.portfolio import get_portfolio_summary
        try:
            portfolio_eur = get_portfolio_summary().total_portfolio_eur
        except Exception:
            portfolio_eur = balance  # fallback

        position_eur = min(
            portfolio_eur * AUTO_MAX_POSITION_PCT,
            balance * 0.98,
        )
        position_eur = max(5.0, position_eur)

        quantity = position_eur / price_eur
        reason   = f"AI BUY signal: conf={c['conf']}/10. {c['bull']}"

        trade   = engine_buy(symbol, quantity, notes=f"[AUTO] {reason[:250]}")
        outcome = "executed" if trade.success else f"failed: {trade.message}"
        log.info("[AUTO] BUY %s: %.4f @ €%.4f = €%.2f conf=%d → %s",
                 symbol, quantity, price_eur, position_eur, c["conf"], outcome)

        dec = dict(
            action="BUY", symbol=symbol, confidence=c["conf"],
            price_usd=price_usd, price_eur=price_eur,
            position_size_eur=position_eur,
            reason=reason, sources=c["sources"], result=outcome,
        )
        db.save_auto_decision(run_id=run_id, **dec)
        decisions.append(dec)

    return decisions


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]


def _needs_fresh_analysis(symbol: str) -> bool:
    """Return True if cached research is missing or older than 12 hours."""
    row = db.get_latest_research(symbol)
    if not row:
        return True
    try:
        age_h = (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
        ).total_seconds() / 3600
        return age_h > 12
    except Exception:
        return True


def _safe_mkt(research: dict) -> dict:
    """Extract market sub-dict from research raw_data safely."""
    rd = research.get("raw_data")
    if isinstance(rd, dict):
        return rd.get("market") or {}
    return {}


# ── Background scheduler ──────────────────────────────────────────────────────

def _should_run_now() -> bool:
    if not AUTO_PAPER_TRADING or is_paused():
        return False
    # Only run on weekdays
    if datetime.now(timezone.utc).weekday() >= 5:
        return False
    last = db.get_auto_setting("last_run_at")
    if not last:
        return True
    try:
        elapsed_h = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last)
        ).total_seconds() / 3600
        return elapsed_h >= AUTO_RUN_INTERVAL_HRS
    except Exception:
        return True


def _scheduler_loop() -> None:
    global _scheduler_active
    log.info("[AUTO] Scheduler started (check every 5 min, run every %dh)", AUTO_RUN_INTERVAL_HRS)
    while _scheduler_active:
        try:
            if _should_run_now():
                log.info("[AUTO] Scheduled trading run triggered")
                run_once()
            if _should_run_scanner():
                log.info("[SCANNER] Scheduled scan triggered")
                run_scanner_and_alert()
            # Daily portfolio snapshot
            _record_snapshot_safe()
            # Weekly AI portfolio review
            _run_review_safe()
        except Exception as exc:
            log.error("[AUTO] Scheduler error: %s", exc, exc_info=True)
        # Sleep 5 minutes in 10-second increments so we can exit cleanly
        for _ in range(30):
            if not _scheduler_active:
                break
            time.sleep(10)
    log.info("[AUTO] Scheduler stopped")


def start_scheduler() -> None:
    global _scheduler_thread, _scheduler_active
    if not AUTO_PAPER_TRADING:
        log.info("[AUTO] AUTO_PAPER_TRADING=False — scheduler not started")
        return
    if _scheduler_thread and _scheduler_thread.is_alive():
        log.warning("[AUTO] Scheduler already running")
        return
    _scheduler_active = True
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="auto-trader-scheduler"
    )
    _scheduler_thread.start()
    log.info("[AUTO] Background scheduler started (daemon thread)")


def stop_scheduler() -> None:
    global _scheduler_active
    _scheduler_active = False
    log.info("[AUTO] Scheduler stop requested")


# ── Scheduled scanner run ─────────────────────────────────────────────────────

def run_scanner_and_alert() -> dict:
    """
    Run the opportunity scanner over SCANNER_UNIVERSE, fire alerts for top stocks,
    and persist scan results to DB. Called by the background scheduler.
    """
    from research.scanner import scan
    from tracking.alerts import maybe_send_alert

    log.info("[SCANNER] Starting scheduled scan — %d tickers", len(SCANNER_UNIVERSE))
    try:
        results = scan(SCANNER_UNIVERSE)
    except Exception as exc:
        log.error("[SCANNER] Scan failed: %s", exc, exc_info=True)
        return {"error": str(exc)}

    alerted: list[str] = []
    for item in results:
        fired = maybe_send_alert(
            symbol=item.get("symbol", ""),
            company_name=item.get("company_name", ""),
            score=item.get("score", 0),
            convergence_count=len(item.get("signals", [])),
            signals=item.get("signals", []),
            price_usd=item.get("price_usd"),
        )
        if fired:
            alerted.append(item["symbol"])

    log.info("[SCANNER] Done — %d results, %d alerts fired", len(results), len(alerted))
    db.set_auto_setting("last_scanner_run_at", datetime.now(timezone.utc).isoformat())
    return {"scanned": len(results), "alerted": alerted}


def _record_snapshot_safe() -> None:
    try:
        from tracking.snapshots import record_snapshot
        record_snapshot()
    except Exception as exc:
        log.warning("[SNAPSHOT] Failed: %s", exc)


def _run_review_safe() -> None:
    try:
        from trading.portfolio_review import run_review
        run_review()
    except Exception as exc:
        log.warning("[REVIEW] Failed: %s", exc)


def _should_run_scanner() -> bool:
    last = db.get_auto_setting("last_scanner_run_at")
    if not last:
        return True
    if datetime.now(timezone.utc).weekday() >= 5:
        return False
    try:
        elapsed_h = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last)
        ).total_seconds() / 3600
        return elapsed_h >= SCANNER_RUN_INTERVAL_HRS
    except Exception:
        return True
