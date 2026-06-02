"""
AI Portfolio Review — weekly Claude analysis of the full portfolio.

Produces a structured critique:
  - What's working (positions beating their entry thesis)
  - What to reconsider (positions where conviction has changed)
  - Sizing notes (over/under-weight given current conviction scores)
  - Overall assessment

Reviews are stored in portfolio_reviews and surfaced on the dashboard.
Falls back gracefully when no API key is set.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

log = logging.getLogger(__name__)

_REVIEW_PROMPT = """You are reviewing a paper trading portfolio. Analyse the positions below and produce a structured portfolio review.

Portfolio snapshot:
{portfolio_json}

Recent auto-trading decisions (last 7 days):
{decisions_json}

Prediction accuracy stats:
{accuracy_json}

Write a review with these exact sections (keep each section to 2-4 bullet points):

## What's Working
- [positions/signals that are performing as expected]

## What to Reconsider
- [positions or decisions that look weaker on reflection]

## Position Sizing
- [observations on concentration, over/under-weighting]

## Overall Assessment
- [1-2 sentences on portfolio health and priority action]

Be direct and specific. Reference actual tickers. No fluff."""


def run_review() -> dict | None:
    """
    Generate and store a portfolio review. Returns the saved review dict or None.
    Skips if a review was already generated within the last 7 days.
    """
    if _review_generated_recently(days=7):
        log.info("[REVIEW] Skipping — review generated within last 7 days")
        return None

    review_text = _generate_review()
    if not review_text:
        return None

    return _save_review(review_text)


def get_latest_review() -> dict | None:
    """Return the most recent portfolio review, or None."""
    from database.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM portfolio_reviews ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    r = dict(row)
    try:
        r["positions_json"] = json.loads(r.get("positions_json") or "[]")
    except Exception:
        pass
    return r


def get_all_reviews(limit: int = 10) -> list[dict]:
    from database.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_reviews ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Internal ──────────────────────────────────────────────────────────────────

def _review_generated_recently(days: int = 7) -> bool:
    review = get_latest_review()
    if not review:
        return False
    try:
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(review["created_at"])
        ).total_seconds() / 86400
        return age < days
    except Exception:
        return False


def _build_context() -> tuple[str, str, str]:
    """Build the three JSON context blocks for the prompt."""
    from trading.portfolio import get_portfolio_summary
    from database.db import get_auto_decisions
    from database.predictions import get_accuracy_stats

    try:
        summary = get_portfolio_summary()
        positions_data = [
            {
                "symbol":             p.symbol,
                "quantity":           round(p.quantity, 4),
                "avg_cost_eur":       round(p.avg_cost_eur, 4),
                "current_price_eur":  round(p.current_price_eur, 4),
                "market_value_eur":   round(p.market_value_eur, 2),
                "unrealised_pnl_pct": round(p.unrealised_pnl_pct or 0, 2),
                "entry_reason":       p.entry_reason or "",
            }
            for p in summary.positions
        ]
        portfolio_json = json.dumps({
            "cash_eur":           round(summary.cash_eur, 2),
            "total_portfolio_eur": round(summary.total_portfolio_eur, 2),
            "total_pnl_pct":      round(summary.total_pnl_pct or 0, 2),
            "positions":          positions_data,
        }, indent=2)
    except Exception as exc:
        portfolio_json = json.dumps({"error": str(exc)})

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent = [
            d for d in get_auto_decisions(limit=30)
            if d.get("timestamp", "") >= cutoff
        ]
        decisions_json = json.dumps(
            [{"symbol": d["symbol"], "action": d["action"],
              "reason": (d.get("reason") or "")[:120],
              "result": d.get("result", "")} for d in recent[:15]],
            indent=2
        )
    except Exception:
        decisions_json = "[]"

    try:
        accuracy_json = json.dumps(get_accuracy_stats(), indent=2)
    except Exception:
        accuracy_json = "{}"

    return portfolio_json, decisions_json, accuracy_json


def _generate_review() -> str | None:
    portfolio_json, decisions_json, accuracy_json = _build_context()

    prompt = _REVIEW_PROMPT.format(
        portfolio_json=portfolio_json,
        decisions_json=decisions_json,
        accuracy_json=accuracy_json,
    )

    if not ANTHROPIC_API_KEY:
        log.info("[REVIEW] No API key — generating rule-based review")
        return _rule_based_review(portfolio_json)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        log.warning("[REVIEW] Claude call failed: %s — falling back to rule-based", exc)
        return _rule_based_review(portfolio_json)


def _rule_based_review(portfolio_json: str) -> str:
    """Simple rule-based review when no API key is available."""
    try:
        data      = json.loads(portfolio_json)
        positions = data.get("positions", [])
        cash_pct  = data["cash_eur"] / max(data["total_portfolio_eur"], 0.01) * 100
        winners   = [p for p in positions if p.get("unrealised_pnl_pct", 0) > 5]
        losers    = [p for p in positions if p.get("unrealised_pnl_pct", 0) < -3]
    except Exception:
        return "## Overall Assessment\n- Unable to generate review — portfolio data unavailable."

    lines = ["## What's Working"]
    if winners:
        for w in winners[:3]:
            lines.append(f"- {w['symbol']} up {w['unrealised_pnl_pct']:+.1f}% — tracking above entry")
    else:
        lines.append("- No positions currently in profit")

    lines += ["\n## What to Reconsider"]
    if losers:
        for l in losers[:3]:
            lines.append(f"- {l['symbol']} down {l['unrealised_pnl_pct']:+.1f}% — review original thesis")
    else:
        lines.append("- No positions at significant loss currently")

    lines += ["\n## Position Sizing"]
    if cash_pct > 60:
        lines.append(f"- Portfolio is {cash_pct:.0f}% cash — consider deploying into high-conviction opportunities")
    elif cash_pct < 10:
        lines.append(f"- Only {cash_pct:.0f}% cash remaining — limited capacity for new positions")
    else:
        lines.append(f"- {cash_pct:.0f}% cash — reasonable balance of deployment and dry powder")

    lines += ["\n## Overall Assessment"]
    pnl = data.get("total_pnl_pct", 0)
    lines.append(f"- Portfolio at {pnl:+.1f}% overall. {len(positions)} open position(s).")
    lines.append("- (AI review unavailable — set ANTHROPIC_API_KEY for detailed analysis)")

    return "\n".join(lines)


def _save_review(review_text: str) -> dict:
    from database.db import get_conn, _now
    from trading.portfolio import get_portfolio_summary

    try:
        summary = get_portfolio_summary()
        positions_json = json.dumps([
            {"symbol": p.symbol, "market_value_eur": round(p.market_value_eur, 2)}
            for p in summary.positions
        ])
    except Exception:
        positions_json = "[]"

    now = _now()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO portfolio_reviews (review_text, positions_json, model_used, created_at)
            VALUES (?, ?, ?, ?)
        """, (review_text, positions_json, ANTHROPIC_MODEL if ANTHROPIC_API_KEY else "rule-based", now))
        rid = cur.lastrowid

    log.info("[REVIEW] Saved review #%d", rid)
    return {"id": rid, "review_text": review_text, "created_at": now}
