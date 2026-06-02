import sys
import traceback
from pathlib import Path
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from config import PAPER_TRADING_ONLY, WEB_HOST, WEB_PORT

if not PAPER_TRADING_ONLY:
    print("FATAL: PAPER_TRADING_ONLY is False. Real trading is not implemented.")
    print("Set PAPER_TRADING_ONLY = True in config.py to start the app.")
    sys.exit(1)

from database.models import init_db
from routers import dashboard, research, trade, positions, transactions, reports, predictions, diagnostics, scanner, analytics, risk
from trading.market import get_quote

# ── Auto-trading router loaded separately so an import error doesn't kill the whole app ──
_auto_trading_module  = None
_auto_trading_tb      = ""
try:
    from routers import auto_trading as _at
    _auto_trading_module = _at
    print("[web_app] auto_trading router loaded OK")
except Exception:
    _auto_trading_tb = traceback.format_exc()
    print(f"[web_app] ⚠️  auto_trading router FAILED to load:\n{_auto_trading_tb}")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="AI Paper Trader", docs_url=None, redoc_url=None)

BASE = Path(__file__).parent
(BASE / "reports_output").mkdir(exist_ok=True)
app.mount("/static",         StaticFiles(directory=str(BASE / "static")),         name="static")
app.mount("/reports_output", StaticFiles(directory=str(BASE / "reports_output")), name="reports_output")

app.include_router(dashboard.router)
app.include_router(research.router)
app.include_router(trade.router)
app.include_router(positions.router)
app.include_router(transactions.router)
app.include_router(reports.router)
app.include_router(predictions.router)
app.include_router(diagnostics.router)
app.include_router(scanner.router)
app.include_router(analytics.router)
app.include_router(risk.router)

if _auto_trading_module:
    app.include_router(_auto_trading_module.router)
else:
    # Fallback: show the import traceback so we can fix it
    _tb_html = _auto_trading_tb.replace("<", "&lt;").replace(">", "&gt;")

    @app.get("/auto-trading")
    def _auto_trading_error():
        return HTMLResponse(f"""<!DOCTYPE html>
<html><body style="background:#0d1117;color:#c9d1d9;font-family:monospace;padding:2rem">
<h2 style="color:#f85149">&#9888; Auto Trading — failed to load</h2>
<p>The <code>routers.auto_trading</code> module threw an exception during import.
Copy the traceback below and share it so the error can be fixed.</p>
<pre style="background:#161b22;padding:1.2rem;border-radius:8px;color:#e6edf3;
            overflow:auto;font-size:.82rem">{_tb_html}</pre>
<a href="/" style="color:#58a6ff">← Dashboard</a>
</body></html>""", status_code=500)


@app.get("/api/quote/{symbol}")
def api_quote(symbol: str):
    return get_quote(symbol.upper().strip())


@app.on_event("startup")
def startup():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    init_db()
    from config import ON_VERCEL
    if _auto_trading_module and not ON_VERCEL:
        try:
            from trading.auto_trader import start_scheduler
            start_scheduler()
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).error("start_scheduler failed: %s", exc, exc_info=True)


@app.get("/ping")
def ping():
    """Version check — tells you if the NEW code is actually running."""
    import time
    return {
        "status": "ok",
        "version": "lazy-auto-trader",
        "auto_trading_loaded": _auto_trading_module is not None,
        "auto_trading_error": _auto_trading_tb[:300] if _auto_trading_tb else None,
        "time": time.time(),
    }


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  AI Paper Trader — NEW VERSION (lazy auto-trader)")
    print(f"  http://{WEB_HOST}:{WEB_PORT}")
    print("  PAPER TRADING ONLY — no real money")
    print("="*50 + "\n")
    uvicorn.run("web_app:app", host=WEB_HOST, port=WEB_PORT, reload=False)
