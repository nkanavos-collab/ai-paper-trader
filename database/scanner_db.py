"""CRUD for scanner runs and results."""
import json
from datetime import datetime, timezone
from database.db import get_conn, _now


def save_scan_run(
    ticker_count: int,
    scored_count: int,
    duration_ms: int,
    universe: list[str],
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO scan_runs (run_at, ticker_count, scored_count, duration_ms, universe)
               VALUES (?, ?, ?, ?, ?)""",
            (_now(), ticker_count, scored_count, duration_ms, json.dumps(universe)),
        )
        return cur.lastrowid


def save_scan_results(run_id: int, results: list[dict]) -> None:
    with get_conn() as conn:
        for r in results:
            conn.execute(
                """INSERT INTO scan_results
                   (run_id, rank, symbol, company_name, sector, score,
                    signals, market_cap, price_usd, change_1m_pct, revenue_growth,
                    short_pct, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    r.get("rank", 0),
                    r["symbol"],
                    r.get("company_name", ""),
                    r.get("sector", ""),
                    r.get("score", 0),
                    json.dumps(r.get("signals", [])),
                    r.get("market_cap"),
                    r.get("price_usd"),
                    r.get("change_1m_pct"),
                    r.get("revenue_growth"),
                    r.get("short_pct"),
                    _now(),
                ),
            )


def get_latest_scan() -> dict | None:
    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM scan_runs ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        if not run:
            return None
        run_dict = dict(run)
        try:
            run_dict["universe"] = json.loads(run_dict.get("universe") or "[]")
        except Exception:
            run_dict["universe"] = []

        results = conn.execute(
            """SELECT * FROM scan_results WHERE run_id = ? ORDER BY rank""",
            (run_dict["id"],),
        ).fetchall()

        parsed = []
        for r in results:
            d = dict(r)
            try:
                d["signals"] = json.loads(d.get("signals") or "[]")
            except Exception:
                d["signals"] = []
            parsed.append(d)

        run_dict["results"] = parsed
        return run_dict


def get_scan_history(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        runs = conn.execute(
            "SELECT id, run_at, ticker_count, scored_count, duration_ms FROM scan_runs ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in runs]
