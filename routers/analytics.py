"""Analytics router — signal attribution, backtests, alerts."""

import logging
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse

from app_templates import templates

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    from tracking.attribution import get_full_attribution
    from research.backtest import get_formatted_results
    from tracking.alerts import get_alert_status
    from database.predictions import get_accuracy_stats

    attribution  = get_full_attribution()
    backtest     = get_formatted_results()
    alert_status = get_alert_status()
    accuracy     = get_accuracy_stats()

    return templates.TemplateResponse(request, "analytics.html", {
        "active":       "analytics",
        "attribution":  attribution,
        "backtest":     backtest,
        "alert_status": alert_status,
        "accuracy":     accuracy,
    })


@router.post("/api/analytics/run-backtest", response_class=JSONResponse)
async def trigger_backtest(background_tasks: BackgroundTasks):
    """Kick off a fresh backtest in the background."""
    from config import SCANNER_UNIVERSE
    from research.backtest import run_and_save

    def _run():
        try:
            rows = run_and_save(SCANNER_UNIVERSE[:20])  # limit to 20 for speed
            log.info("[BACKTEST] Background run done: %d rows", len(rows))
        except Exception as exc:
            log.error("[BACKTEST] Background run failed: %s", exc, exc_info=True)

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Backtest running in background — refresh in ~60 seconds."}


@router.get("/api/analytics/attribution", response_class=JSONResponse)
async def api_attribution():
    from tracking.attribution import get_full_attribution
    return get_full_attribution()


@router.get("/api/analytics/alerts", response_class=JSONResponse)
async def api_alerts():
    from database.predictions import get_recent_alerts
    return get_recent_alerts(limit=50)
