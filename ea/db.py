"""SQLite database layer — stored at ~/.ea/finance.db"""

import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".ea"
DB_PATH = DB_DIR / "finance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    type          TEXT    NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'USD',
    balance       REAL    NOT NULL DEFAULT 0.0,
    credit_limit  REAL,
    interest_rate REAL,
    due_day       INTEGER,
    notes         TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    item_type     TEXT    NOT NULL,
    amount        REAL    NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'USD',
    frequency     TEXT    NOT NULL,
    day_of_month  INTEGER,
    next_date     TEXT    NOT NULL,
    account_id    INTEGER REFERENCES accounts(id),
    category      TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    notes         TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_type       TEXT    NOT NULL,
    description   TEXT    NOT NULL,
    amount        REAL    NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'USD',
    amount_base   REAL    NOT NULL,
    fx_rate       REAL    NOT NULL DEFAULT 1.0,
    category      TEXT,
    account_id    INTEGER REFERENCES accounts(id),
    date          TEXT    NOT NULL,
    recurring_id  INTEGER REFERENCES recurring_items(id),
    notes         TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('base_currency', 'USD')"
    )
    conn.commit()
    conn.close()


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = None) -> str | None:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── Accounts ──────────────────────────────────────────────────────────────────

def add_account(
    name: str, acct_type: str, currency: str, balance: float,
    credit_limit: float = None, interest_rate: float = None,
    due_day: int = None, notes: str = None,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO accounts
               (name, type, currency, balance, credit_limit, interest_rate, due_day, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (name, acct_type, currency.upper(), balance, credit_limit, interest_rate, due_day, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_accounts() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY type, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_account(account_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_account(account_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [account_id]
    conn = get_db()
    conn.execute(f"UPDATE accounts SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def update_account_balance(account_id: int, delta: float):
    conn = get_db()
    conn.execute("UPDATE accounts SET balance = balance + ? WHERE id=?", (delta, account_id))
    conn.commit()
    conn.close()


def delete_account(account_id: int):
    conn = get_db()
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()


# ── Recurring Items ───────────────────────────────────────────────────────────

def add_recurring(
    name: str, item_type: str, amount: float, currency: str,
    frequency: str, next_date: str, account_id: int = None,
    category: str = None, day_of_month: int = None, notes: str = None,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO recurring_items
               (name, item_type, amount, currency, frequency,
                day_of_month, next_date, account_id, category, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, item_type, amount, currency.upper(), frequency,
         day_of_month, next_date, account_id, category, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_recurring(item_type: str = None, active_only: bool = True) -> list[dict]:
    conn = get_db()
    query = (
        "SELECT r.*, a.name AS account_name"
        " FROM recurring_items r"
        " LEFT JOIN accounts a ON r.account_id = a.id"
    )
    conditions, params = [], []
    if active_only:
        conditions.append("r.active=1")
    if item_type:
        conditions.append("r.item_type=?")
        params.append(item_type)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY r.next_date"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recurring(recurring_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM recurring_items WHERE id=?", (recurring_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_recurring_next_date(recurring_id: int, next_date: str):
    conn = get_db()
    conn.execute("UPDATE recurring_items SET next_date=? WHERE id=?", (next_date, recurring_id))
    conn.commit()
    conn.close()


def deactivate_recurring(recurring_id: int):
    conn = get_db()
    conn.execute("UPDATE recurring_items SET active=0 WHERE id=?", (recurring_id,))
    conn.commit()
    conn.close()


# ── Transactions ──────────────────────────────────────────────────────────────

def add_transaction(
    tx_type: str, description: str, amount: float, currency: str,
    amount_base: float, fx_rate: float = 1.0, category: str = None,
    account_id: int = None, date: str = None, recurring_id: int = None,
    notes: str = None,
) -> int:
    from datetime import date as _date
    if date is None:
        date = _date.today().isoformat()
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO transactions
               (tx_type, description, amount, currency, amount_base,
                fx_rate, category, account_id, date, recurring_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (tx_type, description, amount, currency.upper(), amount_base,
         fx_rate, category, account_id, date, recurring_id, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_transactions(
    tx_type: str = None, account_id: int = None,
    month: str = None, limit: int = 50,
) -> list[dict]:
    conn = get_db()
    query = (
        "SELECT t.*, a.name AS account_name"
        " FROM transactions t"
        " LEFT JOIN accounts a ON t.account_id = a.id"
    )
    conditions, params = [], []
    if tx_type:
        conditions.append("t.tx_type=?")
        params.append(tx_type)
    if account_id:
        conditions.append("t.account_id=?")
        params.append(account_id)
    if month:
        conditions.append("strftime('%Y-%m', t.date)=?")
        params.append(month)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_transaction(tx_id: int):
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    conn.commit()
    conn.close()
