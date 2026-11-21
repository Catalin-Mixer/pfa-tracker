import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MIN_DATE = date(2000, 1, 1)
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y.%m.%d")
MAX_AMOUNT = Decimal(10) ** 15
CURRENCY_SYMBOLS = ("RON", "LEI", "EUR", "USD", "GBP", "€", "$", "£")


def parse_date(s: str, allow_future: bool = False) -> date:
    """Reads a date; by default a date after today is refused."""
    s = (s or "").strip()
    if not s.isascii():
        raise ValueError(f"unrecognised date '{s}' (use YYYY-MM-DD or DD.MM.YYYY)")
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if not allow_future and d > date.today():
            raise ValueError(f"date {d.isoformat()} is in the future")
        if d < MIN_DATE:
            raise ValueError(f"date {d.isoformat()} is before 2000")
        return d
    raise ValueError(f"unrecognised date '{s}' (use YYYY-MM-DD or DD.MM.YYYY)")


def parse_amount(s: str) -> Decimal:
    """Accepts 1234.56, 1234,56, 1.234,56, 1,234.56 and 1 234,56.

    A single separator followed by exactly three digits is a thousands separator (1.500 = 1500).
    Thousands groups must have three digits, so 12.34.56 is refused rather than guessed.
    """
    raw = (s or "").strip()
    t = raw.replace("\u00a0", "").replace(" ", "").replace("'", "")
    for sym in CURRENCY_SYMBOLS:
        t = t.replace(sym, "").replace(sym.lower(), "")
    if not t:
        raise ValueError("empty amount")
    bad = ValueError(f"unrecognised amount '{raw}'")
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        thou = "." if dec == "," else ","
        whole, frac = t.rsplit(dec, 1)
        groups = whole.split(thou)
        if thou in frac or not groups[0] or any(len(g) != 3 for g in groups[1:]):
            raise bad
        t = "".join(groups) + "." + frac
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        if len(parts) == 2 and len(parts[1]) != 3:
            t = parts[0] + "." + parts[1]
        elif all(len(p) == 3 for p in parts[1:]):
            t = "".join(parts)
        else:
            raise bad
    if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", t):
        raise bad
    value = Decimal(t)
    if abs(value) >= MAX_AMOUNT:
        raise ValueError(f"amount '{raw}' is too large")
    return value


def canonical(value: Decimal) -> str:
    """One way to write an amount (100, 100,00 and 100.0 are all "100"), so that duplicates are recognised."""
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def parse_rate(s: str) -> Decimal:
    """Rates are plain decimals (4.9750 or 4,9750), so there is no thousands-separator logic here."""
    t = (s or "").strip().replace(",", ".")
    try:
        v = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"unrecognised rate '{s}'")
    if not v.is_finite() or v <= 0:
        raise ValueError(f"unrecognised rate '{s}'")
    return v


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def read_csv(text: str) -> list[dict[str, str]]:
    """Reads comma-, semicolon- or tab-separated text; header names are lower-cased."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [
        {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        for row in csv.DictReader(io.StringIO(text), dialect=dialect)
    ]
