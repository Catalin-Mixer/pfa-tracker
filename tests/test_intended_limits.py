"""Tests that FAIL on purpose.

Each test below describes something the tracker deliberately does not do, written as if it did.
They are marked xfail(strict=True): the suite expects them to fail, and if one of them ever
passes, the run fails — so this file is a living list of the intended limits. Run
"python3 -m pytest tests/test_intended_limits.py -rx" to see the list with the reasons.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import LAST_YEAR, TODAY, YEAR, csv_upload
from tracker import taxes
from tracker.parsing import parse_amount, parse_date
from tracker.rates import find_rate, load_rates

limit = pytest.mark.xfail(strict=True, raises=(AssertionError, ValueError, KeyError))


@limit(reason="month-first (US) dates are not understood; the day always comes before the month")
def test_us_dates():
    assert parse_date("03/15/2025") == date(2025, 3, 15)


@limit(reason="'1.500' is read as one thousand five hundred, never as one and a half")
def test_three_decimals_after_a_single_dot():
    assert parse_amount("1.500") == Decimal("1.5")


@limit(reason="two-digit years are not accepted")
def test_two_digit_years():
    assert parse_date("15.03.25") == date(2025, 3, 15)


@limit(reason="invoices dated after today (advance invoices) cannot be stored, by hand or from a CSV")
def test_invoice_dated_tomorrow(client):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    r = client.post("/income/add", data={"date": tomorrow, "client": "A", "amount": "1", "currency": "RON"},
                    follow_redirects=False)
    assert r.status_code == 303


@limit(reason="expenses dated after today cannot be stored")
def test_expense_dated_tomorrow(client):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    r = client.post("/expenses", data={"date": tomorrow, "description": "x", "amount": "1", "currency": "RON"},
                    follow_redirects=False)
    assert r.status_code == 303


@limit(reason="when a date of payment is given, the income counts in the payment year, not the invoice year")
def test_income_counted_by_invoice_date_when_paid_later(client):
    client.post("/income/add", data={"date": f"{LAST_YEAR}-12-28", "payment_date": f"{YEAR}-01-05",
                                     "client": "A", "amount": "100", "currency": "RON"})
    client.post("/year", data={"year": LAST_YEAR, "page": "income"})
    assert f"Income {LAST_YEAR}" in client.get("/income").text


@limit(reason="years after the current one cannot be viewed, even if settings.json has values for them")
def test_viewing_next_year(client, settings_path):
    import json
    data = json.loads(settings_path.read_text())
    data["years"][str(int(YEAR) + 1)] = dict(data["years"][YEAR])
    settings_path.write_text(json.dumps(data))
    client.post("/year", data={"year": str(int(YEAR) + 1), "page": "overview"})
    assert f"Overview {int(YEAR) + 1}" in client.get("/").text


@limit(reason="a year with no invoices or expenses is not offered, even if settings.json has values for it")
def test_viewing_an_empty_past_year(client):
    client.post("/year", data={"year": LAST_YEAR, "page": "overview"})
    assert f"Overview {LAST_YEAR}" in client.get("/").text


@limit(reason="the year cannot be chosen through the page address; only the year picker changes it")
def test_year_in_the_address(client):
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "x", "amount": "1", "currency": "RON"})
    client.post("/year", data={"year": YEAR, "page": "overview"})
    assert f"Overview {LAST_YEAR}" in client.get(f"/?year={LAST_YEAR}").text


@limit(reason="a payment cannot be dated before its invoice")
def test_payment_before_invoice(client):
    r = client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "payment_date": f"{LAST_YEAR}-05-01",
                                         "client": "A", "amount": "1", "currency": "RON"}, follow_redirects=False)
    assert r.status_code == 303


@limit(reason="a proof must really be the type its name says: a PNG renamed to .pdf is refused")
def test_renamed_proof(client):
    from files import PNG
    r = client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "x", "amount": "1", "currency": "RON"},
                    files={"attachment": ("scan.pdf", PNG, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303


@limit(reason="only PDF, images, text and XML can be attached as proof; office documents cannot")
def test_docx_attachment(client):
    r = client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "A", "amount": "1", "currency": "RON"},
                    files={"attachment": ("contract.docx", b"PK\x03\x04", "application/octet-stream")},
                    follow_redirects=False)
    assert r.status_code == 303


@limit(reason="one proof per invoice or expense; a second file is not stored")
def test_two_attachments(client):
    from tracker import attachments, db
    from files import PDF
    client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "A", "amount": "1", "currency": "RON"},
                files={"attachment": ("one.pdf", PDF, "application/pdf")})
    attachments.save("invoices", 1, "two.pdf", PDF)
    assert len(db.get("invoices", 1)["attachment"].split(",")) == 2


@limit(reason="an invoice keeps the rate it was converted with; changing rates.csv does not re-convert it")
def test_reconversion_after_rates_change(client, rates_path):
    client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "A", "amount": "100", "currency": "EUR"})
    rates_path.write_text(rates_path.read_text().replace("5.0000", "6.0000"))
    assert "600,00" in client.get("/income").text


@limit(reason="a currency missing from rates.csv is refused; there is no default rate")
def test_unknown_currency_defaults_to_one():
    rates, _ = load_rates()
    assert find_rate(rates, "GBP", TODAY)[0] == Decimal(1)


@limit(reason="a gap of more than 10 days in the rates is never bridged")
def test_long_rate_gap(rates_path):
    rates_path.write_text("date,EUR\n2025-01-02,5\n")
    rates, _ = load_rates()
    assert find_rate(rates, "EUR", date(2025, 2, 1))[0] == Decimal(5)


@limit(reason="opting in to CAS below 12 minimum wages is not modelled: under the threshold CAS is always 0")
def test_cas_opt_in():
    assert taxes.compute(LAST_YEAR, Decimal("10000"), Decimal(0))["cas"] > 0


@limit(reason="personal exemptions (for example for people with a disability) are not modelled")
def test_personal_exemption():
    r = taxes.compute(LAST_YEAR, Decimal("100000"), Decimal(0))
    assert r["tax"] == 0


@limit(reason="CAS and CASS are never deductible as expenses themselves; they only reduce the taxable income")
def test_contributions_as_expenses():
    r = taxes.compute(LAST_YEAR, Decimal("100000"), Decimal(0))
    assert r["net"] == Decimal("100000") - r["cas"] - r["cass"]


@limit(reason="a CSV without a currency column is refused; RON is not assumed")
def test_csv_without_currency(client):
    r = client.post("/income/import", files=csv_upload(f"date,client,amount\n{LAST_YEAR}-01-01,A,1\n"))
    assert "1 invoice(s) added" in r.text


@limit(reason="the salaried-job checkbox never changes past years; earlier years are chosen separately")
def test_checkbox_changes_last_year(client, settings_path):
    import json
    client.post("/settings", data={"name": "X", "salaried_now": "1"})
    assert json.loads(settings_path.read_text())["years"][LAST_YEAR].get("has_salary") is True


@limit(reason="a salaried job cannot be marked for a year before the PFA was registered")
def test_salaried_year_before_registration(client, settings_path):
    import json
    client.post("/settings", data={"name": "X", "trade_register": f"F40/1/{YEAR}", "salaried_years": [LAST_YEAR]})
    assert json.loads(settings_path.read_text())["years"][LAST_YEAR].get("has_salary") is True


@limit(reason="only the F<county>/<number>/<year> Trade Register format is accepted, not company (J) numbers")
def test_company_trade_register_number():
    from tracker.settings import clean_trade_register
    assert clean_trade_register("J40/1234/2019")


@limit(reason="invoice proofs are named <number>_<client>; the name of the uploaded file is not kept")
def test_invoice_proof_keeps_upload_name(client):
    import os
    from files import PDF
    from tracker.config import ATTACHMENTS_DIR
    client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "A", "number": "7", "amount": "1",
                                     "currency": "RON"}, files={"attachment": ("my scan.pdf", PDF, "application/pdf")})
    assert "my_scan.pdf" in os.listdir(ATTACHMENTS_DIR / "invoices")[0]



# Invoicing

@pytest.fixture
def invoicing_ready(settings_path):
    """A firm with an address, one series, one Romanian client and one product in RON and one in EUR."""
    import json
    from test_invoicing import add_client, add_product, add_series
    data = json.loads(settings_path.read_text())
    data["firm"]["address"] = "Str. Lungă 1, Cluj-Napoca"
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    return {"series": add_series(), "client": add_client(), "ron": add_product(name="Work"),
            "eur": add_product(name="Support", currency="EUR")}


@limit(reason="no e-Factura: the tracker makes a PDF invoice only, not the XML to upload to ANAF's SPV")
def test_efactura_xml_for_issued_invoices():
    from tracker import services
    assert hasattr(services, "efactura_xml")


@limit(reason="one invoice is in one currency; lines with products in different currencies are refused")
def test_invoice_mixing_currencies(invoicing_ready):
    from test_invoicing import issue
    r = invoicing_ready
    assert issue(r["series"], r["client"], [{"product_id": r["ron"]}, {"product_id": r["eur"]}]).ok


@limit(reason="invoices cannot be issued with a date after today")
def test_issue_invoice_dated_tomorrow(invoicing_ready):
    from test_invoicing import issue
    r = invoicing_ready
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert issue(r["series"], r["client"], [{"product_id": r["ron"]}], date=tomorrow).ok


@limit(reason="an invoice cannot have a negative quantity or price; an issued invoice is reversed with a credit note "
              "from Issued invoices instead")
def test_credit_note_with_negative_quantity(invoicing_ready):
    from test_invoicing import issue
    r = invoicing_ready
    assert issue(r["series"], r["client"], [{"product_id": r["ron"], "quantity": "-1"}]).ok


@limit(reason="a VAT payer's invoice always adds the VAT rate from Invoicing settings; reverse charge for EU "
              "business clients is not applied automatically")
def test_reverse_charge_for_eu_clients(invoicing_ready):
    from test_invoicing import add_client, issue
    from tracker import services
    r = invoicing_ready
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    de = add_client(name="Nordwind GmbH", country="DE", tax_id="DE123456789", city="Berlin")
    out = issue(r["series"], de, [{"product_id": r["eur"]}])
    assert out.ok
    assert services.invoicing_view()["issued"][0]["vat"] == "0"


@limit(reason="changing a generated invoice in Income does not print its PDF again; the PDF stays as issued")
def test_editing_income_reprints_the_pdf(invoicing_ready):
    import io
    from pypdf import PdfReader
    from test_invoicing import issue
    from tracker import services
    from tracker.config import ATTACHMENTS_DIR
    r = invoicing_ready
    out = issue(r["series"], r["client"], [{"product_id": r["ron"]}])
    form = services.invoice_for_edit(out.extra["income_id"])
    services.update_invoice(out.extra["income_id"], {**form, "amount": "999"})
    row = services.invoice_for_edit(out.extra["income_id"])
    text = PdfReader(io.BytesIO((ATTACHMENTS_DIR / "invoices" / row["attachment"]).read_bytes())).pages[0].extract_text()
    assert "999,00" in text

