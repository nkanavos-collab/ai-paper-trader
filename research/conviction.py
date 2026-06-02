"""
Multi-signal conviction engine.

Purpose: independent, data-driven reliability score that is SEPARATE from Claude's
confidence. Claude can be biased by recent news tone. Conviction is purely
whether the data signals agree with each other.

Score: 0–10 (5 = perfectly neutral/conflicted, >7 = strong conviction)
Output includes:
  - conviction_score (0-10)
  - direction (bullish / bearish / mixed)
  - bull_signals / bear_signals — specific signals with weights
  - convergence_count — how many independent signals agree
  - has_conflict — True when both bull and bear signals are strong
  - do_not_trade_flags — hard stops that should suppress trading regardless of score
  - data_completeness_pct — % of expected data sources that returned data
"""

from datetime import datetime, timezone


# ── Signal definitions ────────────────────────────────────────────────────────
# (name, weight)  — weight reflects predictive reliability

_BULL = [
    ("revenue_accelerating",      3),
    ("earnings_beat_streak_4plus", 2),
    ("analyst_upgrading_30d",      2),
    ("analyst_high_target_upside", 2),
    ("institutional_high_pct",    2),
    ("price_above_sma200",        1),
    ("healthy_rsi",               1),
    ("volume_spike",              2),
    ("sector_outperforming",      1),
    ("stocktwits_bullish",        2),
    ("unusual_calls",             2),
    ("options_cheap",             1),
    ("serial_beater",             2),
    ("gov_contracts_recent",      2),
    ("recent_ipo_early_stage",    1),
]

_BEAR = [
    ("revenue_declining",         3),
    ("analyst_downgrading_30d",   2),
    ("price_below_sma200_deep",   2),
    ("earnings_misser",           2),
    ("unusual_puts",              2),
    ("options_expensive",         1),
    ("portfolio_high_correlation", 2),
    ("earnings_imminent_risk",    2),
    ("low_data_quality",          2),
]

_BULL_NAMES = {b[0] for b in _BULL}
_BEAR_NAMES = {b[0] for b in _BEAR}
_WEIGHTS    = {n: w for n, w in _BULL + _BEAR}

# ── Hard stops ────────────────────────────────────────────────────────────────
# When any of these are true, position size should be reduced to near-zero
_HARD_STOPS = [
    "earnings_in_1_2_days",
    "portfolio_extreme_correlation",
    "data_quality_too_low",
]


def _check_signals(data: dict, result: dict) -> tuple[list, list, list, float]:
    """
    Return (active_bull_signals, active_bear_signals, do_not_trade, completeness_pct).
    Each signal is a (name, weight, description) tuple.
    """
    m    = data.get("market", {})
    fund = data.get("fundamentals", {})
    earn = data.get("earnings_history", {})
    rev  = data.get("revisions", {})
    sent = data.get("sentiment", {})
    stkt = data.get("stocktwits", {})
    inst = data.get("institutions", {})
    opts = data.get("options_iv", {})
    corr = data.get("correlation", {})
    uopt = data.get("unusual_options", {})
    sq   = data.get("squeeze", {})
    gov  = data.get("gov_contracts", {})

    bull: list[tuple[str, int, str]] = []
    bear: list[tuple[str, int, str]] = []
    stops: list[str] = []

    # ── FUNDAMENTAL SIGNALS ───────────────────────────────────────────────────
    rev_trend = fund.get("revenue_trend", "")
    yoy_rev   = fund.get("yoy_revenue_growth_pct")
    if rev_trend == "accelerating":
        bull.append(("revenue_accelerating", 3,
                     f"Revenue growth accelerating QoQ — strongest fundamental signal"))
    elif rev_trend in ("decelerating", "declining"):
        bear.append(("revenue_declining", 3,
                     f"Revenue growth {rev_trend} QoQ"))

    beat_rate = earn.get("beat_rate") or 0
    streak    = earn.get("streak", "")
    if beat_rate >= 75:
        bull.append(("serial_beater", 2,
                     f"Beats analyst estimates {beat_rate:.0f}% of the time"))
    elif beat_rate > 0 and beat_rate < 45:
        bear.append(("earnings_misser", 2,
                     f"Misses estimates {100-beat_rate:.0f}% of the time"))

    if streak and "consecutive beats" in streak:
        n = next((int(w) for w in streak.split() if w.isdigit()), 0)
        if n >= 4:
            bull.append(("earnings_beat_streak_4plus", 2, f"{streak}"))

    # ── ANALYST SIGNALS ───────────────────────────────────────────────────────
    mom = rev.get("momentum", "neutral")
    u30 = rev.get("upgrades_30d", 0) or 0
    d30 = rev.get("downgrades_30d", 0) or 0
    if mom in ("positive", "slightly positive") and u30 > d30:
        bull.append(("analyst_upgrading_30d", 2,
                     f"{u30} analyst upgrades in last 30 days"))
    elif mom in ("negative", "slightly negative") and d30 > u30:
        bear.append(("analyst_downgrading_30d", 2,
                     f"{d30} analyst downgrades in last 30 days"))

    target_up = m.get("target_upside_pct") or 0
    if target_up >= 20:
        bull.append(("analyst_high_target_upside", 2,
                     f"Analyst consensus target {target_up:.0f}% above current price"))

    # ── TECHNICAL SIGNALS ─────────────────────────────────────────────────────
    vs200 = m.get("price_vs_sma200_pct") or 0
    if vs200 > 5:
        bull.append(("price_above_sma200", 1,
                     f"Price {vs200:+.1f}% above 200-day MA (uptrend confirmed)"))
    elif vs200 < -15:
        bear.append(("price_below_sma200_deep", 2,
                     f"Price {vs200:+.1f}% below 200-day MA (downtrend)"))

    rsi = m.get("rsi_14") or 50
    if 42 <= rsi <= 65:
        bull.append(("healthy_rsi", 1, f"RSI {rsi:.0f} — healthy momentum zone"))

    vol = m.get("volume_ratio") or 1
    if vol >= 2.0:
        bull.append(("volume_spike", 2,
                     f"Volume {vol:.1f}x average — institutional accumulation signal"))

    rs1m = m.get("rel_strength_1m_pct") or 0
    if rs1m > 8:
        bull.append(("sector_outperforming", 1,
                     f"Outperforming sector by {rs1m:+.1f}% over 1 month"))

    # ── SENTIMENT SIGNALS ─────────────────────────────────────────────────────
    if stkt.get("available"):
        br = stkt.get("bull_ratio", 0.5)
        if br >= 0.65:
            bull.append(("stocktwits_bullish", 2,
                         f"StockTwits {br*100:.0f}% bullish ({stkt.get('total_messages',0)} messages)"))
        elif br <= 0.35:
            bear.append(("stocktwits_bearish_implied", 2,
                         f"StockTwits {(1-br)*100:.0f}% bearish"))

    # ── INSTITUTIONAL + SMART MONEY ───────────────────────────────────────────
    inst_pct = inst.get("inst_pct") or 0
    if inst_pct >= 60:
        bull.append(("institutional_high_pct", 2,
                     f"{inst_pct:.0f}% institutionally owned — validated by smart money"))

    if uopt.get("is_unusual"):
        sig = uopt.get("signal", "")
        if "calls" in sig:
            bull.append(("unusual_calls", 2,
                         f"Unusual call options activity — likely institutional positioning"))
        elif "puts" in sig:
            bear.append(("unusual_puts", 2,
                         "Unusual put options — potential hedge or bearish bet"))

    # ── OPTIONS IV ────────────────────────────────────────────────────────────
    iv_sig = opts.get("signal", "")
    if iv_sig == "cheap":
        bull.append(("options_cheap", 1,
                     f"Options cheap (IV/HV {opts.get('iv_vs_hv','?')}x) — good risk/reward"))
    elif iv_sig == "expensive":
        bear.append(("options_expensive", 1,
                     f"Options expensive (IV/HV {opts.get('iv_vs_hv','?')}x) — poor risk/reward"))

    # ── GOV CONTRACTS ─────────────────────────────────────────────────────────
    if gov.get("available") and gov.get("contract_count", 0) >= 2:
        total = gov.get("total_value", 0) or 0
        bull.append(("gov_contracts_recent", 2,
                     f"${total/1e6:.0f}M in government contracts (last 12 months)"))

    # ── RISK FLAGS ────────────────────────────────────────────────────────────
    dte = m.get("days_until_earnings")
    if dte is not None and dte <= 2:
        bear.append(("earnings_imminent_risk", 2,
                     f"Earnings in {dte} day{'s' if dte != 1 else ''} — binary gap risk"))
        stops.append("earnings_in_1_2_days")

    if corr.get("warning"):
        pairs = corr.get("high_correlation_pairs", [])
        max_c = max((abs(c) for _, c in pairs), default=0)
        bear.append(("portfolio_high_correlation", 2,
                     f"Portfolio correlation ≥0.70 with existing holdings — not diversifying"))
        if max_c >= 0.85:
            stops.append("portfolio_extreme_correlation")

    # Data quality gate
    quality = data.get("market", {}).get("_meta", {})
    sources_ok = sum([
        not data.get("market", {}).get("error"),
        data.get("fundamentals", {}).get("available", False),
        data.get("earnings_history", {}).get("available", False),
        data.get("revisions", {}).get("available", False),
    ])
    if sources_ok < 2:
        bear.append(("low_data_quality", 2,
                     "Insufficient data sources — recommendation unreliable"))
        stops.append("data_quality_too_low")

    # ── DATA COMPLETENESS ─────────────────────────────────────────────────────
    expected = [
        not data.get("market", {}).get("error"),
        data.get("news", {}).get("article_count", 0) > 0,
        data.get("fundamentals", {}).get("available", False),
        data.get("earnings_history", {}).get("available", False),
        data.get("revisions", {}).get("available", False),
        data.get("options_iv", {}).get("available", False),
        data.get("insiders", {}).get("available", False),
        data.get("macro", {}).get("vix") is not None,
        data.get("stocktwits", {}).get("available", False),
        data.get("institutions", {}).get("available", False),
    ]
    completeness = round(sum(expected) / len(expected) * 100, 0)

    return bull, bear, stops, completeness


def compute_conviction(data: dict, result: dict) -> dict:
    """
    Main entry point. Returns full conviction analysis dict.
    Attach result["conviction"] = compute_conviction(data, result) in analyst.py.
    """
    bull, bear, stops, completeness = _check_signals(data, result)

    bull_weight = sum(w for _, w, _ in bull)
    bear_weight = sum(w for _, w, _ in bear)
    total_w = bull_weight + bear_weight

    if total_w == 0:
        score = 5.0
        direction = "neutral"
    else:
        net_ratio = (bull_weight - bear_weight) / total_w
        score = round(5.0 + net_ratio * 5.0, 1)
        score = max(1.0, min(10.0, score))
        if net_ratio >= 0.25:   direction = "bullish"
        elif net_ratio <= -0.25: direction = "bearish"
        else:                    direction = "mixed"

    # Conflict: both sides have significant weight
    min_side = min(bull_weight, bear_weight)
    has_conflict = min_side >= 4 and max(bull_weight, bear_weight) > 0
    conflict_ratio = round(min_side / max(total_w, 1), 2)

    # Agreement % — what fraction of total weight is on the dominant side
    dominant_weight = max(bull_weight, bear_weight)
    agreement_pct = round(dominant_weight / max(total_w, 1) * 100, 1)

    convergence_count = len(bull) if direction in ("bullish", "mixed") and bull_weight >= bear_weight else len(bear)

    return {
        "conviction_score":     score,
        "direction":            direction,
        "bull_signals":         [(n, w, d) for n, w, d in bull],
        "bear_signals":         [(n, w, d) for n, w, d in bear],
        "bull_signal_count":    len(bull),
        "bear_signal_count":    len(bear),
        "bull_weight":          bull_weight,
        "bear_weight":          bear_weight,
        "total_signals":        len(bull) + len(bear),
        "convergence_count":    convergence_count,
        "has_conflict":         has_conflict,
        "conflict_ratio":       conflict_ratio,
        "agreement_pct":        agreement_pct,
        "do_not_trade_flags":   stops,
        "do_not_trade":         bool(stops),
        "data_completeness_pct": completeness,
        "computed_at":          datetime.now(timezone.utc).isoformat(),
    }
