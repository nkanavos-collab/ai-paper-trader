from dataclasses import dataclass
from database import db
from trading.market import get_quote


@dataclass
class TradeResult:
    success: bool
    message: str
    symbol: str = ""
    action: str = ""
    quantity: float = 0.0
    price_usd: float = 0.0
    price_eur: float = 0.0
    total_eur: float = 0.0
    balance_after: float = 0.0


def buy(symbol: str, quantity: float, notes: str = "") -> TradeResult:
    symbol = symbol.upper()
    if quantity <= 0:
        return TradeResult(False, "Quantity must be positive.")

    quote = get_quote(symbol)
    if "error" in quote:
        return TradeResult(False, f"Market data error: {quote['error']}")

    price_usd = quote["price_usd"]
    price_eur = quote["price_eur"]
    eur_usd = quote["eur_usd_rate"]
    total_eur = price_eur * quantity

    balance = db.get_balance()
    if total_eur > balance:
        return TradeResult(
            False,
            f"Insufficient funds. Need €{total_eur:.2f}, have €{balance:.2f}.",
        )

    new_balance = balance - total_eur

    existing = db.get_position(symbol)
    if existing and existing["quantity"] > 0:
        old_qty = existing["quantity"]
        old_avg_usd = existing["avg_cost_usd"]
        old_avg_eur = existing["avg_cost_eur"]
        new_qty = old_qty + quantity
        new_avg_usd = (old_avg_usd * old_qty + price_usd * quantity) / new_qty
        new_avg_eur = (old_avg_eur * old_qty + price_eur * quantity) / new_qty
    else:
        new_qty = quantity
        new_avg_usd = price_usd
        new_avg_eur = price_eur

    db.upsert_position(symbol, new_qty, new_avg_usd, new_avg_eur, entry_reason=notes)
    db.set_balance(new_balance)
    db.record_transaction(
        symbol, "BUY", quantity, price_usd, price_eur,
        eur_usd, total_eur, new_balance, notes,
    )

    return TradeResult(
        success=True,
        message=f"Bought {quantity} × {symbol} @ €{price_eur:.4f} = €{total_eur:.2f}",
        symbol=symbol,
        action="BUY",
        quantity=quantity,
        price_usd=price_usd,
        price_eur=price_eur,
        total_eur=total_eur,
        balance_after=new_balance,
    )


def sell(symbol: str, quantity: float, notes: str = "") -> TradeResult:
    symbol = symbol.upper()
    if quantity <= 0:
        return TradeResult(False, "Quantity must be positive.")

    position = db.get_position(symbol)
    if not position or position["quantity"] <= 0:
        return TradeResult(False, f"No position in {symbol}.")
    if quantity > position["quantity"]:
        return TradeResult(
            False,
            f"Cannot sell {quantity} — only {position['quantity']:.4f} held.",
        )

    quote = get_quote(symbol)
    if "error" in quote:
        return TradeResult(False, f"Market data error: {quote['error']}")

    price_usd = quote["price_usd"]
    price_eur = quote["price_eur"]
    eur_usd = quote["eur_usd_rate"]
    total_eur = price_eur * quantity

    balance = db.get_balance()
    new_balance = balance + total_eur

    remaining = position["quantity"] - quantity
    if remaining < 1e-9:
        db.delete_position(symbol)
    else:
        db.upsert_position(
            symbol, remaining,
            position["avg_cost_usd"], position["avg_cost_eur"],
        )

    cost_basis = position["avg_cost_eur"] * quantity
    pnl = total_eur - cost_basis

    db.set_balance(new_balance)
    db.record_transaction(
        symbol, "SELL", quantity, price_usd, price_eur,
        eur_usd, total_eur, new_balance, notes,
        realized_pnl_eur=pnl,
    )
    pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0.0

    return TradeResult(
        success=True,
        message=(
            f"Sold {quantity} × {symbol} @ €{price_eur:.4f} = €{total_eur:.2f} "
            f"| P&L: {'+'if pnl>=0 else ''}€{pnl:.2f} ({pnl_pct:+.1f}%)"
        ),
        symbol=symbol,
        action="SELL",
        quantity=quantity,
        price_usd=price_usd,
        price_eur=price_eur,
        total_eur=total_eur,
        balance_after=new_balance,
    )
