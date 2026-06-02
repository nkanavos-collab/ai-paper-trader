from urllib.parse import quote_plus
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from app_templates import templates
from trading.engine import buy, sell
from database.db import get_all_positions, get_balance, get_transactions

router = APIRouter(prefix="/trade")


def _redirect(msg: str, status: str) -> RedirectResponse:
    return RedirectResponse(
        f"/trade?msg={quote_plus(msg)}&status={status}",
        status_code=303,
    )


@router.get("")
def trade_page(request: Request, msg: str = None, status: str = None):
    kelly = None
    try:
        from database.predictions import get_kelly_inputs
        kelly = get_kelly_inputs()
    except Exception:
        pass

    portfolio_eur = None
    try:
        from trading.portfolio import get_portfolio_summary
        portfolio_eur = get_portfolio_summary().total_portfolio_eur
    except Exception:
        pass

    return templates.TemplateResponse(request, "trade.html", {
        "positions":     get_all_positions(),
        "cash":          get_balance(),
        "recent":        get_transactions(limit=6),
        "message":       msg,
        "msg_type":      status or "info",
        "active":        "trade",
        "kelly":         kelly,
        "portfolio_eur": portfolio_eur,
    })


@router.post("/buy")
def do_buy(
    symbol: str = Form(...),
    quantity: str = Form(...),
    reason: str = Form(""),
):
    try:
        result = buy(symbol.strip().upper(), float(quantity), reason.strip())
        status = "success" if result.success else "danger"
        return _redirect(result.message, status)
    except ValueError:
        return _redirect("Invalid quantity — enter a number (e.g. 1 or 0.5).", "danger")
    except Exception as exc:
        return _redirect(str(exc), "danger")


@router.post("/sell")
def do_sell(
    symbol: str = Form(...),
    quantity: str = Form(...),
    reason: str = Form(""),
):
    try:
        result = sell(symbol.strip().upper(), float(quantity), reason.strip())
        status = "success" if result.success else "danger"
        return _redirect(result.message, status)
    except ValueError:
        return _redirect("Invalid quantity — enter a number (e.g. 1 or 0.5).", "danger")
    except Exception as exc:
        return _redirect(str(exc), "danger")
