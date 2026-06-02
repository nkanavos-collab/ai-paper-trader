import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Vercel serverless: filesystem is read-only except /tmp
ON_VERCEL = bool(os.getenv("VERCEL"))
DATABASE_PATH = Path("/tmp/trading.db")          if ON_VERCEL else BASE_DIR / "trading.db"
REPORTS_DIR   = Path("/tmp/reports_output")      if ON_VERCEL else BASE_DIR / "reports_output"
REPORTS_DIR.mkdir(exist_ok=True)

STARTING_BALANCE_EUR = 100.0
CURRENCY = "EUR"
EUR_USD_FALLBACK = 1.08  # fallback if live rate unavailable

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

FRED_API_KEY         = os.getenv("FRED_API_KEY", "")          # optional — free at fred.stlouisfed.org
ALPHA_VANTAGE_KEY    = os.getenv("ALPHA_VANTAGE_KEY", "")     # optional — free at alphavantage.co (25 calls/day)
NEWSAPI_KEY          = os.getenv("NEWSAPI_KEY", "")           # optional — free at newsapi.org (1000 calls/day)

MARKET_CACHE_SECONDS = 60  # cache yfinance quotes for 60s

PAPER_TRADING_ONLY = True  # must stay True — app refuses to start if False
WEB_HOST = "127.0.0.1"
WEB_PORT = 8001

# ── Autonomous paper trading ──────────────────────────────────────────────────
# PAPER_TRADING_ONLY must always be True above. The AI may execute simulated
# buy/sell decisions without user confirmation. No real money, no real broker.
AUTO_PAPER_TRADING = os.getenv("AUTO_PAPER_TRADING", "true").lower() == "true"

WATCHLIST: list[str] = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "SPY", "QQQ", "AMD",
]

# Extended scanner universe — small/mid cap growth stocks with higher return potential
SCANNER_UNIVERSE: list[str] = [
    # Large-cap tech (liquidity anchors)
    "AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "TSLA",

    # Cybersecurity (high growth, recurring revenue)
    "CRWD", "NET", "ZS", "PANW", "S", "CYBR", "FTNT",

    # Cloud / SaaS (mid cap, revenue accelerators)
    "DDOG", "SNOW", "MDB", "GTLB", "HUBS", "BILL", "ZI", "VEEV",

    # AI / Semiconductor infrastructure
    "SMCI", "ARM", "MRVL", "AVGO", "ANET", "ONTO", "COHU",

    # Fintech / Payments
    "HOOD", "COIN", "SQ", "AFRM", "UPST", "NU", "SOFI",

    # High-growth consumer / lifestyle
    "ONON", "CELH", "ELF", "DUOL", "APP", "TTD",

    # Biotech catalysts (high beta, event-driven)
    "RXRX", "BEAM", "CRSP", "PACB", "NTLA", "EDIT", "SEER",

    # Small/mid cap momentum
    "AXON", "PLTR", "RKLB", "ASTS", "IONQ", "QBTS", "ACHR",

    # International growth (US-listed)
    "MELI", "SE", "GRAB", "BABA",

    # Short squeeze watchlist (high SI stocks)
    "GME", "AMC", "BBBY",

    # ETF benchmarks for context
    "SPY", "QQQ", "ARKK", "IGV", "SMH",
]

# Risk rules — changing these affects all future autonomous decisions
AUTO_MAX_POSITION_PCT  = 0.20   # max 20% of portfolio per single position
AUTO_MAX_POSITIONS     = 5      # max simultaneous open positions
AUTO_STOP_LOSS_PCT     = 0.05   # hard sell when down 5% from avg cost
AUTO_TAKE_PROFIT_PCT   = 0.10   # hard sell when up 10% from avg cost
AUTO_MIN_CONFIDENCE    = 7      # minimum AI confidence score (out of 10) to BUY
AUTO_SELL_CONFIDENCE   = 3      # sell if AI confidence drops to this or below
AUTO_RUN_INTERVAL_HRS  = 24     # minimum hours between scheduled runs

# ── Scanner scheduling ────────────────────────────────────────────────────────
SCANNER_RUN_INTERVAL_HRS = 24   # how often to run the opportunity scanner
ALERT_MIN_SCORE          = 75   # scanner score threshold to trigger an alert

# ── Email alerts (via SMTP) ───────────────────────────────────────────────────
# Set via .env or environment variables. Leave blank to disable email alerts.
ALERT_EMAIL_TO   = os.getenv("ALERT_EMAIL_TO",   "")   # recipient address
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "")   # sender address
SMTP_HOST        = os.getenv("SMTP_HOST",        "smtp.gmail.com")
SMTP_PORT        = int(os.getenv("SMTP_PORT",    "587"))
SMTP_USER        = os.getenv("SMTP_USER",        "")
SMTP_PASSWORD    = os.getenv("SMTP_PASSWORD",    "")
