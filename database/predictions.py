"""CRUD operations for predictions and prediction_evaluations tables."""

from datetime import datetime, timezone, timedelta
from database.db import get_conn, _now


# ── Create ────────────────────────────────────────────────────────────────────

def create_prediction(
    symbol: str,
    company_name: str,
    recommendation: str,
    confidence: int,
    bull_thesis: str,
    bear_thesis: str,
    price_usd: float,
    price_eur: float,
    eur_usd_rate: float,
    reasoning_engine: str = "",
    sources_count: int = 0,
    research_id: int | None = None,
    conviction_score: float | None = None,
    signals_present: list | None = None,
) -> int:
    """Insert a prediction row and schedule the 4 evaluation windows."""
    import json as _json
    symbol = symbol.upper()
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    signals_json = _json.dumps(signals_present or [])

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO predictions
               (symbol, company_name, recommendation, confidence,
                bull_thesis, bear_thesis, price_usd, price_eur, eur_usd_rate,
                reasoning_engine, sources_count, research_id, created_at,
                conviction_score, signals_present)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, company_name, recommendation, confidence,
             bull_thesis, bear_thesis, price_usd, price_eur, eur_usd_rate,
             reasoning_engine, sources_count, research_id, now_str,
             conviction_score, signals_json),
        )
        pred_id = cur.lastrowid

        for days in (1, 3, 7, 30):
            due = (now + timedelta(days=days)).isoformat()
            conn.execute(
                """INSERT INTO prediction_evaluations
                   (prediction_id, horizon_days, due_at, status)
                   VALUES (?, ?, ?, 'pending')""",
                (pred_id, days, due),
            )

    return pred_id


# ── Read ──────────────────────────────────────────────────────────────────────

def get_recent_prediction(symbol: str, hours: int = 4) -> dict | None:
    """Return the most recent prediction for symbol within the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM predictions
               WHERE symbol = ? AND created_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (symbol.upper(), cutoff),
        ).fetchone()
        return dict(row) if row else None


def get_pending_evaluations() -> list[dict]:
    """All pending evaluations whose due_at has passed."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pe.id, pe.prediction_id, pe.horizon_days, pe.due_at,
                      p.symbol, p.price_usd, p.price_eur, p.recommendation,
                      p.confidence, p.created_at as pred_created_at
               FROM prediction_evaluations pe
               JOIN predictions p ON p.id = pe.prediction_id
               WHERE pe.status = 'pending' AND pe.due_at <= ?
               ORDER BY pe.due_at""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_predictions_with_evals(limit: int = 100) -> list[dict]:
    """All predictions with their 4 evaluation slots as a nested dict."""
    with get_conn() as conn:
        preds = conn.execute(
            "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

        result = []
        for p in preds:
            pd = dict(p)
            evals = conn.execute(
                """SELECT * FROM prediction_evaluations
                   WHERE prediction_id = ? ORDER BY horizon_days""",
                (p["id"],),
            ).fetchall()
            pd["evals"] = {e["horizon_days"]: dict(e) for e in evals}
            result.append(pd)

        return result


def get_evaluated_rows() -> list[dict]:
    """All completed evaluation rows joined with their parent prediction."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.symbol, p.company_name, p.recommendation, p.confidence,
                      p.price_usd, p.price_eur, p.bull_thesis, p.reasoning_engine,
                      p.created_at as pred_created_at,
                      pe.id as eval_id, pe.horizon_days, pe.eval_price_usd,
                      pe.eval_price_eur, pe.return_pct, pe.direction_correct,
                      pe.evaluated_at, pe.status
               FROM prediction_evaluations pe
               JOIN predictions p ON p.id = pe.prediction_id
               WHERE pe.status = 'evaluated'
               ORDER BY pe.evaluated_at DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_accuracy_stats(symbol: str | None = None) -> dict:
    """Win rate and avg return from evaluated BUY/SELL predictions."""
    with get_conn() as conn:
        where = "WHERE pe.status='evaluated'"
        params: list = []
        if symbol:
            where += " AND p.symbol = ?"
            params.append(symbol.upper())

        rows = conn.execute(f"""
            SELECT p.recommendation, p.confidence, pe.return_pct,
                   pe.direction_correct, pe.horizon_days
            FROM prediction_evaluations pe
            JOIN predictions p ON p.id = pe.prediction_id
            {where}
        """, params).fetchall()

        if not rows:
            return {"total": 0}

        rows = [dict(r) for r in rows]
        total = len(rows)

        # Win rate only for directional calls (BUY/SELL); HOLD has direction_correct=NULL
        directional = [r for r in rows if r["direction_correct"] is not None]
        correct = sum(1 for r in directional if r["direction_correct"] == 1)
        win_rate = round(correct / len(directional) * 100, 1) if directional else None

        returns = [r["return_pct"] for r in rows if r["return_pct"] is not None]
        avg_return = round(sum(returns) / len(returns), 2) if returns else None

        # By horizon
        by_horizon: dict = {}
        for h in (1, 3, 7, 30):
            h_dir = [r for r in directional if r["horizon_days"] == h]
            if h_dir:
                hc = sum(1 for r in h_dir if r["direction_correct"] == 1)
                h_ret = [r["return_pct"] for r in rows
                         if r["horizon_days"] == h and r["return_pct"] is not None]
                by_horizon[str(h)] = {
                    "total": len(h_dir),
                    "win_rate": round(hc / len(h_dir) * 100, 1),
                    "avg_return_pct": round(sum(h_ret) / len(h_ret), 2) if h_ret else None,
                }

        def _band(min_c: int, max_c: int) -> dict | None:
            band = [r for r in directional if min_c <= r["confidence"] <= max_c]
            if not band:
                return None
            bc = sum(1 for r in band if r["direction_correct"] == 1)
            br = [r["return_pct"] for r in band if r["return_pct"] is not None]
            return {
                "total": len(band),
                "win_rate": round(bc / len(band) * 100, 1),
                "avg_return_pct": round(sum(br) / len(br), 2) if br else None,
            }

        return {
            "total": total,
            "directional": len(directional),
            "correct": correct,
            "win_rate": win_rate,
            "avg_return_pct": avg_return,
            "by_horizon": by_horizon,
            "by_confidence": {
                "high":   _band(8, 10),
                "medium": _band(5, 7),
                "low":    _band(1, 4),
            },
        }


def get_signal_attribution() -> dict:
    """
    For each signal that has been tracked in signals_present, compute win rate
    and avg return from evaluated predictions. Returns dict keyed by signal name.
    """
    import json as _json
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.signals_present, p.recommendation, p.conviction_score,
                   pe.return_pct, pe.direction_correct, pe.horizon_days
            FROM prediction_evaluations pe
            JOIN predictions p ON p.id = pe.prediction_id
            WHERE pe.status = 'evaluated'
              AND p.signals_present IS NOT NULL
              AND p.signals_present != '[]'
        """).fetchall()

    if not rows:
        return {}

    # Accumulate per-signal stats
    signal_stats: dict[str, dict] = {}

    for row in rows:
        row = dict(row)
        try:
            signals = _json.loads(row.get("signals_present") or "[]")
        except Exception:
            signals = []

        for sig in signals:
            if sig not in signal_stats:
                signal_stats[sig] = {
                    "signal": sig,
                    "count": 0,
                    "correct": 0,
                    "returns": [],
                    "directional": 0,
                }
            s = signal_stats[sig]
            s["count"] += 1
            ret = row.get("return_pct")
            if ret is not None:
                s["returns"].append(ret)
            dc = row.get("direction_correct")
            if dc is not None:
                s["directional"] += 1
                if dc == 1:
                    s["correct"] += 1

    # Compute final stats
    result: dict[str, dict] = {}
    for sig, s in signal_stats.items():
        win_rate = round(s["correct"] / s["directional"] * 100, 1) if s["directional"] > 0 else None
        avg_ret  = round(sum(s["returns"]) / len(s["returns"]), 2) if s["returns"] else None
        result[sig] = {
            "signal":       sig,
            "count":        s["count"],
            "directional":  s["directional"],
            "correct":      s["correct"],
            "win_rate":     win_rate,
            "avg_return":   avg_ret,
        }

    return result


def save_scanner_alert(symbol: str, company_name: str, score: float,
                       convergence_count: int, signals: list,
                       price_usd: float | None, email_sent: bool = False) -> None:
    import json as _json
    from database.db import _now
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO scanner_alerts
            (symbol, company_name, score, convergence_count, signals,
             price_usd, email_sent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, company_name, score, convergence_count,
              _json.dumps(signals), price_usd, int(email_sent), _now()))


def get_recent_alerts(limit: int = 20) -> list[dict]:
    import json as _json
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM scanner_alerts ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try: d["signals"] = _json.loads(d.get("signals") or "[]")
        except Exception: d["signals"] = []
        out.append(d)
    return out


def get_kelly_inputs(symbol: str | None = None) -> dict | None:
    """Kelly criterion inputs from evaluated BUY/SELL predictions. Returns None if < 10 samples."""
    with get_conn() as conn:
        where = "WHERE pe.status='evaluated' AND p.recommendation != 'HOLD' AND pe.direction_correct IS NOT NULL AND pe.return_pct IS NOT NULL"
        params: list = []
        if symbol:
            where += " AND p.symbol = ?"
            params.append(symbol.upper())

        rows = conn.execute(f"""
            SELECT pe.return_pct, pe.direction_correct
            FROM prediction_evaluations pe
            JOIN predictions p ON p.id = pe.prediction_id
            {where}
        """, params).fetchall()

        if len(rows) < 10:
            return None

        rows = [dict(r) for r in rows]
        wins   = [r["return_pct"] for r in rows if r["direction_correct"] == 1]
        losses = [abs(r["return_pct"]) for r in rows if r["direction_correct"] == 0]

        if not wins or not losses:
            return None

        p        = len(wins) / len(rows)
        avg_win  = sum(wins)   / len(wins)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0 or avg_win == 0:
            return None

        kelly_f     = max(0.0, (p * avg_win - (1 - p) * avg_loss) / avg_win)
        half_kelly  = kelly_f * 0.5

        return {
            "kelly_fraction":      round(kelly_f, 4),
            "half_kelly_fraction": round(half_kelly, 4),
            "win_rate":            round(p * 100, 1),
            "avg_win_pct":         round(avg_win, 2),
            "avg_loss_pct":        round(avg_loss, 2),
            "sample_size":         len(rows),
        }


def count_predictions() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM prediction_evaluations WHERE status='pending'"
        ).fetchone()[0]
        evaluated = conn.execute(
            "SELECT COUNT(*) FROM prediction_evaluations WHERE status='evaluated'"
        ).fetchone()[0]
        return {"total": total, "pending_evals": pending, "completed_evals": evaluated}


# ── Update ────────────────────────────────────────────────────────────────────

def save_evaluation(
    eval_id: int,
    eval_price_usd: float,
    eval_price_eur: float,
    return_pct: float,
    direction_correct: int | None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE prediction_evaluations
               SET eval_price_usd = ?, eval_price_eur = ?, return_pct = ?,
                   direction_correct = ?, evaluated_at = ?, status = 'evaluated'
               WHERE id = ?""",
            (eval_price_usd, eval_price_eur, return_pct,
             direction_correct, _now(), eval_id),
        )


def skip_evaluation(eval_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE prediction_evaluations SET status='skipped', evaluated_at=? WHERE id=?",
            (_now(), eval_id),
        )
