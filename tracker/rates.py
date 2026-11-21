import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .config import CENT, MAX_RATE_LOOKBACK_DAYS, RATES_PATH
from .parsing import decode, parse_date, parse_rate, read_csv

Rates = dict[str, dict[date, Decimal]]


def load_rates() -> tuple[Rates, str | None]:
    """Reads the rates file, either "date,currency,rate" rows or "date,EUR,USD,..." columns."""
    if not RATES_PATH.exists():
        return {}, f"rates file not found: {RATES_PATH}"
    rates: Rates = defaultdict(dict)
    try:
        rows = read_csv(decode(RATES_PATH.read_bytes()))
        if not rows:
            return {}, f"rates file is empty: {RATES_PATH}"
        cols = list(rows[0].keys())
        if "date" not in cols:
            return {}, "rates file needs a 'date' column"
        long_format = "currency" in cols and "rate" in cols
        for r in rows:
            if not r.get("date"):
                continue
            d = parse_date(r["date"], allow_future=True)
            if long_format:
                rates[r["currency"].upper()][d] = parse_rate(r["rate"])
            else:
                for c in cols:
                    if c != "date" and r.get(c):
                        rates[c.upper()][d] = parse_rate(r[c])
    except (ValueError, csv.Error) as e:
        return {}, f"could not read rates file {RATES_PATH}: {e}"
    return dict(rates), None


def find_rate(rates: Rates, currency: str, d: date) -> tuple[Decimal, date]:
    """Rate for the day, or the latest earlier one (weekends, holidays) within the lookback window."""
    currency = currency.upper()
    if currency in ("RON", "LEI"):
        return Decimal(1), d
    table = rates.get(currency)
    if not table:
        raise ValueError(f"no rates for {currency} in the rates file")
    for back in range(MAX_RATE_LOOKBACK_DAYS + 1):
        day = d - timedelta(days=back)
        if day in table:
            return table[day], day
    raise ValueError(f"no {currency} rate on {d} or in the {MAX_RATE_LOOKBACK_DAYS} days before")


def convert(rates: Rates, rates_err: str | None, amount: Decimal, currency: str, d: date) -> dict:
    """Converts an amount to RON; raises ValueError with a readable message."""
    currency = "RON" if currency.upper() == "LEI" else currency.upper()
    if currency != "RON" and rates_err:
        raise ValueError(rates_err)
    rate, rate_day = find_rate(rates, currency, d)
    return {"currency": currency, "rate": str(rate), "rate_date": rate_day.isoformat(),
            "amount_ron": str((amount * rate).quantize(CENT, rounding=ROUND_HALF_UP))}


def rates_info() -> dict:
    rates, err = load_rates()
    return {
        "path": str(RATES_PATH), "error": err,
        "currencies": [
            {"code": c, "count": len(t), "first": min(t).isoformat(), "last": max(t).isoformat()}
            for c, t in sorted(rates.items()) if t
        ],
    }
