import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,                  -- invoice date, YYYY-MM-DD
    payment_date TEXT NOT NULL DEFAULT '',      -- day the money was received, if known
    income_date TEXT NOT NULL,                  -- payment_date if known, else date: sets the year and the rate
    client      TEXT NOT NULL,
    number      TEXT NOT NULL DEFAULT '',
    amount      TEXT NOT NULL,                  -- original amount, as a decimal string
    currency    TEXT NOT NULL,
    rate        TEXT NOT NULL,                  -- RON per 1 unit of currency
    rate_date   TEXT NOT NULL,                  -- day of the rate actually used
    amount_ron  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    attachment  TEXT NOT NULL DEFAULT '',      -- stored file name under data/attachments/invoices
    created_at  TEXT NOT NULL,
    UNIQUE (date, client, number, amount, currency)
);
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    amount      TEXT NOT NULL,
    currency    TEXT NOT NULL,
    rate        TEXT NOT NULL,
    rate_date   TEXT NOT NULL,
    amount_ron  TEXT NOT NULL,
    attachment  TEXT NOT NULL DEFAULT '',      -- stored file name under data/attachments/expenses
    created_at  TEXT NOT NULL
);
-- Invoicing (see invoicing.py): the profile's own lists, and the invoices generated from them
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never given out again
    name        TEXT NOT NULL,
    tax_id      TEXT NOT NULL,                  -- CUI / CNP for Romanian clients, VAT or tax number for others
    reg_number  TEXT NOT NULL DEFAULT '',       -- trade register number, if any
    address     TEXT NOT NULL,
    city        TEXT NOT NULL,
    country     TEXT NOT NULL,                  -- two-letter code (RO, DE, ...)
    email       TEXT NOT NULL,
    phone       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never given out again
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL,                  -- one of invoicing.UNITS
    unit_price  TEXT NOT NULL,                  -- price of one unit, as a decimal string
    currency    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice_series (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never given out again
    name        TEXT NOT NULL UNIQUE,           -- e.g. PFA; invoice numbers are PFA-0001, PFA-0002, ...
    next_number INTEGER NOT NULL DEFAULT 1,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issued_invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never given out again
    income_id   INTEGER NOT NULL,               -- the row it added to invoices (Income)
    series      TEXT NOT NULL,
    number      INTEGER NOT NULL,
    full_number TEXT NOT NULL UNIQUE,
    date        TEXT NOT NULL,
    client      TEXT NOT NULL,
    currency    TEXT NOT NULL,
    total       TEXT NOT NULL,
    data        TEXT NOT NULL,                  -- JSON: everything printed on the invoice, as it was issued
    token       TEXT NOT NULL DEFAULT '',       -- the form it was issued from: a form sent twice issues once
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'invoice', -- 'invoice' or 'credit_note'
    credits_id  INTEGER NOT NULL DEFAULT 0      -- a credit note: the id of the issued invoice it reverses
);
CREATE TABLE IF NOT EXISTS invoicing_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""

# columns added after the first release; older databases get them on start
ADDED_COLUMNS = {
    "invoices": [("payment_date", "TEXT NOT NULL DEFAULT ''"), ("income_date", "TEXT NOT NULL DEFAULT ''"),
                 ("attachment", "TEXT NOT NULL DEFAULT ''")],
    "expenses": [("attachment", "TEXT NOT NULL DEFAULT ''")],
    "issued_invoices": [("token", "TEXT NOT NULL DEFAULT ''"), ("kind", "TEXT NOT NULL DEFAULT 'invoice'"),
                        ("credits_id", "INTEGER NOT NULL DEFAULT 0")],
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():
            existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        con.execute("UPDATE invoices SET income_date = date WHERE income_date = ''")
        _canonical_amounts(con)


def _canonical_amounts(con: sqlite3.Connection) -> None:
    """Amounts are stored in one form (100, not 100.00 or 100.0) so duplicates are recognised;
    rows written before that rule are brought in line (a row that would then clash is left alone)."""
    from .parsing import canonical
    for table in ("invoices", "expenses"):
        for row_id, amount in con.execute(f"SELECT id, amount FROM {table}").fetchall():
            try:
                fixed = canonical(Decimal(amount))
            except Exception:
                continue
            if fixed != amount:
                try:
                    con.execute(f"UPDATE {table} SET amount=? WHERE id=?", (fixed, row_id))
                except sqlite3.IntegrityError:
                    pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_year(table: str, year: str) -> list[sqlite3.Row]:
    assert table in ("invoices", "expenses")
    with connect() as con:
        col = "income_date" if table == "invoices" else "date"
        return con.execute(f"SELECT * FROM {table} WHERE substr({col},1,4)=? ORDER BY {col}, id", (year,)).fetchall()


def totals_by_year(table: str) -> tuple[dict[str, Decimal], dict[str, int]]:
    assert table in ("invoices", "expenses")
    sums, counts = defaultdict(Decimal), defaultdict(int)
    col = "income_date" if table == "invoices" else "date"
    with connect() as con:
        for r in con.execute(f"SELECT substr({col},1,4) y, amount_ron FROM {table}"):
            sums[r["y"]] += Decimal(r["amount_ron"])
            counts[r["y"]] += 1
    return sums, counts


def insert_invoice(con: sqlite3.Connection, row: dict) -> int | None:
    """Returns the new id, or None when the same invoice is already stored."""
    cur = con.execute(
        """INSERT OR IGNORE INTO invoices
           (date, payment_date, income_date, client, number, amount, currency, rate, rate_date, amount_ron,
            source, created_at)
           VALUES (:date, :payment_date, :income_date, :client, :number, :amount, :currency, :rate, :rate_date,
                   :amount_ron, :source, :created_at)""",
        {"payment_date": "", **row, "created_at": now()},
    )
    return cur.lastrowid if cur.rowcount > 0 else None


def store_invoice(con: sqlite3.Connection, row: dict) -> tuple[str, int]:
    """Stores an invoice. Returns ("added", id), ("paid", id) when an identical invoice stored without
    a date of payment now gets one (its year and rate follow the payment), or ("duplicate", id)."""
    new_id = insert_invoice(con, row)
    if new_id is not None:
        return "added", new_id
    existing = con.execute(
        "SELECT id, payment_date FROM invoices WHERE date=? AND client=? AND number=? AND amount=? AND currency=?",
        (row["date"], row["client"], row["number"], row["amount"], row["currency"])).fetchone()
    if not existing["payment_date"] and row.get("payment_date"):
        con.execute("UPDATE invoices SET payment_date=?, income_date=?, rate=?, rate_date=?, amount_ron=? WHERE id=?",
                    (row["payment_date"], row["income_date"], row["rate"], row["rate_date"], row["amount_ron"],
                     existing["id"]))
        return "paid", existing["id"]
    return "duplicate", existing["id"]


def insert_expense(row: dict) -> int:
    with connect() as con:
        cur = con.execute(
            """INSERT INTO expenses
               (date, description, category, amount, currency, rate, rate_date, amount_ron, created_at)
               VALUES (:date, :description, :category, :amount, :currency, :rate, :rate_date, :amount_ron, :created_at)""",
            {**row, "created_at": now()},
        )
        return cur.lastrowid


def get(table: str, row_id: int) -> sqlite3.Row | None:
    assert table in ("invoices", "expenses")
    with connect() as con:
        return con.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()


def set_attachment(table: str, row_id: int, stored_name: str) -> None:
    assert table in ("invoices", "expenses")
    with connect() as con:
        con.execute(f"UPDATE {table} SET attachment=? WHERE id=?", (stored_name, row_id))


def update_invoice(row_id: int, row: dict) -> bool:
    """Replaces an invoice's values; False when that would make it identical to another stored invoice."""
    try:
        with connect() as con:
            con.execute(
                """UPDATE invoices SET date=:date, payment_date=:payment_date, income_date=:income_date, client=:client,
                   number=:number, amount=:amount, currency=:currency, rate=:rate, rate_date=:rate_date,
                   amount_ron=:amount_ron WHERE id=:id""", {"payment_date": "", **row, "id": row_id})
    except sqlite3.IntegrityError:
        return False
    return True


def update_expense(row_id: int, row: dict) -> None:
    with connect() as con:
        con.execute(
            """UPDATE expenses SET date=:date, description=:description, category=:category, amount=:amount,
               currency=:currency, rate=:rate, rate_date=:rate_date, amount_ron=:amount_ron WHERE id=:id""",
            {**row, "id": row_id})


def delete(table: str, row_id: int) -> sqlite3.Row | None:
    """Deletes a row and returns it (the caller removes its attachment and picks the year)."""
    assert table in ("invoices", "expenses")
    with connect() as con:
        row = con.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        con.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
    return row
