from fastapi import APIRouter, Request
from app_templates import templates
from trading.portfolio import get_portfolio_summary

router = APIRouter(prefix="/positions")


@router.get("")
def positions_page(request: Request):
    try:
        summary = get_portfolio_summary()
        return templates.TemplateResponse(request, "positions.html", {
            "summary": summary,
            "active": "positions",
        })
    except Exception as exc:
        return templates.TemplateResponse(request, "positions.html", {
            "error": str(exc),
            "active": "positions",
        })
