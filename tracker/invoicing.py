"""Invoicing: the profile's lists of clients, products/services and invoice series, and issuing an
invoice from them.

An invoice gets the next number of its series (PFA-0001, PFA-0002, ...), is printed as a PDF, and is
added to Income in the same step, with the PDF as its proof. Each list lives in the profile's own
database, so every firm has its own clients, products and numbering.

Every check raises ValueError (or returns a list of problems) with a sentence the person can act on.
"""
import json
import re
import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import attachments, db, settings
from .config import CENT
from .invoices import build_invoice
from .parsing import canonical, parse_amount, parse_date
from .rates import convert, load_rates, rates_info

# Units a product or service is sold in: stored key -> label printed on the invoice (Romanian / English)
UNITS = {
    "hour": "oră / hour", "day": "zi / day", "week": "săpt. / week", "month": "lună / month",
    "piece": "buc. / piece", "service": "serviciu / service", "project": "proiect / project",
    "page": "pagină / page", "word": "cuvânt / word", "km": "km", "license": "licență / license",
    "subscription": "abonament / subscription",
}

# Countries a client can be in (Romania first, then the EU, then others)
COUNTRIES = {
    "RO": "Romania", "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France", "DE": "Germany",
    "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LT": "Lithuania",
    "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
    "AE": "United Arab Emirates", "AU": "Australia", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland",
    "CN": "China", "GB": "United Kingdom", "IL": "Israel", "IN": "India", "IS": "Iceland", "JP": "Japan",
    "KR": "South Korea", "MD": "Moldova", "MX": "Mexico", "NO": "Norway", "NZ": "New Zealand", "RS": "Serbia",
    "SG": "Singapore", "TR": "Turkey", "UA": "Ukraine", "US": "United States", "ZA": "South Africa",
}
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
      "LU", "MT", "NL", "PL", "PT", "SK", "SI", "ES", "SE", "RO"}
VAT_PREFIX = {"GR": "EL"}     # Greek VAT numbers start with EL

# printed under the supplier when the firm is not registered for VAT
NOT_VAT_PAYER = ("Neplătitor de TVA — regim special de scutire pentru întreprinderile mici, art. 310 Cod fiscal / "
                 "Not registered for VAT (small business exemption)")
MAX_LINES = 50
MAX_NUMBER = 9_999_999
DEFAULT_SETTINGS = {"vat_payer": "0", "vat_rate": "21", "due_days": "15"}
SOURCE = "invoicing"          # the source of an Income row added by issuing an invoice or a credit note
INVOICE, CREDIT_NOTE = "invoice", "credit_note"     # the kinds of issued documents (issued_invoices.kind)
KIND_NAMES = {INVOICE: "invoice", CREDIT_NOTE: "credit note"}


class InvoicingError(ValueError):
    pass


class AlreadyIssued(InvoicingError):
    """The same form was sent again (a double click, a reload): it issued its invoice or credit note already."""

    def __init__(self, issued_id: int, full_number: str, kind: str = INVOICE):
        super().__init__(f"this form was already used to issue {KIND_NAMES.get(kind, 'invoice')} {full_number}")
        self.issued_id, self.full_number, self.kind = issued_id, full_number, kind


def new_token() -> str:
    """A fresh token for a New invoice form; issuing twice with the same token issues once."""
    import secrets
    return secrets.token_urlsafe(16)


# Characters the invoice font can print; anything else (emoji, for example) would come out as a box.
_PRINTABLE: set[int] | None = None


def unprintable(text: str) -> list[str]:
    """The characters of text that the invoice PDF cannot print."""
    global _PRINTABLE
    if _PRINTABLE is None:
        try:
            from reportlab.pdfbase.ttfonts import TTFontFile
            from .config import bundle_dir
            _PRINTABLE = set(TTFontFile(str(bundle_dir() / "fonts" / "DejaVuSans.ttf")).charToGlyph)
        except Exception:
            _PRINTABLE = set(range(32, 127))
    return sorted({c for c in text if ord(c) not in _PRINTABLE and c not in "\r\n\t"})


def printable_problem(text: str, what: str) -> list[str]:
    bad = unprintable(text)
    return [f"these characters in {what} cannot be printed on the invoice: {' '.join(bad[:5])}"] if bad else []


def _text(fields: dict, key: str) -> str:
    value = fields.get(key, "")
    return "" if value is None else str(value)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _readable(text: str) -> bool:
    return not any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F for c in text)


# ---------------------------------------------------------------- identification numbers

def cnp_valid(cnp: str) -> bool:
    """A Romanian personal numeric code: 13 digits, the last one a check digit (key 279146358279)."""
    if not (len(cnp) == 13 and cnp.isdigit() and cnp[0] != "0"):
        return False
    c = sum(int(d) * int(k) for d, k in zip(cnp[:12], "279146358279")) % 11
    return (1 if c == 10 else c) == int(cnp[12])


def clean_tax_id(text: str, country: str) -> str:
    """The client's tax identification number, cleaned; raises ValueError when it cannot be right.

    Romania: a CUI/CIF (with or without the RO prefix of VAT payers) whose check digit matches, or the
    CNP of a person. Other countries: a VAT or tax number of 4-20 letters and digits; an EU VAT number
    with a country prefix must carry the client's own country."""
    t = re.sub(r"[\s.\-/]", "", text or "").upper()
    if not t:
        raise ValueError("the tax identification number (CUI) is missing")
    if country == "RO":
        digits = t.removeprefix("RO")
        if len(digits) == 13 and digits.isdigit():
            if t.startswith("RO") or not cnp_valid(digits):
                raise ValueError(f"{t} is not a valid CNP (its check digit does not match)")
            return digits
        if not settings.cui_valid(t):
            raise ValueError(f"CUI {t} is not valid (2 to 10 digits, optionally after RO, and the check digit must match)")
        return t
    if not re.fullmatch(r"[A-Z0-9]{4,20}", t) or sum(c.isdigit() for c in t) < 2:
        raise ValueError(f"the tax number {t} is not valid (4 to 20 letters and digits)")
    prefix = t[:2]
    if country in VAT_PREFIX and prefix == country:
        raise ValueError(f"a VAT number from {COUNTRIES[country]} starts with {VAT_PREFIX[country]}, not {country}")
    eu_prefixes = {VAT_PREFIX.get(c, c) for c in EU}
    if prefix.isalpha() and prefix in eu_prefixes and prefix != VAT_PREFIX.get(country, country) and country in EU:
        raise ValueError(f"the VAT number {t} belongs to another country than {COUNTRIES.get(country, country)}")
    return t


def tax_key(tax_id: str, country: str) -> str:
    """What makes two clients the same firm: RO14399840 and 14399840 are one Romanian firm."""
    return f"{country}:{tax_id.removeprefix('RO') if country == 'RO' else tax_id}"


# ---------------------------------------------------------------- clients

CLIENT_FIELDS = ("name", "tax_id", "reg_number", "address", "city", "country", "email", "phone")


def check_client(fields: dict, client_id: int | None = None) -> tuple[dict, list[str]]:
    """Returns (cleaned values, problems). Required: name, tax id, address, city, country, email."""
    v = {k: _text(fields, k).strip() for k in CLIENT_FIELDS}
    v["name"] = _one_line(v["name"])
    v["country"] = v["country"].upper() or "RO"
    problems = []
    if not v["name"]:
        problems.append("the client's name is missing")
    elif not 2 <= len(v["name"]) <= 200 or not _readable(v["name"]) or not re.search(r"[^\W\d_]", v["name"]):
        problems.append("the client's name should be 2 to 200 characters, with letters")
    if v["country"] not in COUNTRIES:
        problems.append(f"choose the client's country from the list (not '{v['country'][:20]}')")
    else:
        try:
            v["tax_id"] = clean_tax_id(v["tax_id"], v["country"])
        except ValueError as e:
            problems.append(str(e))
        else:
            mine = tax_key(v["tax_id"], v["country"])
            for other in list_clients():
                if other["id"] != client_id and tax_key(other["tax_id"], other["country"]) == mine:
                    problems.append(f"the client {other['name']} already has the tax number {other['tax_id']}")
    reg = re.sub(r"\s+", "", v["reg_number"]).upper()
    if reg and not (len(reg) <= 40 and re.fullmatch(r"[A-Z0-9/.\-]+", reg) and any(c.isdigit() for c in reg)):
        problems.append("the trade register number may hold only letters, digits, / . and - (e.g. J40/1234/2020)")
    v["reg_number"] = reg
    if not v["address"]:
        problems.append("the client's address is missing")
    else:
        try:
            v["address"] = settings.clean_address(v["address"])
        except ValueError as e:
            problems.append(str(e).replace("the address", "the client's address"))
    v["city"] = _one_line(v["city"])
    if not v["city"]:
        problems.append("the client's city is missing")
    elif not (len(v["city"]) <= 100 and _readable(v["city"]) and re.search(r"[^\W\d_]", v["city"])):
        problems.append("the client's city is not readable")
    if not v["email"]:
        problems.append("the client's email address is missing")
    else:
        try:
            v["email"] = settings.clean_email(v["email"])
        except ValueError as e:
            problems.append(str(e).replace("the email", "the client's email"))
    if v["phone"]:
        try:
            v["phone"] = settings.clean_phone(v["phone"])
        except ValueError as e:
            problems.append(str(e))
    for key, what in (("name", "the client's name"), ("address", "the client's address"), ("city", "the client's city")):
        problems += printable_problem(v[key], what)
    return v, problems


def list_clients() -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM clients ORDER BY name COLLATE NOCASE, id")]


def get_client(client_id) -> dict | None:
    if not _valid_id(client_id):
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return dict(row) if row else None


def save_client(fields: dict, client_id: int | None = None) -> tuple[int, dict]:
    """Adds (client_id None) or changes a client; raises InvoicingError listing every problem."""
    if client_id is not None and get_client(client_id) is None:
        raise InvoicingError("the client no longer exists")
    v, problems = check_client(fields, client_id)
    if problems:
        raise InvoicingError("; ".join(problems))
    with db.connect() as con:
        if client_id is None:
            cur = con.execute(f"INSERT INTO clients ({', '.join(CLIENT_FIELDS)}, created_at) VALUES "
                              f"({', '.join(':' + k for k in CLIENT_FIELDS)}, :created_at)", {**v, "created_at": db.now()})
            client_id = cur.lastrowid
        else:
            con.execute(f"UPDATE clients SET {', '.join(f'{k}=:{k}' for k in CLIENT_FIELDS)} WHERE id=:id",
                        {**v, "id": client_id})
    return client_id, v


def delete_client(client_id) -> dict | None:
    """Removes a client from the list; invoices already issued keep its details as they were."""
    row = get_client(client_id)
    if row:
        with db.connect() as con:
            con.execute("DELETE FROM clients WHERE id=?", (client_id,))
    return row


# ---------------------------------------------------------------- products and services

PRODUCT_FIELDS = ("name", "description", "unit", "unit_price", "currency")


def currencies() -> list[str]:
    return ["RON"] + [c["code"] for c in rates_info()["currencies"] if c["code"] != "RON"]


MAX_PRICE = Decimal(10) ** 9           # 1.000.000.000 per unit
MAX_QUANTITY = Decimal(10) ** 6
MAX_TOTAL = Decimal(10) ** 12


def _price(text: str, what: str) -> Decimal:
    if not (text or "").strip():
        raise ValueError(f"{what} is missing")
    if not re.fullmatch(r"-?[0-9][0-9 .,'\u00a0]*", text.strip()):
        raise ValueError(f"{what} should be a number only, without a currency (e.g. 1.234,56)")
    try:
        value = parse_amount(text)
    except ValueError as e:
        raise ValueError(f"{what}: {e}")
    if value >= MAX_PRICE:
        raise ValueError(f"{what} should be less than 1.000.000.000")
    if value <= 0:
        raise ValueError(f"{what} must be more than 0")
    if value != value.quantize(Decimal("0.0001")):
        raise ValueError(f"{what} can have at most 4 decimals")
    return value


def check_product(fields: dict, product_id: int | None = None) -> tuple[dict, list[str]]:
    v = {k: _text(fields, k).strip() for k in PRODUCT_FIELDS}
    v["name"] = _one_line(v["name"])
    v["currency"] = v["currency"].upper()
    problems = []
    if not v["name"]:
        problems.append("the name is missing")
    elif len(v["name"]) > 200 or not _readable(v["name"]):
        problems.append("the name should be at most 200 characters, on one line")
    else:
        for other in list_products():
            if other["id"] != product_id and other["name"].casefold() == v["name"].casefold():
                problems.append(f"there is already a product or service named {other['name']}")
    if len(v["description"]) > 500 or any(ord(c) < 32 and c not in "\r\n\t" for c in v["description"]):
        problems.append("the description should be at most 500 characters")
    if v["unit"] not in UNITS:
        problems.append(f"choose the unit from the list ({', '.join(UNITS)})")
    try:
        v["unit_price"] = canonical(_price(v["unit_price"], "the price per unit"))
    except ValueError as e:
        problems.append(str(e))
    if v["currency"] not in currencies():
        problems.append(f"choose the currency from the list ({', '.join(currencies())})")
    problems += printable_problem(v["name"], "the name") + printable_problem(v["description"], "the description")
    return v, problems


def list_products() -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE, id")]


def get_product(product_id) -> dict | None:
    if not _valid_id(product_id):
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    return dict(row) if row else None


def save_product(fields: dict, product_id: int | None = None) -> tuple[int, dict]:
    if product_id is not None and get_product(product_id) is None:
        raise InvoicingError("the product or service no longer exists")
    v, problems = check_product(fields, product_id)
    if problems:
        raise InvoicingError("; ".join(problems))
    with db.connect() as con:
        if product_id is None:
            cur = con.execute(f"INSERT INTO products ({', '.join(PRODUCT_FIELDS)}, created_at) VALUES "
                              f"({', '.join(':' + k for k in PRODUCT_FIELDS)}, :created_at)", {**v, "created_at": db.now()})
            product_id = cur.lastrowid
        else:
            con.execute(f"UPDATE products SET {', '.join(f'{k}=:{k}' for k in PRODUCT_FIELDS)} WHERE id=:id",
                        {**v, "id": product_id})
    return product_id, v


def delete_product(product_id) -> dict | None:
    row = get_product(product_id)
    if row:
        with db.connect() as con:
            con.execute("DELETE FROM products WHERE id=?", (product_id,))
    return row


# ---------------------------------------------------------------- series

def date_order_problems(series: str, iso_date: str, con: sqlite3.Connection | None = None,
                        kind: str = INVOICE) -> list[str]:
    """Invoices of a series are numbered in the order of their dates: a new one cannot be dated before the
    latest invoice already issued in the series (deleted ones from Income included). Credit notes share the
    numbers of their series, so they count here too, and follow the same rule."""
    sql = ("SELECT full_number, date, kind FROM issued_invoices WHERE series=? "
           "AND date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' ORDER BY date DESC, number DESC LIMIT 1")
    if con is None:
        with db.connect() as c:
            last = c.execute(sql, (series,)).fetchone()
    else:
        last = con.execute(sql, (series,)).fetchone()
    if last and iso_date < last["date"]:
        return [f"{KIND_NAMES.get(last['kind'], 'invoice')} {last['full_number']} of series {series} is dated "
                f"{last['date']}, so a new {KIND_NAMES.get(kind, 'invoice')} in that series cannot be dated earlier "
                f"(numbers follow the dates)"]
    return []


def format_number(series: str, number: int) -> str:
    return f"{series}-{number:04d}"


def highest_issued(series: str) -> int:
    with db.connect() as con:
        return con.execute("SELECT COALESCE(MAX(number), 0) FROM issued_invoices WHERE series=?", (series,)).fetchone()[0]


def list_series() -> list[dict]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM invoice_series ORDER BY name, id")]
    for r in rows:
        r["next"] = format_number(r["name"], r["next_number"])
        r["issued"] = issued_count(r["name"])
        r["is_default"] = bool(r["is_default"])
    return rows


def issued_count(series: str) -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM issued_invoices WHERE series=?", (series,)).fetchone()[0]


def get_series(series_id) -> dict | None:
    if not _valid_id(series_id):
        return None
    return next((s for s in list_series() if s["id"] == series_id), None)


def check_series(fields: dict, series_id: int | None = None) -> tuple[dict, list[str]]:
    name = re.sub(r"\s+", "", _text(fields, "name")).upper()
    raw_next = _text(fields, "next_number").strip() or "1"
    default = fields.get("is_default") in (True, "1", "on", "true", 1)
    problems = []
    if not name:
        problems.append("the series name is missing")
    elif not re.fullmatch(r"[A-Z0-9]{1,10}", name) or not name[0].isalpha():
        problems.append("the series name should be 1 to 10 letters and digits, starting with a letter (e.g. PFA or AB2026)")
    else:
        for other in list_series():
            if other["id"] != series_id and other["name"] == name:
                problems.append(f"there is already a series {name}")
    next_number = None
    if not (raw_next.isascii() and raw_next.isdigit()) or not 1 <= int(raw_next) <= MAX_NUMBER:
        problems.append(f"the next number should be a whole number from 1 to {MAX_NUMBER}")
    else:
        next_number = int(raw_next)
        if name and highest_issued(name) >= next_number:
            top = highest_issued(name)
            problems.append(f"invoice {format_number(name, top)} was already issued, so the next number must be "
                            f"{top + 1} or more")
    return {"name": name, "next_number": next_number, "is_default": default}, problems


def save_series(fields: dict, series_id: int | None = None) -> tuple[int, dict]:
    old = get_series(series_id) if series_id is not None else None
    if series_id is not None and old is None:
        raise InvoicingError("the series no longer exists")
    v, problems = check_series(fields, series_id)
    if problems:
        raise InvoicingError("; ".join(problems))
    with db.connect() as con:
        first = con.execute("SELECT COUNT(*) FROM invoice_series").fetchone()[0] == 0
        default = 1 if (v["is_default"] or first or (old and old["is_default"])) else 0
        if default:
            con.execute("UPDATE invoice_series SET is_default=0")
        if series_id is None:
            cur = con.execute("INSERT INTO invoice_series (name, next_number, is_default, created_at) VALUES (?,?,?,?)",
                              (v["name"], v["next_number"], default, db.now()))
            series_id = cur.lastrowid
        else:
            con.execute("UPDATE invoice_series SET name=?, next_number=?, is_default=? WHERE id=?",
                        (v["name"], v["next_number"], default, series_id))
    return series_id, v


def delete_series(series_id) -> dict | None:
    """Removes a series; invoices issued in it stay, and a new series of the same name continues after them."""
    row = get_series(series_id)
    if row:
        with db.connect() as con:
            con.execute("DELETE FROM invoice_series WHERE id=?", (series_id,))
            if row["is_default"]:
                con.execute("UPDATE invoice_series SET is_default=1 WHERE id=(SELECT MIN(id) FROM invoice_series)")
    return row


# ---------------------------------------------------------------- VAT and payment terms

def get_settings() -> dict:
    with db.connect() as con:
        stored = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM invoicing_settings")}
    v = {**DEFAULT_SETTINGS, **{k: stored[k] for k in DEFAULT_SETTINGS if k in stored}}
    return {"vat_payer": v["vat_payer"] == "1", "vat_rate": v["vat_rate"], "due_days": int(v["due_days"])}


def save_settings(fields: dict) -> dict:
    vat_payer = fields.get("vat_payer") in (True, "1", "on", "true", 1)
    problems = []
    rate_text = _text(fields, "vat_rate").strip().rstrip("%").strip() or get_settings()["vat_rate"]
    try:
        if not re.fullmatch(r"[0-9]{1,2}([.,][0-9]{1,2})?", rate_text):
            raise InvalidOperation
        rate = Decimal(rate_text.replace(",", "."))
        if not rate.is_finite() or not 0 < rate < 100 or rate != rate.quantize(Decimal("0.01")):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        problems.append("the VAT rate should be a percentage between 0 and 100, e.g. 21")
        rate = None
    days_text = _text(fields, "due_days").strip() or "0"
    if not (days_text.isascii() and days_text.isdigit()) or int(days_text) > 365:
        problems.append("the payment term should be a number of days from 0 to 365")
    if problems:
        raise InvoicingError("; ".join(problems))
    values = {"vat_payer": "1" if vat_payer else "0", "vat_rate": canonical(rate), "due_days": str(int(days_text))}
    with db.connect() as con:
        con.executemany("INSERT OR REPLACE INTO invoicing_settings (key, value) VALUES (?, ?)", values.items())
    return get_settings()


# ---------------------------------------------------------------- issuing

def seller_problems(firm: dict) -> list[str]:
    """What is missing in Settings before an invoice can show who issued it."""
    missing = [label for key, label in (("name", "name"), ("cui", "CUI"), ("address", "address")) if not firm.get(key)]
    listed = " and ".join([", ".join(missing[:-1]), missing[-1]] if len(missing) > 1 else missing)
    problems = [f"fill in your firm's {listed} in Settings first"] if missing else []
    if firm.get("cui") and not settings.cui_valid(firm["cui"]):
        problems.append("your firm's CUI in Settings is not valid")
    return problems


def _valid_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2**63 - 1


def _as_id(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if _valid_id(value) else None
    text = str(value or "").strip()
    return int(text) if text.isascii() and text.isdigit() and _valid_id(int(text)) else None


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def compute(fields: dict, *, for_issue: bool = False) -> dict:
    """Checks an invoice and works out its lines and totals; raises InvoicingError listing the problems.

    fields: series_id, client_id, date, due_date, payment_date, notes and lines, a list of
    {"product_id", "quantity", "price"} (price empty = the product's own price; empty rows are skipped)."""
    problems = []
    series = get_series(_as_id(fields.get("series_id")))
    if series is None:
        problems.append("choose a series (add one under Series if the list is empty)" if not list_series()
                        else "choose a series")
    client = get_client(_as_id(fields.get("client_id")))
    if client is None:
        problems.append("choose a client (add one under Clients if the list is empty)" if not list_clients()
                        else "choose a client")
    issue_date = due = paid = None
    try:
        if not _text(fields, "date").strip():
            raise ValueError("the invoice date is missing")
        issue_date = parse_date(_text(fields, "date"))
    except ValueError as e:
        problems.append(str(e))
    try:
        if _text(fields, "due_date").strip():
            due = parse_date(_text(fields, "due_date"), allow_future=True)
            if issue_date and due < issue_date:
                problems.append("the due date is before the invoice date")
    except ValueError as e:
        problems.append(f"due date: {e}")
    try:
        if _text(fields, "payment_date").strip():
            paid = parse_date(_text(fields, "payment_date"))
            if issue_date and paid < issue_date:
                problems.append("the date of payment is before the invoice date")
    except ValueError as e:
        problems.append(f"date of payment: {e}")
    if series and issue_date:
        problems += date_order_problems(series["name"], issue_date.isoformat())
    notes = _text(fields, "notes").strip()
    if len(notes) > 1000 or any(ord(c) < 32 and c not in "\r\n\t" for c in notes):
        problems.append("the notes should be at most 1000 characters")
    problems += printable_problem(notes, "the notes")
    lines, line_problems, currency = check_lines(fields)
    problems += line_problems
    conf = get_settings()
    firm = settings.firm()
    if for_issue:
        problems += seller_problems(firm)
    rate_info = None
    if not problems and currency != "RON":
        rates, rates_err = load_rates()
        try:
            conv = convert(rates, rates_err, Decimal(1), currency, issue_date)
            rate_info = {"rate": conv["rate"], "rate_date": conv["rate_date"]}
        except ValueError as e:
            problems.append(str(e))
    if problems:
        raise InvoicingError("; ".join(problems))
    sums = totals(lines, conf)
    return {
        "series": series["name"], "series_id": series["id"], "client": client, "client_id": client["id"],
        "date": issue_date.isoformat(), "due_date": due.isoformat() if due else "",
        "payment_date": paid.isoformat() if paid else "", "notes": notes, "lines": lines, "currency": currency,
        **sums, "rate": rate_info,
        "total_ron": canonical(_money(Decimal(sums["total"]) * Decimal(rate_info["rate"]))) if rate_info else sums["total"],
        "vat_ron": canonical(_money(Decimal(sums["vat"]) * Decimal(rate_info["rate"]))) if rate_info else sums["vat"],
        "seller": {k: firm.get(k, "") for k in ("name", "cui", "trade_register", "address", "iban", "email", "phone")},
    }


def totals(lines: list[dict], conf: dict) -> dict:
    subtotal = sum((Decimal(line["value"]) for line in lines), Decimal(0))
    vat_rate = Decimal(conf["vat_rate"]) if conf["vat_payer"] else None
    vat = _money(subtotal * vat_rate / 100) if vat_rate is not None else Decimal(0)
    return {"subtotal": canonical(subtotal), "vat_payer": conf["vat_payer"],
            "vat_rate": canonical(vat_rate) if vat_rate is not None else "", "vat": canonical(vat),
            "total": canonical(subtotal + vat)}


def check_lines(fields: dict) -> tuple[list[dict], list[str], str]:
    """The invoice lines worked out: (lines, problems, currency)."""
    problems = []
    lines, currencies_used = [], []
    raw_lines = fields.get("lines") or []
    for n, raw in enumerate(raw_lines, start=1):
        product_text, qty_text, price_text = (_text(raw, k).strip() for k in ("product_id", "quantity", "price"))
        if not product_text and not qty_text and not price_text:
            continue
        product = get_product(_as_id(product_text)) if product_text else None
        if product is None:
            problems.append(f"line {n}: choose a product or service" if not product_text
                            else f"line {n}: the product or service no longer exists")
            continue
        try:
            qty = parse_quantity(qty_text or "1")
            if qty <= 0:
                raise ValueError("the quantity must be more than 0")
            if qty != qty.quantize(Decimal("0.001")):
                raise ValueError("the quantity can have at most 3 decimals")
            if qty >= MAX_QUANTITY:
                raise ValueError("the quantity should be less than 1.000.000")
            price = _price(price_text, "the price") if price_text else Decimal(product["unit_price"])
        except ValueError as e:
            problems.append(f"line {n}: {e}")
            continue
        value = _money(qty * price)
        if value <= 0:
            problems.append(f"line {n}: the value rounds to 0")
            continue
        lines.append({"line": n, "product_id": product["id"], "name": product["name"], "description": product["description"],
                      "unit": product["unit"], "unit_label": UNITS.get(product["unit"], product["unit"]),
                      "quantity": canonical(qty), "price": canonical(price), "value": canonical(value),
                      "currency": product["currency"]})
        currencies_used.append(product["currency"])
    if not lines and not any("line " in p for p in problems):
        problems.append("add at least one product or service")
    if len(lines) > MAX_LINES:
        problems.append(f"an invoice can have at most {MAX_LINES} lines")
    if sum((Decimal(line["value"]) for line in lines), Decimal(0)) >= MAX_TOTAL:
        problems.append("the invoice total should be less than 1.000.000.000.000")
    if len(set(currencies_used)) > 1:
        problems.append(f"all lines of one invoice must be in the same currency (here: {', '.join(sorted(set(currencies_used)))})")
    return lines, problems, currencies_used[0] if currencies_used else "RON"


def parse_quantity(text: str) -> Decimal:
    """A quantity: a single comma is always the decimal mark (1,250 hours = 1.25, 0,001 = 0.001), unlike
    amounts, where 1,500 can mean one thousand five hundred. Anything else is read like an amount
    (1.5 = one and a half, 1.500 = one thousand five hundred, 1.234,5)."""
    t = (text or "").strip().replace(" ", "")
    if re.fullmatch(r"[0-9]+,[0-9]+", t):
        return Decimal(t.replace(",", "."))
    return parse_amount(text)


def preview(fields: dict) -> dict:
    """Totals of the lines filled in so far, and every problem still in the way of issuing
    (series, client, dates, lines, firm details) — for showing totals while the form is filled in."""
    lines, line_problems, currency = check_lines(fields)
    try:
        compute(fields, for_issue=True)
        problems = []
    except ValueError as e:
        problems = str(e).split("; ")
    return {"lines": lines, "currency": currency, "line_problems": line_problems, "problems": problems,
            **totals(lines, get_settings())}


def issue(fields: dict) -> dict:
    """Issues an invoice: takes the next number of its series, prints the PDF, adds the invoice to Income
    with the PDF as its proof, and records it. All of it happens, or none of it.

    Income gets the amount without VAT (VAT collected is not income), converted like any invoice:
    at the rate of the payment date if given, else of the invoice date.
    Returns the issued invoice (with "id", "income_id", "full_number", "income_date")."""
    token = _form_token(fields, "New invoice")
    inv = compute(fields, for_issue=True)
    return _issue(token, lambda con: inv, INVOICE)


def _form_token(fields: dict, form_name: str) -> str:
    token = _text(fields, "token").strip()
    if token and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token):
        raise InvoicingError(f"the form is damaged (bad token); open {form_name} again")
    return token


def _refuse_a_used_token(con: sqlite3.Connection, token: str) -> None:
    """Raises AlreadyIssued when the form with this token already issued its invoice or credit note."""
    if token:
        done = con.execute("SELECT id, full_number, kind FROM issued_invoices WHERE token=?", (token,)).fetchone()
        if done:
            raise AlreadyIssued(done["id"], done["full_number"], done["kind"])


def _issue(token: str, checked, kind: str) -> dict:
    """Issues an invoice or a credit note in one transaction: the next number of its series, its Income row
    (the amount without VAT, negative for a credit note), its PDF as the Income row's proof, and its record.
    All of it happens, or none of it.

    checked(con) gives the document, checked and worked out; it is called once the database is locked, so
    what it reads (what is left to credit, for a credit note) cannot change before the document is stored."""
    what = KIND_NAMES[kind]
    rates, rates_err = load_rates()
    saved_file = None
    try:
        with db.connect() as con:
            con.execute("BEGIN IMMEDIATE")      # one number, one document, even with both apps open
            _refuse_a_used_token(con, token)
            inv = checked(con)
            row = con.execute("SELECT name, next_number FROM invoice_series WHERE id=?", (inv["series_id"],)).fetchone()
            if row is None:
                raise InvoicingError("the series no longer exists")
            number = row["next_number"]
            if number > MAX_NUMBER:
                raise InvoicingError(f"the series {row['name']} has used all its numbers; add a new series")
            full = format_number(row["name"], number)
            if con.execute("SELECT 1 FROM issued_invoices WHERE full_number=?", (full,)).fetchone():
                raise InvoicingError(f"{what} {full} was already issued; set a higher next number for the series")
            order = date_order_problems(row["name"], inv["date"], con, kind)
            if order:
                raise InvoicingError(order[0])
            inv.update(series=row["name"], number=number, full_number=full, issued_at=db.now())
            income = build_invoice(rates, rates_err, date_text=inv["date"], payment_text=inv["payment_date"],
                                   client=inv["client"]["name"], number=full, amount_text=inv["subtotal"],
                                   currency=inv["currency"], source=SOURCE)
            outcome, income_id = db.store_invoice(con, income)
            if outcome != "added":
                raise InvoicingError(f"Income already holds {'an invoice' if kind == INVOICE else 'an entry'} {full} "
                                     f"for {inv['client']['name']} "
                                     f"on {inv['date']} with this amount")
            pdf = render_pdf(inv)
            saved_file = attachments.save("invoices", income_id, f"{full}.pdf", pdf,
                                          base_name=attachments.invoice_base_name(full, inv["client"]["name"]))
            con.execute("UPDATE invoices SET attachment=? WHERE id=?", (saved_file, income_id))
            cur = con.execute(
                """INSERT INTO issued_invoices (income_id, series, number, full_number, date, client, currency, total,
                   data, token, created_at, kind, credits_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (income_id, row["name"], number, full, inv["date"], inv["client"]["name"], inv["currency"],
                 inv["total"], json.dumps(inv, ensure_ascii=False), token, inv["issued_at"], kind,
                 inv["credits"]["id"] if kind == CREDIT_NOTE else 0))
            con.execute("UPDATE invoice_series SET next_number=? WHERE id=?", (number + 1, inv["series_id"]))
    except sqlite3.OperationalError as e:
        if saved_file:
            attachments.remove("invoices", saved_file)
        raise InvoicingError(f"the database is busy or locked ({e}); try again")
    except Exception:
        if saved_file:
            attachments.remove("invoices", saved_file)
        raise
    return {**inv, "id": cur.lastrowid, "income_id": income_id, "income_date": income["income_date"]}


def list_issued() -> list[dict]:
    """The issued invoices and credit notes, newest first. Besides what each one is, "kind" ("invoice" or
    "credit_note"); a credit note has "credits_number", the invoice it reverses; an invoice has "credit_notes"
    (the numbers of its credit notes), "credited" ("", "partly" or "fully") and "can_credit" (False when it
    was deleted from Income, is fully credited or its record is damaged)."""
    with db.connect() as con:
        rows = con.execute(
            """SELECT i.*, inv.id AS income_row, inv.income_date, inv.attachment FROM issued_invoices i
               LEFT JOIN invoices inv ON inv.id = i.income_id ORDER BY i.date DESC, i.id DESC""").fetchall()
        numbers = {r["id"]: r["full_number"] for r in rows}
        out = []
        for r in rows:
            data = _snapshot(r["data"])
            entry = {"id": r["id"], "full_number": r["full_number"], "series": r["series"], "number": r["number"],
                     "date": r["date"], "client": r["client"], "currency": r["currency"], "total": r["total"],
                     "subtotal": data.get("subtotal", r["total"]), "vat": data.get("vat", "0"),
                     "due_date": data.get("due_date", ""), "income_id": r["income_row"],
                     "income_year": (r["income_date"] or "")[:4] or None, "in_income": r["income_row"] is not None,
                     "kind": r["kind"], "credits_number": numbers.get(r["credits_id"], ""), "credit_notes": [],
                     "credited": "", "can_credit": False}
            if r["kind"] == INVOICE:
                status = _read_credit_status(con, r["id"])
                if status is not None:
                    entry["credit_notes"] = status["credited_by"]
                    entry["credited"] = ("fully" if status.get("fully_credited")
                                         else "partly" if status["credited_by"] else "")
                    entry["can_credit"] = status["problem"] is None
            out.append(entry)
    return out


def issued_for_income(income_id) -> dict | None:
    """The issued invoice or credit note behind an Income row, or None (an invoice added by hand or imported):
    its "kind", "full_number", for a credit note "credits_number" (the invoice it reverses), and for an
    invoice "credit_notes" (the numbers of its credit notes)."""
    if not _valid_id(income_id):
        return None
    with db.connect() as con:
        row = con.execute("SELECT id, kind, full_number, credits_id FROM issued_invoices WHERE income_id=?",
                          (income_id,)).fetchone()
        if row is None:
            return None
        reversed_row = con.execute("SELECT full_number FROM issued_invoices WHERE id=?", (row["credits_id"],)).fetchone()
        notes = [n["full_number"] for n in con.execute(
            "SELECT full_number FROM issued_invoices WHERE kind=? AND credits_id=? ORDER BY id", (CREDIT_NOTE, row["id"]))]
    return {"kind": row["kind"], "full_number": row["full_number"],
            "credits_number": reversed_row["full_number"] if reversed_row else "",
            "credit_notes": notes if row["kind"] == INVOICE else []}


def get_issued(issued_id) -> dict | None:
    if not _valid_id(issued_id):
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM issued_invoices WHERE id=?", (issued_id,)).fetchone()
    data = _snapshot(row["data"]) if row else {}
    if not data.get("full_number"):
        return None                      # not found, or its record is unreadable (a damaged backup)
    return {**data, "id": row["id"], "income_id": row["income_id"]}


def _snapshot(text: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def issued_pdf(issued_id) -> tuple[str, bytes] | None:
    """(file name, PDF) of an issued invoice, printed again from what was recorded when it was issued."""
    inv = get_issued(issued_id)
    if inv is None:
        return None
    try:
        name = attachments.invoice_base_name(inv["full_number"], inv["client"]["name"]) + ".pdf"
        return name, render_pdf(inv)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None                      # a record changed by hand or damaged: nothing sensible to print


def income_deleted(income_id: int) -> str | None:
    """An invoice deleted from Income stays an issued invoice, marked as no longer in Income (income_id 0):
    its number was given out, so it is never given out again. Returns its number, if it was issued here."""
    with db.connect() as con:
        row = con.execute("SELECT full_number FROM issued_invoices WHERE income_id=?", (income_id,)).fetchone()
        con.execute("UPDATE issued_invoices SET income_id=0 WHERE income_id=?", (income_id,))
    return row["full_number"] if row else None


def is_generated(income_row) -> bool:
    return bool(income_row) and income_row["source"] == SOURCE


# ---------------------------------------------------------------- credit notes
#
# A credit note (factură storno) reverses all or part of one issued invoice: some of its lines, each up to
# the quantity still left to credit, always at the invoice's own prices and with its VAT. It shows the
# quantities and amounts as negative numbers. It takes the next number of a series like an invoice (the
# invoice's own series unless another is chosen), and is added to Income as a negative amount without VAT,
# counted like any invoice: in the year and at the rate of the refund date if given, else of its own date.
# A credit note stays counted against its invoice even if it is later deleted from Income: it was issued.

MAX_REASON = 500


class _Damaged(Exception):
    pass


def _dec(value) -> Decimal:
    """A stored amount as a Decimal; _Damaged when it is not a finite number."""
    try:
        d = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        raise _Damaged
    if not d.is_finite():
        raise _Damaged
    return d


def _read_credit_status(con: sqlite3.Connection, issued_id) -> dict | None:
    """What is left to credit on an issued invoice, read through con, with Decimal amounts. None when there is
    no such issued invoice or credit note, or its record cannot be read. "problem" says why it cannot be
    credited (or is None)."""
    if not _valid_id(issued_id):
        return None
    row = con.execute("SELECT * FROM issued_invoices WHERE id=?", (issued_id,)).fetchone()
    inv = _snapshot(row["data"]) if row else {}
    if not inv.get("full_number"):
        return None
    full = row["full_number"]
    status = {"id": row["id"], "full_number": full, "series": row["series"], "date": row["date"],
              "client": row["client"], "currency": row["currency"], "kind": row["kind"], "in_income": False,
              "credited_by": [], "lines": [], "vat_payer": False, "vat_rate": "", "problem": None, "invoice": inv}
    if row["kind"] != INVOICE:
        status["problem"] = f"{full} is a credit note; only an invoice can be credited"
        return status
    try:
        if not isinstance(inv.get("client"), dict) or not str(inv["client"].get("name") or "").strip():
            raise _Damaged
        status["vat_payer"] = bool(inv["vat_payer"])
        vat_rate = _dec(inv["vat_rate"]) if status["vat_payer"] else None
        status["vat_rate"] = canonical(vat_rate) if vat_rate is not None else ""
        _dec(inv["subtotal"])
        vat = _dec(inv["vat"])
        if row["currency"] != "RON":
            _dec(inv["rate"]["rate"])
        for pos, line in enumerate(inv["lines"], start=1):
            status["lines"].append({
                "line": pos, "product_id": line.get("product_id"), "name": str(line["name"]),
                "description": str(line.get("description") or ""), "unit": str(line.get("unit") or ""),
                "unit_label": str(line.get("unit_label") or ""), "price": _dec(line["price"]),
                "quantity": _dec(line["quantity"]), "value": _dec(line["value"]),
                "credited_quantity": Decimal(0), "credited_value": Decimal(0)})
        if not status["lines"]:
            raise _Damaged
    except (_Damaged, KeyError, TypeError, AttributeError):
        status["problem"] = f"the record of invoice {full} is damaged, so it cannot be credited"
        return status
    credited_vat = Decimal(0)
    notes = con.execute("SELECT full_number, data FROM issued_invoices WHERE kind=? AND credits_id=? ORDER BY id",
                        (CREDIT_NOTE, row["id"])).fetchall()
    for note in notes:
        status["credited_by"].append(note["full_number"])
        data = _snapshot(note["data"])
        try:
            for credited in data["lines"]:
                pos = int(credited["orig_line"])
                if not 1 <= pos <= len(status["lines"]):
                    raise _Damaged
                target = status["lines"][pos - 1]
                target["credited_quantity"] -= _dec(credited["quantity"])      # stored as negative numbers
                target["credited_value"] -= _dec(credited["value"])
            credited_vat -= _dec(data["vat"])
        except (_Damaged, KeyError, TypeError, ValueError):
            status["problem"] = (f"the record of credit note {note['full_number']} is damaged, so what is left to "
                                 f"credit on invoice {full} is not known")
            return status
    for line in status["lines"]:
        line["left_quantity"] = max(line["quantity"] - line["credited_quantity"], Decimal(0))
        line["left_value"] = max(line["value"] - line["credited_value"], Decimal(0))
    status["credited_subtotal"] = sum((line["credited_value"] for line in status["lines"]), Decimal(0))
    status["credited_vat"] = credited_vat
    status["subtotal_left"] = sum((line["left_value"] for line in status["lines"]), Decimal(0))
    status["vat_left"] = max(vat - credited_vat, Decimal(0))
    status["fully_credited"] = all(line["left_quantity"] == 0 for line in status["lines"])
    status["in_income"] = bool(row["income_id"]) and con.execute(
        "SELECT 1 FROM invoices WHERE id=?", (row["income_id"],)).fetchone() is not None
    if not status["in_income"]:
        status["problem"] = (f"invoice {full} was deleted from Income, so it cannot be credited (the credit note "
                             f"would take off income that is not recorded)")
    elif status["fully_credited"]:
        status["problem"] = f"invoice {full} is already fully credited (by {', '.join(status['credited_by'])})"
    return status


def credit_status(issued_id) -> dict | None:
    """What is left to credit on an issued invoice, or None when there is no such invoice or credit note.

    Returns its number, series, date, client, currency, vat_payer and vat_rate; "lines", each with its
    position ("line", what a credit note refers to), name, unit_label, price, quantity and value as issued,
    credited_quantity and credited_value (taken by its credit notes) and left_quantity and left_value;
    subtotal_left, vat_left and total_left; "credited_by" (the numbers of its credit notes), "in_income",
    "fully_credited", and "problem": why it cannot be credited (a credit note, a damaged record, deleted
    from Income, nothing left), or None. Amounts are decimal strings."""
    with db.connect() as con:
        st = _read_credit_status(con, _as_id(issued_id))
    if st is None:
        return None
    out = {k: v for k, v in st.items() if k != "invoice"}
    out["lines"] = [{k: canonical(v) if isinstance(v, Decimal) else v for k, v in line.items()} for line in st["lines"]]
    for key in ("credited_subtotal", "credited_vat", "subtotal_left", "vat_left"):
        out[key] = canonical(st.get(key, Decimal(0)))
    out["total_left"] = canonical(st.get("subtotal_left", Decimal(0)) + st.get("vat_left", Decimal(0)))
    out["fully_credited"] = st.get("fully_credited", False)
    return out


def _credit_lines(status: dict, fields: dict) -> tuple[list[dict], list[str]]:
    """The lines of a credit note worked out from the form: (lines, problems).

    fields["lines"]: a list of {"line": the invoice line's position, "quantity": how much of it to credit}
    (an empty or zero quantity leaves that line out); fields["everything"]: credit all that is left instead.
    Each line is credited at the invoice's price. Its value is worked out on everything credited of that line
    so far, so crediting a line in several pieces adds up to the invoice's value to the cent, and the last of
    a line always takes exactly the value left."""
    problems, chosen, seen = [], {}, set()
    lines = status["lines"]
    if fields.get("everything") in (True, "1", "on", "true", 1):
        chosen = {line["line"]: line["left_quantity"] for line in lines if line["left_quantity"] > 0}
    else:
        raw_lines = fields.get("lines") or []
        for raw in raw_lines if isinstance(raw_lines, list) else []:
            if not isinstance(raw, dict):
                continue
            pos_text, qty_text = _text(raw, "line").strip(), _text(raw, "quantity").strip()
            if not qty_text:
                continue
            pos = _as_id(pos_text)
            if pos is None or pos > len(lines):
                problems.append(f"invoice {status['full_number']} has no line {pos_text[:10] or '(none)'}")
                continue
            line = lines[pos - 1]
            what = f"line {pos} ({line['name']})"
            if pos in seen:
                problems.append(f"{what} is given twice")
                continue
            seen.add(pos)
            try:
                if qty_text.startswith("-"):
                    raise ValueError("write the quantity to credit without a minus sign")
                qty = parse_quantity(qty_text)
                if qty != qty.quantize(Decimal("0.001")):
                    raise ValueError("the quantity can have at most 3 decimals")
            except (ValueError, ArithmeticError) as e:
                problems.append(f"{what}: {e if isinstance(e, ValueError) else 'unrecognised quantity'}")
                continue
            if qty == 0:
                continue
            if line["left_quantity"] == 0:
                problems.append(f"{what} is already fully credited")
            elif qty > line["left_quantity"]:
                problems.append(f"{what}: only {_fmt_num(canonical(line['left_quantity']), 0)} left to credit, "
                                f"not {_fmt_num(canonical(qty), 0)}")
            else:
                chosen[pos] = qty
    out = []
    for pos in sorted(chosen):
        line, qty = lines[pos - 1], chosen[pos]
        if qty == line["left_quantity"]:
            value = line["left_value"]
        else:
            value = _money(line["price"] * (line["credited_quantity"] + qty)) - line["credited_value"]
            value = min(max(value, Decimal(0)), line["left_value"])
        out.append({"line": len(out) + 1, "orig_line": pos, "product_id": line["product_id"], "name": line["name"],
                    "description": line["description"], "unit": line["unit"], "unit_label": line["unit_label"],
                    "quantity": canonical(-qty), "price": canonical(line["price"]), "value": canonical(-value),
                    "currency": status["currency"], "_qty": qty})
    return out, problems


def _credit_totals(status: dict, lines: list[dict]) -> dict:
    """Subtotal, VAT and total of a credit note (as negative decimal strings). The VAT is worked out on all
    that is credited of the invoice so far, at the invoice's rate; crediting the last of the invoice takes
    exactly the VAT left, so its credit notes always add up to the invoice's VAT."""
    subtotal = -sum((Decimal(line["value"]) for line in lines), Decimal(0))
    vat = Decimal(0)
    if status["vat_payer"] and lines:
        taken = {line["orig_line"]: line["_qty"] for line in lines}
        if all(line["left_quantity"] - taken.get(line["line"], 0) == 0 for line in status["lines"]):
            vat = status["vat_left"]
        else:
            rate = Decimal(status["vat_rate"])
            vat = _money((status["credited_subtotal"] + subtotal) * rate / 100) - status["credited_vat"]
            vat = min(max(vat, Decimal(0)), status["vat_left"])
    return {"subtotal": canonical(-subtotal), "vat_payer": status["vat_payer"], "vat_rate": status["vat_rate"],
            "vat": canonical(-vat), "total": canonical(-(subtotal + vat))}


def compute_credit(fields: dict, *, for_issue: bool = False, con: sqlite3.Connection | None = None) -> dict:
    """Checks a credit note and works out its lines and totals; raises InvoicingError listing the problems.

    fields: issued_id (the invoice it reverses), series_id (empty = the invoice's own series), date,
    payment_date (the refund date, optional), reason (optional, printed on it), and lines, a list of
    {"line", "quantity"} (see _credit_lines), or everything=True to credit all that is left.
    con: read what is left to credit through this connection (issuing does, once the database is locked)."""
    if con is None:
        with db.connect() as c:
            status = _read_credit_status(c, _as_id(fields.get("issued_id")))
    else:
        status = _read_credit_status(con, _as_id(fields.get("issued_id")))
    if status is None:
        raise InvoicingError("choose the invoice to credit (it does not exist, or its record cannot be read)")
    if status["problem"]:
        raise InvoicingError(status["problem"])
    problems = []
    full, inv = status["full_number"], status["invoice"]
    series_text = _text(fields, "series_id").strip()
    if series_text:
        series = get_series(_as_id(series_text))
        if series is None:
            problems.append("choose a series")
    else:
        series = next((s for s in list_series() if s["name"] == status["series"]), None)
        if series is None:
            problems.append(f"the series {status['series']} of invoice {full} is no longer in the list; choose a series"
                            if list_series() else "choose a series (add one under Series if the list is empty)")
    credit_date = refund = None
    try:
        if not _text(fields, "date").strip():
            raise ValueError("the credit note date is missing")
        credit_date = parse_date(_text(fields, "date"))
        if credit_date.isoformat() < status["date"]:
            problems.append(f"the credit note cannot be dated before the invoice it reverses ({full} is dated "
                            f"{status['date']})")
    except ValueError as e:
        problems.append(str(e))
    try:
        if _text(fields, "payment_date").strip():
            refund = parse_date(_text(fields, "payment_date"))
            if credit_date and refund < credit_date:
                problems.append("the date of the refund is before the credit note date")
    except ValueError as e:
        problems.append(f"date of the refund: {e}")
    if series and credit_date:
        problems += date_order_problems(series["name"], credit_date.isoformat(), con, CREDIT_NOTE)
    reason = _text(fields, "reason").strip()
    if len(reason) > MAX_REASON or any(ord(c) < 32 and c not in "\r\n\t" for c in reason):
        problems.append(f"the reason should be at most {MAX_REASON} characters")
    problems += printable_problem(reason, "the reason")
    lines, line_problems = _credit_lines(status, fields)
    problems += line_problems
    sums = _credit_totals(status, lines)
    if not lines and not line_problems:
        problems.append("choose at least one line of the invoice and the quantity to credit")
    elif lines and Decimal(sums["total"]) == 0 and not all(
            line["_qty"] == status["lines"][line["orig_line"] - 1]["left_quantity"] for line in lines):
        # worth 0 only makes sense for closing what is left of a line (whose value earlier credits took already)
        problems.append(f"the chosen quantities are worth 0,00 {status['currency']}; choose more to credit")
    firm = settings.firm()
    if for_issue:
        problems += seller_problems(firm)
    currency = status["currency"]
    if not problems and currency != "RON":
        rates, rates_err = load_rates()      # the Income row is converted on the refund date, or its own date
        try:
            convert(rates, rates_err, Decimal(1), currency, refund or credit_date)
        except ValueError as e:
            problems.append(str(e))
    if problems:
        raise InvoicingError("; ".join(problems))
    rate_info = inv.get("rate") if currency != "RON" else None      # the invoice's rate, for the VAT in RON
    rate = Decimal(str(rate_info["rate"])) if rate_info else None
    return {
        "kind": CREDIT_NOTE,
        "credits": {"id": status["id"], "full_number": full, "date": status["date"], "series": status["series"]},
        "series": series["name"], "series_id": series["id"], "client": inv["client"], "client_id": inv.get("client_id"),
        "date": credit_date.isoformat(), "due_date": "", "payment_date": refund.isoformat() if refund else "",
        "reason": reason, "notes": "", "currency": currency,
        "lines": [{k: v for k, v in line.items() if k != "_qty"} for line in lines], **sums, "rate": rate_info,
        "total_ron": canonical(_money(Decimal(sums["total"]) * rate)) if rate else sums["total"],
        "vat_ron": canonical(_money(Decimal(sums["vat"]) * rate)) if rate else sums["vat"],
        "seller": {k: firm.get(k, "") for k in ("name", "cui", "trade_register", "address", "iban", "email", "phone")},
    }


def credit_preview(fields: dict) -> dict:
    """The lines and totals of a credit note being filled in (over the lines that are right so far), and
    every problem still in the way of issuing it. Nothing is saved."""
    with db.connect() as con:
        status = _read_credit_status(con, _as_id(fields.get("issued_id")))
    lines, line_problems = [], []
    if status is not None and not status["problem"]:
        lines, line_problems = _credit_lines(status, fields)
    sums = _credit_totals(status, lines) if lines else {"subtotal": "0", "vat": "0", "total": "0",
                                                        "vat_payer": bool(status and status["vat_payer"]),
                                                        "vat_rate": status["vat_rate"] if status else ""}
    try:
        compute_credit(fields, for_issue=True)
        problems = []
    except ValueError as e:
        problems = str(e).split("; ")
    return {"lines": [{k: v for k, v in line.items() if k != "_qty"} for line in lines],
            "currency": status["currency"] if status else "", "line_problems": line_problems, "problems": problems,
            **sums}


def issue_credit_note(fields: dict) -> dict:
    """Issues a credit note for an invoice: takes the next number of its series, prints the PDF, adds it to
    Income as a negative amount without VAT with the PDF as its proof, and records it. All of it happens, or
    none of it. What is left to credit is checked again once the database is locked, so two credit notes
    issued at the same moment (both apps open) can never credit more than the invoice holds.

    fields: as for compute_credit, plus token (a form sent twice issues once).
    Returns the credit note (with "id", "income_id", "full_number", "income_date")."""
    token = _form_token(fields, "the credit note form")
    with db.connect() as con:
        _refuse_a_used_token(con, token)           # sent twice: say so, rather than "already fully credited"
    compute_credit(fields, for_issue=True)         # the problems, before anything is locked
    return _issue(token, lambda con: compute_credit(fields, for_issue=True, con=con), CREDIT_NOTE)


# ---------------------------------------------------------------- PDF

def _fmt_date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if iso else ""


def _fmt_num(value: str, min_decimals: int = 2) -> str:
    """Romanian format with at least min_decimals decimals: 1.234,5 -> 1.234,50; 2,125 stays 2,125."""
    d = Decimal(value)
    exp = -d.as_tuple().exponent if d.as_tuple().exponent < 0 else 0
    places = max(min_decimals, exp)
    s = f"{abs(d):,.{places}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if d < 0 else "") + s


def _font_names() -> tuple[str, str]:
    """Registers DejaVu Sans (has ă, â, î, ș, ț) once; falls back to Helvetica if the font files are missing."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from .config import bundle_dir
    if "DejaVuSans" in pdfmetrics.getRegisteredFontNames():
        return "DejaVuSans", "DejaVuSans-Bold"
    folder = bundle_dir() / "fonts"
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(folder / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(folder / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold")
    except Exception:
        return "Helvetica", "Helvetica-Bold"
    return "DejaVuSans", "DejaVuSans-Bold"


def render_pdf(inv: dict) -> bytes:
    """The invoice or credit note as an A4 PDF, labels in Romanian and English.

    A credit note (kind "credit_note") is titled FACTURĂ STORNO / CREDIT NOTE and names the invoice it
    reverses; it has no due date and no line asking for payment, its quantities and totals are negative
    (as recorded), its reason is printed when one was given, and in another currency it shows the reversed
    invoice's exchange rate, which its VAT and total in RON were worked out with."""
    import io
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _font_names()
    base = ParagraphStyle("base", fontName=regular, fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=10, textColor=colors.HexColor("#555555"))
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    title = ParagraphStyle("title", parent=base, fontName=bold, fontSize=18, leading=22)
    head = ParagraphStyle("head", parent=base, fontName=bold, fontSize=8, leading=10, textColor=colors.white)
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    e = lambda s: escape(str(s or ""))
    cur = inv["currency"]
    credit = inv.get("kind") == CREDIT_NOTE
    seller, client = inv["seller"], inv["client"]

    def party(label: str, lines: list[tuple[str, str]], name: str) -> list:
        out = [Paragraph(f"<font size=7.5 color='#555555'>{e(label)}</font>", base),
               Paragraph(f"<b>{e(name)}</b>", ParagraphStyle("n", parent=base, fontSize=10.5, leading=14))]
        out += [Paragraph(f"<font color='#555555'>{e(k)}:</font> {e(v)}", base) for k, v in lines if v]
        return out

    seller_lines = [("CUI / Tax ID", seller["cui"]), ("Nr. Reg. Com. / Trade register", seller["trade_register"]),
                    ("Adresa / Address", seller["address"]), ("IBAN", seller["iban"]), ("Email", seller["email"]),
                    ("Telefon / Phone", seller["phone"])]
    tax_label = "CNP / Personal ID" if client["country"] == "RO" and len(client["tax_id"]) == 13 else "CUI / Tax ID"
    client_lines = [(tax_label, client["tax_id"]), ("Nr. Reg. Com. / Trade register", client["reg_number"]),
                    ("Adresa / Address", f"{client['address']}, {client['city']}, {COUNTRIES.get(client['country'], client['country'])}"),
                    ("Email", client["email"]), ("Telefon / Phone", client["phone"])]
    left = party("FURNIZOR / SUPPLIER", seller_lines, seller["name"])
    if not inv["vat_payer"]:
        left.append(Paragraph(NOT_VAT_PAYER, small))
    parties = Table([[left, party("CLIENT / CUSTOMER", client_lines, client["name"])]],
                    colWidths=[87 * mm, 87 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (0, 0), 8 * mm)]))

    meta = [f"Seria / Series: <b>{e(inv['series'])}</b> &nbsp; Nr. / No.: <b>{inv['number']:04d}</b>",
            f"Data / Date: <b>{_fmt_date(inv['date'])}</b>"]
    if inv.get("due_date") and not credit:
        meta.append(f"Scadență / Due date: <b>{_fmt_date(inv['due_date'])}</b>")
    if credit:
        # the title is wider than an invoice's, so it has the whole width; what it reverses goes under it
        reverses = inv["credits"]
        reference = ParagraphStyle("reference", parent=base, fontSize=10, leading=14)
        header = Table([[Paragraph("FACTURĂ STORNO / CREDIT NOTE", title), ""],
                        [Paragraph(f"Stornează factura / Reverses invoice<br/><b>{e(reverses['full_number'])}</b> "
                                   f"din / of <b>{_fmt_date(reverses['date'])}</b>", reference),
                         [Paragraph(m, right) for m in meta]]],
                       colWidths=[90 * mm, 84 * mm])
        header.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                    ("BOTTOMPADDING", (0, 0), (-1, 0), 3 * mm)]))
    else:
        header = Table([[Paragraph("FACTURĂ / INVOICE", title), [Paragraph(m, right) for m in meta]]],
                       colWidths=[90 * mm, 84 * mm])
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))

    rows = [[Paragraph("Nr.", head), Paragraph("Denumire / Description", head), Paragraph("U.M. / Unit", head),
             Paragraph("Cant. / Qty", head_r), Paragraph(f"Preț unitar / Unit price ({e(cur)})", head_r),
             Paragraph(f"Valoare / Amount ({e(cur)})", head_r)]]
    for n, line in enumerate(inv["lines"], start=1):
        desc = f"<b>{e(line['name'])}</b>"
        if line.get("description"):
            desc += "<br/>" + e(line["description"]).replace("\n", "<br/>")
        rows.append([Paragraph(str(n), base), Paragraph(desc, base), Paragraph(e(line["unit_label"]), base),
                     Paragraph(_fmt_num(line["quantity"], 0), right), Paragraph(_fmt_num(line["price"]), right),
                     Paragraph(_fmt_num(line["value"]), right)])
    items = Table(rows, colWidths=[10 * mm, 70 * mm, 24 * mm, 18 * mm, 26 * mm, 26 * mm], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4858")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))

    total_rows = [[Paragraph("Total fără TVA / Subtotal", right), Paragraph(f"{_fmt_num(inv['subtotal'])} {e(cur)}", right)]]
    if inv["vat_payer"]:
        total_rows.append([Paragraph(f"TVA / VAT {_fmt_num(inv['vat_rate'], 0)}%", right),
                           Paragraph(f"{_fmt_num(inv['vat'])} {e(cur)}", right)])
    total_rows.append([Paragraph("<b>Total de plată / Total due</b>", right),
                       Paragraph(f"<b>{_fmt_num(inv['total'])} {e(cur)}</b>", right)])
    totals = Table(total_rows, colWidths=[60 * mm, 40 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))

    story = [header, Spacer(1, 8 * mm), parties, Spacer(1, 8 * mm), items, Spacer(1, 4 * mm), totals]
    if inv.get("rate"):
        rate_label = "Curs BNR al facturii stornate / Exchange rate of the reversed invoice" if credit \
            else "Curs BNR / Exchange rate"
        story += [Spacer(1, 3 * mm), Paragraph(
            f"{rate_label} ({_fmt_date(inv['rate']['rate_date'])}): 1 {e(cur)} = "
            f"{_fmt_num(inv['rate']['rate'], 4)} RON &nbsp;·&nbsp; "
            + (f"TVA / VAT: {_fmt_num(inv['vat_ron'])} RON &nbsp;·&nbsp; " if inv["vat_payer"] and inv.get("vat_ron") else "")
            + f"Total: {_fmt_num(inv['total_ron'])} RON", small)]
    if inv.get("notes"):
        story += [Spacer(1, 6 * mm), Paragraph("<b>Mențiuni / Notes</b>", base),
                  Paragraph(e(inv["notes"]).replace("\n", "<br/>"), base)]
    if credit and str(inv.get("reason") or "").strip():
        story += [Spacer(1, 6 * mm), Paragraph("<b>Motiv / Reason</b>", base),
                  Paragraph(e(inv["reason"]).replace("\n", "<br/>"), base)]
    if seller.get("iban") and not credit:
        story += [Spacer(1, 6 * mm), Paragraph(f"Plata în contul / Payment to: <b>{e(seller['iban'])}</b>", base)]
    story += [Spacer(1, 10 * mm),
              Paragraph("Factura circulă fără semnătură și ștampilă. / Valid without signature and stamp.", small)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm,
                            title=f"Factura storno {inv['full_number']}" if credit else f"Factura {inv['full_number']}",
                            author=seller["name"],
                            subject=f"Credit note {inv['full_number']}" if credit else f"Invoice {inv['full_number']}",
                            creator="PFA tax tracker")
    doc.build(story)
    return buf.getvalue()
