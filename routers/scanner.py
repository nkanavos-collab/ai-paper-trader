import json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from app_templates import templates
from database.scanner_db import get_latest_scan, get_scan_history

router = APIRouter(prefix="/scanner")

_scan_running = False
_last_scan_error: str = ""


def _do_scan():
    global _scan_running, _last_scan_error
    _last_scan_error = ""
    try:
        from research.scanner import run_scan
        run_scan()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Scan failed: %s", exc, exc_info=True)
        _last_scan_error = str(exc)
    finally:
        _scan_running = False


@router.get("")
def scanner_page(request: Request):
    latest  = get_latest_scan()
    history = get_scan_history(10)
    return templates.TemplateResponse(request, "scanner.html", {
        "latest":       latest,
        "history":      history,
        "scan_running": _scan_running,
        "scan_error":   _last_scan_error,
        "active":       "scanner",
    })


@router.post("/run")
def run_scanner(background_tasks: BackgroundTasks):
    global _scan_running
    if _scan_running:
        return JSONResponse({"status": "already_running"})
    _scan_running = True
    background_tasks.add_task(_do_scan)
    return JSONResponse({"status": "started"})


@router.get("/status")
def scan_status():
    return JSONResponse({
        "running": _scan_running,
        "error":   _last_scan_error,
    })
