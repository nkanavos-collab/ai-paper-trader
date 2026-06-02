"""Risk dashboard router."""

import json
import logging
import traceback
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse

from app_templates import templates

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/risk", response_class=HTMLResponse)
async def risk_page(request: Request):
    try:
        from trading.risk import get_risk_report
        from tracking.snapshots import get_equity_chart_data
        from trading.portfolio_review import get_latest_review

        report = get_risk_report()
        chart  = get_equity_chart_data(days=90)
        review = get_latest_review()

        if "days" not in chart:
            chart["days"] = len(chart.get("labels", []))

        corr_rows = []
        symbols   = report.get("symbols", [])
        matrix    = report.get("correlation", {}).get("matrix", [])
        for i, sym in enumerate(symbols):
            row_vals = matrix[i] if i < len(matrix) else []
            corr_rows.append({"symbol": sym, "cols": row_vals})

        return templates.TemplateResponse(request, "risk.html", {
            "active":     "risk",
            "report":     report,
            "chart":      chart,
            "chart_json": json.dumps(chart),
            "review":     review,
            "corr_rows":  corr_rows,
        })

    except Exception:
        err = traceback.format_exc()
        log.error("[RISK] Page error:\n%s", err)
        return HTMLResponse(
            f"<pre style='background:#111;color:#f87171;padding:2rem'>{err}</pre>",
            status_code=500,
        )


@router.post("/api/risk/run-review", response_class=JSONResponse)
async def trigger_review(background_tasks: BackgroundTasks):
    def _run():
        try:
            from trading.portfolio_review import run_review
            result = run_review()
            if result:
                log.info("[REVIEW] Background review saved")
        except Exception as exc:
            log.error("[REVIEW] Background review failed: %s", exc, exc_info=True)

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Review generating — refresh in ~15 seconds."}


@router.get("/api/risk/report", response_class=JSONResponse)
async def api_risk_report():
    from trading.risk import get_risk_report
    report = get_risk_report()
    report.pop("positions", None)
    return report
