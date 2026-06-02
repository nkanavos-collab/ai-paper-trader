"""Aggregate performance metrics from completed evaluations."""

from database.predictions import get_evaluated_rows, count_predictions, get_pending_evaluations


def _adj(return_pct: float, recommendation: str) -> float:
    """Direction-adjusted return: positive = trader profits."""
    return -return_pct if recommendation == "SELL" else return_pct


def _safe_mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _safe_pct(num: int, denom: int) -> float | None:
    return round(num / denom * 100, 1) if denom else None


def get_performance_stats() -> dict:
    rows = get_evaluated_rows()
    counts = count_predictions()

    # Unique prediction count (a prediction has 4 eval rows)
    unique_preds = len({(r["symbol"], r["pred_created_at"]) for r in rows})

    if not rows:
        return {
            "counts": counts,
            "unique_evaluated": 0,
            "pending_due": len(get_pending_evaluations()),
            "accuracy_pct": None,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "by_horizon": {d: _empty_bucket() for d in (1, 3, 7, 30)},
            "by_confidence": {b: _empty_bucket() for b in ("1-3", "4-6", "7-8", "9-10")},
            "by_recommendation": {r: _empty_bucket() for r in ("BUY", "HOLD", "SELL")},
            "best": [],
            "worst": [],
            "all_evaluated": [],
        }

    # ── Overall ───────────────────────────────────────────────────────────────
    directional = [r for r in rows if r["recommendation"] in ("BUY", "SELL")
                   and r["direction_correct"] is not None]
    adj_returns = [_adj(r["return_pct"], r["recommendation"]) for r in rows
                   if r["return_pct"] is not None]

    accuracy = _safe_pct(
        sum(r["direction_correct"] for r in directional), len(directional)
    )
    win_rate = _safe_pct(sum(1 for x in adj_returns if x > 0), len(adj_returns))
    avg_return = _safe_mean(adj_returns)

    # ── By Horizon ────────────────────────────────────────────────────────────
    by_horizon: dict = {}
    for days in (1, 3, 7, 30):
        h_rows = [r for r in rows if r["horizon_days"] == days]
        by_horizon[days] = _bucket_stats(h_rows)

    # ── By Confidence Bucket ──────────────────────────────────────────────────
    buckets = [("1-3", 1, 3), ("4-6", 4, 6), ("7-8", 7, 8), ("9-10", 9, 10)]
    by_confidence: dict = {}
    for label, lo, hi in buckets:
        b_rows = [r for r in rows if lo <= (r["confidence"] or 5) <= hi]
        by_confidence[label] = _bucket_stats(b_rows)

    # ── By Recommendation ─────────────────────────────────────────────────────
    by_rec: dict = {}
    for rec in ("BUY", "HOLD", "SELL"):
        r_rows = [r for r in rows if r["recommendation"] == rec]
        by_rec[rec] = _bucket_stats(r_rows)

    # ── Best / Worst (7D + 30D only, unique symbol+created_at) ───────────────
    scored = []
    seen_keys: set = set()
    for r in rows:
        if r["horizon_days"] not in (7, 30) or r["return_pct"] is None:
            continue
        key = (r["symbol"], r["pred_created_at"], r["horizon_days"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        scored.append({
            **r,
            "adj_return": round(_adj(r["return_pct"], r["recommendation"]), 2),
        })

    scored.sort(key=lambda x: x["adj_return"], reverse=True)
    best  = scored[:5]
    worst = list(reversed(scored[-5:])) if len(scored) >= 5 else list(reversed(scored))

    return {
        "counts":            counts,
        "unique_evaluated":  unique_preds,
        "pending_due":       len(get_pending_evaluations()),
        "accuracy_pct":      accuracy,
        "win_rate_pct":      win_rate,
        "avg_return_pct":    avg_return,
        "by_horizon":        by_horizon,
        "by_confidence":     by_confidence,
        "by_recommendation": by_rec,
        "best":              best,
        "worst":             worst,
        "all_evaluated":     rows[:200],
    }


def _empty_bucket() -> dict:
    return {"count": 0, "accuracy_pct": None, "win_rate_pct": None, "avg_return_pct": None}


def _bucket_stats(rows: list[dict]) -> dict:
    if not rows:
        return _empty_bucket()

    directional = [r for r in rows if r["recommendation"] in ("BUY", "SELL")
                   and r["direction_correct"] is not None]
    adj_returns = [_adj(r["return_pct"], r["recommendation"]) for r in rows
                   if r["return_pct"] is not None]

    return {
        "count":       len(rows),
        "accuracy_pct": _safe_pct(
            sum(r["direction_correct"] for r in directional), len(directional)
        ),
        "win_rate_pct": _safe_pct(
            sum(1 for x in adj_returns if x > 0), len(adj_returns)
        ),
        "avg_return_pct": _safe_mean(adj_returns),
    }
