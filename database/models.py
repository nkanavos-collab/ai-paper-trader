import sqlite3
from datetime import datetime, timezone
from config import DATABASE_PATH, STARTING_BALANCE_EUR

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    balance_eur REAL NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL UNIQUE,
    quantity      REAL NOT NULL DEFAULT 0,
    avg_cost_usd  REAL NOT NULL,
    avg_cost_eur  REAL NOT NULL,
    entry_reason  TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    quantity          REAL NOT NULL,
    price_usd         REAL NOT NULL,
    price_eur         REAL NOT NULL,
    eur_usd_rate      REAL NOT NULL,
    total_eur         REAL NOT NULL,
    balance_after_eur REAL NOT NULL,
    realized_pnl_eur  REAL DEFAULT 0,
    timestamp         TEXT NOT NULL,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    company_name     TEXT DEFAULT '',
    recommendation   TEXT NOT NULL CHECK (recommendation IN ('BUY','HOLD','SELL')),
    confidence       INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 10),
    bull_thesis      TEXT DEFAULT '',
    bear_thesis      TEXT DEFAULT '',
    price_usd        REAL NOT NULL,
    price_eur        REAL NOT NULL,
    eur_usd_rate     REAL NOT NULL DEFAULT 1.08,
    reasoning_engine TEXT DEFAULT '',
    sources_count    INTEGER DEFAULT 0,
    research_id      INTEGER,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_evaluations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id    INTEGER NOT NULL REFERENCES predictions(id),
    horizon_days     INTEGER NOT NULL CHECK (horizon_days IN (1, 3, 7, 30)),
    eval_price_usd   REAL,
    eval_price_eur   REAL,
    return_pct       REAL,
    direction_correct INTEGER,
    evaluated_at     TEXT,
    due_at           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','evaluated','skipped'))
);

CREATE TABLE IF NOT EXISTS research_cache (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    company_name     TEXT,
    analysis         TEXT NOT NULL,
    recommendation   TEXT NOT NULL CHECK (recommendation IN ('BUY','HOLD','SELL')),
    confidence       INTEGER CHECK (confidence BETWEEN 1 AND 10),
    target_price_usd REAL,
    bull_thesis      TEXT DEFAULT '',
    bear_thesis      TEXT DEFAULT '',
    risks            TEXT DEFAULT '[]',
    sources_used     TEXT DEFAULT '[]',
    raw_data         TEXT DEFAULT '{}',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanner_alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    company_name     TEXT DEFAULT '',
    score            REAL DEFAULT 0,
    convergence_count INTEGER DEFAULT 0,
    signals          TEXT DEFAULT '[]',
    price_usd        REAL,
    email_sent       INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key   TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    universe     TEXT DEFAULT '[]',
    occurrences  INTEGER DEFAULT 0,
    win_rate     REAL,
    avg_return   REAL,
    result_json  TEXT DEFAULT '{}',
    computed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT NOT NULL,
    ticker_count  INTEGER DEFAULT 0,
    scored_count  INTEGER DEFAULT 0,
    duration_ms   INTEGER DEFAULT 0,
    universe      TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS scan_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES scan_runs(id),
    rank          INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    company_name  TEXT DEFAULT '',
    sector        TEXT DEFAULT '',
    score         REAL DEFAULT 0,
    signals       TEXT DEFAULT '[]',
    market_cap    REAL,
    price_usd     REAL,
    change_1m_pct REAL,
    revenue_growth REAL,
    short_pct     REAL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('BUY','SELL','HOLD','SKIP')),
    confidence        INTEGER DEFAULT 0,
    price_usd         REAL    DEFAULT 0,
    price_eur         REAL    DEFAULT 0,
    position_size_eur REAL    DEFAULT 0,
    reason            TEXT    DEFAULT '',
    sources           TEXT    DEFAULT '[]',
    result            TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date    TEXT NOT NULL UNIQUE,
    portfolio_eur    REAL NOT NULL,
    cash_eur         REAL NOT NULL,
    positions_eur    REAL NOT NULL,
    spy_price        REAL,
    qqq_price        REAL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    review_text      TEXT NOT NULL,
    positions_json   TEXT DEFAULT '[]',
    model_used       TEXT DEFAULT '',
    created_at       TEXT NOT NULL
);
"""

_MIGRATIONS = [
    # scanner tables (handled by CREATE IF NOT EXISTS, but listed for clarity)
    "SELECT 1",  # no-op placeholder
    "ALTER TABLE predictions ADD COLUMN conviction_score REAL DEFAULT NULL",
    "ALTER TABLE predictions ADD COLUMN signals_present TEXT DEFAULT '[]'",
    "ALTER TABLE positions ADD COLUMN entry_reason TEXT DEFAULT ''",
    "ALTER TABLE transactions ADD COLUMN realized_pnl_eur REAL DEFAULT 0",
    "ALTER TABLE research_cache ADD COLUMN bull_thesis TEXT DEFAULT ''",
    "ALTER TABLE research_cache ADD COLUMN bear_thesis TEXT DEFAULT ''",
    "ALTER TABLE research_cache ADD COLUMN risks TEXT DEFAULT '[]'",
    "ALTER TABLE research_cache ADD COLUMN sources_used TEXT DEFAULT '[]'",
    "ALTER TABLE research_cache ADD COLUMN raw_data TEXT DEFAULT '{}'",
]


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists


def init_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        now = datetime.now(timezone.utc).isoformat()
        existing = conn.execute("SELECT id FROM account WHERE id = 1").fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO account (id, balance_eur, created_at, updated_at) VALUES (1, ?, ?, ?)",
                (STARTING_BALANCE_EUR, now, now),
            )
        conn.commit()
