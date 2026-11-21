from decimal import ROUND_HALF_UP, Decimal

from .config import CENT


def money(v) -> str:
    """Romanian number format: 12.345,67"""
    if v is None or v == "":
        return ""
    v = Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if v < 0 else "") + s


def number(v) -> str:
    return format(v.normalize(), "f") if isinstance(v, Decimal) else str(v)


def percent(v) -> str:
    return number(Decimal(str(v)) * 100) + "%"


def rate(v) -> str:
    return "" if v in (None, "") else str(v)
