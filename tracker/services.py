"""Everything the tracker can do, independent of how it is shown.

Both front ends use only this module: the browser app (web.py) and the desktop app (desktop/).
Each operation returns plain data or an Outcome, so both show the same results and messages.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import attachments, backup, caen, config, db, invoicing, profiles, settings, taxes, theme
from .config import CAEN_PATH, DB_PATH, SETTINGS_PATH
from .invoices import add_invoice as store_invoice
from .invoices import build_invoice, import_invoices
from .parsing import canonical, decode, parse_amount, parse_date
from .rates import convert, load_rates, rates_info

Upload = tuple[str, bytes] | None


@dataclass
class Outcome:
    """What an action did: a message or an error, the year to show next, and the form to show again."""
    ok: bool
    message: str | None = None
    error: str | None = None
    year: str | None = None
    form: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


def today() -> date:
    return date.today()


def start() -> None:
    """Prepares the data files: the profiles (the first time) and the database of the profile in use."""
    profiles.ensure()
    db.init_db()


# Profiles

def profiles_enabled() -> bool:
    return not config.single_file_mode()


def profiles_view() -> dict:
    """The profiles with their firm and how much each holds, and which one is in use."""
    if not profiles_enabled():
        return {"enabled": False, "profiles": [], "current": None}
    current = profiles.current_id()
    rows = []
    for p in profiles.all_profiles():
        with profiles.using(p["id"]):
            db.init_db()
            counts = record_counts()
        firm_info = profiles.firm_of(p["id"])
        rows.append({**p, "firm": str(firm_info.get("name") or ""), "cui": str(firm_info.get("cui") or ""),
                     "invoices": counts.get("invoices", 0), "expenses": counts.get("expenses", 0),
                     "current": p["id"] == current})
    return {"enabled": True, "profiles": rows, "current": current, "current_name": profiles.current()["name"]}


def profile_for_request(profile_id: str | None):
    """A context in which the given profile (if it exists) is in use, without changing the last used one."""
    return profiles.using(profile_id if profiles_enabled() and profiles.exists(profile_id) else None)


def switch_profile(profile_id: str) -> Outcome:
    try:
        profiles.activate(profile_id)
    except profiles.ProfileError as e:
        return Outcome(False, error=f"Profile not changed: {e}.")
    db.init_db()
    return Outcome(True, message=f"Now showing the profile {profiles.current()['name']}.", extra={"profile": profile_id})


def create_profile(name: str) -> Outcome:
    try:
        new_id = profiles.create(name)
        profiles.activate(new_id)
    except profiles.ProfileError as e:
        return Outcome(False, error=f"Profile not created: {e}.")
    db.init_db()
    return Outcome(True, message=f"Profile {profiles.current()['name']} created — fill in its firm below.",
                   extra={"profile": new_id})


def rename_profile(profile_id: str, name: str) -> Outcome:
    try:
        profiles.rename(profile_id, name)
    except profiles.ProfileError as e:
        return Outcome(False, error=f"Profile not renamed: {e}.")
    return Outcome(True, message="Profile renamed.")


def delete_profile(profile_id: str, confirmed: bool) -> Outcome:
    if not confirmed:
        return Outcome(False, error="Profile not deleted: the deletion was not confirmed.")
    try:
        name = next(p["name"] for p in profiles.all_profiles() if p["id"] == profile_id)
        kept = profiles.delete(profile_id)
    except (profiles.ProfileError, StopIteration) as e:
        return Outcome(False, error=f"Profile not deleted: {e or 'there is no such profile'}.")
    return Outcome(True, message=f"Profile {name} deleted. A backup of it was saved in {kept}.")


# Theme

LIGHT_PALETTE = dict(theme.LIGHT)


def theme_view(app: str) -> dict:
    """The theme of one app ("web" or "desktop"; each has its own) and the saved custom themes."""
    name, colors, saved_name = theme.app_theme(app)
    pal = theme.palette(name, colors)
    return {"theme": name, "colors": colors, "saved_name": saved_name, "palette": pal, "themes": theme.THEMES,
            "dark": theme.luminance(pal["background"]) < 0.2, "saved": theme.saved_themes()}


def save_theme(app: str, name: str, colors: dict | None = None) -> Outcome:
    """Uses a theme in one app only."""
    try:
        theme.set_app_theme(app, name, colors)
    except ValueError as e:
        return Outcome(False, error=f"Theme not saved: {e}.", form={"theme": name, "colors": colors or {}})
    return Outcome(True, message=f"Theme set to {name}.")


def save_custom_theme(app: str, name: str, colors: dict) -> Outcome:
    """Saves custom colours under a name (offered in both apps) and uses them in this app."""
    try:
        replaced = theme.save_named(name, colors)
        clean_name = theme.clean_theme_name(name)
        theme.set_app_theme(app, "custom", colors, saved_name=clean_name)
    except ValueError as e:
        return Outcome(False, error=f"Theme not saved: {e}.", form={"theme": "custom", "colors": colors or {},
                                                                  "theme_name": name})
    return Outcome(True, message=f"Custom theme “{clean_name}” {'updated' if replaced else 'saved'} and in use.")


def use_saved_theme(app: str, name: str) -> Outcome:
    found = theme.find_named(name)
    if not found:
        return Outcome(False, error="Theme not changed: there is no saved theme with that name.")
    theme.set_app_theme(app, "custom", found["colors"], saved_name=found["name"])
    return Outcome(True, message=f"Theme “{found['name']}” in use.")


def delete_saved_theme(name: str) -> Outcome:
    try:
        theme.delete_named(name)
    except ValueError as e:
        return Outcome(False, error=f"Theme not deleted: {e}.")
    return Outcome(True, message=f"Saved theme “{name.strip()}” deleted.")


# Years

def available_years(summaries: list[dict] | None = None) -> list[str]:
    """The current year and the years that have invoices or expenses, newest first; never a future year."""
    summaries = taxes.all_years() if summaries is None else summaries
    years = {s["year"] for s in summaries} | {str(today().year)}
    return sorted((y for y in years if len(y) == 4 and y.isascii() and y.isdigit() and int(y) <= today().year),
                  reverse=True)


def resolve_year(wanted: str | None, summaries: list[dict] | None = None) -> str:
    """The wanted year if it can be shown, otherwise the latest year with records (or the current year)."""
    summaries = taxes.all_years() if summaries is None else summaries
    years = available_years(summaries)
    if wanted in years:
        return wanted
    return next((s["year"] for s in summaries if s["year"] in years), years[0])


def year_view(wanted: str | None) -> dict:
    """Everything the Overview, Income and Expenses pages show for a year."""
    summaries = taxes.all_years()
    year = resolve_year(wanted, summaries)
    current = next((s for s in summaries if s["year"] == year), None) or taxes.year_summary(year)
    invoices = db.list_year("invoices", year)
    return {"year": year, "years": available_years(summaries), "summaries": summaries, "current": current,
            "invoices": invoices, "expenses": db.list_year("expenses", year),
            # what the question before deleting each invoice adds (see delete_warning)
            "delete_notes": {r["id"]: delete_warning(r["id"]) for r in invoices if invoicing.is_generated(r)}}


def currencies() -> list[str]:
    return ["RON"] + [c["code"] for c in rates_info()["currencies"] if c["code"] != "RON"]


def firm() -> dict:
    return settings.firm()


# Proofs

def _attach(table: str, row_id: int, upload: Upload) -> str | None:
    """Stores a proof for a row; returns a warning text if it could not be stored."""
    if upload is None:
        return None
    base = None
    if table == "invoices":
        row = db.get(table, row_id)
        base = attachments.invoice_base_name(row["number"], row["client"])
    try:
        db.set_attachment(table, row_id, attachments.save(table, row_id, *upload, base_name=base))
    except ValueError as e:
        return f"saved without the attachment: {e}"
    return None


def proof_name(table: str, stored: str) -> str:
    """The name a proof is shown and saved under."""
    return attachments.download_name(table, stored)


def proof(table: str, row_id: int) -> tuple[Path, str] | None:
    """(file path, name to save it as) of a row's proof, or None."""
    row = db.get(table, row_id) if 1 <= row_id <= 2**63 - 1 else None
    p = attachments.path(table, row["attachment"]) if row else None
    return (p, attachments.download_name(table, row["attachment"])) if p else None


# Income

def import_csv(data: bytes, filename: str) -> Outcome:
    if not data:
        return Outcome(False, error="Choose a CSV file to import.")
    result = import_invoices(decode(data), filename or "upload.csv")
    changed = result["added"] + result["paid"]
    year = changed[0]["income_date"][:4] if changed else None
    return Outcome(not result["fatal"], year=year, extra={"import": result})


def add_invoice(fields: dict, upload: Upload = None) -> Outcome:
    form = {k: str(fields.get(k, "") or "") for k in ("date", "payment_date", "client", "number", "amount", "currency")}
    try:
        if upload:
            attachments.check(*upload)
        outcome, inv_id = store_invoice(date_text=form["date"], payment_text=form["payment_date"], client=form["client"],
                                        number=form["number"], amount_text=form["amount"], currency=form["currency"])
    except ValueError as e:
        return Outcome(False, error=f"Invoice not added: {e}", form=form)
    if outcome == "duplicate":
        return Outcome(False, form=form,
                       error="Invoice not added: the same invoice is already stored. To change it, delete it and add it again.")
    row = db.get("invoices", inv_id)
    warning = None
    if upload and row["attachment"]:
        warning = "it already has a proof, the new file was not stored"
    elif upload:
        warning = _attach("invoices", inv_id, upload)
    text = "Invoice added" if outcome == "added" else "Date of payment added to the invoice already stored"
    return Outcome(True, message=text + (f" ({warning})." if warning else "."),
                   year=db.get("invoices", inv_id)["income_date"][:4])


INVOICE_FIELDS = ("date", "payment_date", "client", "number", "amount", "currency")
EXPENSE_FIELDS = ("date", "description", "category", "amount", "currency")


def _valid_id(row_id) -> bool:
    return isinstance(row_id, int) and 1 <= row_id <= 2**63 - 1


def _amount_for_form(amount: str) -> str:
    """12.345,67 for amounts in cents (as typed in the forms); other amounts exactly as stored."""
    from decimal import Decimal
    from . import formatting
    value = Decimal(amount)
    return formatting.money(value) if value == value.quantize(Decimal("0.01")) else str(value)


def invoice_for_edit(row_id: int) -> dict | None:
    """A stored invoice as the form shows it (amounts in the Romanian format), or None."""
    row = db.get("invoices", row_id) if _valid_id(row_id) else None
    if row is None:
        return None
    issued = invoicing.issued_for_income(row["id"]) if invoicing.is_generated(row) else None
    return {"id": row["id"], "date": row["date"], "payment_date": row["payment_date"], "client": row["client"],
            "number": row["number"], "amount": _amount_for_form(row["amount"]), "currency": row["currency"],
            "attachment": row["attachment"], "generated": invoicing.is_generated(row),
            "credit_note": issued["full_number"] if issued and issued["kind"] == invoicing.CREDIT_NOTE else ""}


def expense_for_edit(row_id: int) -> dict | None:
    row = db.get("expenses", row_id) if _valid_id(row_id) else None
    if row is None:
        return None
    return {"id": row["id"], "date": row["date"], "description": row["description"], "category": row["category"],
            "amount": _amount_for_form(row["amount"]), "currency": row["currency"], "attachment": row["attachment"]}


def _replace_proof(table: str, row, upload: Upload, remove_proof: bool, base: str | None) -> str | None:
    """After an edit: a new proof replaces the old one; remove_proof drops it; otherwise it stays
    (an invoice proof is renamed when its number or client changed). Returns a warning or None."""
    stored = row["attachment"]
    if upload:
        try:
            new = attachments.save(table, row["id"], *upload, base_name=base)
        except ValueError as e:
            return f"the proof was not changed: {e}"
        if stored:
            attachments.remove(table, stored)
        db.set_attachment(table, row["id"], new)
    elif remove_proof and stored:
        attachments.remove(table, stored)
        db.set_attachment(table, row["id"], "")
    elif stored and table == "invoices" and base:
        db.set_attachment(table, row["id"], attachments.rename_invoice_proof(stored, base))
    return None


def update_invoice(row_id: int, fields: dict, upload: Upload = None, remove_proof: bool = False) -> Outcome:
    """Changes a stored invoice. It is checked and converted again exactly like a new one (at the rate of
    its payment date, or of its invoice date)."""
    form = {k: str(fields.get(k, "") or "") for k in INVOICE_FIELDS}
    row = db.get("invoices", row_id) if _valid_id(row_id) else None
    if row is None:
        return Outcome(False, error="Invoice not changed: it no longer exists.")
    try:
        if upload:
            attachments.check(*upload)
        rates, rates_err = load_rates()
        new = build_invoice(rates, rates_err, date_text=form["date"], payment_text=form["payment_date"],
                            client=form["client"], number=form["number"], amount_text=form["amount"],
                            currency=form["currency"], source=row["source"])
    except ValueError as e:
        return Outcome(False, error=f"Invoice not changed: {e}", form={**form, "id": row_id})
    issued = invoicing.issued_for_income(row_id) if invoicing.is_generated(row) else None
    if issued and issued["kind"] == invoicing.CREDIT_NOTE and not new["amount"].startswith("-"):
        return Outcome(False, form={**form, "id": row_id},
                       error=f"Invoice not changed: {issued['full_number']} is a credit note, so its amount must stay "
                             f"negative.")
    if not db.update_invoice(row_id, new):
        return Outcome(False, form={**form, "id": row_id},
                       error="Invoice not changed: another stored invoice already has this date, client, number, amount and currency.")
    base = attachments.invoice_base_name(new["number"], new["client"])
    warning = _replace_proof("invoices", db.get("invoices", row_id), upload, remove_proof, base)
    return Outcome(True, message="Invoice changed" + (f" ({warning})." if warning else "."), year=new["income_date"][:4])


def update_expense(row_id: int, fields: dict, upload: Upload = None, remove_proof: bool = False) -> Outcome:
    form = {k: str(fields.get(k, "") or "") for k in EXPENSE_FIELDS}
    row = db.get("expenses", row_id) if _valid_id(row_id) else None
    if row is None:
        return Outcome(False, error="Expense not changed: it no longer exists.")
    try:
        if upload:
            attachments.check(*upload)
        d = parse_date(form["date"])
        if not form["description"].strip():
            raise ValueError("description is empty")
        value = parse_amount(form["amount"])
        if value <= 0:
            raise ValueError("amount must be positive")
        rates, rates_err = load_rates()
        conv = convert(rates, rates_err, value, form["currency"] or "RON", d)
    except ValueError as e:
        return Outcome(False, error=f"Expense not changed: {e}", form={**form, "id": row_id})
    db.update_expense(row_id, {"date": d.isoformat(), "description": form["description"].strip(),
                               "category": form["category"].strip(), "amount": canonical(value), **conv})
    warning = _replace_proof("expenses", db.get("expenses", row_id), upload, remove_proof, None)
    return Outcome(True, message="Expense changed" + (f" ({warning})." if warning else "."), year=str(d.year))


def entry_year(table: str, row_id: int) -> str | None:
    """The year an invoice (by the date it counts on) or an expense belongs to."""
    row = db.get(table, row_id) if table in ("invoices", "expenses") and _valid_id(row_id) else None
    if row is None:
        return None
    return (row["income_date"] if table == "invoices" else row["date"])[:4]


def delete(table: str, row_id: int) -> Outcome:
    """Deletes an invoice or expense and its proof."""
    valid = 1 <= row_id <= 2**63 - 1
    credit_notes = ((invoicing.issued_for_income(row_id) or {}).get("credit_notes", [])
                    if valid and table == "invoices" else [])
    row = db.delete(table, row_id) if valid else None
    if row is None:
        return Outcome(False, error="Nothing to delete.")
    attachments.remove(table, row["attachment"])
    issued = invoicing.income_deleted(row_id) if table == "invoices" else None
    what = "Invoice" if table == "invoices" else "Expense"
    year = (row["income_date"] if table == "invoices" else row["date"])[:4]
    if issued:
        still = (f" Its credit notes ({', '.join(credit_notes)}) stay in Income." if credit_notes else "")
        return Outcome(True, year=year, message=(
            f"Invoice {issued} deleted from Income. It stays in Invoicing → Issued invoices, marked as deleted "
            f"from Income, and its number is not given out again.{still}"))
    return Outcome(True, message=f"{what} deleted.", year=year)


# Expenses

def add_expense(fields: dict, upload: Upload = None) -> Outcome:
    form = {k: str(fields.get(k, "") or "") for k in ("date", "description", "category", "amount", "currency")}
    try:
        if upload:
            attachments.check(*upload)
        d = parse_date(form["date"])
        if not form["description"].strip():
            raise ValueError("description is empty")
        value = parse_amount(form["amount"])
        if value <= 0:
            raise ValueError("amount must be positive")
        rates, rates_err = load_rates()
        conv = convert(rates, rates_err, value, form["currency"] or "RON", d)
    except ValueError as e:
        return Outcome(False, error=f"Expense not added: {e}", form=form)
    new_id = db.insert_expense({"date": d.isoformat(), "description": form["description"].strip(),
                                "category": form["category"].strip(), "amount": canonical(value), **conv})
    warning = _attach("expenses", new_id, upload)
    return Outcome(True, message="Expense added" + (f" ({warning})." if warning else "."), year=str(d.year))


# Settings

OPTIONAL_FIELDS = (("trade_register", settings.clean_trade_register), ("phone", settings.clean_phone),
                   ("email", settings.clean_email), ("iban", settings.clean_iban), ("address", settings.clean_address))


def record_counts() -> dict:
    try:
        with db.connect() as con:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("invoices", "expenses")}
    except Exception:
        return {}


def settings_view(form: dict | None = None, salary: dict | None = None) -> dict:
    """Everything the Settings page shows; form/salary are the values just submitted, if any."""
    firm_values = form or settings.firm()
    codes, caen_err = caen.load_caen()
    try:
        blocks, settings_err = settings.year_blocks(), None
    except settings.SettingsError as e:
        blocks, settings_err = {}, str(e)
    this_year = str(today().year)
    tax_years = []
    for y in available_years():
        values, src, err = settings.tax_settings(y)
        tax_years.append({"year": y, "tax": values, "source": src, "error": err,
                          "own": isinstance(blocks.get(y), dict) and "minimum_wage" in blocks[y]})
    stored_register = settings.firm()["trade_register"]
    lifetime = settings.lifetime_years(firm_values.get("trade_register", "")) or settings.lifetime_years(stored_register)
    marked = settings.marked_years()
    earlier = sorted(set(lifetime) | {y for y in marked if y < this_year}, reverse=True)
    salary = salary or {y: y in marked for y in [this_year] + earlier}
    rev = firm_values.get("caen_revision") or "3"
    code = (firm_values.get("caen_code") or "").strip()
    rev_codes = codes.get(rev, {})
    return {
        "form": firm_values, "tax_years": tax_years, "settings_err": settings_err,
        "caen": codes, "caen_err": caen_err, "rev": rev,
        "secondary": [{"code": c, "entry": rev_codes.get(c)} for c in firm_values.get("secondary_caen", [])],
        "caen_entry": rev_codes.get(code), "caen_other": codes.get("2" if rev == "3" else "3", {}).get(code),
        "caen_excluded": sorted((c for rc in codes.values() for c in rc.values() if c["excluded"]),
                                key=lambda c: (c["rev"], c["code"])),
        "cui_ok": settings.cui_valid(firm_values.get("cui", "")) if firm_values.get("cui") else None,
        "this_year": this_year, "lifetime": lifetime, "earlier": earlier, "salary": salary,
        "earlier_marked": any(salary.get(y) for y in earlier),
        "registered": settings.registration_year(firm_values.get("trade_register", ""))
        or settings.registration_year(stored_register),
        "rates": rates_info(), "caen_path": str(CAEN_PATH), "settings_path": str(SETTINGS_PATH),
        "db_path": str(DB_PATH), "data_dir": str(DB_PATH.parent), "counts": record_counts(),
    }


def save_settings(values: dict) -> Outcome:
    """Checks and saves the Settings form.

    values: text fields by name, plus "salaried_now" (bool), "salaried_years" (the ticked earlier years)
    and "salaried_shown" (the earlier years that were on screen; years not shown are never changed).
    On failure, Outcome.form and Outcome.extra["salary"] hold what was submitted.
    """
    text = lambda key: str(values.get(key, "") or "")
    firm_values = {
        "name": text("name").strip(),
        "cui": text("cui").strip().upper().replace(" ", ""),
        "caen_code": text("caen_code").strip().replace(".", ""),
        "caen_revision": "2" if text("caen_revision") == "2" else "3",
    }
    problems = []
    if firm_values["cui"] and not settings.cui_valid(firm_values["cui"]):
        problems.append(f"CUI {firm_values['cui']} is not valid (its check digit does not match)")
    if firm_values["caen_code"] and not (firm_values["caen_code"].isdigit() and len(firm_values["caen_code"]) == 4):
        problems.append("the CAEN code must be 4 digits, e.g. 6210")
    if firm_values["cui"] and profiles_enabled():
        # one firm belongs to one profile
        mine = firm_values["cui"].removeprefix("RO")
        for p in profiles.all_profiles():
            other = str(profiles.firm_of(p["id"]).get("cui") or "").upper().removeprefix("RO")
            if p["id"] != profiles.current_id() and other and other == mine:
                problems.append(f"the firm with CUI {firm_values['cui']} already belongs to the profile {p['name']}")
    invalid = set()
    for key, clean in OPTIONAL_FIELDS:
        raw = text(key).strip()
        firm_values[key] = raw
        if raw:
            try:
                firm_values[key] = clean(raw)
            except ValueError as e:
                problems.append(str(e))
                invalid.add(key)
    try:
        firm_values["secondary_caen"] = settings.clean_caen_list(text("secondary_caen"), firm_values["caen_code"])
    except ValueError as e:
        firm_values["secondary_caen"] = [c for c in text("secondary_caen").replace(",", " ").split() if c]
        problems.append(str(e))

    # The salaried-job checkbox has no year: it is about now, so it sets the current year only.
    # Earlier years are ticked in their own list, and only years in which the PFA existed can be
    # newly ticked. Years shown in that list but not ticked were unticked.
    this_year = str(today().year)
    trade_register_ok = "trade_register" not in invalid
    lifetime = settings.lifetime_years(firm_values["trade_register"] if trade_register_ok
                                       else settings.firm()["trade_register"])
    marked = {y for y in settings.marked_years() if y < this_year}
    shown_raw = values.get("salaried_shown", [])
    shown_list = shown_raw.split(",") if isinstance(shown_raw, str) else list(shown_raw)
    shown = set(shown_list) & (set(lifetime) | marked)
    chosen = {str(y) for y in values.get("salaried_years", [])}
    newly_outside = sorted(y for y in chosen - set(lifetime) - marked)
    if newly_outside and trade_register_ok:
        problems.append(f"a salaried job can only be marked for years in which the PFA existed "
                        f"({', '.join(lifetime[::-1]) or 'none yet'}), not {', '.join(newly_outside[:5])}")
    now = values.get("salaried_now")
    salary = {this_year: now is True or now == "1"}
    salary.update({y: y in chosen for y in shown | (chosen & set(lifetime))})
    if problems:
        return Outcome(False, error="Not saved: " + "; ".join(problems) + ".", form=firm_values, extra={"salary": salary})
    try:
        settings.save_firm_and_salary(firm_values, salary)
    except settings.SettingsError as e:
        return Outcome(False, error=f"Not saved: {e}", form=firm_values, extra={"salary": salary})
    return Outcome(True, message="Settings saved.")


# Invoicing: clients, products/services, series, and issuing an invoice that goes straight to Income

GENERATED_NOTE = ("This invoice was issued in Invoicing. Changes here change Income only: its PDF stays as it was "
                  "issued.")
# added to the question before deleting such an invoice from Income
GENERATED_DELETE_NOTE = ("It was issued in Invoicing: it will stay in the list of issued invoices, marked as deleted "
                         "from Income, and its number will not be given out again.")


def invoicing_view() -> dict:
    """Everything the Invoicing tab shows: the three lists, VAT settings, the issued invoices, and what the
    New invoice form starts with."""
    firm_info = settings.firm()
    conf = invoicing.get_settings()
    series = invoicing.list_series()
    products = [{**p, "unit_label": invoicing.UNITS.get(p["unit"], p["unit"]),
                 "price_display": invoicing._fmt_num(p["unit_price"])} for p in invoicing.list_products()]
    clients = [{**c, "country_name": invoicing.COUNTRIES.get(c["country"], c["country"])}
               for c in invoicing.list_clients()]
    issued = [{**i, "total_display": invoicing._fmt_num(i["total"]), "credit_info": credit_info(i)}
              for i in invoicing.list_issued()]
    return {
        "clients": clients, "products": products, "series": series, "settings": conf, "issued": issued,
        "units": dict(invoicing.UNITS), "countries": dict(invoicing.COUNTRIES), "currencies": currencies(),
        "firm": firm_info, "firm_problems": invoicing.seller_problems(firm_info),
        "default_series": next((s["id"] for s in series if s["is_default"]), None),
        "today": today().isoformat(),
        "default_due": (today() + timedelta(days=conf["due_days"])).isoformat() if conf["due_days"] else "",
        "max_lines": invoicing.MAX_LINES,
    }


def _id_or_none(value) -> int | None:
    return invoicing._as_id(value)


def save_client(fields: dict, client_id=None) -> Outcome:
    """Adds a client (client_id None) or changes one. On failure Outcome.form holds what was submitted."""
    form = {k: str(fields.get(k, "") or "") for k in invoicing.CLIENT_FIELDS}
    cid = None if client_id in (None, "") else _id_or_none(client_id)
    if client_id not in (None, "") and cid is None:
        return Outcome(False, error="Client not saved: it no longer exists.", form=form)
    try:
        new_id, v = invoicing.save_client(form, cid)
    except ValueError as e:
        return Outcome(False, error=f"Client not saved: {e}.", form={**form, "id": cid} if cid else form)
    return Outcome(True, message=f"Client {v['name']} {'changed' if cid else 'added'}.", extra={"id": new_id})


def client_for_edit(client_id) -> dict | None:
    return invoicing.get_client(_id_or_none(client_id))


def delete_client(client_id) -> Outcome:
    row = invoicing.delete_client(_id_or_none(client_id))
    if row is None:
        return Outcome(False, error="Nothing to delete.")
    return Outcome(True, message=f"Client {row['name']} deleted (invoices already issued to it keep its details).")


def save_product(fields: dict, product_id=None) -> Outcome:
    """Adds a product or service (product_id None) or changes one."""
    form = {k: str(fields.get(k, "") or "") for k in invoicing.PRODUCT_FIELDS}
    pid = None if product_id in (None, "") else _id_or_none(product_id)
    if product_id not in (None, "") and pid is None:
        return Outcome(False, error="Product not saved: it no longer exists.", form=form)
    try:
        new_id, v = invoicing.save_product(form, pid)
    except ValueError as e:
        return Outcome(False, error=f"Product not saved: {e}.", form={**form, "id": pid} if pid else form)
    return Outcome(True, message=f"{v['name']} {'changed' if pid else 'added'}.", extra={"id": new_id})


def product_for_edit(product_id) -> dict | None:
    row = invoicing.get_product(_id_or_none(product_id))
    return {**row, "unit_price": _amount_for_form(row["unit_price"])} if row else None


def delete_product(product_id) -> Outcome:
    row = invoicing.delete_product(_id_or_none(product_id))
    if row is None:
        return Outcome(False, error="Nothing to delete.")
    return Outcome(True, message=f"{row['name']} deleted (invoices already issued keep it).")


def save_series(fields: dict, series_id=None) -> Outcome:
    """Adds a series (series_id None) or changes one: name, next_number, is_default."""
    form = {"name": str(fields.get("name", "") or ""), "next_number": str(fields.get("next_number", "") or ""),
            "is_default": fields.get("is_default") in (True, "1", "on", "true", 1)}
    sid = None if series_id in (None, "") else _id_or_none(series_id)
    if series_id not in (None, "") and sid is None:
        return Outcome(False, error="Series not saved: it no longer exists.", form=form)
    try:
        new_id, v = invoicing.save_series(form, sid)
    except ValueError as e:
        return Outcome(False, error=f"Series not saved: {e}.", form={**form, "id": sid} if sid else form)
    return Outcome(True, message=f"Series {v['name']} {'changed' if sid else 'added'}: the next invoice will be "
                                 f"{invoicing.format_number(v['name'], v['next_number'])}.", extra={"id": new_id})


def series_for_edit(series_id) -> dict | None:
    return invoicing.get_series(_id_or_none(series_id))


def delete_series(series_id) -> Outcome:
    row = invoicing.delete_series(_id_or_none(series_id))
    if row is None:
        return Outcome(False, error="Nothing to delete.")
    return Outcome(True, message=f"Series {row['name']} deleted (invoices already issued in it stay).")


def save_invoicing_settings(fields: dict) -> Outcome:
    """VAT payer or not, the VAT rate, and the usual payment term in days."""
    form = {"vat_payer": fields.get("vat_payer") in (True, "1", "on", "true", 1),
            "vat_rate": str(fields.get("vat_rate", "") or ""), "due_days": str(fields.get("due_days", "") or "")}
    try:
        invoicing.save_settings(form)
    except ValueError as e:
        return Outcome(False, error=f"Not saved: {e}.", form=form)
    return Outcome(True, message="Invoice settings saved.")


def _invoice_form(fields: dict) -> dict:
    lines = [{k: str(line.get(k, "") or "") for k in ("product_id", "quantity", "price")}
             for line in (fields.get("lines") or []) if isinstance(line, dict)]
    form = {k: str(fields.get(k, "") or "") for k in ("series_id", "client_id", "date", "due_date", "payment_date", "notes",
                                                      "token")}
    return {**form, "lines": lines}


def new_invoice_token() -> str:
    """A token for one New invoice form (put it in the form as "token"): if the same form is sent twice
    (a double click, a page reload), the invoice is issued only once."""
    return invoicing.new_token()


def invoice_totals(fields: dict) -> dict:
    """The totals of an invoice being filled in, and what still stops it from being issued (nothing is saved).

    Returns {"ok" (True = it can be issued as it is), "error" (the problems as one sentence, or None),
    "currency", "lines" (the valid lines so far, each with value_display), "subtotal", "vat" ("" when not a
    VAT payer), "vat_rate", "total" (display text, Romanian format, over the valid lines), "next_number"}.
    When the lines are in different currencies there is no total: currency, subtotal, vat and total are ""."""
    form = _invoice_form(fields)
    series = invoicing.get_series(_id_or_none(form["series_id"]))
    p = invoicing.preview(form)
    f = invoicing._fmt_num
    mixed = len({line["currency"] for line in p["lines"]}) > 1     # no total across currencies
    return {"ok": not p["problems"], "error": "; ".join(p["problems"]) or None,
            "currency": "" if mixed else p["currency"], "next_number": series["next"] if series else "",
            "lines": [{**line, "value_display": f(line["value"])} for line in p["lines"]],
            "subtotal": "" if mixed else f(p["subtotal"]), "vat": f(p["vat"]) if p["vat_payer"] and not mixed else "",
            "vat_rate": p["vat_rate"], "total": "" if mixed else f(p["total"])}


def generate_invoice(fields: dict) -> Outcome:
    """Issues an invoice and adds it to Income with its PDF as proof.

    fields: series_id, client_id, date, due_date (optional), payment_date (optional), notes (optional),
    lines: a list of {"product_id", "quantity", "price"} (price empty = the product's price),
    token (optional but strongly advised, from new_invoice_token(): a form sent twice issues once; the second
    time the Outcome is not ok and extra has already_issued=True, issued_id and number).
    Outcome.extra: issued_id, income_id, number. On failure Outcome.form holds what was submitted."""
    form = _invoice_form(fields)
    try:
        inv = invoicing.issue(form)
    except invoicing.AlreadyIssued as e:
        return Outcome(False, error=f"Not issued again: this form was already used to issue invoice {e.full_number} "
                                    f"(it was sent twice). Find it under Issued invoices.",
                       extra={"issued_id": e.issued_id, "number": e.full_number, "already_issued": True})
    except ValueError as e:
        return Outcome(False, error=f"Invoice not issued: {e}.", form=form)
    year = inv["income_date"][:4]
    vat_note = f" (without VAT: {invoicing._fmt_num(inv['subtotal'])} {inv['currency']})" if inv["vat_payer"] else ""
    return Outcome(True, year=year, extra={"issued_id": inv["id"], "income_id": inv["income_id"],
                                           "number": inv["full_number"]},
                   message=(f"Invoice {inv['full_number']} issued to {inv['client']['name']} for "
                            f"{invoicing._fmt_num(inv['total'])} {inv['currency']} and added to Income for {year}"
                            f"{vat_note}. Its PDF is the invoice's proof."))


def issued_pdf(issued_id) -> tuple[str, bytes] | None:
    """(file name, PDF bytes) of an issued invoice, printed again from what was recorded when it was issued."""
    return invoicing.issued_pdf(_id_or_none(issued_id))


# Credit notes: reversing all or some of an issued invoice, by quantity, straight into Income as a negative amount

CREDIT_NOTE_EDIT_NOTE = "It is a credit note, so its amount must stay negative."


def credit_info(issued: dict) -> str:
    """What the Issued invoices list says about credit notes for one row (from invoicing_view()["issued"])."""
    if issued.get("kind") == invoicing.CREDIT_NOTE:
        return f"Credit note reversing {issued['credits_number']}" if issued.get("credits_number") else "Credit note"
    if issued.get("credited"):
        return f"{issued['credited'].capitalize()} credited by {', '.join(issued['credit_notes'])}"
    return ""


def credit_note_view(issued_id) -> dict | None:
    """Everything the credit note form of one issued invoice shows, or None when there is no such invoice.

    Returns the invoice's id, full_number, date, client, currency, vat_payer, vat_rate, total_display;
    "lines", each with its position ("line"), name, unit_label and, for showing, price_display,
    quantity_display (invoiced), credited_display, left_display (what is left to credit, written so that it
    can be typed back) and left_quantity; "credited_by" (its credit notes); "series" (to choose from) and
    "own_series" (the id of the invoice's own series, if it is still in the list); "today";
    "firm_problems"; and "problem": why it cannot be credited, or None."""
    status = invoicing.credit_status(_id_or_none(issued_id))
    if status is None:
        return None
    f = invoicing._fmt_num
    issued = invoicing.get_issued(status["id"]) or {}
    series = invoicing.list_series()
    lines = [{"line": line["line"], "name": line["name"], "unit_label": line["unit_label"],
              "price_display": f(line["price"]), "quantity_display": f(line["quantity"], 0),
              "credited_display": f(line["credited_quantity"], 0), "left_display": f(line["left_quantity"], 0),
              "left_quantity": line["left_quantity"]} for line in status["lines"]]
    firm_info = settings.firm()
    return {"id": status["id"], "full_number": status["full_number"], "date": status["date"],
            "client": status["client"], "currency": status["currency"], "vat_payer": status["vat_payer"],
            "vat_rate": status["vat_rate"], "total_display": f(issued["total"]) if issued.get("total") else "",
            "lines": lines, "credited_by": status["credited_by"], "problem": status["problem"],
            "series": series, "own_series": next((s["id"] for s in series if s["name"] == status["series"]), None),
            "today": today().isoformat(), "firm_problems": invoicing.seller_problems(firm_info)}


def start_credit_note(issued_id) -> Outcome:
    """Opens the credit note form of an issued invoice. Outcome.extra: "view" (see credit_note_view) and "form",
    what the form starts with: the invoice's own series, today, no refund date, no reason, no quantities, and a
    token (a form sent twice issues one credit note). Not ok when the invoice cannot be credited, saying why."""
    view = credit_note_view(issued_id)
    if view is None:
        return Outcome(False, error="No credit note can be issued: that invoice does not exist (any more).")
    if view["problem"]:
        return Outcome(False, error=f"No credit note can be issued: {view['problem']}.")
    form = {"issued_id": str(view["id"]), "series_id": str(view["own_series"] or ""), "date": view["today"],
            "payment_date": "", "reason": "", "token": invoicing.new_token(),
            "lines": [{"line": str(line["line"]), "quantity": ""} for line in view["lines"]]}
    return Outcome(True, extra={"view": view, "form": form})


def credit_all(view: dict) -> list[dict]:
    """The form's lines with every quantity set to what is left to credit (the "Credit all" button)."""
    return [{"line": str(line["line"]), "quantity": line["left_display"] if line["left_quantity"] != "0" else ""}
            for line in view["lines"]]


def _credit_form(fields: dict) -> dict:
    lines = [{"line": str(line.get("line", "") or ""), "quantity": str(line.get("quantity", "") or "")}
             for line in (fields.get("lines") or []) if isinstance(line, dict)]
    form = {k: str(fields.get(k, "") or "") for k in ("issued_id", "series_id", "date", "payment_date", "reason", "token")}
    return {**form, "lines": lines}


def credit_note_totals(fields: dict) -> dict:
    """The totals of a credit note being filled in, and what still stops it from being issued (nothing is saved).

    fields: issued_id, series_id, date, payment_date (the refund date, optional), reason (optional), and lines,
    a list of {"line" (the invoice line's position), "quantity" (how much of it to credit; empty = none)}.
    Returns {"ok", "error" (the problems as one sentence, or None), "currency", "lines" (the lines credited so
    far, each with "line" as on the invoice and value_display), "subtotal", "vat" ("" when the invoice has no
    VAT), "vat_rate", "total" (display text, negative), "next_number" (of the chosen series)}."""
    form = _credit_form(fields)
    p = invoicing.credit_preview(form)
    status = invoicing.credit_status(_id_or_none(form["issued_id"]))
    if form["series_id"]:
        series = invoicing.get_series(_id_or_none(form["series_id"]))
    else:
        series = next((s for s in invoicing.list_series() if status and s["name"] == status["series"]), None)
    f = invoicing._fmt_num
    return {"ok": not p["problems"], "error": "; ".join(p["problems"]) or None, "currency": p["currency"],
            "next_number": series["next"] if series else "",
            "lines": [{"line": line["orig_line"], "quantity": line["quantity"], "value_display": f(line["value"])}
                      for line in p["lines"]],
            "subtotal": f(p["subtotal"]), "vat": f(p["vat"]) if p["vat_payer"] else "", "vat_rate": p["vat_rate"],
            "total": f(p["total"])}


def generate_credit_note(fields: dict) -> Outcome:
    """Issues a credit note for an issued invoice and adds it to Income as a negative amount without VAT, with
    its PDF as proof. fields: as for credit_note_totals, plus token (from start_credit_note: a form sent twice
    issues once; the second time the Outcome is not ok and extra has already_issued=True, issued_id, number).
    Outcome.extra: issued_id, income_id, number, credits (the invoice's number). On failure Outcome.form holds
    what was submitted."""
    form = _credit_form(fields)
    try:
        cn = invoicing.issue_credit_note(form)
    except invoicing.AlreadyIssued as e:
        return Outcome(False, error=f"Not issued again: this form was already used to issue "
                                    f"{invoicing.KIND_NAMES.get(e.kind, 'invoice')} {e.full_number} (it was sent twice). "
                                    f"Find it under Issued invoices.",
                       extra={"issued_id": e.issued_id, "number": e.full_number, "already_issued": True})
    except ValueError as e:
        return Outcome(False, error=f"Credit note not issued: {e}.", form=form)
    year = cn["income_date"][:4]
    f = invoicing._fmt_num
    vat_note = f" (without VAT: {f(cn['subtotal'])} {cn['currency']})" if cn["vat_payer"] else ""
    return Outcome(True, year=year, extra={"issued_id": cn["id"], "income_id": cn["income_id"],
                                           "number": cn["full_number"], "credits": cn["credits"]["full_number"]},
                   message=(f"Credit note {cn['full_number']} issued to {cn['client']['name']} for {f(cn['total'])} "
                            f"{cn['currency']}, reversing invoice {cn['credits']['full_number']}, and added to Income "
                            f"for {year}{vat_note}. Its PDF is the credit note's proof."))


def delete_warning(row_id) -> str:
    """What the question before deleting an Income row adds to "Delete this invoice?": for an invoice issued in
    Invoicing, that it stays an issued invoice; if it has credit notes, that they stay in Income; "" otherwise."""
    issued = invoicing.issued_for_income(row_id) if _valid_id(row_id) else None
    if issued is None:
        return ""
    note = GENERATED_DELETE_NOTE
    if issued["credit_notes"]:
        note += (f" It has credit notes ({', '.join(issued['credit_notes'])}): they stay in Income, so their negative "
                 f"amounts will count without this invoice.")
    return note


# Backup

def export_backup() -> tuple[str, bytes]:
    return backup.export_bundle()


def restore_backup(data: bytes, confirmed: bool) -> Outcome:
    if not confirmed:
        return Outcome(False, error="Not restored: tick the box to confirm that everything here will be replaced.")
    try:
        manifest = backup.restore_bundle(data)
    except backup.BackupError as e:
        return Outcome(False, error=f"Not restored: {e}.")
    firm_info = manifest.get("firm") or {}
    who = " · ".join(v for v in (firm_info.get("name"), firm_info.get("cui")) if v) or "an unnamed firm"
    n = manifest["restored_counts"]
    return Outcome(True, message=(f"Backup restored: {who}, made {manifest.get('created', '?')[:16].replace('T', ' ')} — "
                                  f"{n['invoices']} invoice(s), {n['expenses']} expense(s), {n['attachments']} proof(s). "
                                  f"The previous state was saved in {manifest['safety_copy']}."))
