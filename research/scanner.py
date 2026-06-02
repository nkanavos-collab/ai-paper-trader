"""
Opportunity Scanner — two-stage process:
  Stage 1: Parallel batch fetch of market data for entire universe (~60 tickers)
  Stage 2: Deep fetch (fundamentals, earnings, revisions) for top 25 from stage 1
  Output: Ranked list of opportunities with composite scores and explanations.

Scoring targets growth stocks — not large-cap defensives.
Max score: 100. Threshold for "opportunity": >= 55.
"""

import concurrent.futures
import json
import logging
import time
from datetime import datetime, timezone

from research.sources.market import get_market_data
from research.sources.fundamentals import get_quarterly_trends
from research.sources.earnings_history import get_earnings_history
from research.sources.revisions import get_analyst_revisions
from research.sources.stocktwits import get_stocktwits
from research.sources.squeeze import get_squeeze_score
from database.scanner_db import save_scan_run, save_scan_results

log = logging.getLogger(__name__)


# ── Composite score ────────────────────────────────────────────────────────────

def _score(symbol: str, m: dict, fund: dict, earn: dict, rev: dict) -> tuple[float, list[str]]:
    """
    Score a ticker 0-100. Higher = better growth opportunity.
    Sections: Growth (35), Momentum (25), Quality (20), Catalyst (20).
    """
    score = 45.0  # neutral baseline
    reasons: list[str] = []

    price     = m.get("price_usd", 0) or 0
    mktcap    = m.get("market_cap", 0) or 0
    rev_growth= m.get("revenue_growth", 0) or 0
    vs_sma200 = m.get("price_vs_sma200_pct", 0) or 0
    vs_sma50  = m.get("price_vs_sma50_pct", 0) or 0
    rsi       = m.get("rsi_14", 50) or 50
    vol_ratio = m.get("volume_ratio", 1) or 1
    short_pct = m.get("short_pct_float", 0) or 0
    short_ratio = m.get("short_ratio", 99) or 99
    target_up = m.get("target_upside_pct", 0) or 0
    rs1m      = m.get("rel_strength_1m_pct", 0) or 0
    dte       = m.get("days_until_earnings")

    # ── GROWTH (max +35) ──────────────────────────────────────────────────────
    if   rev_growth >= 0.40: score += 35; reasons.append(f"Exceptional revenue growth {rev_growth*100:.0f}%")
    elif rev_growth >= 0.25: score += 25; reasons.append(f"Strong revenue growth {rev_growth*100:.0f}%")
    elif rev_growth >= 0.15: score += 15; reasons.append(f"Good revenue growth {rev_growth*100:.0f}%")
    elif rev_growth >= 0.05: score += 5
    elif rev_growth < -0.05: score -= 12; reasons.append(f"Revenue declining {rev_growth*100:.0f}%")

    # Revenue acceleration (second derivative of growth)
    trend = fund.get("revenue_trend", "")
    if   trend == "accelerating":  score += 15; reasons.append("Revenue growth accelerating QoQ ↑")
    elif trend == "growing":        score += 5
    elif trend == "decelerating":   score -= 8;  reasons.append("Revenue growth decelerating QoQ ↓")
    elif trend == "declining":      score -= 15; reasons.append("Revenue declining sequentially")

    # Market cap sweet spot ($300M–$20B = high return potential)
    if   300e6 <= mktcap < 2e9:   score += 12; reasons.append(f"Small cap ${mktcap/1e6:.0f}M — high upside potential")
    elif 2e9   <= mktcap < 10e9:  score += 8;  reasons.append(f"Mid cap ${mktcap/1e9:.1f}B")
    elif 10e9  <= mktcap < 30e9:  score += 3
    elif mktcap > 200e9:          score -= 6   # Large cap hard to move meaningfully

    # ── EARNINGS QUALITY (max +20) ────────────────────────────────────────────
    beat_rate = earn.get("beat_rate", 0) or 0
    avg_surp  = earn.get("avg_surprise_pct", 0) or 0
    streak    = earn.get("streak", "")

    if   beat_rate >= 87: score += 12; reasons.append(f"Beats estimates {beat_rate:.0f}% — exceptional consistency")
    elif beat_rate >= 75: score += 8;  reasons.append(f"Beats estimates {beat_rate:.0f}% of the time")
    elif beat_rate >= 50: score += 3
    elif beat_rate > 0 and beat_rate < 40: score -= 5

    if avg_surp >= 15: score += 8;  reasons.append(f"Avg EPS beat +{avg_surp:.0f}% — consistently underestimated")
    elif avg_surp >= 8: score += 4

    if streak and "consecutive beats" in streak:
        n = next((int(w) for w in streak.split() if w.isdigit()), 0)
        if n >= 6: score += 8; reasons.append(f"{streak} — strong execution")
        elif n >= 4: score += 4; reasons.append(streak)

    # ── ANALYST MOMENTUM (part of quality, max +15) ───────────────────────────
    mom = rev.get("momentum", "neutral")
    u30 = rev.get("upgrades_30d", 0) or 0
    d30 = rev.get("downgrades_30d", 0) or 0
    if   mom == "positive":          score += 12; reasons.append(f"Analysts upgrading ({u30} upgrades 30d)")
    elif mom == "slightly positive":  score += 6;  reasons.append("Slight positive analyst revision trend")
    elif mom == "negative":          score -= 10; reasons.append(f"Analysts downgrading ({d30} downgrades 30d)")
    elif mom == "slightly negative":  score -= 5

    if u30 >= 4: score += 5; reasons.append(f"{u30} analyst upgrades in past 30 days")

    # Analyst target upside
    if   target_up > 30: score += 8;  reasons.append(f"Analyst consensus {target_up:.0f}% above current price")
    elif target_up > 15: score += 4
    elif target_up < -15: score -= 6; reasons.append(f"Analysts see {target_up:.0f}% downside")

    # ── MOMENTUM (max +25) ────────────────────────────────────────────────────
    if   vs_sma200 > 20: score += 10; reasons.append(f"Strong uptrend: +{vs_sma200:.1f}% above 200-day MA")
    elif vs_sma200 > 5:  score += 6;  reasons.append(f"Above 200-day MA by {vs_sma200:.1f}%")
    elif vs_sma200 > 0:  score += 2
    elif vs_sma200 < -20: score -= 10; reasons.append(f"Downtrend: {vs_sma200:.1f}% below 200-day MA")
    elif vs_sma200 < -5:  score -= 4

    if vs_sma50 > 5:  score += 4
    elif vs_sma50 < -10: score -= 4

    if   45 <= rsi <= 62: score += 6;  reasons.append(f"RSI {rsi:.0f} — healthy momentum zone")
    elif rsi < 32:         score += 8;  reasons.append(f"RSI {rsi:.0f} — oversold, mean-reversion potential")
    elif rsi > 78:         score -= 5;  reasons.append(f"RSI {rsi:.0f} — overbought")

    if   vol_ratio > 3.0: score += 8;  reasons.append(f"Volume {vol_ratio:.1f}x average — strong accumulation signal")
    elif vol_ratio > 1.8: score += 4
    elif vol_ratio < 0.5: score -= 3

    if rs1m > 10:  score += 5; reasons.append(f"Outperforming sector by {rs1m:+.1f}%")
    elif rs1m < -10: score -= 4

    # ── CATALYST (max +20) ────────────────────────────────────────────────────
    squeeze = get_squeeze_score(m)
    sq_score = squeeze.get("score", 0)
    if   sq_score >= 70: score += 15; reasons.append(f"Short squeeze: {squeeze.get('level')} potential ({short_pct*100:.0f}% short)")
    elif sq_score >= 45: score += 8;  reasons.append(f"Elevated short interest {short_pct*100:.0f}% — squeeze possible")
    elif sq_score >= 25: score += 4

    # Earnings as catalyst (serial beaters approaching earnings = strong setup)
    if dte is not None and 5 <= dte <= 21 and beat_rate >= 75:
        score += 12; reasons.append(f"Earnings in {dte}d — serial beater ({beat_rate:.0f}% beat rate) = high-probability catalyst")
    elif dte is not None and 3 <= dte <= 21:
        score += 5;  reasons.append(f"Earnings catalyst in {dte} days")

    return max(0.0, min(100.0, round(score, 1))), reasons[:6]


# ── Scan runner ────────────────────────────────────────────────────────────────

def _fetch_market(symbol: str) -> tuple[str, dict]:
    try:
        return symbol, get_market_data(symbol)
    except Exception as exc:
        return symbol, {"error": str(exc), "symbol": symbol}


def _fetch_deep(symbol: str) -> tuple[str, dict, dict, dict]:
    fund = earn = rev = {}
    try:
        fund = get_quarterly_trends(symbol)
    except Exception:
        pass
    try:
        earn = get_earnings_history(symbol)
    except Exception:
        pass
    try:
        rev = get_analyst_revisions(symbol)
    except Exception:
        pass
    return symbol, fund, earn, rev


def run_scan(universe: list[str] | None = None) -> dict:
    """
    Full two-stage scan. Returns results dict with ranked opportunities.
    Saves results to DB automatically.
    """
    from config import SCANNER_UNIVERSE
    tickers = [s.upper() for s in (universe or SCANNER_UNIVERSE)]
    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order

    t0 = time.time()
    log.info("Scanner starting: %d tickers", len(tickers))

    # ── Stage 1: Parallel market data for all tickers ───────────────────────
    market_map: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(_fetch_market, sym): sym for sym in tickers}
        for fut in concurrent.futures.as_completed(futs, timeout=60):
            try:
                sym, data = fut.result()
                market_map[sym] = data
            except Exception as exc:
                sym = futs[fut]
                market_map[sym] = {"error": str(exc)}

    # Quick pre-score using market data only (for stage-2 filtering)
    pre_scores: list[tuple[float, str]] = []
    for sym, m in market_map.items():
        if m.get("error") or not m.get("price_usd"):
            continue
        s, _ = _score(sym, m, {}, {}, {})
        pre_scores.append((s, sym))

    pre_scores.sort(reverse=True)
    top_symbols = [sym for _, sym in pre_scores[:25]]
    log.info("Stage 1 done: top 25 = %s", top_symbols[:5])

    # ── Stage 2: Deep fetch for top 25 (fund + earn + rev + stocktwits) ─────
    deep_map: dict[str, tuple[dict, dict, dict, dict]] = {}

    def _fetch_deep_with_stkt(sym: str) -> tuple[str, dict, dict, dict, dict]:
        fund = earn = rev = stkt = {}
        try: fund = get_quarterly_trends(sym)
        except Exception: pass
        try: earn = get_earnings_history(sym)
        except Exception: pass
        try: rev  = get_analyst_revisions(sym)
        except Exception: pass
        try: stkt = get_stocktwits(sym)
        except Exception: pass
        return sym, fund, earn, rev, stkt

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(_fetch_deep_with_stkt, sym): sym for sym in top_symbols}
        for fut in concurrent.futures.as_completed(futs, timeout=90):
            try:
                sym, fund, earn, rev, stkt = fut.result()
                deep_map[sym] = (fund, earn, rev, stkt)
            except Exception as exc:
                sym = futs[fut]
                deep_map[sym] = ({}, {}, {}, {})

    # ── Final scoring with full data ─────────────────────────────────────────
    final: list[dict] = []
    for sym in top_symbols:
        m = market_map.get(sym, {})
        fund, earn, rev, stkt = deep_map.get(sym, ({}, {}, {}, {}))
        score, signals = _score(sym, m, fund, earn, rev)

        # Convergence: count how many bull signals fire simultaneously
        conv_signals = []
        if fund.get("revenue_trend") == "accelerating":        conv_signals.append("rev_accel")
        if (earn.get("beat_rate") or 0) >= 75:                 conv_signals.append("serial_beater")
        if rev.get("momentum") in ("positive",):               conv_signals.append("analyst_up")
        if (m.get("price_vs_sma200_pct") or 0) > 5:           conv_signals.append("above_sma200")
        if (m.get("volume_ratio") or 1) > 1.5:                 conv_signals.append("volume_spike")
        if stkt.get("available") and (stkt.get("bull_ratio") or 0) >= 0.65: conv_signals.append("stkt_bull")
        if (m.get("rel_strength_1m_pct") or 0) > 5:           conv_signals.append("sector_outperform")
        if m.get("is_recent_listing"):                          conv_signals.append("recent_ipo")

        # Bonus for StockTwits in score
        if stkt.get("available"):
            br = stkt.get("bull_ratio", 0.5)
            if br >= 0.70:   score = min(100, score + 8)
            elif br >= 0.60: score = min(100, score + 4)

        # IPO bonus — recent listings with strong fundamentals often have more upside
        if m.get("is_recent_listing"):
            score = min(100, score + 6)

        final.append({
            "symbol":           sym,
            "company_name":     m.get("name", sym),
            "sector":           m.get("sector", ""),
            "score":            score,
            "signals":          signals,
            "market_cap":       m.get("market_cap"),
            "price_usd":        m.get("price_usd"),
            "price_eur":        m.get("price_eur"),
            "change_1d_pct":    m.get("change_pct"),
            "change_1m_pct":    m.get("change_1m_pct"),
            "revenue_growth":   m.get("revenue_growth"),
            "short_pct":        m.get("short_pct_float"),
            "rsi":              m.get("rsi_14"),
            "vol_ratio":        m.get("volume_ratio"),
            "beat_rate":        earn.get("beat_rate"),
            "rev_momentum":     rev.get("momentum"),
            "revenue_trend":    fund.get("revenue_trend"),
            "days_to_earnings": m.get("days_until_earnings"),
            "stkt_bull_ratio":  stkt.get("bull_ratio") if stkt.get("available") else None,
            "is_recent_listing": m.get("is_recent_listing", False),
            "listing_age_days": m.get("listing_age_days"),
            "convergence_count": len(conv_signals),
            "convergence_signals": conv_signals,
            "is_high_convergence": len(conv_signals) >= 5,
        })

    final.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(final):
        r["rank"] = i + 1

    duration_ms = round((time.time() - t0) * 1000)
    log.info("Scan complete in %dms: %d results, top=%s (%.1f)",
             duration_ms, len(final), final[0]["symbol"] if final else "—",
             final[0]["score"] if final else 0)

    # Persist to DB
    run_id = save_scan_run(
        ticker_count=len(tickers),
        scored_count=len(final),
        duration_ms=duration_ms,
        universe=tickers,
    )
    save_scan_results(run_id, final)

    return {
        "run_id":       run_id,
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "ticker_count": len(tickers),
        "scored_count": len(final),
        "duration_ms":  duration_ms,
        "results":      final,
    }
