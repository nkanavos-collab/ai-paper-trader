import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote_plus
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from app_templates import templates
from config import REPORTS_DIR

router = APIRouter(prefix="/reports")


def _list_reports() -> list[dict]:
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.xlsx"), reverse=True):
        stat = f.stat()
        reports.append({
            "name": f.name,
            "url": f"/reports_output/{f.name}",
            "size_kb": round(stat.st_size / 1024, 1),
            "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })
    return reports


@router.get("")
def reports_page(request: Request, msg: str = None, status: str = None):
    return templates.TemplateResponse(request, "reports.html", {
        "reports": _list_reports(),
        "message": msg,
        "msg_type": status or "info",
        "active": "reports",
    })


@router.post("/generate")
def generate_report():
    try:
        from reports.excel import generate_report as _gen
        path = _gen()
        return RedirectResponse(
            f"/reports?msg={quote_plus(f'Report saved: {path.name}')}&status=success",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            f"/reports?msg={quote_plus(str(exc))}&status=danger",
            status_code=303,
        )
