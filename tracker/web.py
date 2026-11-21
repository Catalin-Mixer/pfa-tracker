"""The browser front end: FastAPI pages over tracker.services."""
import json
import re
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, Path, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import attachments, formatting, services
from .config import SETTINGS_PATH, STATIC_DIR, TEMPLATES_DIR

services.start()
app = FastAPI(title="PFA tax tracker", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters.update(money=formatting.money, num=formatting.number, pct=formatting.percent,
                             rate=formatting.rate)
templates.env.globals["download_name"] = attachments.download_name
templates.env.globals["generated_note"] = services.GENERATED_NOTE
templates.env.globals["credit_note_edit_note"] = services.CREDIT_NOTE_EDIT_NOTE

# The selected year and one-off messages travel in cookies, never in the address, so the
# address of every page is fixed ("/", "/income", "/expenses", "/settings") and cannot be
# edited to reach another year.
YEAR_COOKIE = "pfa_year"
PROFILE_COOKIE = "pfa_profile"
FLASH_COOKIE = "pfa_flash"
PAGES = {"overview": "/", "income": "/income", "expenses": "/expenses", "settings": "/settings",
         "invoicing": "/invoicing", "invoicing_issued": "/invoicing/issued", "invoicing_clients": "/invoicing/clients",
         "invoicing_products": "/invoicing/products", "invoicing_series": "/invoicing/series"}
ISSUED_COOKIE = "pfa_issued"        # the invoice just issued, shown once on the New invoice page


def go(page: str, message: str | None = None, error: str | None = None, year: str | None = None) -> RedirectResponse:
    """Redirects to a page, optionally selecting a year and leaving a message for it."""
    response = RedirectResponse(PAGES.get(page, "/"), status_code=303)
    if year:
        response.set_cookie(YEAR_COOKIE, year, httponly=True, samesite="strict")
    if message or error:
        flash = quote(json.dumps({"message": message, "error": error}))
        response.set_cookie(FLASH_COOKIE, flash, httponly=True, samesite="strict")
    return response


def go_after(page: str, outcome: services.Outcome) -> RedirectResponse:
    return go(page, message=outcome.message, error=outcome.error, year=outcome.year)


@app.middleware("http")
async def no_query_strings(request: Request, call_next):
    """Pages take nothing from the address: any ?... is dropped with a note."""
    if request.method == "GET" and request.url.path in PAGES.values() and request.url.query:
        page = next(k for k, v in PAGES.items() if v == request.url.path)
        return go(page, error="Addresses with extra parts are not used. Choose the year with the year picker.")
    return await call_next(request)


@app.middleware("http")
async def profile_in_use(request: Request, call_next):
    """Each browser keeps its own profile (a cookie); without one, the last profile used is shown."""
    with services.profile_for_request(request.cookies.get(PROFILE_COOKIE)):
        return await call_next(request)


def render(request: Request, template: str, page: str, **ctx) -> HTMLResponse:
    flash = {}
    if FLASH_COOKIE in request.cookies:
        try:
            flash = json.loads(unquote(request.cookies[FLASH_COOKIE]))
        except ValueError:
            flash = {}
        flash = flash if isinstance(flash, dict) else {}
    ctx["message"] = ctx.get("message") or (flash.get("message") if isinstance(flash.get("message"), str) else None)
    ctx["error"] = ctx.get("error") or (flash.get("error") if isinstance(flash.get("error"), str) else None)
    look = services.theme_view("web")
    response = templates.TemplateResponse(request, template, {
        "page": page, "firm": services.firm(), "today": services.today().isoformat(),
        "palette": look["palette"], "palette_dark": look["dark"],
        "profiles": services.profiles_view(), **ctx})
    if FLASH_COOKIE in request.cookies:
        response.delete_cookie(FLASH_COOKIE)
    return response


def year_page(request: Request, template: str, page: str, force_year: str | None = None, **ctx) -> HTMLResponse:
    view = services.year_view(force_year if force_year in services.available_years() else request.cookies.get(YEAR_COOKIE))
    return render(request, template, page, **view, **ctx)


@app.post("/year")
def choose_year(year: str = Form(""), page: str = Form("overview")):
    page = page if page in PAGES else "overview"
    if year not in services.available_years():
        return go(page, error="That year is not available.")
    return go(page, year=year)


async def read_upload(upload: UploadFile | None) -> services.Upload:
    if upload is None or not upload.filename:
        return None
    return upload.filename, await upload.read()


def serve_proof(table: str, row_id: int):
    found = services.proof(table, row_id)
    if found is None:
        return HTMLResponse("No such attachment.", status_code=404)
    return FileResponse(found[0], filename=found[1])


def row_id():
    return Path(ge=1, le=2**63 - 1)


# Overview

@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    return year_page(request, "overview.html", "overview", settings_path=str(SETTINGS_PATH))


# Income

@app.get("/income", response_class=HTMLResponse)
def income(request: Request):
    return year_page(request, "income.html", "income", form={})


@app.post("/income/import", response_class=HTMLResponse)
async def income_import(request: Request, file: UploadFile = File(...)):
    outcome = services.import_csv(await file.read(), file.filename or "upload.csv")
    response = year_page(request, "income.html", "income", error=outcome.error, imp=outcome.extra.get("import"),
                         form={}, force_year=outcome.year)
    if outcome.year:
        response.set_cookie(YEAR_COOKIE, outcome.year, httponly=True, samesite="strict")
    return response


@app.post("/income/add", response_class=HTMLResponse)
async def income_add(request: Request, date_: str = Form("", alias="date"), payment_date: str = Form(""),
                     client: str = Form(""), number: str = Form(""), amount: str = Form(""),
                     currency: str = Form(""), attachment: UploadFile | None = File(None)):
    fields = {"date": date_, "payment_date": payment_date, "client": client, "number": number, "amount": amount,
              "currency": currency}
    outcome = services.add_invoice(fields, await read_upload(attachment))
    if not outcome.ok:
        return year_page(request, "income.html", "income", error=outcome.error, form=outcome.form)
    return go_after("income", outcome)


@app.get("/income/{invoice_id}/edit", response_class=HTMLResponse)
def income_edit_form(request: Request, invoice_id: int = row_id()):
    editing = services.invoice_for_edit(invoice_id)
    if editing is None:
        return go("income", error="That invoice does not exist (any more).")
    year = db_year("invoices", invoice_id)
    return year_page(request, "income.html", "income", form=editing, editing=editing, force_year=year)


@app.post("/income/{invoice_id}/edit", response_class=HTMLResponse)
async def income_edit(request: Request, invoice_id: int = row_id(), date_: str = Form("", alias="date"),
                      payment_date: str = Form(""), client: str = Form(""), number: str = Form(""),
                      amount: str = Form(""), currency: str = Form(""), remove_proof: str = Form(""),
                      attachment: UploadFile | None = File(None)):
    fields = {"date": date_, "payment_date": payment_date, "client": client, "number": number, "amount": amount,
              "currency": currency}
    outcome = services.update_invoice(invoice_id, fields, await read_upload(attachment), remove_proof == "1")
    if not outcome.ok:
        editing = services.invoice_for_edit(invoice_id)
        if editing is None:
            return go("income", error=outcome.error)
        return year_page(request, "income.html", "income", error=outcome.error, form={**editing, **fields},
                         editing=editing, force_year=db_year("invoices", invoice_id))
    return go_after("income", outcome)


def db_year(table: str, row_id: int) -> str | None:
    return services.entry_year(table, row_id)


@app.post("/income/{invoice_id}/delete")
def income_delete(invoice_id: int = row_id()):
    return go_after("income", services.delete("invoices", invoice_id))


@app.get("/income/{invoice_id}/attachment")
def income_attachment(invoice_id: int = row_id()):
    return serve_proof("invoices", invoice_id)


# Expenses

def expenses_page(request: Request, **ctx) -> HTMLResponse:
    ctx.setdefault("form", {})
    return year_page(request, "expenses.html", "expenses", currencies=services.currencies(), **ctx)


@app.get("/expenses", response_class=HTMLResponse)
def expenses(request: Request):
    return expenses_page(request)


@app.post("/expenses", response_class=HTMLResponse)
async def expenses_add(request: Request, date_: str = Form("", alias="date"), description: str = Form(""),
                       category: str = Form(""), amount: str = Form(""), currency: str = Form("RON"),
                       attachment: UploadFile | None = File(None)):
    fields = {"date": date_, "description": description, "category": category, "amount": amount, "currency": currency}
    outcome = services.add_expense(fields, await read_upload(attachment))
    if not outcome.ok:
        return expenses_page(request, error=outcome.error, form=outcome.form)
    return go_after("expenses", outcome)


@app.get("/expenses/{expense_id}/edit", response_class=HTMLResponse)
def expenses_edit_form(request: Request, expense_id: int = row_id()):
    editing = services.expense_for_edit(expense_id)
    if editing is None:
        return go("expenses", error="That expense does not exist (any more).")
    return expenses_page(request, form=editing, editing=editing, force_year=db_year("expenses", expense_id))


@app.post("/expenses/{expense_id}/edit", response_class=HTMLResponse)
async def expenses_edit(request: Request, expense_id: int = row_id(), date_: str = Form("", alias="date"),
                        description: str = Form(""), category: str = Form(""), amount: str = Form(""),
                        currency: str = Form("RON"), remove_proof: str = Form(""),
                        attachment: UploadFile | None = File(None)):
    fields = {"date": date_, "description": description, "category": category, "amount": amount, "currency": currency}
    outcome = services.update_expense(expense_id, fields, await read_upload(attachment), remove_proof == "1")
    if not outcome.ok:
        editing = services.expense_for_edit(expense_id)
        if editing is None:
            return go("expenses", error=outcome.error)
        return expenses_page(request, error=outcome.error, form={**editing, **fields}, editing=editing,
                             force_year=db_year("expenses", expense_id))
    return go_after("expenses", outcome)


@app.post("/expenses/{expense_id}/delete")
def expenses_delete(expense_id: int = row_id()):
    return go_after("expenses", services.delete("expenses", expense_id))


@app.get("/expenses/{expense_id}/attachment")
def expenses_attachment(expense_id: int = row_id()):
    return serve_proof("expenses", expense_id)


# Settings

def settings_page(request: Request, form: dict | None = None, salary: dict | None = None, theme_form: dict | None = None,
                  **ctx) -> HTMLResponse:
    look = services.theme_view("web")
    if theme_form:
        look = {**look, "theme": theme_form.get("theme", look["theme"]), "theme_name": theme_form.get("theme_name", ""),
                "colors": {**look["colors"], **{k: v for k, v in theme_form.get("colors", {}).items() if v}}}
    return render(request, "settings.html", "settings", **services.settings_view(form, salary), look=look, **ctx)


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    return settings_page(request)


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    form = await request.form()
    values = {k: str(form.get(k, "") or "") for k in form.keys() if k not in ("salaried_years", "salaried_shown")}
    values["salaried_years"] = form.getlist("salaried_years")
    values["salaried_shown"] = str(form.get("salaried_shown", "") or "")
    outcome = services.save_settings(values)
    if not outcome.ok:
        return settings_page(request, form=outcome.form, salary=outcome.extra.get("salary"), error=outcome.error)
    return go_after("settings", outcome)


# Invoicing: New invoice, Issued invoices, Clients, Products & services, Series & VAT

CLIENT_FORM = ("name", "tax_id", "reg_number", "address", "city", "country", "email", "phone")
PRODUCT_FORM = ("name", "description", "unit", "unit_price", "currency")
INVOICE_FORM = ("series_id", "client_id", "date", "due_date", "payment_date", "notes", "token")
LINE_FIELD = re.compile(r"(product_id|quantity|price)_(\d{1,3})")
FIRST_ROWS = 1          # line rows the New invoice form starts with; "Add a line" adds one more each time


def text_fields(form, names) -> dict:
    """The named form fields as text (a file sent in their place counts as empty)."""
    return {k: v if isinstance(v := form.get(k, ""), str) else "" for k in names}


def invoice_fields(form) -> tuple[dict, int]:
    """The New invoice form as services expects it, and how many line rows it had.

    Line rows are named product_id_N, quantity_N and price_N; they are kept in the order of N."""
    rows: dict[int, dict] = {}
    for key, value in form.multi_items():
        found = LINE_FIELD.fullmatch(key)
        if found and isinstance(value, str):
            rows.setdefault(int(found[2]), {"product_id": "", "quantity": "", "price": ""})[found[1]] = value
    return {**text_fields(form, INVOICE_FORM), "lines": [rows[n] for n in sorted(rows)]}, len(rows)


def invoicing_render(request: Request, template: str, section: str, **ctx) -> HTMLResponse:
    return render(request, template, "invoicing", section=section, inv=services.invoicing_view(), **ctx)


def new_invoice_page(request: Request, form: dict | None = None, rows: int = 0, view: dict | None = None,
                     **ctx) -> HTMLResponse:
    view = view or services.invoicing_view()
    if form is None:
        form = {"series_id": str(view["default_series"] or ""), "date": view["today"], "due_date": view["default_due"],
                "token": services.new_invoice_token()}      # a form sent twice issues one invoice
    lines = list(form.get("lines") or [])
    while lines and not any(str(v).strip() for v in lines[-1].values()):
        lines.pop()
    count = max(min(view["max_lines"], max(rows, FIRST_ROWS)), len(lines))
    lines += [{"product_id": "", "quantity": "", "price": ""} for _ in range(count - len(lines))]
    form = {**form, "lines": lines}
    return render(request, "invoicing_new.html", "invoicing", section="new", inv=view, form=form,
                  totals=services.invoice_totals(form), **ctx)


@app.get("/invoicing", response_class=HTMLResponse)
def invoicing(request: Request):
    view = services.invoicing_view()
    wanted = request.cookies.get(ISSUED_COOKIE, "")
    issued = next((i for i in view["issued"] if str(i["id"]) == wanted), None)
    response = new_invoice_page(request, view=view, issued=issued)
    if ISSUED_COOKIE in request.cookies:
        response.delete_cookie(ISSUED_COOKIE)
    return response


@app.post("/invoicing/issue", response_class=HTMLResponse)
async def invoicing_issue(request: Request):
    """Issue (the main button); "more" adds line rows and "totals" works the totals out (both for
    browsers without JavaScript); neither of those saves anything."""
    form = await request.form()
    fields, rows = invoice_fields(form)
    action = form.get("action", "issue")
    if action == "more":
        return new_invoice_page(request, form=fields, rows=rows + 1)
    if action == "totals":
        return new_invoice_page(request, form=fields, rows=rows)
    outcome = services.generate_invoice(fields)
    if outcome.extra.get("already_issued"):
        response = go("invoicing", error=outcome.error)
        response.set_cookie(ISSUED_COOKIE, str(outcome.extra["issued_id"]), httponly=True, samesite="strict")
        return response
    if not outcome.ok:
        return new_invoice_page(request, form={**fields, **outcome.form}, rows=rows, error=outcome.error)
    response = go_after("invoicing", outcome)
    response.set_cookie(ISSUED_COOKIE, str(outcome.extra["issued_id"]), httponly=True, samesite="strict")
    return response


@app.post("/invoicing/totals")
async def invoicing_totals(request: Request):
    """The live totals of the New invoice form (nothing is saved)."""
    fields, _ = invoice_fields(await request.form())
    return services.invoice_totals(fields)


@app.get("/invoicing/issued", response_class=HTMLResponse)
def invoicing_issued(request: Request):
    view = services.invoicing_view()
    wanted = request.cookies.get(ISSUED_COOKIE, "")
    issued = next((i for i in view["issued"] if str(i["id"]) == wanted), None)     # a credit note just issued
    response = render(request, "invoicing_issued.html", "invoicing", section="issued", inv=view, issued=issued)
    if ISSUED_COOKIE in request.cookies:
        response.delete_cookie(ISSUED_COOKIE)
    return response


# Credit notes: from a row of Issued invoices

CREDIT_FORM = ("series_id", "date", "payment_date", "reason", "token")
CREDIT_LINE = re.compile(r"quantity_(\d{1,3})")


def credit_fields(form, issued_id: int) -> dict:
    """The credit note form as services expects it: quantity_N is how much of the invoice's line N to credit.
    The invoice is the one in the address, never one named in the form."""
    lines = []
    for key, value in form.multi_items():
        found = CREDIT_LINE.fullmatch(key)
        if found and isinstance(value, str):
            lines.append({"line": found[1], "quantity": value})
    return {**text_fields(form, CREDIT_FORM), "issued_id": str(issued_id), "lines": lines}


def credit_page(request: Request, view: dict, form: dict, **ctx) -> HTMLResponse:
    quantities = {str(line["line"]): line["quantity"] for line in form.get("lines", [])}
    return render(request, "invoicing_credit.html", "invoicing", section="issued", inv=services.invoicing_view(),
                  cn=view, form=form, quantities=quantities, totals=services.credit_note_totals(form), **ctx)


@app.get("/invoicing/issued/{issued_id}/credit", response_class=HTMLResponse)
def credit_note_form(request: Request, issued_id: int = row_id()):
    outcome = services.start_credit_note(issued_id)
    if not outcome.ok:
        return go("invoicing_issued", error=outcome.error)
    return credit_page(request, outcome.extra["view"], outcome.extra["form"])


@app.post("/invoicing/issued/{issued_id}/credit", response_class=HTMLResponse)
async def credit_note_issue(request: Request, issued_id: int = row_id()):
    """Issue (the main button); "all" fills in everything left to credit and "totals" works the totals out (both
    for browsers without JavaScript); neither of those saves anything."""
    form = await request.form()
    fields = credit_fields(form, issued_id)
    view = services.credit_note_view(issued_id)
    if view is None:
        return go("invoicing_issued", error="No credit note can be issued: that invoice does not exist (any more).")
    action = form.get("action", "issue")
    if action == "all":
        return credit_page(request, view, {**fields, "lines": services.credit_all(view)})
    if action == "totals":
        return credit_page(request, view, fields)
    outcome = services.generate_credit_note(fields)
    if outcome.extra.get("already_issued"):
        response = go("invoicing_issued", error=outcome.error)
        response.set_cookie(ISSUED_COOKIE, str(outcome.extra["issued_id"]), httponly=True, samesite="strict")
        return response
    if not outcome.ok:
        return credit_page(request, services.credit_note_view(issued_id) or view, fields, error=outcome.error)
    response = go_after("invoicing_issued", outcome)
    response.set_cookie(ISSUED_COOKIE, str(outcome.extra["issued_id"]), httponly=True, samesite="strict")
    return response


@app.post("/invoicing/issued/{issued_id}/credit/totals")
async def credit_note_totals(request: Request, issued_id: int = row_id()):
    """The live totals of the credit note form (nothing is saved)."""
    return services.credit_note_totals(credit_fields(await request.form(), issued_id))


@app.get("/invoicing/issued/{issued_id}/pdf")
def invoicing_pdf(issued_id: int = row_id()):
    found = services.issued_pdf(issued_id)
    if found is None:
        return HTMLResponse("This invoice's PDF cannot be made: the invoice does not exist or its record is damaged.",
                            status_code=404)
    name, data = found
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"})


def clients_page(request: Request, form: dict | None = None, editing: dict | None = None, **ctx) -> HTMLResponse:
    return invoicing_render(request, "invoicing_clients.html", "clients", form=form or {"country": "RO"},
                            editing=editing, **ctx)


@app.get("/invoicing/clients", response_class=HTMLResponse)
def invoicing_clients(request: Request):
    return clients_page(request)


@app.post("/invoicing/clients", response_class=HTMLResponse)
async def invoicing_client_add(request: Request):
    outcome = services.save_client(text_fields(await request.form(), CLIENT_FORM))
    if not outcome.ok:
        return clients_page(request, form=outcome.form, error=outcome.error)
    return go_after("invoicing_clients", outcome)


@app.get("/invoicing/clients/{client_id}/edit", response_class=HTMLResponse)
def invoicing_client_edit_form(request: Request, client_id: int = row_id()):
    editing = services.client_for_edit(client_id)
    if editing is None:
        return go("invoicing_clients", error="That client does not exist (any more).")
    return clients_page(request, form=editing, editing=editing)


@app.post("/invoicing/clients/{client_id}/edit", response_class=HTMLResponse)
async def invoicing_client_edit(request: Request, client_id: int = row_id()):
    fields = text_fields(await request.form(), CLIENT_FORM)
    outcome = services.save_client(fields, client_id)
    if not outcome.ok:
        editing = services.client_for_edit(client_id)
        if editing is None:
            return go("invoicing_clients", error=outcome.error)
        return clients_page(request, form=fields, editing=editing, error=outcome.error)
    return go_after("invoicing_clients", outcome)


@app.post("/invoicing/clients/{client_id}/delete")
def invoicing_client_delete(client_id: int = row_id()):
    return go_after("invoicing_clients", services.delete_client(client_id))


def products_page(request: Request, form: dict | None = None, editing: dict | None = None, **ctx) -> HTMLResponse:
    return invoicing_render(request, "invoicing_products.html", "products", form=form or {"currency": "RON"},
                            editing=editing, **ctx)


@app.get("/invoicing/products", response_class=HTMLResponse)
def invoicing_products(request: Request):
    return products_page(request)


@app.post("/invoicing/products", response_class=HTMLResponse)
async def invoicing_product_add(request: Request):
    outcome = services.save_product(text_fields(await request.form(), PRODUCT_FORM))
    if not outcome.ok:
        return products_page(request, form=outcome.form, error=outcome.error)
    return go_after("invoicing_products", outcome)


@app.get("/invoicing/products/{product_id}/edit", response_class=HTMLResponse)
def invoicing_product_edit_form(request: Request, product_id: int = row_id()):
    editing = services.product_for_edit(product_id)
    if editing is None:
        return go("invoicing_products", error="That product or service does not exist (any more).")
    return products_page(request, form=editing, editing=editing)


@app.post("/invoicing/products/{product_id}/edit", response_class=HTMLResponse)
async def invoicing_product_edit(request: Request, product_id: int = row_id()):
    fields = text_fields(await request.form(), PRODUCT_FORM)
    outcome = services.save_product(fields, product_id)
    if not outcome.ok:
        editing = services.product_for_edit(product_id)
        if editing is None:
            return go("invoicing_products", error=outcome.error)
        return products_page(request, form=fields, editing=editing, error=outcome.error)
    return go_after("invoicing_products", outcome)


@app.post("/invoicing/products/{product_id}/delete")
def invoicing_product_delete(product_id: int = row_id()):
    return go_after("invoicing_products", services.delete_product(product_id))


def series_page(request: Request, form: dict | None = None, editing: dict | None = None,
                vat_form: dict | None = None, **ctx) -> HTMLResponse:
    return invoicing_render(request, "invoicing_series.html", "series", form=form or {"next_number": "1"},
                            editing=editing, vat_form=vat_form, **ctx)


def series_fields(form) -> dict:
    return {**text_fields(form, ("name", "next_number")), "is_default": form.get("is_default") == "1"}


@app.get("/invoicing/series", response_class=HTMLResponse)
def invoicing_series(request: Request):
    return series_page(request)


@app.post("/invoicing/series", response_class=HTMLResponse)
async def invoicing_series_add(request: Request):
    outcome = services.save_series(series_fields(await request.form()))
    if not outcome.ok:
        return series_page(request, form=outcome.form, error=outcome.error)
    return go_after("invoicing_series", outcome)


@app.get("/invoicing/series/{series_id}/edit", response_class=HTMLResponse)
def invoicing_series_edit_form(request: Request, series_id: int = row_id()):
    editing = services.series_for_edit(series_id)
    if editing is None:
        return go("invoicing_series", error="That series does not exist (any more).")
    return series_page(request, form=editing, editing=editing)


@app.post("/invoicing/series/{series_id}/edit", response_class=HTMLResponse)
async def invoicing_series_edit(request: Request, series_id: int = row_id()):
    fields = series_fields(await request.form())
    outcome = services.save_series(fields, series_id)
    if not outcome.ok:
        editing = services.series_for_edit(series_id)
        if editing is None:
            return go("invoicing_series", error=outcome.error)
        return series_page(request, form=fields, editing=editing, error=outcome.error)
    return go_after("invoicing_series", outcome)


@app.post("/invoicing/series/{series_id}/delete")
def invoicing_series_delete(series_id: int = row_id()):
    return go_after("invoicing_series", services.delete_series(series_id))


@app.post("/invoicing/vat", response_class=HTMLResponse)
async def invoicing_vat(request: Request):
    form = await request.form()
    fields = {**text_fields(form, ("vat_rate", "due_days")), "vat_payer": form.get("vat_payer") == "1"}
    outcome = services.save_invoicing_settings(fields)
    if not outcome.ok:
        return series_page(request, vat_form=outcome.form, error=outcome.error)
    return go_after("invoicing_series", outcome)


# Backup and restore

@app.get("/backup")
def backup_download():
    name, data = services.export_backup()
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/restore", response_class=HTMLResponse)
async def backup_restore(request: Request, file: UploadFile = File(...), confirm: str = Form("")):
    outcome = services.restore_backup(await file.read(), confirm == "1")
    if not outcome.ok:
        return settings_page(request, error=outcome.error)
    return go_after("settings", outcome)


# Profiles

def go_profile(page: str, outcome: services.Outcome) -> RedirectResponse:
    response = go_after(page, outcome)
    if outcome.ok and outcome.extra.get("profile"):
        response.set_cookie(PROFILE_COOKIE, outcome.extra["profile"], httponly=True, samesite="strict")
        response.delete_cookie(YEAR_COOKIE)          # the other profile has its own years
    return response


@app.post("/profile")
def profile_switch(profile: str = Form(""), page: str = Form("overview")):
    return go_profile(page if page in PAGES else "overview", services.switch_profile(profile))


@app.post("/profiles/new")
def profile_new(name: str = Form("")):
    return go_profile("settings", services.create_profile(name))


@app.post("/profiles/{profile_id}/rename")
def profile_rename(profile_id: str, name: str = Form("")):
    return go_after("settings", services.rename_profile(profile_id, name))


@app.post("/profiles/{profile_id}/delete")
def profile_delete(request: Request, profile_id: str, confirm: str = Form("")):
    outcome = services.delete_profile(profile_id, confirm == "1")
    response = go_after("settings", outcome)
    if outcome.ok and request.cookies.get(PROFILE_COOKIE) == profile_id:
        response.delete_cookie(PROFILE_COOKIE)
        response.delete_cookie(YEAR_COOKIE)
    return response


# Theme

@app.post("/theme", response_class=HTMLResponse)
def theme_save(request: Request, theme_name: str = Form("light", alias="theme"), accent: str = Form(""),
               background: str = Form(""), surface: str = Form(""), text: str = Form(""), action: str = Form("apply"),
               saved_name: str = Form("")):
    """The browser's own theme (the desktop app has its own). action=save_as also saves the colours by name."""
    colors = {"accent": accent, "background": background, "surface": surface, "text": text}
    if action == "save_as":
        outcome = services.save_custom_theme("web", saved_name, colors)
    else:
        outcome = services.save_theme("web", theme_name, colors)
    if not outcome.ok:
        return settings_page(request, error=outcome.error, theme_form=outcome.form or None)
    return go_after("settings", outcome)


@app.post("/theme/use")
def theme_use(name: str = Form("")):
    return go_after("settings", services.use_saved_theme("web", name))


@app.post("/theme/delete")
def theme_delete(name: str = Form("")):
    return go_after("settings", services.delete_saved_theme(name))
