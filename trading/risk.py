"""
Portfolio risk metrics.

  - Max drawdown from peak (from snapshot history)
  - Value at Risk 95% (parametric, from daily returns)
  - Sector concentration (using yfinance sector info)
  - Correlation matrix between held positions (30-day daily returns)
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


# ── Max drawdown ──────────────────────────────────────────────────────────────

def max_drawdown(portfolio_values: list[float]) -> dict:
    """
    Compute max drawdown from a time series of portfolio values.
    Returns: {max_dd_pct, peak_value, trough_value, peak_idx, trough_idx}
    """
    if len(portfolio_values) < 2:
        return {"max_dd_pct": 0.0, "peak_value": None, "trough_value": None}

    peak = portfolio_values[0]
    max_dd = 0.0
    peak_val = portfolio_values[0]
    trough_val = portfolio_values[0]

    for v in portfolio_values[1:]:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            peak_val = peak
            trough_val = v

    return {
        "max_dd_pct":    round(max_dd * 100, 2),
        "peak_value":    round(peak_val, 2),
        "trough_value":  round(trough_val, 2),
    }


# ── Value at Risk (parametric, 95%) ──────────────────────────────────────────

def value_at_risk(portfolio_values: list[float], confidence: float = 0.95) -> dict:
    """
    Parametric VaR from daily returns.
    Returns: {var_pct, var_eur, expected_shortfall_pct}
    """
    import math

    if len(portfolio_values) < 10:
        return {"var_pct": None, "var_eur": None}

    returns = [
        (portfolio_values[i] - portfolio_values[i-1]) / portfolio_values[i-1]
        for i in range(1, len(portfolio_values))
        if portfolio_values[i-1] > 0
    ]

    if not returns:
        return {"var_pct": None, "var_eur": None}

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / max(n - 1, 1)
    std = math.sqrt(variance)

    # z-score for 95% confidence (one-tailed)
    z = 1.645
    var_pct = mean - z * std

    current = portfolio_values[-1]
    var_eur = abs(var_pct * current)

    # Expected shortfall (average of losses beyond VaR)
    losses = sorted([r for r in returns if r < var_pct])
    es_pct = sum(losses) / len(losses) if losses else var_pct

    return {
        "var_pct":   round(var_pct * 100, 2),
        "var_eur":   round(var_eur, 2),
        "es_pct":    round(es_pct * 100, 2),
        "std_daily": round(std * 100, 2),
    }


# ── Sector concentration ──────────────────────────────────────────────────────

def sector_concentration(positions: list[dict]) -> dict:
    """
    Given list of position dicts with {symbol, market_value_eur},
    return sector breakdown using yfinance sector info.
    """
    import concurrent.futures

    if not positions:
        return {"sectors": {}, "herfindahl": 0.0, "top_sector": None}

    total_value = sum(p.get("market_value_eur", 0) or 0 for p in positions)
    if total_value == 0:
        return {"sectors": {}, "herfindahl": 0.0, "top_sector": None}

    def _get_sector(symbol: str) -> str:
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).fast_info
            # fast_info doesn't have sector; fall back to info dict
            full = yf.Ticker(symbol).info
            return full.get("sector") or "Unknown"
        except Exception:
            return "Unknown"

    symbol_sectors: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_get_sector, p["symbol"]): p["symbol"] for p in positions}
        for fut in concurrent.futures.as_completed(futures):
            sym = futures[fut]
            try:
                symbol_sectors[sym] = fut.result()
            except Exception:
                symbol_sectors[sym] = "Unknown"

    # Aggregate by sector
    sectors: dict[str, float] = {}
    for p in positions:
        sector = symbol_sectors.get(p["symbol"], "Unknown")
        val = p.get("market_value_eur", 0) or 0
        sectors[sector] = sectors.get(sector, 0) + val

    # Normalise to percentages
    sector_pcts = {s: round(v / total_value * 100, 1) for s, v in sectors.items()}
    sector_pcts = dict(sorted(sector_pcts.items(), key=lambda x: x[1], reverse=True))

    # Herfindahl-Hirschman Index (concentration measure, 0–10000)
    hhi = sum((p / 100) ** 2 for p in sector_pcts.values()) * 10000
    top = max(sector_pcts, key=sector_pcts.get) if sector_pcts else None

    return {
        "sectors":     sector_pcts,
        "herfindahl":  round(hhi, 0),
        "top_sector":  top,
        "top_pct":     sector_pcts.get(top, 0) if top else 0,
    }


# ── Correlation matrix ────────────────────────────────────────────────────────

def correlation_matrix(symbols: list[str], days: int = 30) -> dict:
    """
    Compute pairwise Pearson correlation of daily returns over last `days` days.
    Returns {symbols, matrix (list of lists), pairs (flat list of high correlations)}
    """
    if len(symbols) < 2:
        return {"symbols": symbols, "matrix": [], "high_corr_pairs": []}

    try:
        import yfinance as yf
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 5)
        df = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )["Close"]

        if df is None or df.empty:
            return {"symbols": symbols, "matrix": [], "high_corr_pairs": []}

        # Flatten multi-level if single symbol
        if len(symbols) == 1:
            df = df.to_frame(name=symbols[0])

        returns = df.pct_change().dropna()
        corr = returns.corr()

        matrix_out = []
        for s1 in symbols:
            row = []
            for s2 in symbols:
                try:
                    val = corr.loc[s1, s2]
                    row.append(round(float(val), 3) if val == val else 0.0)
                except Exception:
                    row.append(1.0 if s1 == s2 else 0.0)
            matrix_out.append(row)

        # Flag high correlations (|r| >= 0.7, non-self)
        high_pairs = []
        for i, s1 in enumerate(symbols):
            for j, s2 in enumerate(symbols):
                if i >= j:
                    continue
                val = matrix_out[i][j]
                if abs(val) >= 0.7:
                    high_pairs.append({
                        "sym1": s1, "sym2": s2,
                        "corr": val,
                        "label": f"{s1}/{s2}: {val:+.2f}",
                    })

        return {
            "symbols":         symbols,
            "matrix":          matrix_out,
            "high_corr_pairs": sorted(high_pairs, key=lambda x: abs(x["corr"]), reverse=True),
        }

    except Exception as exc:
        log.warning("[RISK] Correlation matrix failed: %s", exc)
        return {"symbols": symbols, "matrix": [], "high_corr_pairs": [], "error": str(exc)}


# ── Full risk report ──────────────────────────────────────────────────────────

def get_risk_report() -> dict:
    """Build the full risk report for the /risk page."""
    from tracking.snapshots import get_snapshots
    from database.db import get_all_positions

    positions = get_all_positions()
    snaps     = get_snapshots(days=90)
    values    = [s["portfolio_eur"] for s in snaps]

    symbols = [p["symbol"] for p in positions]

    dd   = max_drawdown(values)
    var  = value_at_risk(values)
    corr = correlation_matrix(symbols) if len(symbols) >= 2 else {"symbols": symbols, "matrix": [], "high_corr_pairs": []}

    # Sector concentration requires yfinance — do it only if positions exist
    sec = sector_concentration(
        [{"symbol": p["symbol"], "market_value_eur": p.get("market_value_eur", 0)}
         for p in positions]
    ) if positions else {"sectors": {}, "herfindahl": 0.0, "top_sector": None}

    # Rolling 30-day return
    rolling_30d = None
    if len(values) >= 30:
        rolling_30d = round((values[-1] - values[-30]) / values[-30] * 100, 2)
    elif len(values) >= 2:
        rolling_30d = round((values[-1] - values[0]) / values[0] * 100, 2)

    return {
        "has_positions":   bool(positions),
        "has_history":     len(values) >= 2,
        "positions":       positions,
        "symbols":         symbols,
        "drawdown":        dd,
        "var":             var,
        "sector":          sec,
        "correlation":     corr,
        "rolling_30d":     rolling_30d,
        "snapshot_count":  len(snaps),
    }
