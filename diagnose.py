"""
Run:  python diagnose.py
This writes ALL errors to diagnose_output.txt so you can read them.
"""
import sys, traceback, os

OUT = "diagnose_output.txt"

def log(msg=""):
    print(msg)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# Clear old file
open(OUT, "w").close()

log("="*60)
log("PYTHON: " + sys.version)
log("CWD:    " + os.getcwd())
log("="*60)
log()

steps = [
    ("config",               "import config"),
    ("dotenv",               "from dotenv import load_dotenv"),
    ("fastapi",              "from fastapi import FastAPI"),
    ("uvicorn",              "import uvicorn"),
    ("yfinance",             "import yfinance"),
    ("database.models",      "from database.models import init_db"),
    ("database.db",          "from database import db"),
    ("trading.market",       "from trading.market import get_quote"),
    ("trading.engine",       "from trading.engine import buy, sell"),
    ("trading.portfolio",    "from trading.portfolio import get_portfolio_summary"),
    ("research.sources.market", "from research.sources.market import get_market_data"),
    ("research.sources.news",   "from research.sources.news import get_news"),
    ("research.sources.sentiment","from research.sources.sentiment import get_sentiment"),
    ("research.sources.macro",   "from research.sources.macro import get_macro_data"),
    ("research.analyst",     "from research.analyst import analyse"),
    ("routers.dashboard",    "from routers import dashboard"),
    ("routers.research",     "from routers import research"),
    ("routers.trade",        "from routers import trade"),
    ("routers.positions",    "from routers import positions"),
    ("routers.transactions", "from routers import transactions"),
    ("routers.reports",      "from routers import reports"),
    ("routers.predictions",  "from routers import predictions"),
    ("routers.diagnostics",  "from routers import diagnostics"),
    ("routers.auto_trading", "from routers import auto_trading"),
    ("trading.auto_trader",  "from trading import auto_trader"),
]

failed = []
for name, code in steps:
    try:
        exec(code, {})
        log(f"  OK   {name}")
    except Exception as exc:
        tb = traceback.format_exc()
        log(f"  FAIL {name}: {exc}")
        log(tb)
        failed.append(name)

log()
log("="*60)
if failed:
    log(f"BROKEN: {failed}")
else:
    log("ALL OK — try:  python web_app.py")
log("="*60)
log(f"\nFull output saved to: {os.path.abspath(OUT)}")
