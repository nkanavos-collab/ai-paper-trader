from pathlib import Path
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _eur(v) -> str:
    if v is None:
        return "€0.00"
    try:
        return f"€{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v) -> str:
    if v is None:
        return "0.00%"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _pct_plain(v) -> str:
    if v is None:
        return "0.00%"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


templates.env.filters["eur"] = _eur
templates.env.filters["pct"] = _pct
templates.env.filters["pct_plain"] = _pct_plain
templates.env.filters["abs"] = abs
