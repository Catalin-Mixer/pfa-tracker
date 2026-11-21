"""The Invoicing tab of the browser version: clients, products and services, series and VAT, issuing an
invoice that goes straight to Income, the issued list with its PDFs, and the marks it leaves in Income."""
import json
import re
from html import unescape
from pathlib import Path

import pytest

from conftest import TODAY, YEAR
from tracker import config, services

PAGES = ["/invoicing", "/invoicing/issued", "/invoicing/clients", "/invoicing/products", "/invoicing/series"]
CLIENT = {"name": "Țară Soft SRL — «Ünïcödé»", "tax_id": "RO 14399840", "reg_number": "J40/1234/2020",
          "address": "Bd. Unirii 10, bl. 3", "city": "București", "country": "RO", "email": "billing@tara.ro",
          "phone": "+40 721 000 000"}
DE_CLIENT = {"name": "Acme GmbH", "tax_id": "DE123456789", "address": "Hauptstraße 5", "city": "Berlin",
             "country": "DE", "email": "bill@acme.de"}
HOUR = {"name": "Consultanță IT & „QA”", "description": "Dezvoltare software\nluna octombrie", "unit": "hour",
        "unit_price": "150,50", "currency": "RON"}
PAGE = {"name": "Traducere", "unit": "page", "unit_price": "12,3456", "currency": "RON"}
SUPPORT = {"name": "Support", "unit": "month", "unit_price": "1.000", "currency": "EUR"}


@pytest.fixture
def firm_with_address(settings_path):
    data = json.loads(settings_path.read_text())
    data["firm"]["address"] = "Str. Științei 1, Iași"
    settings_path.write_text(json.dumps(data), encoding="utf-8")


def text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def flash(response) -> dict:
    from urllib.parse import unquote
    return json.loads(unquote(response.cookies["pfa_flash"]))


def post(http, url, data):
    return http.post(url, data=data, follow_redirects=False)


def setup_lists(http, *products):
    assert post(http, "/invoicing/clients", CLIENT).status_code == 303
    for p in products or (HOUR,):
        assert post(http, "/invoicing/products", p).status_code == 303
    assert post(http, "/invoicing/series", {"name": "pfa", "next_number": "1", "is_default": "1"}).status_code == 303


def invoice(lines, **fields):
    data = {"series_id": "1", "client_id": "1", "date": TODAY.isoformat(), "action": "issue",
            "token": services.new_invoice_token(), **fields}
    for n, (product, qty, price) in enumerate(lines, start=1):
        data.update({f"product_id_{n}": product, f"quantity_{n}": qty, f"price_{n}": price})
    return data


# The tab and its sections

def test_the_invoicing_tab_sits_before_settings_and_every_section_opens(client):
    html = client.get("/").text
    assert html.index('href="/invoicing"') < html.index('href="/settings"')
    for url in PAGES:
        r = client.get(url)
        assert r.status_code == 200, url
        assert f'href="{url}" class="on"' in r.text
        assert 'href="/invoicing" class="on">Invoicing</a>' in r.text


@pytest.mark.parametrize("url", PAGES)
def test_invoicing_addresses_take_nothing_from_the_query_string(client, url):
    r = client.get(url + "?client_id=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == url


def test_empty_lists_say_what_to_do(client):
    assert "No clients yet — add one below." in client.get("/invoicing/clients").text
    assert "No products or services yet — add one below." in client.get("/invoicing/products").text
    assert "No series yet — add one below." in client.get("/invoicing/series").text
    assert "No invoices issued yet" in client.get("/invoicing/issued").text
    new = text(client.get("/invoicing").text)
    assert "No series yet — add one under Series & VAT" in new
    assert "No clients yet — add one under Clients" in new
    assert "No products or services yet — add one under Products & services" in new


def test_missing_firm_details_are_pointed_out_with_a_way_to_settings(client, firm_with_address, settings_path):
    assert 'id="firm-problems"' not in client.get("/invoicing").text
    data = json.loads(settings_path.read_text())
    data["firm"]["address"] = ""
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    html = client.get("/invoicing").text
    assert 'id="firm-problems"' in html and "address" in text(html) and '<a href="/settings">Open Settings</a>' in html


# Clients

def test_a_client_is_added_listed_edited_and_deleted(client):
    r = post(client, "/invoicing/clients", CLIENT)
    assert r.status_code == 303 and r.headers["location"] == "/invoicing/clients"
    assert "added" in flash(r)["message"]
    page = client.get("/invoicing/clients").text
    assert "Țară Soft SRL — «Ünïcödé»" in page and "RO14399840" in page and "J40/1234/2020" in page
    assert "București</td><td>Romania" in page and "billing@tara.ro" in page
    assert '<a class="button secondary" href="/invoicing/clients/1/edit">Edit</a>' in page
    assert "return confirm('Delete this client?')" in page

    form = client.get("/invoicing/clients/1/edit").text
    assert "<h3>Edit client</h3>" in form and 'action="/invoicing/clients/1/edit"' in form
    assert 'value="Țară Soft SRL — «Ünïcödé»"' in form and '<option value="RO" selected>' in form
    r = post(client, "/invoicing/clients/1/edit", {**CLIENT, "city": "Cluj-Napoca"})
    assert r.status_code == 303 and "changed" in flash(r)["message"]
    assert "Cluj-Napoca</td><td>Romania" in client.get("/invoicing/clients").text

    r = post(client, "/invoicing/clients/1/delete", {})
    assert r.status_code == 303 and "deleted" in flash(r)["message"]
    assert "No clients yet" in client.get("/invoicing/clients").text


def test_a_name_that_cannot_be_printed_on_the_invoice_is_refused(client):
    r = post(client, "/invoicing/clients", {**CLIENT, "name": "Rocket 🚀 SRL"})
    assert r.status_code == 200 and "Client not saved" in r.text and "🚀" in r.text
    assert 'value="Rocket 🚀 SRL"' in r.text and "No clients yet" in r.text


def test_a_new_client_form_starts_with_romania(client):
    html = client.get("/invoicing/clients").text
    assert '<option value="RO" selected>Romania</option>' in html
    for label in ("Name", "CUI / tax ID", "Trade register no.", "Address", "City", "Country", "Email", "Phone"):
        assert label in html


def test_a_wrong_client_is_shown_again_with_what_was_typed(client):
    r = post(client, "/invoicing/clients", {**CLIENT, "tax_id": "RO123", "email": "not-an-email",
                                            "name": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "Client not saved" in r.text and "CUI" in r.text
    assert 'value="RO123"' in r.text and 'value="not-an-email"' in r.text
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
    assert "No clients yet" in r.text


def test_editing_a_client_that_is_gone_says_so(client):
    r = client.get("/invoicing/clients/7/edit", follow_redirects=False)
    assert r.status_code == 303 and "does not exist" in flash(r)["error"]
    r = post(client, "/invoicing/clients/7/edit", CLIENT)
    assert r.status_code == 303 and flash(r)["error"]
    r = post(client, "/invoicing/clients/7/delete", {})
    assert r.status_code == 303 and flash(r)["error"] == "Nothing to delete."


@pytest.mark.parametrize("bad", ["0", "-1", "abc", str(2**63)])
def test_row_numbers_outside_the_range_are_refused(client, bad):
    for url in (f"/invoicing/clients/{bad}/edit", f"/invoicing/products/{bad}/edit", f"/invoicing/series/{bad}/edit",
                f"/invoicing/issued/{bad}/pdf"):
        assert client.get(url).status_code == 422, url


# Products and services

def test_a_product_with_four_decimals_keeps_them(client):
    assert post(client, "/invoicing/products", PAGE).status_code == 303
    page = client.get("/invoicing/products").text
    assert "12,3456" in page and "pagină / page" in page
    form = client.get("/invoicing/products/1/edit").text
    assert "Edit product or service" in form and 'value="12.3456"' in form     # as stored: not whole cents
    assert '<option value="page" selected>' in form


def test_a_product_is_edited_and_deleted_with_a_confirmation(client):
    post(client, "/invoicing/products", HOUR)
    page = client.get("/invoicing/products").text
    assert "Consultanță IT &amp; „QA”" in page and "Dezvoltare software" in page and "150,50" in page
    assert "return confirm('Delete this product or service?')" in page
    r = post(client, "/invoicing/products/1/edit", {**HOUR, "unit_price": "175", "currency": "EUR"})
    assert r.status_code == 303
    page = client.get("/invoicing/products").text
    assert "175,00" in page and "EUR" in page
    assert post(client, "/invoicing/products/1/delete", {}).status_code == 303
    assert "No products or services yet" in client.get("/invoicing/products").text


def test_a_wrong_product_is_shown_again(client):
    r = post(client, "/invoicing/products", {"name": "Zero", "unit": "parsec", "unit_price": "0", "currency": "XYZ"})
    assert r.status_code == 200 and "Product not saved" in r.text
    assert 'value="Zero"' in r.text


# Series and VAT

def test_series_are_added_made_default_edited_and_deleted(client):
    assert post(client, "/invoicing/series", {"name": "pfa", "next_number": "7"}).status_code == 303
    assert post(client, "/invoicing/series", {"name": "EXP", "next_number": "1", "is_default": "1"}).status_code == 303
    page = client.get("/invoicing/series").text
    assert "PFA-0007" in page and "EXP-0001" in page and '<span class="tag">default</span>' in page
    assert "return confirm('Delete this series?')" in page
    new = client.get("/invoicing").text
    assert re.search(r'<option value="2" selected>EXP', new)
    form = client.get("/invoicing/series/1/edit").text
    assert "Edit series" in form and 'value="7"' in form
    assert post(client, "/invoicing/series/1/edit", {"name": "PFA", "next_number": "12"}).status_code == 303
    assert "PFA-0012" in client.get("/invoicing/series").text
    assert post(client, "/invoicing/series/1/delete", {}).status_code == 303
    assert "PFA-0012" not in client.get("/invoicing/series").text


def test_a_wrong_series_is_refused(client):
    r = post(client, "/invoicing/series", {"name": "9 lives!", "next_number": "-3"})
    assert r.status_code == 200 and "Series not saved" in r.text


def test_vat_settings_are_saved_and_checked(client):
    r = post(client, "/invoicing/vat", {"vat_payer": "1", "vat_rate": "19", "due_days": "30"})
    assert r.status_code == 303 and flash(r)["message"] == "Invoice settings saved."
    page = client.get("/invoicing/series").text
    assert re.search(r'name="vat_payer" value="1" checked', page) and 'value="19"' in page and 'value="30"' in page
    new = client.get("/invoicing").text
    assert "VAT 19%" in new
    r = post(client, "/invoicing/vat", {"vat_rate": "150", "due_days": "a lot"})
    assert r.status_code == 200 and "Not saved" in r.text and 'value="a lot"' in r.text


# New invoice

def test_the_new_invoice_form_starts_with_today_the_due_date_and_the_default_series(client):
    setup_lists(client)
    view = services.invoicing_view()
    html = client.get("/invoicing").text
    assert f'name="date" value="{TODAY.isoformat()}"' in html
    assert f'name="due_date" value="{view["default_due"]}"' in html
    assert re.search(r'<option value="1" selected>PFA', html)
    assert "this will be PFA-0001" in html
    assert len(re.findall(r'name="product_id_\d+"', html)) == 1          # one line; "Add a line" adds more
    assert "Issue invoice and add to Income" in html and '/static/invoicing.js' in html


def test_issuing_adds_the_invoice_to_income_and_offers_its_pdf(client, firm_with_address):
    setup_lists(client, HOUR, PAGE)
    r = post(client, "/invoicing/issue", invoice([("1", "10", ""), ("", "", ""), ("2", "3", "12,3456")],
                                                 notes="Mulțumim! Plata în 15 zile."))
    assert r.status_code == 303 and r.headers["location"] == "/invoicing"
    assert r.cookies["pfa_year"] == YEAR
    message = flash(r)["message"]
    assert "PFA-0001" in message and "1.542,04 RON" in message and f"Income for {YEAR}" in message

    page = client.get("/invoicing").text
    assert 'id="just-issued"' in page and '/invoicing/issued/1/pdf' in page
    assert f'name="year" value="{YEAR}">Show in Income {YEAR}</button>' in page
    assert "this will be PFA-0002" in page
    assert 'id="just-issued"' not in client.get("/invoicing").text      # shown once

    pdf = client.get("/invoicing/issued/1/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert 'filename="PFA-0001_Tara_Soft_SRL' in pdf.headers["content-disposition"]

    issued = client.get("/invoicing/issued").text
    assert "PFA-0001" in issued and "1.542,04" in issued and "yes · " + YEAR in issued

    income = client.get("/income").text
    assert re.search(r'PFA-0001 <span class="tag"[^>]*>issued</span>', income)
    assert "1.542,04" in income and "📎" in income
    edit = client.get("/income/1/edit").text
    assert services.GENERATED_NOTE in unescape(edit)


def test_invoices_entered_by_hand_get_neither_the_tag_nor_the_note(client):
    client.post("/income/add", data={"date": TODAY.isoformat(), "client": "Acme", "number": "A-1", "amount": "100",
                                     "currency": "RON"})
    assert '<span class="tag"' not in client.get("/income").text
    assert 'id="generated-note"' not in client.get("/income/1/edit").text


def test_the_open_income_button_shows_the_year_of_the_payment(client, firm_with_address):
    setup_lists(client)
    last_year = str(TODAY.year - 1)
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")], date=f"{last_year}-12-15"))
    assert r.status_code == 303 and r.cookies["pfa_year"] == last_year
    r = client.post("/year", data={"page": "income", "year": last_year}, follow_redirects=False)
    assert r.headers["location"] == "/income"
    assert "PFA-0001" in client.get("/income").text


def test_a_failed_issue_keeps_everything_that_was_typed(client, firm_with_address):
    setup_lists(client, HOUR, PAGE)
    r = post(client, "/invoicing/issue", invoice([("1", "2,5", "99,99"), ("", "", ""), ("2", "4", "")],
                                                 client_id="", notes="Keep me ✨"))
    assert r.status_code == 200
    assert "Invoice not issued" in r.text and "choose a client" in r.text
    assert 'value="2,5"' in r.text and 'value="99,99"' in r.text and "Keep me ✨" in r.text
    assert re.search(r'name="product_id_3".*?<option value="2"[^>]*selected', r.text, re.S)
    assert "PFA-0001" not in client.get("/income").text
    assert services.invoicing_view()["issued"] == []


def test_without_firm_address_nothing_is_issued(client):
    setup_lists(client)
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    assert r.status_code == 200 and "Invoice not issued" in r.text and "address" in r.text


def test_a_product_deleted_while_the_form_was_open_is_named(client, firm_with_address):
    setup_lists(client, HOUR, PAGE)
    post(client, "/invoicing/products/2/delete", {})
    r = post(client, "/invoicing/issue", invoice([("1", "1", ""), ("2", "1", "")]))
    assert r.status_code == 200 and "line 2: the product or service no longer exists" in r.text
    assert '<option value="2" data-gone="1" selected>(deleted from the list — choose another)' in r.text
    assert services.invoicing_view()["issued"] == []


def test_mixed_currencies_are_refused_and_not_added_up(client, firm_with_address):
    setup_lists(client, HOUR, SUPPORT)
    r = post(client, "/invoicing/issue", invoice([("1", "1", ""), ("2", "1", "")]))
    assert r.status_code == 200 and "same currency" in r.text
    assert '<td class="n" id="ni-total">—</td>' in r.text
    assert "150,50 RON" in r.text and "1.000,00 EUR" in r.text      # each line keeps its own value


def test_fifty_lines_can_be_issued_and_fifty_one_cannot(client, firm_with_address):
    setup_lists(client, PAGE)
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")] * 51))
    assert r.status_code == 200 and "at most 50 lines" in r.text
    r = post(client, "/invoicing/issue", invoice([("1", str(n), "") for n in range(1, 51)]))
    assert r.status_code == 303
    total = sum(round(n * 12.3456 + 1e-9, 2) for n in range(1, 51))
    assert services.invoicing_view()["issued"][0]["total_display"] == f"{total:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def test_more_lines_without_javascript_add_one_keep_the_form_and_stop_at_the_limit(client):
    setup_lists(client)
    r = post(client, "/invoicing/issue", {**invoice([("1", "3", "")]), "action": "more"})
    assert r.status_code == 200 and len(re.findall(r'name="product_id_\d+"', r.text)) == 2
    assert 'name="quantity_1" value="3"' in r.text
    r = post(client, "/invoicing/issue", {**invoice([("", "", "")] * 49), "action": "more"})
    assert len(re.findall(r'name="product_id_\d+"', r.text)) == 50 and 'id="ni-more"' not in r.text
    assert services.invoicing_view()["issued"] == []


def test_update_totals_without_javascript_shows_the_totals_and_saves_nothing(client, firm_with_address):
    setup_lists(client, HOUR)
    post(client, "/invoicing/vat", {"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    r = post(client, "/invoicing/issue", {**invoice([("1", "2", "")]), "action": "totals"})
    assert r.status_code == 200
    html = text(r.text)
    assert "Total without VAT 301,00 RON" in html and "VAT 21% 63,21 RON" in html and "Total 364,21 RON" in html
    assert services.invoicing_view()["issued"] == []


def test_live_totals_name_the_row_of_each_value(client):
    setup_lists(client, HOUR)
    r = client.post("/invoicing/totals", data=invoice([("1", "2", ""), ("", "x", ""), ("1", "1", "100")]))
    t = r.json()
    assert t["ok"] is False and "line 2" in t["error"]
    assert [(line["line"], line["value_display"]) for line in t["lines"]] == [(1, "301,00"), (3, "100,00")]
    assert t["total"] == "401,00" and t["next_number"] == "PFA-0001"
    assert services.invoicing_view()["issued"] == []


def test_the_pdf_of_an_unknown_invoice_is_not_found(client):
    assert client.get("/invoicing/issued/99/pdf").status_code == 404


def test_an_invoice_deleted_from_income_stays_in_the_issued_list_with_its_pdf(client, firm_with_address):
    setup_lists(client)
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    income = client.get("/income").text
    assert services.GENERATED_DELETE_NOTE in unescape(income)
    r = post(client, "/income/1/delete", {})
    assert r.status_code == 303 and "stays in Invoicing" in flash(r)["message"]
    issued = client.get("/invoicing/issued").text
    assert re.search(r'<tr class="gone"><td class="d"><b>PFA-0001</b>', issued)
    assert "no — deleted from Income" in issued and "yes · " not in issued
    pdf = client.get("/invoicing/issued/1/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "this will be PFA-0002" in client.get("/invoicing").text
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    assert r.status_code == 303 and "PFA-0002" in flash(r)["message"]


def test_the_income_delete_confirmation_warns_only_for_issued_invoices(client, firm_with_address):
    setup_lists(client)
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    client.post("/income/add", data={"date": TODAY.isoformat(), "client": "Acme", "number": "A-1", "amount": "100",
                                     "currency": "RON"})
    html = client.get("/income").text
    assert html.count("onsubmit='return confirm(\"Delete this invoice? It was issued in Invoicing") == 1
    assert html.count("return confirm('Delete this invoice?')") == 1


def test_a_damaged_issued_record_lists_but_has_no_pdf(client, firm_with_address):
    from tracker import db
    setup_lists(client)
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data='{not json' WHERE id=1")
    assert "PFA-0001" in client.get("/invoicing/issued").text
    r = client.get("/invoicing/issued/1/pdf")
    assert r.status_code == 404 and "damaged" in r.text


def test_a_new_invoice_cannot_be_dated_before_the_last_one_of_its_series(client, firm_with_address):
    setup_lists(client)
    assert post(client, "/invoicing/issue", invoice([("1", "1", "")])).status_code == 303
    earlier = (TODAY.replace(day=1) if TODAY.day > 1 else TODAY.replace(year=TODAY.year - 1, month=12, day=1)).isoformat()
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")], date=earlier))
    assert r.status_code == 200 and "Invoice not issued" in r.text and "PFA-0001" in r.text
    assert len(services.invoicing_view()["issued"]) == 1


def test_a_comma_in_the_quantity_is_the_decimal_mark(client):
    setup_lists(client)
    t = client.post("/invoicing/totals", data=invoice([("1", "1,250", ""), ("1", "2.5", "")])).json()
    assert [line["quantity"] for line in t["lines"]] == ["1.25", "2.5"]
    assert "a comma is the decimal mark" in client.get("/invoicing").text


def test_the_series_name_field_allows_ten_characters(client):
    assert 'name="name" value="" placeholder="e.g. PFA" maxlength="10"' in client.get("/invoicing/series").text


def test_the_invoicing_styles_use_the_theme_colours_only():
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()
    block = css[css.index("/* Invoicing */"):css.index("footer {")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(", block)


def test_the_script_is_served(client):
    r = client.get("/static/invoicing.js")
    assert r.status_code == 200 and "/invoicing/totals" in r.text


# Profiles: each profile has its own lists

@pytest.fixture
def web(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import app
    monkeypatch.delenv("PFA_DB", raising=False)
    monkeypatch.delenv("PFA_SETTINGS", raising=False)
    monkeypatch.setenv("PFA_HOME", str(tmp_path))
    token = config.ACTIVE_PROFILE.set(None)
    services.start()
    yield TestClient(app)
    config.ACTIVE_PROFILE.reset(token)


def test_each_profile_has_its_own_invoicing_lists(web):
    post(web, "/invoicing/clients", CLIENT)
    assert "Țară Soft SRL" in web.get("/invoicing/clients").text
    assert post(web, "/profiles/new", {"name": "Second firm"}).status_code == 303
    assert "No clients yet" in web.get("/invoicing/clients").text
    post(web, "/invoicing/clients", DE_CLIENT)
    post(web, "/profile", {"profile": "p1", "page": "invoicing_clients"})
    page = web.get("/invoicing/clients").text
    assert "Țară Soft SRL" in page and "Acme GmbH" not in page


# One form, one invoice

def token_of(html: str) -> str:
    return re.search(r'name="token" value="([^"]*)"', html)[1]


def test_every_new_form_has_its_own_token_and_keeps_it_after_an_error(client, firm_with_address):
    setup_lists(client)
    first, second = token_of(client.get("/invoicing").text), token_of(client.get("/invoicing").text)
    assert first and second and first != second
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")], client_id="", token=first))
    assert r.status_code == 200 and token_of(r.text) == first
    r = post(client, "/invoicing/issue", {**invoice([("1", "1", "")], token=first), "action": "more"})
    assert token_of(r.text) == first


def test_a_form_sent_twice_issues_one_invoice(client, firm_with_address):
    setup_lists(client)
    data = invoice([("1", "2", "")])
    r = post(client, "/invoicing/issue", data)
    assert r.status_code == 303 and "PFA-0001" in flash(r)["message"]
    r = post(client, "/invoicing/issue", data)
    assert r.status_code == 303 and r.headers["location"] == "/invoicing"
    assert flash(r)["error"].startswith("Not issued again: this form was already used to issue invoice PFA-0001")
    page = client.get("/invoicing").text
    assert 'id="just-issued"' in page and "/invoicing/issued/1/pdf" in page and "this will be PFA-0002" in page
    assert len(services.invoicing_view()["issued"]) == 1
    assert client.get("/income").text.count("PFA-0001 <span") == 1


def test_the_issue_button_is_switched_off_once_the_form_is_sent(client):
    js = client.get("/static/invoicing.js").text
    assert "button.disabled = true" in js and "e.submitter === button" in js


# Same words as the desktop app

def test_the_status_line_says_what_is_missing_or_that_it_is_ready(client, firm_with_address):
    setup_lists(client)
    assert "Still needed before issuing: choose a client; add at least one product or service." in text(client.get("/invoicing").text)
    r = post(client, "/invoicing/issue", {**invoice([("1", "1", "")]), "action": "totals"})
    assert "Ready to be issued." in r.text
    js = client.get("/static/invoicing.js").text
    assert "Still needed before issuing: " in js and "Ready to be issued." in js


def test_a_deleted_client_or_series_is_shown_as_deleted(client, firm_with_address):
    setup_lists(client)
    post(client, "/invoicing/clients", DE_CLIENT)
    post(client, "/invoicing/clients/1/delete", {})
    post(client, "/invoicing/series/1/delete", {})
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    assert r.status_code == 200
    assert '<option value="1" data-gone="1" selected>(deleted from the list — choose another)' in r.text
    assert r.text.count("(deleted from the list — choose another)") == 1     # the client; no series left to list
    post(client, "/invoicing/series", {"name": "NEW", "next_number": "1"})
    r = post(client, "/invoicing/issue", invoice([("1", "1", "")], client_id=""))
    assert '<option value="1" data-gone="1" selected>(deleted from the list — choose another)' in r.text


def test_the_empty_choices_and_column_names(client, firm_with_address):
    setup_lists(client)
    new = client.get("/invoicing").text
    for label in ("— choose a series —", "— choose a client —", "— choose —"):
        assert f">{label}</option>" in new
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))
    heads = lambda url: re.findall(r"<th(?: [^>]*)?>([^<]*)", client.get(url).text)
    assert heads("/invoicing/issued")[:7] == ["Number", "Invoice date", "Due date", "Client", "Total", "Currency",
                                              "In Income"]
    assert heads("/invoicing/clients")[:7] == ["Name", "CUI / tax ID", "Trade register no.", "Address", "Country",
                                               "Email", "Phone"]
    assert heads("/invoicing/products")[:5] == ["Name", "Description", "Unit", "Price per unit", "Currency"]
    assert ">Save VAT settings</button>" in client.get("/invoicing/series").text


@pytest.mark.parametrize("price", ["€5", "5 RON", "1.000.000.000"])
def test_prices_must_be_plain_numbers_below_a_billion(client, price):
    r = post(client, "/invoicing/products", {**PAGE, "unit_price": price})
    assert r.status_code == 200 and "Product not saved" in r.text
