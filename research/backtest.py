"""
Historical technical signal backtesting.

Tests four price-derived signals across the configured universe:
  - price_above_sma200   : Close > SMA(200)
  - price_below_sma200_deep : Close < SMA(200) * 0.85
  - healthy_rsi          : RSI(14) in [42, 65]
  - volume_spike         : Volume > 2× rolling-20d average

For each signal × horizon (1, 3, 7, 30 days):
  - Walk every trading day in the last 2 years
  - Record forward return when the signal is True
  - Aggregate win rate + avg return across the universe

Results are cached in the backtest_results table (recomputed on demand or
when the user visits the Analytics page with stale data).
"""

import json
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd

log = logging.getLogger(__name__)

_HORIZONS = (1, 3, 7, 30)

# Signal definitions: (key, function(df) -> pd.Series[bool])
def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()

def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(n, min_periods=n).mean()
    loss  = (-delta.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)

SIGNALS: dict[str, callable] = {
    "price_above_sma200":      lambda df: df["Close"] > _sma(df["Close"], 200),
    "price_below_sma200_deep": lambda df: df["Close"] < _sma(df["Close"], 200) * 0.85,
    "healthy_rsi":             lambda df: _rsi(df["Close"]).between(42, 65),
    "volume_spike":            lambda df: df["Volume"] > df["Volume"].rolling(20, min_periods=10).mean() * 2,
}


# ── Fetch history ─────────────────────────────────────────────────────────────

def _fetch_history(symbol: str, years: int = 2) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=years * 365 + 60)  # extra buffer for SMA(200)
        df = yf.download(
            symbol, start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True,
        )
        if df is None or len(df) < 220:
            return None
        # Flatten multi-level columns that yfinance sometimes returns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df[["Close", "Volume"]].dropna()
    except Exception as exc:
        log.warning("[BACKTEST] fetch %s: %s", symbol, exc)
        return None


# ── Per-symbol computation ─────────────────────────────────────────────────────

def _compute_symbol(symbol: str) -> dict[tuple[str, int], list[float]]:
    """
    Returns dict: (signal_key, horizon_days) → list of forward returns (float).
    Only rows where the signal fires AND forward close exists are included.
    """
    df = _fetch_history(symbol)
    if df is None:
        return {}

    closes = df["Close"]
    results: dict[tuple[str, int], list[float]] = {}

    for sig_key, sig_fn in SIGNALS.items():
        try:
            fired: pd.Series = sig_fn(df).fillna(False)
        except Exception as exc:
            log.debug("[BACKTEST] signal %s on %s: %s", sig_key, symbol, exc)
            continue

        for h in _HORIZONS:
            fwd_ret = closes.pct_change(periods=h).shift(-h)  # return h days ahead
            mask    = fired & fwd_ret.notna()
            returns = fwd_ret[mask].tolist()
            if returns:
                key = (sig_key, h)
                results.setdefault(key, [])
                results[key].extend(returns)

    return results


# ── Aggregate across universe ─────────────────────────────────────────────────

def run_backtest(universe: list[str]) -> list[dict]:
    """
    Run backtests for all signals × horizons over the given universe.
    Returns list of result dicts ready to save/display.
    """
    import concurrent.futures

    combined: dict[tuple[str, int], list[float]] = {}

    log.info("[BACKTEST] Starting — %d symbols, 4 signals × 4 horizons", len(universe))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_compute_symbol, sym): sym for sym in universe}
        for fut in concurrent.futures.as_completed(futures):
            sym = futures[fut]
            try:
                sym_results = fut.result()
                for k, returns in sym_results.items():
                    combined.setdefault(k, []).extend(returns)
            except Exception as exc:
                log.warning("[BACKTEST] symbol %s failed: %s", sym, exc)

    rows = []
    for (sig_key, horizon), returns in combined.items():
        if not returns:
            continue
        n       = len(returns)
        wins    = sum(1 for r in returns if r > 0)
        win_rate = round(wins / n * 100, 1)
        avg_ret  = round(sum(returns) / n * 100, 2)
        rows.append({
            "signal_key":   sig_key,
            "horizon_days": horizon,
            "universe":     universe,
            "occurrences":  n,
            "win_rate":     win_rate,
            "avg_return":   avg_ret,
        })

    log.info("[BACKTEST] Done — %d (signal, horizon) pairs", len(rows))
    return rows


# ── DB persistence ────────────────────────────────────────────────────────────

def save_backtest_results(rows: list[dict]) -> None:
    from database.db import get_conn, _now
    now = _now()
    with get_conn() as conn:
        # Clear previous results so we don't accumulate stale runs
        conn.execute("DELETE FROM backtest_results")
        for r in rows:
            conn.execute("""
                INSERT INTO backtest_results
                (signal_key, horizon_days, universe, occurrences, win_rate, avg_return,
                 result_json, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["signal_key"], r["horizon_days"],
                json.dumps(r["universe"]),
                r["occurrences"], r["win_rate"], r["avg_return"],
                json.dumps(r), now,
            ))


def get_backtest_results() -> list[dict]:
    from database.db import get_conn
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT signal_key, horizon_days, occurrences, win_rate, avg_return, computed_at
            FROM backtest_results
            ORDER BY signal_key, horizon_days
        """).fetchall()
    return [dict(r) for r in rows]


def get_backtest_age_hours() -> float | None:
    """Return hours since last backtest, or None if never run."""
    from database.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT computed_at FROM backtest_results ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        ts = datetime.fromisoformat(row["computed_at"])
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except Exception:
        return None


def run_and_save(universe: list[str]) -> list[dict]:
    """Run full backtest and persist results. Returns the result rows."""
    rows = run_backtest(universe)
    if rows:
        save_backtest_results(rows)
    return rows


# ── Formatted output for templates ───────────────────────────────────────────

_SIGNAL_LABELS = {
    "price_above_sma200":       "Price Above 200-Day MA",
    "price_below_sma200_deep":  "Price >15% Below 200-Day MA",
    "healthy_rsi":              "Healthy RSI (42–65)",
    "volume_spike":             "Volume Spike (>2× Avg)",
}


def get_formatted_results() -> dict:
    """Group results by signal key for template rendering."""
    raw = get_backtest_results()
    age = get_backtest_age_hours()

    if not raw:
        return {"has_data": False, "age_hours": None, "by_signal": {}}

    by_signal: dict[str, dict] = {}
    for r in raw:
        key = r["signal_key"]
        if key not in by_signal:
            by_signal[key] = {
                "key":    key,
                "label":  _SIGNAL_LABELS.get(key, key.replace("_", " ").title()),
                "horizons": {},
            }
        by_signal[key]["horizons"][r["horizon_days"]] = r

    return {
        "has_data":   True,
        "age_hours":  round(age, 1) if age is not None else None,
        "by_signal":  by_signal,
        "signals":    list(by_signal.values()),
    }
