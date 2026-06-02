"""
Short squeeze detector.
Uses data already collected by market.py — no extra API calls needed.
Squeeze score = potential intensity of a short squeeze event.
"""
from datetime import datetime, timezone


def get_squeeze_score(market_data: dict) -> dict:
    """
    Compute squeeze potential from market data dict.
    Returns a score 0-100 and explanatory signals.
    """
    m = market_data

    short_pct   = m.get("short_pct_float") or 0.0   # e.g. 0.18 = 18%
    short_ratio = m.get("short_ratio")     or 99.0   # days to cover
    vol_ratio   = m.get("volume_ratio")    or 1.0    # today / 20d avg
    rsi         = m.get("rsi_14")          or 50.0
    vs_sma50    = m.get("price_vs_sma50_pct") or 0.0
    float_pct   = m.get("short_pct_float") or 0.0

    score = 0.0
    signals: list[str] = []

    # ── Short interest (0-40 pts) ───────────────────────────────────────────
    if short_pct >= 0.30:
        score += 40
        signals.append(f"Extreme short interest: {short_pct*100:.1f}% of float")
    elif short_pct >= 0.20:
        score += 28
        signals.append(f"Very high short interest: {short_pct*100:.1f}% of float")
    elif short_pct >= 0.15:
        score += 18
        signals.append(f"High short interest: {short_pct*100:.1f}% of float")
    elif short_pct >= 0.10:
        score += 8
    # Low short interest = no squeeze potential

    # ── Days to cover (0-30 pts — lower = faster squeeze) ──────────────────
    if short_ratio <= 1.0:
        score += 30
        signals.append(f"Days to cover: {short_ratio:.1f} (explosive — shorts trapped)")
    elif short_ratio <= 2.5:
        score += 20
        signals.append(f"Days to cover: {short_ratio:.1f} (fast cover possible)")
    elif short_ratio <= 5.0:
        score += 10
        signals.append(f"Days to cover: {short_ratio:.1f}")
    # > 5 days = slow bleed, not a squeeze

    # ── Volume surge (0-20 pts) ──────────────────────────────────────────────
    if vol_ratio >= 4.0:
        score += 20
        signals.append(f"Volume {vol_ratio:.1f}x average — shorts being squeezed NOW")
    elif vol_ratio >= 2.5:
        score += 12
        signals.append(f"Volume {vol_ratio:.1f}x average — unusual buying pressure")
    elif vol_ratio >= 1.5:
        score += 5

    # ── Momentum confirmation (0-10 pts) ────────────────────────────────────
    if vs_sma50 > 5 and rsi > 55:
        score += 10
        signals.append("Price breaking upward — shorts in the red")
    elif vs_sma50 > 0:
        score += 4
    elif vs_sma50 < -10:
        score -= 5  # Downtrend = squeeze unlikely without reversal catalyst

    score = max(0.0, min(100.0, round(score, 1)))

    level = (
        "extreme"  if score >= 75 else
        "high"     if score >= 55 else
        "moderate" if score >= 35 else
        "low"
    )

    return {
        "score":       score,
        "level":       level,
        "signals":     signals,
        "short_pct":   round(short_pct * 100, 1),
        "days_to_cover": short_ratio,
        "available":   short_pct > 0,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
