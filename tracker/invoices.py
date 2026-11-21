from datetime import date

from . import db
from .parsing import canonical, parse_amount, parse_date, read_csv
from .rates import Rates, convert, load_rates

COLUMN_ALIASES = {
    "date": ("date", "data", "invoice_date", "invoice date", "data factura", "data_factura", "issue_date"),
    "payment_date": ("payment_date", "payment date", "paid", "paid_on", "data_plata", "data plata",
                     "data_incasare", "data incasare", "data incasarii"),
    "client": ("client", "customer", "beneficiar", "client_name", "client name"),
    "amount": ("amount", "suma", "valoare", "total", "value"),
    "currency": ("currency", "moneda", "valuta", "ccy"),
    "number": ("number", "invoice", "invoice_number", "invoice number", "invoice_no", "nr", "numar",
               "nr factura", "serie_numar"),
}
REQUIRED = ("date", "client", "amount", "currency")


def map_columns(cols: list[str]) -> dict[str, str]:
    found = {}
    for field, names in COLUMN_ALIASES.items():
        match = next((n for n in names if n in cols), None)
        if match:
            found[field] = match
    return found


def build_invoice(rates: Rates, rates_err: str | None, *, date_text: str, payment_text: str, client: str,
                  number: str, amount_text: str, currency: str, source: str) -> dict:
    """Validates one invoice and converts it to RON; raises ValueError with a readable message.

    The income counts in the year the money was received, so the payment date (when known)
    decides both the year and the exchange rate.
    """
    if not date_text.strip():
        raise ValueError("the invoice date is missing")
    d = parse_date(date_text)
    paid: date | None = parse_date(payment_text) if payment_text.strip() else None
    if paid and paid < d:
        raise ValueError(f"date of payment {paid.isoformat()} is before the invoice date {d.isoformat()}")
    client = client.strip()
    if not client:
        raise ValueError("empty client")
    amount = parse_amount(amount_text)
    if not currency.strip():
        raise ValueError("empty currency")
    income_date = paid or d
    conv = convert(rates, rates_err, amount, currency.strip(), income_date)
    return {"date": d.isoformat(), "payment_date": paid.isoformat() if paid else "",
            "income_date": income_date.isoformat(), "client": client, "number": number.strip(),
            "amount": canonical(amount), "source": source, **conv}


def add_invoice(**fields) -> tuple[str, int]:
    """Stores one invoice entered by hand; returns the outcome of db.store_invoice."""
    rates, rates_err = load_rates()
    row = build_invoice(rates, rates_err, source="manual", **fields)
    with db.connect() as con:
        return db.store_invoice(con, row)


def import_invoices(text: str, source: str) -> dict:
    """Imports valid rows, skips invoices already stored and lists rows with problems."""
    result = {"source": source, "added": [], "paid": [], "duplicates": [], "errors": [], "fatal": None}
    rows = read_csv(text)
    if not rows:
        result["fatal"] = "The file has no data rows."
        return result
    colmap = map_columns(list(rows[0].keys()))
    missing = [f for f in REQUIRED if f not in colmap]
    if missing:
        result["fatal"] = f"Missing column(s): {', '.join(missing)}. Found: {', '.join(rows[0].keys())}"
        return result
    rates, rates_err = load_rates()
    with db.connect() as con:
        for line, r in enumerate(rows, start=2):
            if not any(r.values()):
                continue
            try:
                item = build_invoice(
                    rates, rates_err, date_text=r[colmap["date"]],
                    payment_text=r[colmap["payment_date"]] if "payment_date" in colmap else "",
                    client=r[colmap["client"]], number=r[colmap["number"]] if "number" in colmap else "",
                    amount_text=r[colmap["amount"]], currency=r[colmap["currency"]], source=source)
            except ValueError as e:
                result["errors"].append({"line": line, "row": r, "error": str(e)})
                continue
            outcome, _ = db.store_invoice(con, item)
            result[{"added": "added", "paid": "paid", "duplicate": "duplicates"}[outcome]].append(item)
    return result
