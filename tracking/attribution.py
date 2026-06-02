"""
Signal attribution — answers: "which signals have actually predicted returns?"

Two layers:
1. Per-signal win rate from evaluated predictions (accumulates over time)
2. Summary stats with a human-readable interpretation of which signals to trust

The data comes from the signals_present column stored with each prediction.
As more predictions are evaluated, the data becomes more reliable.
"""

from database.predictions import get_signal_attribution, get_accuracy_stats

# Human-readable labels for signal names
_SIGNAL_LABELS: dict[str, str] = {
    "revenue_accelerating":      "Revenue Growth Accelerating QoQ",
    "earnings_beat_streak_4plus":"Earnings Beat Streak (4+ quarters)",
    "analyst_upgrading_30d":     "Analyst Upgrades in Last 30 Days",
    "analyst_high_target_upside":"Analyst Target >20% Upside",
    "institutional_high_pct":    "High Institutional Ownership (>60%)",
    "price_above_sma200":        "Price Above 200-Day Moving Average",
    "healthy_rsi":               "Healthy RSI (42–65 range)",
    "volume_spike":              "Volume Spike (>2x Average)",
    "sector_outperforming":      "Stock Outperforming Sector",
    "stocktwits_bullish":        "StockTwits Bullish (>65%)",
    "unusual_calls":             "Unusual Call Options Activity",
    "options_cheap":             "Options Cheap (Low IV/HV)",
    "serial_beater":             "Serial Earnings Beater (>75%)",
    "gov_contracts_recent":      "Government Contracts (12 months)",
    "recent_ipo_early_stage":    "Recent IPO / Early Stage Listing",
    "revenue_declining":         "Revenue Declining",
    "analyst_downgrading_30d":   "Analyst Downgrades in Last 30 Days",
    "price_below_sma200_deep":   "Price >15% Below 200-Day MA",
    "earnings_misser":           "Frequent Earnings Miss (<45%)",
    "unusual_puts":              "Unusual Put Options Activity",
    "options_expensive":         "Options Expensive (High IV/HV)",
    "portfolio_high_correlation":"High Portfolio Correlation",
    "earnings_imminent_risk":    "Earnings in ≤2 Days (Event Risk)",
    "low_data_quality":          "Insufficient Data Sources",
}

_SIGNAL_DIRECTION: dict[str, str] = {
    "revenue_accelerating":      "bull",
    "earnings_beat_streak_4plus":"bull",
    "analyst_upgrading_30d":     "bull",
    "analyst_high_target_upside":"bull",
    "institutional_high_pct":    "bull",
    "price_above_sma200":        "bull",
    "healthy_rsi":               "bull",
    "volume_spike":              "bull",
    "sector_outperforming":      "bull",
    "stocktwits_bullish":        "bull",
    "unusual_calls":             "bull",
    "options_cheap":             "bull",
    "serial_beater":             "bull",
    "gov_contracts_recent":      "bull",
    "recent_ipo_early_stage":    "bull",
    "revenue_declining":         "bear",
    "analyst_downgrading_30d":   "bear",
    "price_below_sma200_deep":   "bear",
    "earnings_misser":           "bear",
    "unusual_puts":              "bear",
    "options_expensive":         "bear",
    "portfolio_high_correlation":"bear",
    "earnings_imminent_risk":    "bear",
    "low_data_quality":          "bear",
}


def get_full_attribution() -> dict:
    """Return complete signal attribution analysis."""
    raw = get_signal_attribution()
    overall = get_accuracy_stats()

    if not raw:
        return {
            "has_data": False,
            "total_evaluated": overall.get("total", 0),
            "message": (
                "No signal attribution data yet. Every new research analysis will "
                "store which signals fired. After evaluations complete (1–30 days), "
                "this page will show which signals have actually been predictive."
            ),
            "overall": overall,
            "signals": [],
        }

    # Build enriched signal list
    signals = []
    for name, stats in raw.items():
        label     = _SIGNAL_LABELS.get(name, name.replace("_", " ").title())
        direction = _SIGNAL_DIRECTION.get(name, "unknown")
        win_rate  = stats.get("win_rate")
        avg_ret   = stats.get("avg_return")

        # Reliability rating
        if stats["directional"] < 5:
            reliability = "insufficient_data"
        elif win_rate is not None and win_rate >= 70:
            reliability = "strong"
        elif win_rate is not None and win_rate >= 55:
            reliability = "moderate"
        else:
            reliability = "weak"

        signals.append({
            **stats,
            "label":       label,
            "direction":   direction,
            "reliability": reliability,
        })

    # Sort: reliable bull signals first, then bear signals
    bull_sigs = sorted([s for s in signals if s["direction"] == "bull"],
                       key=lambda x: (x.get("win_rate") or 0), reverse=True)
    bear_sigs = sorted([s for s in signals if s["direction"] == "bear"],
                       key=lambda x: (x.get("win_rate") or 0), reverse=True)

    # Find best and worst signals
    with_rates = [s for s in signals if s.get("win_rate") is not None and s["directional"] >= 5]
    best_signal  = max(with_rates, key=lambda x: x["win_rate"], default=None)
    worst_signal = min(with_rates, key=lambda x: x["win_rate"], default=None)
    best_return  = max(with_rates, key=lambda x: x.get("avg_return") or 0, default=None)

    # Insight text
    insights = []
    if best_signal:
        insights.append(
            f"'{best_signal['label']}' is your most reliable signal: "
            f"{best_signal['win_rate']}% win rate over {best_signal['directional']} directional predictions."
        )
    if best_return and best_return != best_signal:
        insights.append(
            f"'{best_return['label']}' produces the best average returns "
            f"({best_return['avg_return']:+.1f}%) when it fires."
        )
    if worst_signal and worst_signal.get("win_rate", 100) < 50:
        insights.append(
            f"⚠️ '{worst_signal['label']}' has been below-chance ({worst_signal['win_rate']}%). "
            f"Consider reducing its weight in the conviction score."
        )

    return {
        "has_data":          True,
        "total_evaluated":   overall.get("total", 0),
        "overall":           overall,
        "signals":           bull_sigs + bear_sigs,
        "bull_signals":      bull_sigs,
        "bear_signals":      bear_sigs,
        "best_signal":       best_signal,
        "best_return_signal": best_return,
        "worst_signal":      worst_signal,
        "insights":          insights,
        "signal_count":      len(signals),
    }
