# AI Investment Research & Paper Trading Simulator

A Python app for AI-driven investment research and paper trading. Starts with a virtual **€100** balance and trades US stocks & ETFs with no real money involved.

> **PAPER TRADING ONLY — NO REAL MONEY — NO BROKER CONNECTION**

---

## Features

- **Web dashboard** — FastAPI + Jinja2 UI with 6 pages
- **Paper trading** — buy/sell US stocks and ETFs with fractional share support
- **Live market data** — real prices via `yfinance` with automatic EUR/USD conversion
- **AI research** — Claude-powered BUY/HOLD/SELL analysis with confidence scores
  - Falls back to rule-based scoring when no API key is configured
- **SQLite persistence** — all trades, positions, and research saved locally
- **Excel reports** — 4-sheet workbooks downloadable from the Reports page

---

## Project Structure

```
Trading/
├── web_app.py               # Web app entry point  →  python web_app.py
├── main.py                  # CLI entry point      →  python main.py
├── config.py                # Settings (PAPER_TRADING_ONLY enforced here)
├── app_templates.py         # Shared Jinja2 templates instance
├── requirements.txt
├── .env.example
├── trading.db               # SQLite DB (auto-created on first run)
├── reports_output/          # Generated Excel reports
├── database/
│   ├── models.py            # Schema + DB init + migrations
│   └── db.py                # CRUD (account, positions, transactions, research)
├── trading/
│   ├── engine.py            # buy() / sell() logic with P&L tracking
│   ├── portfolio.py         # Live portfolio + P&L summary
│   └── market.py            # yfinance wrapper + EUR/USD rate
├── research/
│   ├── analyst.py           # Claude API + rule-based fallback
│   └── prompts.py           # Prompt templates
├── reports/
│   └── excel.py             # Excel generation (4 sheets)
├── routers/                 # FastAPI route handlers
│   ├── dashboard.py         # GET /
│   ├── research.py          # GET /research
│   ├── trade.py             # GET/POST /trade
│   ├── positions.py         # GET /positions
│   ├── transactions.py      # GET /transactions
│   └── reports.py           # GET/POST /reports
├── templates/               # Jinja2 HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── research.html
│   ├── trade.html
│   ├── positions.html
│   ├── transactions.html
│   └── reports.html
└── static/
    └── style.css
```

---

## Setup

### 1. Install Python

Download Python 3.11+ from https://www.python.org/downloads/ — check **"Add to PATH"** during install.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. (Optional) Enable AI research

```bash
copy .env.example .env
```

Edit `.env` and set your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without a key the app uses rule-based scoring automatically.

### 4. Run the web app

```bash
python web_app.py
```

Open **http://127.0.0.1:8000** in your browser.

### 4b. Run the CLI instead

```bash
python main.py
```

---

## Web Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Portfolio value, P&L cards, recent trades |
| Research | `/research` | AI analysis: BUY/HOLD/SELL, confidence, thesis |
| Trade | `/trade` | Buy/sell with live quote lookup and confirmation |
| Positions | `/positions` | Open positions with entry price, P&L, reason |
| Transactions | `/transactions` | Full buy/sell history with realized P&L |
| Reports | `/reports` | Generate & download Excel reports |

---

## Safety

- `PAPER_TRADING_ONLY = True` is hardcoded in `config.py`. The app **refuses to start** if this is `False`.
- A yellow banner appears on every page: **PAPER TRADING ONLY — NO REAL MONEY — NO BROKER CONNECTION**
- No real brokerage API is connected or referenced anywhere in the codebase.

---

## Notes

- Prices are fetched in USD and converted to EUR via a live `EURUSD=X` rate
- Fractional quantities are supported (e.g. 0.1 shares of AAPL)
- Research is cached in SQLite; tick "Force refresh" to re-run the analysis
- Delete `trading.db` to reset the simulation to €100 starting balance
