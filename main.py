import sys
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
from rich.text import Text

from database.models import init_db
from database import db
from trading.engine import buy, sell
from trading.market import get_quote
from trading.portfolio import get_portfolio_summary
from research.analyst import analyse
from reports.excel import generate_report
from config import STARTING_BALANCE_EUR, ANTHROPIC_API_KEY

console = Console()


def _pnl_text(value: float, pct: float | None = None) -> Text:
    color = "green" if value >= 0 else "red"
    sign = "+" if value >= 0 else ""
    txt = f"{sign}€{value:.2f}"
    if pct is not None:
        txt += f" ({sign}{pct:.1f}%)"
    return Text(txt, style=color)


def show_header():
    summary = get_portfolio_summary()
    pnl_color = "green" if summary.overall_return_pct >= 0 else "red"
    header = (
        f"[bold white]Cash:[/] [cyan]€{summary.cash_eur:.2f}[/]  "
        f"[bold white]Invested:[/] [cyan]€{summary.total_market_value_eur:.2f}[/]  "
        f"[bold white]Total:[/] [cyan]€{summary.total_portfolio_eur:.2f}[/]  "
        f"[bold white]Return:[/] [{pnl_color}]{summary.overall_return_pct:+.2f}%[/]"
        + (" [dim](AI on)[/]" if ANTHROPIC_API_KEY else " [dim](rule-based)[/]")
    )
    console.print(Panel(header, title="[bold]AI Paper Trader[/]", border_style="blue"))


def cmd_portfolio():
    summary = get_portfolio_summary()
    if not summary.positions:
        console.print("[yellow]No open positions.[/]")
    else:
        t = Table(box=box.ROUNDED, show_header=True, header_style="bold blue")
        for col in ["Symbol", "Name", "Qty", "Avg Cost €", "Price €", "Value €", "P&L €", "P&L %", "Today"]:
            t.add_column(col, justify="right" if col not in ("Symbol", "Name") else "left")
        for p in summary.positions:
            pnl_col = "green" if p.unrealised_pnl_eur >= 0 else "red"
            today_col = "green" if p.change_today_pct >= 0 else "red"
            t.add_row(
                p.symbol, p.name[:22],
                f"{p.quantity:.4f}",
                f"€{p.avg_cost_eur:.4f}",
                f"€{p.current_price_eur:.4f}",
                f"€{p.market_value_eur:.2f}",
                f"[{pnl_col}]{'+'if p.unrealised_pnl_eur>=0 else ''}€{p.unrealised_pnl_eur:.2f}[/]",
                f"[{pnl_col}]{p.unrealised_pnl_pct:+.1f}%[/]",
                f"[{today_col}]{p.change_today_pct:+.1f}%[/]",
            )
        console.print(t)
        console.print(
            f"  [dim]Total invested: €{summary.total_invested_eur:.2f} | "
            f"Market value: €{summary.total_market_value_eur:.2f} | "
            f"Unrealised P&L: {'+'if summary.total_pnl_eur>=0 else ''}€{summary.total_pnl_eur:.2f}[/]"
        )


def cmd_quote():
    symbol = Prompt.ask("Symbol (e.g. AAPL, SPY, MSFT)").strip().upper()
    console.print(f"[dim]Fetching {symbol}...[/]")
    q = get_quote(symbol)
    if "error" in q:
        console.print(f"[red]Error: {q['error']}[/]")
        return
    change_col = "green" if q["change_pct"] >= 0 else "red"
    t = Table(box=box.SIMPLE, show_header=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    rows = [
        ("Symbol", q["symbol"]),
        ("Name", q.get("name", "")),
        ("Price (USD)", f"${q['price_usd']:.4f}"),
        ("Price (EUR)", f"€{q['price_eur']:.4f}"),
        ("EUR/USD Rate", f"{q['eur_usd_rate']:.4f}"),
        ("Change Today", f"[{change_col}]{q['change_pct']:+.2f}%[/]"),
        ("52w High", f"${q.get('52w_high') or 'N/A'}"),
        ("52w Low", f"${q.get('52w_low') or 'N/A'}"),
        ("P/E Ratio", str(q.get("pe_ratio") or "N/A")),
        ("Forward P/E", str(q.get("forward_pe") or "N/A")),
        ("Beta", str(q.get("beta") or "N/A")),
        ("Sector", q.get("sector") or "N/A"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)


def cmd_buy():
    symbol = Prompt.ask("Symbol to buy").strip().upper()
    console.print(f"[dim]Fetching {symbol}...[/]")
    q = get_quote(symbol)
    if "error" in q:
        console.print(f"[red]{q['error']}[/]")
        return
    console.print(f"  {q['symbol']} — {q.get('name','')} — €{q['price_eur']:.4f}/share")
    cash = db.get_balance()
    console.print(f"  Available cash: €{cash:.2f}")
    qty_str = Prompt.ask("Quantity (supports decimals, e.g. 0.5)")
    try:
        qty = float(qty_str)
    except ValueError:
        console.print("[red]Invalid quantity.[/]")
        return
    result = buy(symbol, qty)
    if result.success:
        console.print(f"[green]✓ {result.message}[/]")
        console.print(f"  Cash remaining: €{result.balance_after:.2f}")
    else:
        console.print(f"[red]✗ {result.message}[/]")


def cmd_sell():
    positions = db.get_all_positions()
    if not positions:
        console.print("[yellow]No open positions to sell.[/]")
        return
    console.print("[bold]Open positions:[/]")
    for p in positions:
        console.print(f"  [cyan]{p['symbol']}[/] — {p['quantity']:.4f} shares")
    symbol = Prompt.ask("Symbol to sell").strip().upper()
    qty_str = Prompt.ask("Quantity")
    try:
        qty = float(qty_str)
    except ValueError:
        console.print("[red]Invalid quantity.[/]")
        return
    result = sell(symbol, qty)
    if result.success:
        console.print(f"[green]✓ {result.message}[/]")
        console.print(f"  Cash after sale: €{result.balance_after:.2f}")
    else:
        console.print(f"[red]✗ {result.message}[/]")


def cmd_research():
    symbol = Prompt.ask("Symbol to analyse").strip().upper()
    force = Confirm.ask("Force fresh analysis? (no = use cache if available)", default=False)
    console.print(f"[dim]Analysing {symbol}...[/]")
    result = analyse(symbol, force_refresh=force)
    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/]")
        return

    rec = result.get("recommendation", "HOLD")
    rec_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(rec, "white")
    conf = result.get("confidence", 5)
    bars = "█" * conf + "░" * (10 - conf)

    console.print(Panel(
        f"[bold]{result.get('company_name', symbol)}[/] ({symbol})\n\n"
        f"Recommendation: [{rec_color}][bold]{rec}[/][/]   "
        f"Confidence: [{rec_color}]{bars}[/] {conf}/10\n\n"
        f"[bold]Thesis:[/] {result.get('thesis', '')}\n\n"
        f"[bold green]Bull Case:[/] {result.get('bull_case', '')}\n"
        f"[bold red]Bear Case:[/] {result.get('bear_case', '')}\n\n"
        f"[bold]Valuation:[/] {result.get('key_metrics_assessment', '')}\n"
        f"[bold]Time Horizon:[/] {result.get('time_horizon', '')}\n"
        f"[bold]Target Price:[/] "
        + (f"${result.get('target_price_usd'):.2f}" if result.get("target_price_usd") else "N/A")
        + f"\n[bold]Suggested Position Size:[/] €{result.get('suggested_position_size_eur', 0):.2f}\n"
        f"[bold]Suitable for Small Account:[/] "
        + ("[green]Yes[/]" if result.get("suitable_for_small_account") else "[red]No[/]"),
        title=f"[bold]Research — {symbol}[/]",
        border_style=rec_color,
    ))


def cmd_history():
    symbol = Prompt.ask("Symbol (leave blank for all)", default="").strip().upper() or None
    txs = db.get_transactions(symbol=symbol, limit=20)
    if not txs:
        console.print("[yellow]No transactions found.[/]")
        return
    t = Table(box=box.ROUNDED, header_style="bold blue")
    for col in ["Time", "Symbol", "Action", "Qty", "Price €", "Total €", "Balance €"]:
        t.add_column(col, justify="right" if col not in ("Time", "Symbol", "Action") else "left")
    for tx in txs:
        action_col = "green" if tx["action"] == "BUY" else "red"
        t.add_row(
            tx["timestamp"][:16].replace("T", " "),
            tx["symbol"],
            f"[{action_col}]{tx['action']}[/]",
            f"{tx['quantity']:.4f}",
            f"€{tx['price_eur']:.4f}",
            f"€{tx['total_eur']:.2f}",
            f"€{tx['balance_after_eur']:.2f}",
        )
    console.print(t)


def cmd_report():
    console.print("[dim]Generating Excel report...[/]")
    try:
        path = generate_report()
        console.print(f"[green]✓ Report saved:[/] {path}")
    except Exception as e:
        console.print(f"[red]Report generation failed: {e}[/]")


MENU = [
    ("1", "View Portfolio",       cmd_portfolio),
    ("2", "Buy Stock / ETF",      cmd_buy),
    ("3", "Sell Stock / ETF",     cmd_sell),
    ("4", "Market Quote",         cmd_quote),
    ("5", "AI Research Analysis", cmd_research),
    ("6", "Transaction History",  cmd_history),
    ("7", "Generate Excel Report",cmd_report),
    ("8", "Exit",                 None),
]


def main():
    init_db()
    console.clear()

    while True:
        show_header()
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column("Key", style="bold cyan", width=4)
        t.add_column("Action")
        for key, label, _ in MENU:
            t.add_row(key, label)
        console.print(t)

        choice = Prompt.ask("[bold]Choose[/]", default="1").strip()

        if choice == "8":
            console.print("[dim]Goodbye.[/]")
            sys.exit(0)

        for key, _, fn in MENU:
            if choice == key and fn:
                console.print()
                try:
                    fn()
                except KeyboardInterrupt:
                    console.print("\n[dim]Cancelled.[/]")
                break
        else:
            console.print("[red]Invalid choice.[/]")

        console.print()
        Prompt.ask("[dim]Press Enter to continue[/]", default="")
        console.clear()


if __name__ == "__main__":
    main()
