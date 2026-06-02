from urllib.parse import quote_plus
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from app_templates import templates
from tracking.evaluator import evaluate_pending
from tracking.metrics import get_performance_stats
from database.predictions import get_predictions_with_evals

router = APIRouter(prefix="/predictions")


@router.get("")
def predictions_page(request: Request):
    newly_evaluated = evaluate_pending(max_evals=30)
    stats = get_performance_stats()
    predictions = get_predictions_with_evals(limit=100)

    return templates.TemplateResponse(request, "predictions.html", {
        "stats":            stats,
        "predictions":      predictions,
        "newly_evaluated":  newly_evaluated,
        "active":           "predictions",
    })


@router.post("/evaluate")
def force_evaluate():
    n = evaluate_pending(max_evals=100)
    return RedirectResponse(
        f"/predictions?msg={quote_plus(f'{n} evaluation(s) completed')}&status=success",
        status_code=303,
    )
