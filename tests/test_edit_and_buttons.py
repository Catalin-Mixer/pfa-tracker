"""Editing invoices and expenses in the browser version, the row buttons, and switching/deleting
profiles only from Settings → Profiles."""
import json
import os
import re

import pytest

from conftest import LAST_YEAR, TODAY, YEAR, csv_upload, sample_csv
from files import PDF, PNG
from tracker import db, services
from tracker.config import ATTACHMENTS_DIR


def add_invoice(http, **fields):
    data = {"date": f"{LAST_YEAR}-05-05", "client": "Acme", "number": "A-1", "amount": "100", "currency": "EUR"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post("/income/add", data=data, files=files, follow_redirects=False)


def edit_invoice(http, row_id=1, **fields):
    data = {"date": f"{LAST_YEAR}-05-05", "client": "Acme", "number": "A-1", "amount": "100", "currency": "EUR"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post(f"/income/{row_id}/edit", data=data, files=files, follow_redirects=False)


def add_expense(http, **fields):
    data = {"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "100", "currency": "RON"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post("/expenses", data=data, files=files, follow_redirects=False)


def edit_expense(http, row_id=1, **fields):
    data = {"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "100", "currency": "RON"}
    data.update({k: v for k, v in fields.items() if k != "attachment"})
    files = {"attachment": fields["attachment"]} if "attachment" in fields else None
    return http.post(f"/expenses/{row_id}/edit", data=data, files=files, follow_redirects=False)


def proofs(table):
    folder = ATTACHMENTS_DIR / table
    return sorted(os.listdir(folder)) if folder.exists() else []


# Editing invoices

def test_every_invoice_row_has_edit_and_delete_buttons(client):
    add_invoice(client)
    html = client.get("/income").text
    assert '<a class="button secondary" href="/income/1/edit">Edit</a>' in html
    assert '<button class="danger" type="submit">Delete</button>' in html


def test_the_edit_form_is_filled_with_the_stored_invoice(client):
    add_invoice(client, amount="1234.5", payment_date=f"{LAST_YEAR}-05-20", attachment=("s.pdf", PDF, "application/pdf"))
    html = client.get("/income/1/edit").text
    assert "<h3>Edit invoice</h3>" in html and 'action="/income/1/edit"' in html
    for value in (f'value="{LAST_YEAR}-05-05"', f'value="{LAST_YEAR}-05-20"', 'value="Acme"', 'value="A-1"',
                  'value="1.234,50"', 'value="EUR"'):
        assert value in html, value
    assert "Save changes" in html and '<a class="button secondary" href="/income">Cancel</a>' in html
    assert "Remove the current proof (A-1_Acme.pdf)" in html and 'class="editing"' in html


def test_editing_changes_the_values_and_converts_again(client):
    add_invoice(client)
    assert db.get("invoices", 1)["amount_ron"] == "500.00"
    r = edit_invoice(client, amount="250", currency="USD")
    assert r.status_code == 303 and r.headers["location"] == "/income"
    assert "Invoice changed." in client.get("/income").text
    row = db.get("invoices", 1)
    assert (row["amount"], row["currency"], row["rate"], row["amount_ron"]) == ("250", "USD", "4.0000", "1000.00")


def test_adding_a_payment_date_moves_the_invoice_to_the_payment_year(client):
    add_invoice(client, date=f"{LAST_YEAR}-12-20")
    edit_invoice(client, date=f"{LAST_YEAR}-12-20", payment_date=f"{YEAR}-01-05")
    row = db.get("invoices", 1)
    assert row["income_date"] == f"{YEAR}-01-05" and row["payment_date"] == f"{YEAR}-01-05"
    html = client.get("/income").text
    assert f"Income {YEAR}" in html and "Acme" in html


def test_removing_the_payment_date_moves_it_back(client):
    add_invoice(client, date=f"{LAST_YEAR}-12-20", payment_date=f"{YEAR}-01-05")
    edit_invoice(client, date=f"{LAST_YEAR}-12-20", payment_date="")
    assert db.get("invoices", 1)["income_date"] == f"{LAST_YEAR}-12-20"


@pytest.mark.parametrize("field, value, message", [
    ("date", "", "the invoice date is missing"),
    ("date", "2999-01-01", "in the future"),
    ("payment_date", f"{LAST_YEAR}-01-01", "is before the invoice date"),
    ("client", " ", "empty client"),
    ("amount", "abc", "unrecognised amount"),
    ("currency", "GBP", "no rates for GBP"),
])
def test_a_wrong_edit_is_refused_nothing_changes_and_the_form_stays_in_edit_mode(client, field, value, message):
    add_invoice(client)
    before = dict(db.get("invoices", 1))
    r = edit_invoice(client, **{field: value})
    assert r.status_code == 200 and "Invoice not changed" in r.text and message in r.text
    assert "<h3>Edit invoice</h3>" in r.text and 'action="/income/1/edit"' in r.text
    assert dict(db.get("invoices", 1)) == before


def test_an_edit_cannot_make_two_invoices_identical(client):
    add_invoice(client)
    add_invoice(client, number="A-2")
    r = edit_invoice(client, row_id=2, number="A-1")
    assert "another stored invoice already has this" in r.text and db.get("invoices", 2)["number"] == "A-2"
    assert edit_invoice(client, row_id=2, number="A-2", amount="101").status_code == 303


def test_the_proof_is_kept_and_renamed_when_number_or_client_change(client):
    add_invoice(client, attachment=("s.pdf", PDF, "application/pdf"))
    assert proofs("invoices") == ["A-1_Acme.pdf"]
    edit_invoice(client, number="B-9", client="Ştefan SRL")
    assert proofs("invoices") == ["B-9_Stefan_SRL.pdf"] and db.get("invoices", 1)["attachment"] == "B-9_Stefan_SRL.pdf"
    assert client.get("/income/1/attachment").content == PDF


def test_the_proof_can_be_replaced(client):
    add_invoice(client, attachment=("s.pdf", PDF, "application/pdf"))
    edit_invoice(client, attachment=("new.png", PNG, "image/png"))
    assert proofs("invoices") == ["A-1_Acme.png"] and client.get("/income/1/attachment").content == PNG


def test_the_proof_can_be_removed(client):
    add_invoice(client, attachment=("s.pdf", PDF, "application/pdf"))
    edit_invoice(client, remove_proof="1")
    assert proofs("invoices") == [] and db.get("invoices", 1)["attachment"] == ""
    assert "Remove the current proof" not in client.get("/income/1/edit").text


def test_a_bad_replacement_proof_is_refused_and_the_old_one_stays(client):
    add_invoice(client, attachment=("s.pdf", PDF, "application/pdf"))
    before = dict(db.get("invoices", 1))
    r = edit_invoice(client, amount="999", attachment=("fake.pdf", PNG, "application/pdf"))
    assert "Invoice not changed" in r.text and "not a PDF file" in r.text
    assert dict(db.get("invoices", 1)) == before and proofs("invoices") == ["A-1_Acme.pdf"]


def test_an_invoice_without_proof_can_get_one_by_editing(client):
    add_invoice(client)
    edit_invoice(client, attachment=("s.pdf", PDF, "application/pdf"))
    assert proofs("invoices") == ["A-1_Acme.pdf"]


def test_editing_entries_that_do_not_exist(client):
    r = client.get("/income/42/edit", follow_redirects=False)
    assert r.status_code == 303 and "does not exist" in client.get("/income").text
    r = edit_invoice(client, row_id=42)
    assert r.status_code == 303 and "no longer exists" in client.get("/income").text
    assert client.get("/income/0/edit").status_code == 422
    assert client.get("/income/abc/edit").status_code == 422
    assert client.get("/income/99999999999999999999999/edit").status_code == 422


def test_an_imported_invoice_can_be_edited_too(client):
    client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    edit_invoice(client, row_id=1, date=f"{LAST_YEAR}-01-15", client="Nordwind GmbH", number="A-1", amount="1100",
                 currency="EUR")
    row = db.get("invoices", 1)
    assert row["amount_ron"] == "5500.00" and row["source"] == "invoices.csv"


# Editing expenses

def test_every_expense_row_has_edit_and_delete_buttons_and_a_filled_edit_form(client):
    add_expense(client, category="office", attachment=("bon.png", PNG, "image/png"))
    html = client.get("/expenses").text
    assert '<a class="button secondary" href="/expenses/1/edit">Edit</a>' in html and 'class="danger"' in html
    html = client.get("/expenses/1/edit").text
    assert "<h3>Edit expense</h3>" in html and 'value="Desk"' in html and 'value="office"' in html
    assert 'value="100,00"' in html and "Remove the current proof (bon.png)" in html


def test_editing_an_expense(client):
    add_expense(client)
    r = edit_expense(client, date=f"{int(LAST_YEAR) - 1}-03-03", description="Chair", amount="10", currency="USD")
    assert r.status_code == 303
    row = db.get("expenses", 1)
    assert (row["date"], row["description"], row["amount_ron"]) == (f"{int(LAST_YEAR) - 1}-03-03", "Chair", "40.00")
    assert f"Expenses {int(LAST_YEAR) - 1}" in client.get("/expenses").text


@pytest.mark.parametrize("field, value, message", [("date", "", "unrecognised date"), ("description", "", "description is empty"),
                                                   ("amount", "0", "must be positive"), ("currency", "XXX", "no rates for XXX")])
def test_a_wrong_expense_edit_is_refused(client, field, value, message):
    add_expense(client)
    before = dict(db.get("expenses", 1))
    r = edit_expense(client, **{field: value})
    assert "Expense not changed" in r.text and message in r.text and "<h3>Edit expense</h3>" in r.text
    assert dict(db.get("expenses", 1)) == before


def test_expense_proof_replace_and_remove(client):
    add_expense(client, attachment=("a.pdf", PDF, "application/pdf"))
    edit_expense(client, attachment=("b.png", PNG, "image/png"))
    assert proofs("expenses") == ["1_b.png"]
    edit_expense(client, remove_proof="1")
    assert proofs("expenses") == [] and db.get("expenses", 1)["attachment"] == ""


# Buttons everywhere

def test_no_text_style_delete_links_are_left(client, settings_path):
    add_invoice(client)
    add_expense(client)
    services.save_custom_theme("web", "Mine", {"accent": "#7a3fb0", "background": "#f6f3fa", "surface": "#ffffff", "text": "#221b2b"})
    for path in ("/", "/income", "/expenses", "/settings"):
        html = client.get(path).text
        assert 'class="link"' not in html and ">delete<" not in html, path
    assert client.get("/settings").text.count('<button class="danger" type="submit">Delete</button>') >= 1
    css = client.get("/static/style.css").text
    assert "button.danger" in css and "border: 1px solid var(--bad)" in css



# The same amount written differently is the same invoice

@pytest.mark.parametrize("first, second", [("100", "100,00"), ("100.0", "100"), ("1.234,50", "1234.5"), ("1234,5", "1.234,50")])
def test_an_amount_written_differently_is_still_a_duplicate(client, first, second):
    assert add_invoice(client, amount=first).status_code == 303
    r = add_invoice(client, amount=second)
    assert r.status_code == 200 and "already stored" in r.text


def test_saving_an_edit_unchanged_keeps_it_identical(client):
    add_invoice(client, amount="100")
    add_invoice(client, number="A-2", amount="100")
    edit_invoice(client, row_id=2, number="A-2", amount="100,00")          # what the edit form shows
    r = edit_invoice(client, row_id=2, number="A-1", amount="100,00")
    assert "another stored invoice already has this" in r.text
    text = sample_csv(LAST_YEAR)
    client.post("/income/import", files=csv_upload(text))
    r = client.post("/income/import", files=csv_upload(text))
    assert "0 invoice(s) added" in r.text


def test_old_rows_are_brought_to_the_same_form(client):
    add_invoice(client)
    with db.connect() as con:
        con.execute("UPDATE invoices SET amount='100.00' WHERE id=1")
    db.init_db()
    assert db.get("invoices", 1)["amount"] == "100"
