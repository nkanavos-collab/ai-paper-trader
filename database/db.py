import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from config import DATABASE_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Account ──────────────────────────────────────────────────────────────────

def get_balance() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance_eur FROM account WHERE id = 1").fetchone()
        return row["balance_eur"] if row else 0.0


def set_balance(new_balance: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE account SET balance_eur = ?, updated_at = ? WHERE id = 1",
            (new_balance, _now()),
        )


# ── Positions ─────────────────────────────────────────────────────────────────

def get_all_positions() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE quantity > 0 ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]


def get_position(symbol: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
        return dict(row) if row else None


def upsert_position(
    symbol: str,
    quantity: float,
    avg_cost_usd: float,
    avg_cost_eur: float,
    entry_reason: str = "",
) -> None:
    symbol = symbol.upper()
    now = _now()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE positions
                   SET quantity = ?, avg_cost_usd = ?, avg_cost_eur = ?, updated_at = ?
                   WHERE symbol = ?""",
                (quantity, avg_cost_usd, avg_cost_eur, now, symbol),
            )
        else:
            conn.execute(
                """INSERT INTO positions
                   (symbol, quantity, avg_cost_usd, avg_cost_eur, entry_reason, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, quantity, avg_cost_usd, avg_cost_eur, entry_reason, now, now),
            )


def delete_position(symbol: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol.upper(),))


# ── Transactions ──────────────────────────────────────────────────────────────

def record_transaction(
    symbol: str,
    action: str,
    quantity: float,
    price_usd: float,
    price_eur: float,
    eur_usd_rate: float,
    total_eur: float,
    balance_after_eur: float,
    notes: str = "",
    realized_pnl_eur: float = 0.0,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
               (symbol, action, quantity, price_usd, price_eur, eur_usd_rate,
                total_eur, balance_after_eur, realized_pnl_eur, timestamp, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol.upper(), action, quantity, price_usd, price_eur,
                eur_usd_rate, total_eur, balance_after_eur, realized_pnl_eur, _now(), notes,
            ),
        )
        return cur.lastrowid


def get_realized_pnl() -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl_eur), 0.0) FROM transactions WHERE action = 'SELL'"
        ).fetchone()
        return float(row[0]) if row else 0.0


def get_transactions(symbol: str | None = None, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Research Cache ────────────────────────────────────────────────────────────

def save_research(
    symbol: str,
    company_name: str,
    analysis: str,
    recommendation: str,
    confidence: int,
    target_price_usd: float | None = None,
    bull_thesis: str = "",
    bear_thesis: str = "",
    risks: str = "[]",
    sources_used: str = "[]",
    raw_data: str = "{}",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO research_cache
               (symbol, company_name, analysis, recommendation, confidence, target_price_usd,
                bull_thesis, bear_thesis, risks, sources_used, raw_data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol.upper(), company_name, analysis, recommendation, confidence, target_price_usd,
             bull_thesis, bear_thesis, risks, sources_used, raw_data, _now()),
        )


def get_latest_research(symbol: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM research_cache WHERE symbol = ?
               ORDER BY created_at DESC LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None


def get_all_research() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research_cache ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Autonomous trading settings ───────────────────────────────────────────────

def get_auto_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM auto_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_auto_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auto_settings (key, value) VALUES (?, ?)",
            (key, value),
        )


# ── Autonomous decision log ───────────────────────────────────────────────────

def save_auto_decision(
    run_id: str,
    action: str,
    symbol: str,
    confidence: int,
    price_usd: float,
    price_eur: float,
    position_size_eur: float,
    reason: str,
    sources: list,
    result: str,
) -> None:
    import json
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auto_decisions
               (run_id, timestamp, symbol, action, confidence,
                price_usd, price_eur, position_size_eur, reason, sources, result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, _now(), symbol.upper(), action, int(confidence),
                float(price_usd), float(price_eur), float(position_size_eur),
                reason, json.dumps(sources if isinstance(sources, list) else []),
                result,
            ),
        )


def get_auto_decisions(limit: int = 50, action: str | None = None) -> list[dict]:
    import json
    with get_conn() as conn:
        if action:
            rows = conn.execute(
                "SELECT * FROM auto_decisions WHERE action = ?"
                " ORDER BY timestamp DESC LIMIT ?",
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM auto_decisions ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d.get("sources") or "[]")
            except Exception:
                d["sources"] = []
            out.append(d)
        return out


def count_auto_trades_today() -> int:
    today = _now()[:10]  # "YYYY-MM-DD"
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM auto_decisions
               WHERE action IN ('BUY','SELL') AND result LIKE 'executed%'
               AND timestamp LIKE ?""",
            (today + "%",),
        ).fetchone()
        return int(row[0]) if row else 0
