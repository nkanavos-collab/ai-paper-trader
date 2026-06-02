from dataclasses import dataclass, field
from database import db
from trading.market import get_quote
from config import STARTING_BALANCE_EUR


@dataclass
class PositionSummary:
    symbol: str
    name: str
    quantity: float
    avg_cost_eur: float
    current_price_eur: float
    market_value_eur: float
    cost_basis_eur: float
    unrealised_pnl_eur: float
    unrealised_pnl_pct: float
    change_today_pct: float
    entry_reason: str = ""


@dataclass
class PortfolioSummary:
    cash_eur: float
    positions: list[PositionSummary] = field(default_factory=list)
    total_invested_eur: float = 0.0
    total_market_value_eur: float = 0.0
    total_pnl_eur: float = 0.0
    total_pnl_pct: float = 0.0
    total_portfolio_eur: float = 0.0
    overall_return_pct: float = 0.0


def get_portfolio_summary() -> PortfolioSummary:
    cash = db.get_balance()
    raw_positions = db.get_all_positions()

    positions: list[PositionSummary] = []
    total_invested = 0.0
    total_market_value = 0.0

    for pos in raw_positions:
        quote = get_quote(pos["symbol"])
        if "error" in quote:
            current_price_eur = pos["avg_cost_eur"]
            change_today = 0.0
            name = pos["symbol"]
        else:
            current_price_eur = quote["price_eur"]
            change_today = quote["change_pct"]
            name = quote.get("short_name", pos["symbol"])

        qty = pos["quantity"]
        cost_basis = pos["avg_cost_eur"] * qty
        market_value = current_price_eur * qty
        upnl = market_value - cost_basis
        upnl_pct = (upnl / cost_basis * 100) if cost_basis else 0.0

        total_invested += cost_basis
        total_market_value += market_value

        positions.append(PositionSummary(
            symbol=pos["symbol"],
            name=name,
            quantity=qty,
            avg_cost_eur=pos["avg_cost_eur"],
            current_price_eur=current_price_eur,
            market_value_eur=market_value,
            cost_basis_eur=cost_basis,
            unrealised_pnl_eur=upnl,
            unrealised_pnl_pct=upnl_pct,
            change_today_pct=change_today,
            entry_reason=pos.get("entry_reason") or "",
        ))

    total_pnl = total_market_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0.0
    total_portfolio = cash + total_market_value
    overall_return_pct = ((total_portfolio - STARTING_BALANCE_EUR) / STARTING_BALANCE_EUR * 100)

    return PortfolioSummary(
        cash_eur=cash,
        positions=positions,
        total_invested_eur=total_invested,
        total_market_value_eur=total_market_value,
        total_pnl_eur=total_pnl,
        total_pnl_pct=total_pnl_pct,
        total_portfolio_eur=total_portfolio,
        overall_return_pct=overall_return_pct,
    )
