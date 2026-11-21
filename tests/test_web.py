"""The pages: tabs, year selection and its limits, invoices by hand, expenses, proofs, settings."""
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import LAST_YEAR, TODAY, YEAR, csv_upload, sample_csv
from files import GIF, JPEG, PDF, PNG, WEBP, XML, big_pdf
from tracker import db
from tracker.config import ATTACHMENTS_DIR, MAX_ATTACHMENT_BYTES

PAGES = {"/": "overview", "/income": "income", "/expenses": "expenses", "/settings": "settings"}


def count(table: str) -> int:
    with db.connect() as con:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def show(http, path: str, year: str | None = None) -> str:
    """Opens a page, first choosing a year with the year picker if one is given."""
    if year:
        http.post("/year", data={"year": year, "page": PAGES[path]})
    return http.get(path).text


def offered_years(html: str) -> list[str]:
    return re.findall(r'name="year" value="(\d{4})" class="pill', html) or re.findall(r'<option value="(\d{4})"', html)


def add_invoice(http, **fields):
    data = {"date": f"{LAST_YEAR}-05-05", "client": "Acme", "amount": "100", "currency": "RON"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post("/income/add", data=data, files=files, follow_redirects=False)


def add_expense(http, **fields):
    data = {"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "100", "currency": "RON"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post("/expenses", data=data, files=files, follow_redirects=False)


# Tabs, years and addresses

def test_every_tab_opens_on_an_empty_database(client):
    for path in ("/", "/income", "/expenses", "/settings", "/static/style.css"):
        assert client.get(path).status_code == 200, path
    assert "No data yet" in client.get("/").text


def test_only_the_current_year_is_offered_when_there_are_no_records(client):
    for path in ("/", "/income", "/expenses"):
        assert offered_years(client.get(path).text) == [YEAR], path


def test_years_with_records_are_offered_and_nothing_else(client):
    client.post("/income/import", files=csv_upload(sample_csv(str(int(YEAR) - 1))))
    add_expense(client, date=f"{int(YEAR) - 2}-02-02")
    assert offered_years(client.get("/").text) == [YEAR, str(int(YEAR) - 1), str(int(YEAR) - 2)]


def test_a_year_loses_its_button_when_its_last_record_is_deleted(client):
    add_expense(client)
    assert LAST_YEAR in offered_years(client.get("/").text)
    client.post("/expenses/1/delete")
    assert offered_years(client.get("/").text) == [YEAR]


def test_page_addresses_never_carry_a_year(client):
    add_expense(client)
    for path in PAGES:
        html = client.get(path).text
        assert "?year=" not in html and "?msg=" not in html, path
        assert not re.search(r'href="[^"]*\?', html), path


@pytest.mark.parametrize("query", ["year=1999", f"year={LAST_YEAR}", f"year={YEAR}", "year=2999", "year=abc",
                                   "year=../..", "year=%3Cscript%3E", "msg=hello", "msg=%3Cscript%3Ealert(1)%3C/script%3E",
                                   "anything", "year=2025&year=2026", "year[]=1"])
def test_anything_added_to_a_page_address_is_dropped(client, query):
    add_expense(client)
    for path in PAGES:
        r = client.get(f"{path}?{query}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == path, (path, query)
        page = client.get(path).text
        assert "Addresses with extra parts are not used" in page
        assert "<script>alert" not in page and "alert(1)" not in page


def test_the_year_picker_changes_the_year_on_every_tab(client):
    add_expense(client)
    r = client.post("/year", data={"year": LAST_YEAR, "page": "income"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/income"
    for path, title in (("/", "Overview"), ("/income", "Income"), ("/expenses", "Expenses")):
        assert f"{title} {LAST_YEAR}" in client.get(path).text


@pytest.mark.parametrize("year", ["1999", str(int(YEAR) + 1), "2999", "abc", "", "20255", " " + YEAR, "２０２６",
                                  "<script>", "../..", LAST_YEAR])
def test_the_year_picker_refuses_years_without_records(client, year):
    r = client.post("/year", data={"year": year, "page": "overview"})
    assert r.status_code == 200 and "That year is not available" in r.text
    assert f"Overview {YEAR}" in r.text


def test_the_year_picker_only_returns_to_known_pages(client):
    for page in ("http://evil.example", "/etc/passwd", "../", "", "admin"):
        r = client.post("/year", data={"year": YEAR, "page": page}, follow_redirects=False)
        assert r.headers["location"] == "/"


@pytest.mark.parametrize("cookie", ["1999", str(int(YEAR) + 1), LAST_YEAR, "abc", "", "<script>", "9" * 3000])
def test_a_tampered_year_cookie_is_ignored(client, cookie):
    client.cookies.set("pfa_year", cookie)
    assert f"Overview {YEAR}" in client.get("/").text


def test_a_tampered_message_cookie_cannot_inject_html(client):
    client.cookies.set("pfa_flash", "%7B%22message%22%3A%20%22%3Cscript%3Ex%3C/script%3E%22%7D")
    html = client.get("/").text
    assert "<script>x" not in html and "&lt;script&gt;x" in html
    for junk in ("garbage{", "%5B1%2C2%5D", "%7B%22message%22%3A%205%7D"):
        client.cookies.set("pfa_flash", junk)
        assert client.get("/").status_code == 200


def test_messages_are_shown_once(client):
    add_expense(client)
    assert "Expense added." in client.get("/expenses").text
    assert "Expense added." not in client.get("/expenses").text


def test_year_buttons_become_a_drop_down_when_there_are_many_years(client):
    for y in range(int(YEAR) - 5, int(YEAR)):          # five past years + the current one = 6 buttons
        add_expense(client, date=f"{y}-03-03")
    html = client.get("/").text
    assert "year-select" not in html and len(offered_years(html)) == 6
    add_expense(client, date=f"{int(YEAR) - 6}-03-03")  # a seventh year: the buttons become a drop-down
    html = client.get("/").text
    assert "year-select" in html and html.count("<option") == 7
    assert re.search(r'<option value="\d{4}" selected', html)


def test_after_an_import_the_year_of_the_new_invoices_is_shown_and_kept(client):
    r = client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    assert f"Income {LAST_YEAR}" in r.text and "3 invoice(s) added" in r.text
    assert f"Overview {LAST_YEAR}" in client.get("/").text


def test_import_needs_a_file(client):
    assert "Choose a CSV file" in client.post("/income/import", files=csv_upload("", "empty.csv")).text


# Invoices by hand: the payment date decides the year and the rate

def test_invoice_paid_next_year_counts_next_year_at_the_payment_day_rate(client):
    r = add_invoice(client, date=f"{LAST_YEAR}-12-20", payment_date=f"{YEAR}-01-10", amount="1.000,00",
                    currency="eur", number="F-1", attachment=("proof (1).pdf", PDF, "application/pdf"))
    assert r.status_code == 303 and r.headers["location"] == "/income"
    row = dict(db.get("invoices", 1))
    assert row["date"] == f"{LAST_YEAR}-12-20" and row["payment_date"] == f"{YEAR}-01-10"
    assert row["income_date"] == f"{YEAR}-01-10" and row["amount_ron"] == "5000.00"
    assert f"{YEAR}-01-01" <= row["rate_date"] <= f"{YEAR}-01-10"
    html = client.get("/income").text
    assert f"Income {YEAR}" in html and "Acme" in html and "5.000,00" in html
    assert offered_years(html) == [YEAR]


def test_payment_day_rate_is_used_not_the_invoice_day_rate(client, rates_path):
    rates_path.write_text(f"date,EUR\n{LAST_YEAR}-03-02,4.0000\n{LAST_YEAR}-03-16,6.0000\n")
    add_invoice(client, date=f"{LAST_YEAR}-03-02", payment_date=f"{LAST_YEAR}-03-16", currency="EUR", amount="10")
    row = db.get("invoices", 1)
    assert (row["rate"], row["rate_date"], row["amount_ron"]) == ("6.0000", f"{LAST_YEAR}-03-16", "60.00")


def test_invoice_without_payment_date_counts_on_its_invoice_date(client):
    add_invoice(client, date=f"{LAST_YEAR}-12-20")
    assert db.get("invoices", 1)["income_date"] == f"{LAST_YEAR}-12-20"
    assert "Acme" in client.get("/income").text


def test_payment_on_the_invoice_day_is_allowed(client):
    assert add_invoice(client, date=f"{LAST_YEAR}-05-05", payment_date=f"{LAST_YEAR}-05-05").status_code == 303


@pytest.mark.parametrize("paid", [f"{LAST_YEAR}-05-04", f"{LAST_YEAR}-01-01", "2000-01-01"])
def test_payment_before_the_invoice_date_is_refused(client, paid):
    r = add_invoice(client, date=f"{LAST_YEAR}-05-05", payment_date=paid)
    assert r.status_code == 200 and "is before the invoice date" in r.text and count("invoices") == 0


def test_payment_date_field_cannot_be_set_before_the_invoice_date_in_the_browser(client):
    assert 'document.getElementById("inv-paid").min = this.value' in client.get("/income").text
    r = add_invoice(client, date=f"{LAST_YEAR}-05-05", payment_date=f"{LAST_YEAR}-05-01")
    assert f'min="{LAST_YEAR}-05-05"' in r.text


def test_duplicate_invoice_by_hand_is_refused(client):
    assert add_invoice(client).status_code == 303
    r = add_invoice(client)
    assert r.status_code == 200 and "already stored" in r.text and "delete it and add it again" in r.text
    assert count("invoices") == 1


def test_an_unpaid_invoice_gets_its_payment_date_when_entered_again(client):
    add_invoice(client, date=f"{LAST_YEAR}-12-20", currency="EUR", amount="10")
    assert db.get("invoices", 1)["income_date"] == f"{LAST_YEAR}-12-20"
    r = add_invoice(client, date=f"{LAST_YEAR}-12-20", currency="EUR", amount="10", payment_date=f"{YEAR}-01-05",
                    attachment=("paid.pdf", PDF, "application/pdf"))
    assert r.status_code == 303 and "Date of payment added" in client.get("/income").text
    row = db.get("invoices", 1)
    assert count("invoices") == 1 and row["payment_date"] == f"{YEAR}-01-05" and row["income_date"] == f"{YEAR}-01-05"
    assert f"{YEAR}-01-01" <= row["rate_date"] <= f"{YEAR}-01-05" and row["attachment"] == "no-number_Acme.pdf"
    r = add_invoice(client, date=f"{LAST_YEAR}-12-20", currency="EUR", amount="10", payment_date=f"{YEAR}-01-06")
    assert r.status_code == 200 and "already stored" in r.text          # a recorded payment is not overwritten
    assert db.get("invoices", 1)["payment_date"] == f"{YEAR}-01-05"


@pytest.mark.parametrize("field, value, message", [
    ("date", (TODAY + timedelta(days=1)).isoformat(), "in the future"),
    ("date", "2999-01-01", "in the future"),
    ("payment_date", (TODAY + timedelta(days=1)).isoformat(), "in the future"),
    ("date", "", "the invoice date is missing"),
    ("date", "01/31/2025", "unrecognised date"),
    ("date", "1999-12-31", "before 2000"),
    ("client", "   ", "empty client"),
    ("amount", "abc", "unrecognised amount"),
    ("amount", "", "empty amount"),
    ("amount", "1" + "0" * 20, "too large"),
    ("currency", "", "empty currency"),
    ("currency", "GBP", "no rates for GBP"),
])
def test_invoice_by_hand_is_refused_with_a_reason_and_keeps_the_form(client, field, value, message):
    r = add_invoice(client, **{field: value})
    assert r.status_code == 200 and "Invoice not added" in r.text and message in r.text
    assert 'value="Acme"' in r.text or field == "client"
    assert count("invoices") == 0


def test_date_inputs_are_capped_at_today(client):
    assert client.get("/income").text.count(f'max="{TODAY.isoformat()}"') == 2
    assert client.get("/expenses").text.count(f'max="{TODAY.isoformat()}"') == 1


def test_deleting_an_invoice_removes_its_proof(client):
    add_invoice(client, attachment=("a.pdf", PDF, "application/pdf"))
    assert os.listdir(ATTACHMENTS_DIR / "invoices") == ["no-number_Acme.pdf"]
    r = client.post("/income/1/delete", follow_redirects=False)
    assert r.headers["location"] == "/income" and "Invoice deleted." in client.get("/income").text
    assert os.listdir(ATTACHMENTS_DIR / "invoices") == []
    assert client.get("/income/1/attachment").status_code == 404


def test_deleting_a_missing_or_impossible_row(client):
    client.post("/income/999999/delete", follow_redirects=False)
    assert "Nothing to delete." in client.get("/income").text
    assert client.post("/income/abc/delete").status_code == 422
    assert client.post("/income/0/delete").status_code == 422
    assert client.get("/income/99999999999999999999999/attachment").status_code == 422


# Expenses

def test_expense_with_proof_in_foreign_currency(client):
    r = add_expense(client, amount="10", currency="USD", category="tools", attachment=("bill.png", PNG, "image/png"))
    assert r.headers["location"] == "/expenses"
    html = client.get("/expenses").text
    assert f"Expenses {LAST_YEAR}" in html and "40,00" in html and "tools" in html and "Expense added." in html
    assert client.get("/expenses/1/attachment").content == PNG


@pytest.mark.parametrize("field, value, message", [
    ("date", (TODAY + timedelta(days=1)).isoformat(), "in the future"),
    ("date", "", "unrecognised date"),
    ("date", "0001-01-01", "before 2000"),
    ("description", "", "description is empty"),
    ("amount", "0", "must be positive"),
    ("amount", "-5", "must be positive"),
    ("amount", "1e9", "unrecognised amount"),
    ("currency", "XXX", "no rates for XXX"),
])
def test_expense_is_refused_with_a_reason_and_keeps_the_form(client, field, value, message):
    r = add_expense(client, **{field: value})
    assert r.status_code == 200 and "Expense not added" in r.text and message in r.text
    assert 'value="Desk"' in r.text or field == "description"
    assert count("expenses") == 0


def test_expense_date_defaults_to_today_in_the_current_year(client):
    assert f'value="{TODAY.isoformat()}" max="{TODAY.isoformat()}"' in client.get("/expenses").text
    add_expense(client)
    assert f'value="" max="{TODAY.isoformat()}"' in show(client, "/expenses", LAST_YEAR)


def test_deleting_an_expense_removes_its_proof(client):
    add_expense(client, attachment=("bill.pdf", PDF, "application/pdf"))
    client.post("/expenses/1/delete")
    assert os.listdir(ATTACHMENTS_DIR / "expenses") == []


# Proofs

def test_invoice_and_expense_proofs_live_in_separate_folders(client):
    add_invoice(client, attachment=("same.pdf", PDF, "application/pdf"))
    add_expense(client, attachment=("same.png", PNG, "image/png"))
    assert os.listdir(ATTACHMENTS_DIR / "invoices") == ["no-number_Acme.pdf"]
    assert os.listdir(ATTACHMENTS_DIR / "expenses") == ["1_same.png"]
    assert client.get("/income/1/attachment").content == PDF
    assert client.get("/expenses/1/attachment").content == PNG


@pytest.mark.parametrize("name, data", [("p.pdf", PDF), ("p.PDF", PDF), ("i.png", PNG), ("i.jpg", JPEG),
                                        ("i.jpeg", JPEG), ("i.gif", GIF), ("i.webp", WEBP), ("x.xml", XML),
                                        ("t.txt", "Chitanță nr. 12\nSumă: 100 lei\n".encode())])
def test_every_allowed_type_is_accepted_when_the_content_matches(client, name, data):
    assert add_expense(client, attachment=(name, data, "application/octet-stream")).status_code == 303
    assert client.get("/expenses/1/attachment").content == data


@pytest.mark.parametrize("name, data, message", [
    ("fake.pdf", PNG, "not a PDF file"),
    ("fake.pdf", b"%PDF-1.4 just a header, nothing else", "damaged or unreadable PDF"),
    ("cut.pdf", PDF[: len(PDF) // 2], "damaged or unreadable PDF"),
    ("fake.png", PDF, "not a readable PNG image"),
    ("fake.png", JPEG, "is a JPEG image but is named .png"),
    ("fake.jpg", PNG, "is a PNG image but is named .jpg"),
    ("fake.gif", b"GIF89a" + b"\x00" * 10, "not a readable GIF image"),
    ("fake.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "not a readable WEBP image"),
    ("cut.png", PNG[:30], "not a readable PNG image"),
    ("fake.xml", b"<a><b></a>", "not well-formed XML"),
    ("bomb.xml", b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>', "not well-formed XML"),
    ("fake.txt", b"\x00\x01\x02binary", "binary data"),
    ("fake.txt", b"\xff\xfe\xfd not utf-8", "not a UTF-8 text file"),
    ("virus.exe", b"MZ", "not an allowed type"),
    ("page.html", b"<html></html>", "not an allowed type"),
    ("x.pdf.exe", PDF, "not an allowed type"),
    ("noext", PDF, "not an allowed type"),
    ("empty.pdf", b"", "attachment is empty"),
])
def test_renamed_damaged_or_wrong_files_are_refused(client, name, data, message):
    r = add_invoice(client, attachment=(name, data, "application/pdf"))
    assert r.status_code == 200 and message in r.text
    assert count("invoices") == 0
    assert not (ATTACHMENTS_DIR / "invoices").exists() or os.listdir(ATTACHMENTS_DIR / "invoices") == []
    r = add_expense(client, attachment=(name, data, "application/pdf"))
    assert r.status_code == 200 and message in r.text and count("expenses") == 0


def test_files_over_20_mb_are_refused(client):
    r = add_invoice(client, attachment=("big.pdf", PDF + b"%" * MAX_ATTACHMENT_BYTES, "application/pdf"))
    assert r.status_code == 200 and "larger than 20 MB" in r.text and count("invoices") == 0


def test_size_limit_boundary_is_inclusive(client, monkeypatch):
    from tracker import attachments
    limit = len(big_pdf(50_000))
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", limit)
    r = add_invoice(client, attachment=("over.pdf", big_pdf(limit + 1), "application/pdf"))
    assert r.status_code == 200 and "larger than" in r.text
    assert add_invoice(client, attachment=("exact.pdf", big_pdf(limit), "application/pdf")).status_code == 303


@pytest.mark.parametrize("name", ["../../../etc/passwd.pdf", "..\\..\\x.pdf", "/etc/passwd.pdf", "a/b/c.pdf",
                                  "ünïcödé ţ.pdf", "..pdf", "a" * 300 + ".pdf", ".pdf", "x.exe.pdf"])
def test_expense_proof_names_are_made_safe(client, name):
    assert add_expense(client, attachment=(name, PDF, "application/pdf")).status_code == 303
    stored = os.listdir(ATTACHMENTS_DIR / "expenses")
    assert len(stored) == 1 and re.fullmatch(r"1_[A-Za-z0-9._-]{1,90}\.pdf", stored[0]), stored
    assert ".." not in stored[0] and not Path("/etc/passwd.pdf").exists()


def test_proof_links_cannot_reach_other_files(client):
    add_invoice(client)
    db.set_attachment("invoices", 1, "../../settings.json")
    assert client.get("/income/1/attachment").status_code == 404
    db.set_attachment("invoices", 1, "/etc/passwd")
    assert client.get("/income/1/attachment").status_code == 404
    assert client.get("/income/1/attachment/../../settings.json").status_code == 404
    assert client.get("/expenses/1/attachment").status_code == 404


# Settings

def test_settings_are_saved_and_shown_in_the_header(client, settings_path):
    add_expense(client)
    r = client.post("/settings", data={"name": "Popescu Ion PFA", "cui": "ro 14399840", "caen_code": "69.20",
                                       "caen_revision": "3", "trade_register": f"F40/1234/{int(YEAR) - 3}",
                                       "salaried_years": [LAST_YEAR]}, follow_redirects=False)
    assert r.headers["location"] == "/settings"
    saved = json.loads(settings_path.read_text())
    assert {k: saved["firm"][k] for k in ("name", "cui", "caen_code", "caen_revision")} == {
        "name": "Popescu Ion PFA", "cui": "RO14399840", "caen_code": "6920", "caen_revision": "3"}
    assert saved["years"][LAST_YEAR]["has_salary"] is True and saved["years"][YEAR]["has_salary"] is False
    assert "Popescu Ion PFA · CUI RO14399840 · CAEN 6920" in client.get("/").text


def test_salaried_job_cannot_be_set_for_years_outside_the_pfa_lifetime(client, settings_path):
    before = settings_path.read_text()
    r = client.post("/settings", data={"name": "X", "trade_register": f"F40/1/{LAST_YEAR}", "salaried_years": ["1999", "2999"]})
    assert "Not saved" in r.text and "years in which the PFA existed" in r.text
    assert settings_path.read_text() == before


@pytest.mark.parametrize("data, message", [
    ({"cui": "RO14399841"}, "check digit does not match"),
    ({"cui": "abc"}, "not valid"),
    ({"caen_code": "62"}, "must be 4 digits"),
    ({"caen_code": "62a0"}, "must be 4 digits"),
    ({"caen_code": "6210" * 10}, "must be 4 digits"),
])
def test_bad_firm_details_are_refused_and_nothing_saved(client, settings_path, data, message):
    before = settings_path.read_text()
    r = client.post("/settings", data={"name": "X", "salary_years": "", **data})
    assert r.status_code == 200 and "Not saved" in r.text and message in r.text
    assert settings_path.read_text() == before


def test_unknown_caen_code_is_saved_with_a_warning(client):
    r = client.post("/settings", data={"name": "X", "caen_code": "9999", "caen_revision": "3", "salary_years": ""},
                    follow_redirects=True)
    assert "9999 is not a CAEN Rev.3 code" in r.text


def test_a_rev2_code_entered_as_rev3_gets_a_hint(client):
    r = client.post("/settings", data={"name": "X", "caen_code": "6201", "caen_revision": "3", "salary_years": ""},
                    follow_redirects=True)
    assert "exists in Rev.2" in r.text


def test_pages_survive_broken_settings_rates_and_caen_files(client, settings_path, rates_path, caen_path):
    settings_path.write_text("{ broken")
    rates_path.write_text("garbage")
    caen_path.write_bytes(b"garbage")
    for path in PAGES:
        assert client.get(path).status_code == 200, path
    assert "not valid JSON" in client.get("/").text
    r = client.post("/settings", data={"name": "X", "salary_years": ""})
    assert r.text.count("Not saved") == 1 and '"msg err">settings file' not in r.text


def test_unknown_pages_and_methods(client):
    assert client.get("/nope").status_code == 404
    assert client.get("/income/1/attachment").status_code == 404
    assert client.post("/income/import").status_code == 422
    assert client.get("/year").status_code == 405
    assert client.get("/income/add").status_code == 405
