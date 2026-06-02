from fastapi import APIRouter, Request
from app_templates import templates
from database.db import get_transactions

router = APIRouter(prefix="/transactions")


@router.get("")
def transactions_page(request: Request, symbol: str = None):
    symbol = symbol.upper().strip() if symbol else None
    txs = get_transactions(symbol=symbol, limit=200)
    return templates.TemplateResponse(request, "transactions.html", {
        "transactions": txs,
        "filter_symbol": symbol or "",
        "active": "transactions",
    })
