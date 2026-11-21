from decimal import ROUND_HALF_UP, Decimal, localcontext

from . import caen, db, settings
from .config import CENT
from .formatting import money, number

ZERO = Decimal(0)
FALLBACK_TAX_RATE = Decimal("0.10")


def r2(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _cas(net: Decimal, s: dict) -> tuple[Decimal, str]:
    mw, low_n, high_n = s["minimum_wage"], s["cas_low_wages"], s["cas_high_wages"]
    low, high = low_n * mw, high_n * mw
    if net < low:
        return ZERO, f"net income is under {number(low_n)} minimum wages ({money(low)}), so no CAS is due"
    if net < high:
        return low, (f"net income is between {number(low_n)} and {number(high_n)} minimum wages, "
                     f"so the base is {number(low_n)} × {money(mw)} = {money(low)}")
    return high, (f"net income is at least {number(high_n)} minimum wages ({money(high)}), "
                  f"so the base is {number(high_n)} × {money(mw)} = {money(high)}")


def _cass(net: Decimal, s: dict) -> tuple[Decimal, str]:
    mw = s["minimum_wage"]
    floor, ceiling = s["cass_floor_wages"] * mw, s["cass_ceiling_wages"] * mw
    base = max(net, ZERO)
    if base > ceiling:
        return ceiling, f"net income is above the ceiling of {number(s['cass_ceiling_wages'])} minimum wages, so the base is capped at {money(ceiling)}"
    if base < floor and not s["has_salary"]:
        return floor, f"net income is under the floor of {number(s['cass_floor_wages'])} minimum wages, so the base is raised to {money(floor)}"
    if base < floor:
        return base, "net income is under the floor, but with a salaried job the floor does not apply, so the base is the net income"
    return base, "the base is the net income (between the floor and the ceiling)"


def compute(year: str, income: Decimal, expenses: Decimal) -> dict:
    with localcontext() as ctx:
        ctx.prec = 60   # even absurd amounts must not overflow
        return _compute(year, income, expenses)


def _compute(year: str, income: Decimal, expenses: Decimal) -> dict:
    net = income - expenses
    res = {"net": net, "cas": ZERO, "cass": ZERO, "cas_base": ZERO, "cass_base": ZERO,
           "taxable": max(net, ZERO), "tax": ZERO, "total_due": ZERO, "cas_note": "", "cass_note": "",
           "tax_exempt_note": None}
    s, src, err = settings.tax_settings(year)
    res.update(settings=s, settings_year=src, settings_error=err)
    if s is None:
        res["tax"] = res["total_due"] = r2(res["taxable"] * FALLBACK_TAX_RATE)
        return res
    res["cas_base"], res["cas_note"] = _cas(net, s)
    res["cas"] = r2(res["cas_base"] * s["cas_rate"])
    res["cass_base"], res["cass_note"] = _cass(net, s)
    res["cass"] = r2(res["cass_base"] * s["cass_rate"])
    res["taxable"] = max(net - res["cas"] - res["cass"], ZERO)
    res["tax_exempt_note"] = caen.exemption(year)
    res["tax"] = ZERO if res["tax_exempt_note"] else r2(res["taxable"] * s["income_tax_rate"])
    res["total_due"] = res["cas"] + res["cass"] + res["tax"]
    return res


def year_summary(year: str, income: Decimal = ZERO, expenses: Decimal = ZERO,
                 n_invoices: int = 0, n_expenses: int = 0) -> dict:
    row = {"year": year, "income": income, "expenses": expenses, "n_invoices": n_invoices, "n_expenses": n_expenses}
    row.update(compute(year, income, expenses))
    return row


def all_years() -> list[dict]:
    income, n_inv = db.totals_by_year("invoices")
    expenses, n_exp = db.totals_by_year("expenses")
    return [year_summary(y, income.get(y, ZERO), expenses.get(y, ZERO), n_inv.get(y, 0), n_exp.get(y, 0))
            for y in sorted(set(income) | set(expenses), reverse=True)]
