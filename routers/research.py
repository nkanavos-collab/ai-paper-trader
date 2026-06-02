import json
from fastapi import APIRouter, Request
from app_templates import templates
from research.analyst import analyse
from database.db import get_all_research
from database.predictions import get_accuracy_stats

router = APIRouter(prefix="/research")


def _normalize(raw: dict) -> dict:
    """Merge DB row with its inner analysis JSON, plus parse stored risks/sources/raw_data."""
    if not raw:
        return raw

    out = dict(raw)

    # Merge analysis JSON into the flat dict
    if isinstance(out.get("analysis"), str):
        try:
            out.update(json.loads(out["analysis"]))
        except Exception:
            pass

    # Parse JSON-string list fields
    for field in ("risks", "sources_used"):
        if isinstance(out.get(field), str):
            try:
                out[field] = json.loads(out[field])
            except Exception:
                out[field] = []

    # Parse raw_data
    if isinstance(out.get("raw_data"), str):
        try:
            out["raw_data"] = json.loads(out["raw_data"])
        except Exception:
            out["raw_data"] = {}

    return out


@router.get("")
def research_page(request: Request, symbol: str = None, force: str = None):
    result = None
    error = None

    if symbol:
        symbol = symbol.upper().strip()
        try:
            raw = analyse(symbol, force_refresh=(force == "true"))
            if "error" in raw and not raw.get("company_name"):
                error = raw["error"]
            else:
                result = _normalize(raw)
        except Exception as exc:
            error = str(exc)

    history          = [_normalize(r) for r in get_all_research()[:20]]
    overall_accuracy = get_accuracy_stats()
    symbol_accuracy  = get_accuracy_stats(symbol) if symbol else None

    return templates.TemplateResponse(request, "research.html", {
        "symbol":           symbol,
        "result":           result,
        "error":            error,
        "history":          history,
        "overall_accuracy": overall_accuracy,
        "symbol_accuracy":  symbol_accuracy,
        "active":           "research",
    })
