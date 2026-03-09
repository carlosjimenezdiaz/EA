"""Personal Financial Assistant — CLI entry point."""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ea import db, fx

# ── App & sub-apps ─────────────────────────────────────────────────────────────

app = typer.Typer(
    name="ea",
    help="Personal Financial Assistant",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
accounts_app = typer.Typer(help="Manage financial accounts", no_args_is_help=True)
income_app   = typer.Typer(help="Manage income sources",    no_args_is_help=True)
expense_app  = typer.Typer(help="Manage expenses",          no_args_is_help=True)
tx_app       = typer.Typer(help="Transaction log",          no_args_is_help=True)
config_app   = typer.Typer(help="Configuration",            no_args_is_help=True)

app.add_typer(accounts_app, name="accounts")
app.add_typer(income_app,   name="income")
app.add_typer(expense_app,  name="expense")
app.add_typer(tx_app,       name="tx")
app.add_typer(config_app,   name="config")

console = Console()

ACCOUNT_TYPES = ["bank", "credit_card", "loan_personal", "loan_car", "cash", "investment"]
FREQUENCIES   = ["weekly", "biweekly", "monthly", "yearly"]


# ── DB init callback ───────────────────────────────────────────────────────────

@app.callback()
def _startup():
    db.init_db()


# ── Date helpers ───────────────────────────────────────────────────────────────

def _advance_date(d: date, frequency: str, day_of_month: int = None) -> date:
    """Return the next occurrence date after *d* for the given frequency."""
    if frequency == "weekly":
        return d + timedelta(days=7)
    if frequency == "biweekly":
        return d + timedelta(days=14)
    if frequency == "monthly":
        m, y = d.month + 1, d.year
        if m > 12:
            m, y = 1, y + 1
        target = day_of_month if day_of_month else d.day
        target = min(target, calendar.monthrange(y, m)[1])
        return date(y, m, target)
    if frequency == "yearly":
        try:
            return date(d.year + 1, d.month, d.day)
        except ValueError:          # Feb 29 on non-leap year
            return date(d.year + 1, d.month, 28)
    return d


def _get_occurrences(item: dict, start: date, end: date) -> list[date]:
    """All occurrence dates of *item* in [start, end]."""
    occurrences: list[date] = []
    current = date.fromisoformat(item["next_date"])
    dom = item.get("day_of_month")
    freq = item["frequency"]

    safety = 0
    while current < start and safety < 1000:
        current = _advance_date(current, freq, dom)
        safety += 1

    safety = 0
    while current <= end and safety < 500:
        occurrences.append(current)
        current = _advance_date(current, freq, dom)
        safety += 1

    return occurrences


# ── FX helper ──────────────────────────────────────────────────────────────────

def _convert(amount: float, from_ccy: str, to_ccy: str) -> tuple[float, float]:
    """Convert with graceful fallback (returns 1:1 if offline)."""
    if from_ccy.upper() == to_ccy.upper():
        return amount, 1.0
    try:
        return fx.convert(amount, from_ccy, to_ccy)
    except Exception:
        console.print(
            f"[yellow]⚠ Could not fetch FX rate {from_ccy}→{to_ccy}. Using 1:1 fallback.[/yellow]"
        )
        return amount, 1.0


# ── Shared table printer ───────────────────────────────────────────────────────

def _print_recurring_table(items: list[dict], title: str):
    base = db.get_setting("base_currency", "USD")
    t = Table(title=title, box=box.ROUNDED)
    t.add_column("ID",        style="dim", width=4)
    t.add_column("Name",      style="bold")
    t.add_column("Amount",    justify="right")
    t.add_column("Frequency")
    t.add_column("Next Date")
    t.add_column("Category")
    t.add_column("Account")
    t.add_column("On?", justify="center")
    for item in items:
        style = "green" if item["item_type"] == "income" else "red"
        t.add_row(
            str(item["id"]),
            item["name"],
            f"[{style}]{item['amount']:,.2f} {item['currency']}[/{style}]",
            item["frequency"],
            item["next_date"],
            item["category"] or "-",
            item["account_name"] or "-",
            "✓" if item["active"] else "✗",
        )
    console.print(t)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@config_app.callback()
def _config_startup():
    db.init_db()


@config_app.command("set-currency")
def config_set_currency(
    currency: str = typer.Argument(..., help="Your base currency code (e.g. USD, EUR, COP)"),
):
    """Set your local/base currency."""
    db.set_setting("base_currency", currency.upper())
    console.print(f"[green]Base currency set to {currency.upper()}[/green]")


@config_app.command("show")
def config_show():
    """Show current configuration."""
    base = db.get_setting("base_currency", "USD")
    console.print(Panel(f"Base currency: [bold cyan]{base}[/bold cyan]", title="Config"))


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════

@accounts_app.callback()
def _accounts_startup():
    db.init_db()


@accounts_app.command("add")
def accounts_add(
    name:          str           = typer.Option(...,   "--name",     "-n", help="Account name"),
    acct_type:     str           = typer.Option(...,   "--type",     "-t", help=f"One of: {', '.join(ACCOUNT_TYPES)}"),
    currency:      str           = typer.Option("USD", "--currency", "-c", help="Account currency"),
    balance:       float         = typer.Option(0.0,   "--balance",  "-b", help="Current balance (use negative for debt)"),
    credit_limit:  Optional[float] = typer.Option(None, "--limit",       help="Credit limit (credit cards)"),
    interest_rate: Optional[float] = typer.Option(None, "--rate",        help="Annual interest rate %"),
    due_day:       Optional[int]   = typer.Option(None, "--due-day",     help="Payment due day of month"),
    notes:         Optional[str]   = typer.Option(None, "--notes"),
):
    """Add a bank account, credit card, or loan."""
    if acct_type not in ACCOUNT_TYPES:
        console.print(f"[red]Invalid type '{acct_type}'. Choose: {', '.join(ACCOUNT_TYPES)}[/red]")
        raise typer.Exit(1)
    aid = db.add_account(name, acct_type, currency, balance,
                         credit_limit, interest_rate, due_day, notes)
    console.print(f"[green]Account '{name}' added (ID: {aid})[/green]")


@accounts_app.command("list")
def accounts_list():
    """List all accounts."""
    accounts = db.list_accounts()
    if not accounts:
        console.print("[yellow]No accounts yet. Add one: ea accounts add --help[/yellow]")
        return
    t = Table(title="Accounts", box=box.ROUNDED)
    t.add_column("ID",       style="dim", width=4)
    t.add_column("Name",     style="bold")
    t.add_column("Type")
    t.add_column("Currency", justify="center")
    t.add_column("Balance",  justify="right")
    t.add_column("Details",  justify="right")
    t.add_column("Due Day",  justify="center")
    for a in accounts:
        bal_style = "red" if a["balance"] < 0 else "green"
        details = ""
        if a["credit_limit"]:
            used = abs(a["balance"]) / a["credit_limit"] * 100 if a["balance"] < 0 else 0
            details = f"Limit {a['credit_limit']:,.0f} | Used {used:.0f}%"
        elif a["interest_rate"]:
            details = f"Rate {a['interest_rate']}%"
        t.add_row(
            str(a["id"]), a["name"], a["type"], a["currency"],
            f"[{bal_style}]{a['balance']:,.2f}[/{bal_style}]",
            details,
            str(a["due_day"]) if a["due_day"] else "-",
        )
    console.print(t)


@accounts_app.command("update")
def accounts_update(
    account_id:    int            = typer.Argument(...),
    name:          Optional[str]   = typer.Option(None, "--name"),
    balance:       Optional[float] = typer.Option(None, "--balance",  "-b", help="Set new balance"),
    interest_rate: Optional[float] = typer.Option(None, "--rate"),
    due_day:       Optional[int]   = typer.Option(None, "--due-day"),
    notes:         Optional[str]   = typer.Option(None, "--notes"),
):
    """Update account fields."""
    kwargs = {k: v for k, v in dict(
        name=name, balance=balance, interest_rate=interest_rate,
        due_day=due_day, notes=notes,
    ).items() if v is not None}
    if not kwargs:
        console.print("[yellow]Nothing to update.[/yellow]")
        return
    db.update_account(account_id, **kwargs)
    console.print(f"[green]Account {account_id} updated.[/green]")


@accounts_app.command("delete")
def accounts_delete(account_id: int = typer.Argument(...)):
    """Delete an account."""
    acct = db.get_account(account_id)
    if not acct:
        console.print(f"[red]Account {account_id} not found.[/red]")
        raise typer.Exit(1)
    if typer.confirm(f"Delete account '{acct['name']}'?"):
        db.delete_account(account_id)
        console.print("[green]Deleted.[/green]")


# ══════════════════════════════════════════════════════════════════════════════
# INCOME
# ══════════════════════════════════════════════════════════════════════════════

@income_app.callback()
def _income_startup():
    db.init_db()


@income_app.command("add")
def income_add(
    name:         str           = typer.Option(...,      "--name",      "-n"),
    amount:       float         = typer.Option(...,      "--amount",    "-a"),
    currency:     str           = typer.Option("USD",    "--currency",  "-c"),
    frequency:    str           = typer.Option(...,      "--frequency", "-f", help=f"One of: {', '.join(FREQUENCIES)}"),
    next_date:    str           = typer.Option(...,      "--next-date", "-d", help="Next occurrence YYYY-MM-DD"),
    account_id:   Optional[int] = typer.Option(None,     "--account",       help="Account ID to receive funds"),
    category:     str           = typer.Option("salary", "--category"),
    day_of_month: Optional[int] = typer.Option(None,     "--day",           help="Day of month (for monthly frequency)"),
    notes:        Optional[str] = typer.Option(None,     "--notes"),
):
    """Add a recurring income source (paycheck, freelance, etc.)."""
    if frequency not in FREQUENCIES:
        console.print(f"[red]Invalid frequency. Choose: {', '.join(FREQUENCIES)}[/red]")
        raise typer.Exit(1)
    iid = db.add_recurring(name, "income", amount, currency, frequency,
                            next_date, account_id, category, day_of_month, notes)
    console.print(f"[green]Income source '{name}' added (ID: {iid})[/green]")


@income_app.command("list")
def income_list():
    """List all recurring income sources."""
    items = db.list_recurring("income")
    if not items:
        console.print("[yellow]No income sources yet. Add one: ea income add --help[/yellow]")
        return
    _print_recurring_table(items, "Income Sources")


@income_app.command("log")
def income_log(
    description: str           = typer.Option(...,   "--desc",     "-d"),
    amount:      float         = typer.Option(...,   "--amount",   "-a"),
    currency:    str           = typer.Option("USD", "--currency", "-c"),
    account_id:  Optional[int] = typer.Option(None,  "--account"),
    tx_date:     Optional[str] = typer.Option(None,  "--date",     help="YYYY-MM-DD (default: today)"),
    notes:       Optional[str] = typer.Option(None,  "--notes"),
):
    """Log a one-time income transaction."""
    base = db.get_setting("base_currency", "USD")
    amount_base, rate = _convert(amount, currency, base)
    tx_date = tx_date or date.today().isoformat()
    tid = db.add_transaction("income", description, amount, currency,
                              amount_base, rate, "other", account_id, tx_date, notes=notes)
    if account_id:
        db.update_account_balance(account_id, amount_base)
    console.print(f"[green]Income logged (TX #{tid}): {amount:,.2f} {currency} = {amount_base:,.2f} {base}[/green]")


@income_app.command("mark-paid")
def income_mark_paid(
    item_id:  int           = typer.Argument(..., help="Recurring income ID"),
    amount:   Optional[float] = typer.Option(None, "--amount", help="Override amount"),
    tx_date:  Optional[str]   = typer.Option(None, "--date",   help="YYYY-MM-DD (default: today)"),
):
    """Record that a recurring income was received and advance its next date."""
    item = db.get_recurring(item_id)
    if not item or item["item_type"] != "income":
        console.print(f"[red]Income source {item_id} not found.[/red]")
        raise typer.Exit(1)
    base = db.get_setting("base_currency", "USD")
    actual = amount or item["amount"]
    amount_base, rate = _convert(actual, item["currency"], base)
    tx_date = tx_date or date.today().isoformat()
    tid = db.add_transaction(
        "income", item["name"], actual, item["currency"],
        amount_base, rate, item.get("category", "other"),
        item["account_id"], tx_date, item_id,
    )
    if item["account_id"]:
        db.update_account_balance(item["account_id"], amount_base)
    next_d = _advance_date(
        date.fromisoformat(item["next_date"]),
        item["frequency"], item.get("day_of_month"),
    )
    db.update_recurring_next_date(item_id, next_d.isoformat())
    console.print(f"[green]Logged: {actual:,.2f} {item['currency']} → {amount_base:,.2f} {base} (TX #{tid})[/green]")
    console.print(f"[dim]Next occurrence: {next_d.isoformat()}[/dim]")


@income_app.command("deactivate")
def income_deactivate(item_id: int = typer.Argument(...)):
    """Deactivate a recurring income source."""
    db.deactivate_recurring(item_id)
    console.print(f"[green]Income source {item_id} deactivated.[/green]")


# ══════════════════════════════════════════════════════════════════════════════
# EXPENSE
# ══════════════════════════════════════════════════════════════════════════════

@expense_app.callback()
def _expense_startup():
    db.init_db()


@expense_app.command("add")
def expense_add(
    name:         str           = typer.Option(...,   "--name",      "-n"),
    amount:       float         = typer.Option(...,   "--amount",    "-a"),
    currency:     str           = typer.Option("USD", "--currency",  "-c"),
    frequency:    str           = typer.Option(...,   "--frequency", "-f", help=f"One of: {', '.join(FREQUENCIES)}"),
    next_date:    str           = typer.Option(...,   "--next-date", "-d", help="Next due date YYYY-MM-DD"),
    account_id:   Optional[int] = typer.Option(None,  "--account"),
    category:     str           = typer.Option("other", "--category"),
    day_of_month: Optional[int] = typer.Option(None,   "--day", help="Day of month (for monthly)"),
    notes:        Optional[str] = typer.Option(None,   "--notes"),
):
    """Add a recurring expense (rent, loan, subscription, etc.)."""
    if frequency not in FREQUENCIES:
        console.print(f"[red]Invalid frequency. Choose: {', '.join(FREQUENCIES)}[/red]")
        raise typer.Exit(1)
    eid = db.add_recurring(name, "expense", amount, currency, frequency,
                            next_date, account_id, category, day_of_month, notes)
    console.print(f"[green]Expense '{name}' added (ID: {eid})[/green]")


@expense_app.command("list")
def expense_list():
    """List all recurring expenses."""
    items = db.list_recurring("expense")
    if not items:
        console.print("[yellow]No expenses yet. Add one: ea expense add --help[/yellow]")
        return
    _print_recurring_table(items, "Recurring Expenses")


@expense_app.command("log")
def expense_log(
    description: str           = typer.Option(...,     "--desc",     "-d"),
    amount:      float         = typer.Option(...,     "--amount",   "-a"),
    currency:    str           = typer.Option("USD",   "--currency", "-c"),
    account_id:  Optional[int] = typer.Option(None,    "--account"),
    category:    str           = typer.Option("other", "--category"),
    tx_date:     Optional[str] = typer.Option(None,    "--date",     help="YYYY-MM-DD (default: today)"),
    notes:       Optional[str] = typer.Option(None,    "--notes"),
):
    """Log a one-time expense."""
    base = db.get_setting("base_currency", "USD")
    amount_base, rate = _convert(amount, currency, base)
    tx_date = tx_date or date.today().isoformat()
    tid = db.add_transaction("expense", description, amount, currency,
                              amount_base, rate, category, account_id, tx_date, notes=notes)
    if account_id:
        db.update_account_balance(account_id, -amount_base)
    console.print(f"[green]Expense logged (TX #{tid}): {amount:,.2f} {currency} = {amount_base:,.2f} {base}[/green]")


@expense_app.command("mark-paid")
def expense_mark_paid(
    item_id:  int             = typer.Argument(..., help="Recurring expense ID"),
    amount:   Optional[float] = typer.Option(None,  "--amount", help="Override amount"),
    tx_date:  Optional[str]   = typer.Option(None,  "--date",   help="YYYY-MM-DD (default: today)"),
):
    """Record that a recurring expense was paid and advance its next due date."""
    item = db.get_recurring(item_id)
    if not item or item["item_type"] != "expense":
        console.print(f"[red]Expense {item_id} not found.[/red]")
        raise typer.Exit(1)
    base = db.get_setting("base_currency", "USD")
    actual = amount or item["amount"]
    amount_base, rate = _convert(actual, item["currency"], base)
    tx_date = tx_date or date.today().isoformat()
    tid = db.add_transaction(
        "expense", item["name"], actual, item["currency"],
        amount_base, rate, item.get("category", "other"),
        item["account_id"], tx_date, item_id,
    )
    if item["account_id"]:
        db.update_account_balance(item["account_id"], -amount_base)
    next_d = _advance_date(
        date.fromisoformat(item["next_date"]),
        item["frequency"], item.get("day_of_month"),
    )
    db.update_recurring_next_date(item_id, next_d.isoformat())
    console.print(f"[green]Logged: {actual:,.2f} {item['currency']} → {amount_base:,.2f} {base} (TX #{tid})[/green]")
    console.print(f"[dim]Next due: {next_d.isoformat()}[/dim]")


@expense_app.command("deactivate")
def expense_deactivate(item_id: int = typer.Argument(...)):
    """Deactivate a recurring expense."""
    db.deactivate_recurring(item_id)
    console.print(f"[green]Expense {item_id} deactivated.[/green]")


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════

@tx_app.callback()
def _tx_startup():
    db.init_db()


@tx_app.command("list")
def tx_list(
    month:      Optional[str] = typer.Option(None, "--month",   "-m", help="Filter YYYY-MM"),
    tx_type:    Optional[str] = typer.Option(None, "--type",    "-t", help="income | expense"),
    account_id: Optional[int] = typer.Option(None, "--account", "-a"),
    limit:      int           = typer.Option(50,   "--limit",   "-l"),
):
    """List transactions."""
    base = db.get_setting("base_currency", "USD")
    txs = db.list_transactions(tx_type, account_id, month, limit)
    if not txs:
        console.print("[yellow]No transactions found.[/yellow]")
        return
    t = Table(title="Transactions", box=box.ROUNDED)
    t.add_column("ID",          style="dim", width=4)
    t.add_column("Date")
    t.add_column("Type")
    t.add_column("Description")
    t.add_column("Amount",      justify="right")
    t.add_column(base,          justify="right")
    t.add_column("Category")
    t.add_column("Account")
    for tx in txs:
        s    = "green" if tx["tx_type"] == "income" else "red"
        sign = "+" if tx["tx_type"] == "income" else "-"
        t.add_row(
            str(tx["id"]),
            tx["date"],
            f"[{s}]{tx['tx_type']}[/{s}]",
            tx["description"],
            f"[{s}]{sign}{tx['amount']:,.2f} {tx['currency']}[/{s}]",
            f"[{s}]{sign}{tx['amount_base']:,.2f}[/{s}]",
            tx["category"] or "-",
            tx["account_name"] or "-",
        )
    console.print(t)


@tx_app.command("delete")
def tx_delete(tx_id: int = typer.Argument(...)):
    """Delete a transaction (does not reverse the account balance)."""
    if typer.confirm(f"Delete transaction {tx_id}?"):
        db.delete_transaction(tx_id)
        console.print("[green]Deleted.[/green]")


# ══════════════════════════════════════════════════════════════════════════════
# CASH FLOW
# ══════════════════════════════════════════════════════════════════════════════

@app.command("cashflow")
def cashflow(
    days:       int           = typer.Option(60,  "--days",    "-d", help="Projection window in days"),
    account_id: Optional[int] = typer.Option(None, "--account",      help="Limit to one account"),
):
    """Show projected cash flow for the coming days based on recurring items."""
    base = db.get_setting("base_currency", "USD")
    accounts = db.list_accounts()

    if account_id:
        liquid = [a for a in accounts if a["id"] == account_id]
    else:
        liquid = [a for a in accounts if a["type"] in ("bank", "cash")]

    starting = sum(a["balance"] for a in liquid)
    today    = date.today()
    end_date = today + timedelta(days=days)

    # Collect all projected events
    events: list[tuple[date, str, str, float, str]] = []
    for item in db.list_recurring():
        for occ in _get_occurrences(item, today, end_date):
            amount_base, _ = _convert(item["amount"], item["currency"], base)
            events.append((occ, item["name"], item["item_type"], amount_base, item["currency"]))
    events.sort(key=lambda x: x[0])

    t = Table(title=f"Cash Flow — Next {days} Days", box=box.ROUNDED)
    t.add_column("Date",                  style="dim")
    t.add_column("Description")
    t.add_column("Type")
    t.add_column("Amount",                justify="right")
    t.add_column(f"Balance ({base})",     justify="right")

    balance = starting
    t.add_row(today.isoformat(), "[bold]Starting balance[/bold]", "", "",
              f"[bold]{balance:,.2f}[/bold]")

    for evt_date, name, evt_type, amount_base, currency in events:
        if evt_type == "income":
            balance += amount_base
            s, sign = "green", "+"
        else:
            balance -= amount_base
            s, sign = "red", "-"
        bal_style = "green" if balance >= 0 else "bold red"
        t.add_row(
            evt_date.isoformat(), name,
            f"[{s}]{evt_type}[/{s}]",
            f"[{s}]{sign}{amount_base:,.2f}[/{s}]",
            f"[{bal_style}]{balance:,.2f}[/{bal_style}]",
        )

    console.print(t)
    bal_style = "green" if balance >= 0 else "bold red"
    console.print(
        f"\nProjected balance on {end_date}: [{bal_style}]{balance:,.2f} {base}[/{bal_style}]"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

@app.command("summary")
def summary():
    """Overall financial snapshot: accounts, net worth, and upcoming 30-day flow."""
    base     = db.get_setting("base_currency", "USD")
    accounts = db.list_accounts()

    if not accounts:
        console.print("[yellow]No accounts yet. Start with: ea accounts add --help[/yellow]")
        return

    # Net worth
    assets      = sum(a["balance"] for a in accounts if a["balance"] > 0)
    liabilities = sum(a["balance"] for a in accounts if a["balance"] < 0)
    net_worth   = assets + liabilities

    # Accounts table
    t = Table(title="Accounts", box=box.ROUNDED)
    t.add_column("Name",     style="bold")
    t.add_column("Type")
    t.add_column("Currency", justify="center")
    t.add_column("Balance",  justify="right")
    t.add_column("Details")
    for a in accounts:
        bs = "red" if a["balance"] < 0 else "green"
        details = ""
        if a["credit_limit"]:
            used = abs(a["balance"]) / a["credit_limit"] * 100 if a["balance"] < 0 else 0
            details = f"Limit {a['credit_limit']:,.0f} | Used {used:.0f}%"
        elif a["interest_rate"]:
            details = f"Rate {a['interest_rate']}%"
        if a["due_day"]:
            details += f"  Due day {a['due_day']}"
        t.add_row(a["name"], a["type"], a["currency"],
                  f"[{bs}]{a['balance']:,.2f}[/{bs}]", details)
    console.print(t)

    nw_s = "green" if net_worth >= 0 else "bold red"
    console.print(f"\n  Assets:      [green]{assets:,.2f} {base}[/green]")
    console.print(f"  Liabilities: [red]{liabilities:,.2f} {base}[/red]")
    console.print(f"  Net Worth:   [{nw_s}]{net_worth:,.2f} {base}[/{nw_s}]\n")

    # 30-day upcoming
    today   = date.today()
    end_30  = today + timedelta(days=30)
    upcoming_income:   list[tuple] = []
    upcoming_expenses: list[tuple] = []

    for item in db.list_recurring():
        occs = _get_occurrences(item, today, end_30)
        if not occs:
            continue
        amount_base, _ = _convert(item["amount"], item["currency"], base)
        total = amount_base * len(occs)
        entry = (item["name"], item["currency"], item["amount"], total, len(occs), occs[0])
        if item["item_type"] == "income":
            upcoming_income.append(entry)
        else:
            upcoming_expenses.append(entry)

    def _upcoming_table(rows, title, style):
        ut = Table(title=title, box=box.SIMPLE)
        ut.add_column("Name")
        ut.add_column("Per occurrence", justify="right")
        ut.add_column("#", justify="center")
        ut.add_column(f"Total ({base})", justify="right")
        ut.add_column("Next")
        for name, ccy, amt, total, cnt, nxt in rows:
            ut.add_row(name, f"{amt:,.2f} {ccy}", str(cnt),
                       f"[{style}]{total:,.2f}[/{style}]", nxt.isoformat())
        console.print(ut)

    if upcoming_income:
        _upcoming_table(upcoming_income, "Upcoming Income (30 days)", "green")
    if upcoming_expenses:
        _upcoming_table(upcoming_expenses, "Upcoming Expenses (30 days)", "red")

    total_in  = sum(e[3] for e in upcoming_income)
    total_out = sum(e[3] for e in upcoming_expenses)
    net30     = total_in - total_out
    ns = "green" if net30 >= 0 else "bold red"
    console.print(f"\n  30-day net: [{ns}]{net30:,.2f} {base}[/{ns}]  "
                  f"([green]+{total_in:,.2f}[/green] / [red]-{total_out:,.2f}[/red])\n")


# ══════════════════════════════════════════════════════════════════════════════
# MONTHLY REPORT
# ══════════════════════════════════════════════════════════════════════════════

@app.command("report")
def report(
    month: Optional[str] = typer.Option(
        None, "--month", "-m", help="YYYY-MM (default: current month)"
    ),
):
    """Monthly income vs. expense report with category breakdown."""
    base  = db.get_setting("base_currency", "USD")
    month = month or date.today().strftime("%Y-%m")
    txs   = db.list_transactions(month=month, limit=2000)

    income_txs  = [t for t in txs if t["tx_type"] == "income"]
    expense_txs = [t for t in txs if t["tx_type"] == "expense"]

    total_in  = sum(t["amount_base"] for t in income_txs)
    total_out = sum(t["amount_base"] for t in expense_txs)
    net       = total_in - total_out

    console.print(Panel(f"[bold]Monthly Report: {month}[/bold]", style="blue"))

    if expense_txs:
        by_cat: dict[str, float] = defaultdict(float)
        for t in expense_txs:
            by_cat[t["category"] or "other"] += t["amount_base"]

        cat_t = Table(title="Expenses by Category", box=box.SIMPLE)
        cat_t.add_column("Category")
        cat_t.add_column(f"Amount ({base})", justify="right")
        cat_t.add_column("%", justify="right")
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            pct = amt / total_out * 100 if total_out else 0
            cat_t.add_row(cat, f"[red]{amt:,.2f}[/red]", f"{pct:.1f}%")
        console.print(cat_t)

    ns = "green" if net >= 0 else "bold red"
    console.print(f"\n  Income:   [green]{total_in:,.2f} {base}[/green]")
    console.print(f"  Expenses: [red]{total_out:,.2f} {base}[/red]")
    console.print(f"  Net:      [{ns}]{net:,.2f} {base}[/{ns}]\n")


# ══════════════════════════════════════════════════════════════════════════════
# FX RATES
# ══════════════════════════════════════════════════════════════════════════════

@app.command("fx")
def fx_rates(
    base:    Optional[str] = typer.Option(None, "--base",    "-b", help="Base currency (default: your base)"),
    targets: Optional[str] = typer.Option(None, "--targets", "-t", help="Comma-separated targets, e.g. EUR,GBP,COP"),
):
    """Show live exchange rates."""
    if not base:
        base = db.get_setting("base_currency", "USD")
    base = base.upper()
    try:
        rates = fx.get_rates(base)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if targets:
        show = [c.strip().upper() for c in targets.split(",")]
    else:
        show = ["EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "MXN", "BRL", "COP", "CLP", "ARS"]

    t = Table(title=f"Exchange Rates (base: {base})", box=box.ROUNDED)
    t.add_column("Currency")
    t.add_column("Rate", justify="right")
    t.add_column(f"1 {base} =", justify="right")
    for ccy in show:
        if ccy in rates:
            t.add_row(ccy, f"{rates[ccy]:.4f}", f"{rates[ccy]:,.4f} {ccy}")
    console.print(t)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    app()
