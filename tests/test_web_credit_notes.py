"""Credit notes in the browser version: the Credit note button of Issued invoices, its form (each line with what
is left to credit, Credit all, series, date, refund date, reason) with live totals, refused input kept with the
reason, issuing with its PDF and a way to Income, what the list says about credit notes, and in Income the
warning before deleting a credited invoice and a credit note's amount that stays negative."""
import io
import re
from html import unescape

import pytest

from conftest import TODAY, YEAR
from test_web_invoicing import HOUR, PAGE, firm_with_address, flash, invoice, post, setup_lists, text  # noqa: F401
from tracker import db, services

TODAY_ISO = TODAY.isoformat()


def credit_data(quantities: dict, **fields) -> dict:
    """The credit note form as sent: {line: quantity}, the invoice's series, today and a fresh token."""
    data = {"series_id": "1", "date": TODAY_ISO, "payment_date": "", "reason": "", "action": "issue",
            "token": services.new_invoice_token(), **fields}
    data.update({f"quantity_{n}": q for n, q in quantities.items()})
    return data


def credit(http, issued_id, quantities, **fields):
    return post(http, f"/invoicing/issued/{issued_id}/credit", credit_data(quantities, **fields))


def issued_row(html: str, number: str) -> str:
    """The HTML of one row of the Issued invoices list."""
    start = html.index(f"<b>{number}</b>")
    return html[start:html.index("</tr>", start)]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def issued(client, firm_with_address):
    """Invoice PFA-0001: 10 hours at 150,50 and 4 pages at 12,3456 (1.554,38 RON), in Income for this year."""
    setup_lists(client, HOUR, PAGE)
    r = post(client, "/invoicing/issue", invoice([("1", "10", ""), ("2", "4", "")]))
    assert r.status_code == 303
    return client


# The list

def test_each_invoice_offers_a_credit_note_unless_it_is_one_was_deleted_from_income_or_is_fully_credited(issued):
    client = issued
    for _ in range(2):
        assert post(client, "/invoicing/issue", invoice([("1", "1", "")])).status_code == 303    # PFA-0002, PFA-0003
    html = client.get("/invoicing/issued").text
    assert all(f"/invoicing/issued/{n}/credit" in html for n in (1, 2, 3))
    assert credit(client, 1, {1: "10", 2: "4"}).status_code == 303                                  # PFA-0004: all
    assert credit(client, 3, {1: "0,5"}).status_code == 303                                          # PFA-0005: part
    assert post(client, "/income/2/delete", {}).status_code == 303
    html = client.get("/invoicing/issued").text
    assert "Credit note reversing PFA-0001" in issued_row(html, "PFA-0004")
    assert "Credit note reversing PFA-0003" in issued_row(html, "PFA-0005")
    assert "Fully credited by PFA-0004" in issued_row(html, "PFA-0001")
    assert "Partly credited by PFA-0005" in issued_row(html, "PFA-0003")
    offered = {n for n in ("PFA-0001", "PFA-0002", "PFA-0003", "PFA-0004", "PFA-0005")
               if ">Credit note</a>" in issued_row(html, n)}
    assert offered == {"PFA-0003"}
    assert "-1.554,38" in issued_row(html, "PFA-0004") and "-75,25" in issued_row(html, "PFA-0005")
    heads = re.findall(r"<th(?: [^>]*)?>([^<]*)", html)
    assert heads[:8] == ["Number", "Invoice date", "Due date", "Client", "Total", "Currency", "In Income", "Credit"]


# The form

def test_the_form_shows_each_line_with_what_is_left_to_credit(issued):
    client = issued
    assert credit(client, 1, {2: "4"}).status_code == 303                                          # PFA-0002
    r = client.get("/invoicing/issued/1/credit")
    assert r.status_code == 200 and 'href="/invoicing/issued" class="on"' in r.text
    page = text(r.text)
    assert "Credit note for invoice PFA-0001 · this will be PFA-0003" in page
    assert "Invoice PFA-0001 of " + TODAY_ISO + " · Țară Soft SRL — «Ünïcödé» · 1.881,80 RON" not in page  # not a VAT payer
    assert "Invoice PFA-0001 of " + TODAY_ISO + " · Țară Soft SRL — «Ünïcödé» · 1.554,38 RON · credited so far by PFA-0002" in page
    heads = re.findall(r"<th(?: [^>]*)?>([^<]*)", r.text)
    assert heads == ["#", "Product or service", "Unit", "Price per unit", "Invoiced", "Credited", "Left",
                     "Quantity to credit", "Value"]
    assert "1 Consultanță IT & „QA” oră / hour 150,50 10 0 10" in page
    assert "2 Traducere pagină / page 12,3456 4 4 0 fully credited" in page
    assert 'name="quantity_1" value="" data-left="10"' in r.text and 'name="quantity_2"' not in r.text
    assert re.search(r'<option value="1" selected>PFA \(next PFA-0003\)', r.text)
    assert f'name="date" value="{TODAY_ISO}" min="{TODAY_ISO}" max="{TODAY_ISO}"' in r.text
    assert 'name="payment_date" value=""' in r.text and 'name="reason" rows="3" maxlength="500"' in r.text
    assert ">Credit all</button>" in r.text and ">Issue credit note and add to Income</button>" in r.text
    assert "Still needed before issuing: choose at least one line of the invoice and the quantity to credit." in page
    assert 'name="token" value="' in r.text and "/static/invoicing.js" in r.text


@pytest.mark.parametrize("setup, words", [
    ("credit note", "No credit note can be issued: PFA-0002 is a credit note; only an invoice can be credited."),
    ("deleted", "No credit note can be issued: invoice PFA-0001 was deleted from Income, so it cannot be credited"),
    ("fully", "No credit note can be issued: invoice PFA-0001 is already fully credited (by PFA-0002)."),
    ("unknown", "No credit note can be issued: that invoice does not exist (any more)."),
])
def test_a_form_that_cannot_be_used_is_not_opened_and_says_why(issued, setup, words):
    client = issued
    target = {"credit note": 2, "deleted": 1, "fully": 1, "unknown": 99}[setup]
    if setup in ("credit note", "fully"):
        credit(client, 1, {1: "10", 2: "4"})
    if setup == "deleted":
        post(client, "/income/1/delete", {})
    r = client.get(f"/invoicing/issued/{target}/credit", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/invoicing/issued"
    assert flash(r)["error"].startswith(words)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", str(2**63)])
def test_row_numbers_outside_the_range_are_refused(client, bad):
    assert client.get(f"/invoicing/issued/{bad}/credit").status_code == 422
    assert client.post(f"/invoicing/issued/{bad}/credit", data={}).status_code == 422
    assert client.post(f"/invoicing/issued/{bad}/credit/totals", data={}).status_code == 422


def test_the_form_address_takes_nothing_from_the_query_string(issued):
    r = issued.get("/invoicing/issued?x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/invoicing/issued"


# Live totals, Credit all

def test_live_totals_follow_what_is_typed_and_save_nothing(issued):
    client = issued
    t = client.post("/invoicing/issued/1/credit/totals", data=credit_data({1: "2,5", 2: ""})).json()
    assert t["ok"] is True and t["error"] is None and t["next_number"] == "PFA-0002"
    assert [(line["line"], line["value_display"]) for line in t["lines"]] == [(1, "-376,25")]
    assert (t["subtotal"], t["vat"], t["total"], t["currency"]) == ("-376,25", "", "-376,25", "RON")
    t = client.post("/invoicing/issued/1/credit/totals", data=credit_data({1: "11", 2: "1"})).json()
    assert t["ok"] is False and "line 1 (Consultanță IT & „QA”): only 10 left to credit, not 11" in t["error"]
    assert [line["line"] for line in t["lines"]] == [2] and t["total"] == "-12,35"
    assert services.invoicing_view()["issued"][0]["full_number"] == "PFA-0001"      # nothing issued
    assert len(services.year_view(YEAR)["invoices"]) == 1


def test_the_totals_show_the_invoices_vat(client, firm_with_address):
    setup_lists(client, HOUR)
    post(client, "/invoicing/vat", {"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    post(client, "/invoicing/issue", invoice([("1", "2", "")]))
    post(client, "/invoicing/vat", {"vat_payer": "", "vat_rate": "19", "due_days": "15"})    # changed since
    t = client.post("/invoicing/issued/1/credit/totals", data=credit_data({1: "1"})).json()
    assert (t["subtotal"], t["vat"], t["vat_rate"], t["total"]) == ("-150,50", "-31,61", "21", "-182,11")
    html = text(client.get("/invoicing/issued/1/credit").text)
    assert "Total without VAT" in html and "VAT 21%" in html


def test_credit_all_and_update_totals_without_javascript_fill_in_and_save_nothing(issued):
    client = issued
    credit(client, 1, {1: "3"})
    r = post(client, "/invoicing/issued/1/credit", credit_data({1: ""}, action="all", reason="keep"))
    assert r.status_code == 200
    assert 'name="quantity_1" value="7" data-left="7"' in r.text and 'name="quantity_2" value="4" data-left="4"' in r.text
    assert "keep</textarea>" in r.text and "Ready to be issued." in r.text
    assert "-1.102,88 RON" in r.text                                      # 7 × 150,50 + the line's 49,38
    r = post(client, "/invoicing/issued/1/credit", credit_data({1: "1"}, action="totals"))
    assert r.status_code == 200 and "-150,50 RON" in r.text
    assert len(services.invoicing_view()["issued"]) == 2


def test_the_script_drives_the_credit_note_form_like_the_new_invoice_form(client):
    js = client.get("/static/invoicing.js").text
    for words in ('getElementById("credit-note")', 'getAttribute("data-totals")', 'getAttribute("data-left")',
                  '"Still needed before issuing: "', '"Ready to be issued."', "Issue credit note and add to Income",
                  "button.disabled = true"):
        assert words in js, words


# Issuing

def test_issuing_adds_the_credit_note_to_income_and_offers_its_pdf(issued):
    client = issued
    r = credit(client, 1, {1: "2,5", 2: ""}, reason="Ore facturate în plus")
    assert r.status_code == 303 and r.headers["location"] == "/invoicing/issued"
    assert r.cookies["pfa_year"] == YEAR
    assert flash(r)["message"] == (f"Credit note PFA-0002 issued to Țară Soft SRL — «Ünïcödé» for -376,25 RON, "
                                   f"reversing invoice PFA-0001, and added to Income for {YEAR}. Its PDF is the "
                                   f"credit note's proof.")
    page = client.get("/invoicing/issued").text
    box = text(page[page.index('id="just-issued"'):page.index("</div>\n</div>", page.index('id="just-issued"'))])
    assert "Credit note PFA-0002 · reverses PFA-0001 · Țară Soft SRL — «Ünïcödé» · -376,25 RON" in box
    assert '/invoicing/issued/2/pdf">Download PDF</a>' in page
    assert f'name="year" value="{YEAR}">Show in Income {YEAR}</button>' in page
    assert 'id="just-issued"' not in client.get("/invoicing/issued").text                # shown once

    pdf = client.get("/invoicing/issued/2/pdf")
    assert pdf.status_code == 200 and 'filename="PFA-0002_Tara_Soft_SRL' in pdf.headers["content-disposition"]
    words = pdf_text(pdf.content)
    assert "FACTURĂ STORNO / CREDIT NOTE" in words and "Ore facturate în plus" in words

    income = client.get("/income").text
    assert re.search(r'PFA-0002 <span class="tag"[^>]*>issued</span>', income)
    assert "-376,25" in income and "1.178,13" in income                    # the year's total: 1.554,38 - 376,25
    assert "this will be PFA-0003" in client.get("/invoicing").text        # the series moved on


def test_refused_input_stays_in_the_form_with_the_reason_and_nothing_is_issued(issued):
    client = issued
    post(client, "/invoicing/series", {"name": "ST", "next_number": "40"})
    data = credit_data({1: "12", 2: "1,5"}, series_id="2", reason="<b>Anulare</b> „parțială”",
                       payment_date=TODAY_ISO)
    r = post(client, "/invoicing/issued/1/credit", data)
    assert r.status_code == 200
    assert "Credit note not issued: line 1 (Consultanță IT &amp; „QA”): only 10 left to credit, not 12." in r.text
    assert 'name="quantity_1" value="12"' in r.text and 'name="quantity_2" value="1,5"' in r.text
    assert "&lt;b&gt;Anulare&lt;/b&gt; „parțială”</textarea>" in r.text
    assert re.search(r'<option value="2" selected>ST', r.text) and f'name="payment_date" value="{TODAY_ISO}"' in r.text
    assert f'name="token" value="{data["token"]}"' in r.text                      # the same form, still
    assert len(services.invoicing_view()["issued"]) == 1 and len(services.year_view(YEAR)["invoices"]) == 1
    r = post(client, "/invoicing/issued/1/credit", {**data, "quantity_1": "10"})  # corrected: issued
    assert r.status_code == 303 and "Credit note ST-0040 issued" in flash(r)["message"]


def test_a_credit_note_dated_before_its_invoice_is_refused(issued):
    r = credit(issued, 1, {1: "1"}, date="2001-01-01")
    assert r.status_code == 200 and "the credit note cannot be dated before the invoice it reverses" in unescape(r.text)


def test_without_the_firm_address_nothing_is_issued(issued, settings_path):
    import json
    data = json.loads(settings_path.read_text())
    data["firm"]["address"] = ""
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    page = issued.get("/invoicing/issued/1/credit").text
    assert 'id="firm-problems"' in page and '<a href="/settings">Open Settings</a>' in page
    r = credit(issued, 1, {1: "1"})
    assert r.status_code == 200 and "fill in your firm&#39;s address in Settings first" in r.text


def test_a_form_sent_twice_issues_one_credit_note(issued):
    client = issued
    data = credit_data({1: "1"})
    assert post(client, "/invoicing/issued/1/credit", data).status_code == 303
    r = post(client, "/invoicing/issued/1/credit", data)
    assert r.status_code == 303 and r.headers["location"] == "/invoicing/issued"
    assert flash(r)["error"] == ("Not issued again: this form was already used to issue credit note PFA-0002 (it was "
                                 "sent twice). Find it under Issued invoices.")
    assert "Credit note PFA-0002" in text(client.get("/invoicing/issued").text)
    assert len(services.invoicing_view()["issued"]) == 2


def test_the_invoice_credited_is_the_one_in_the_address(issued):
    client = issued
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))                     # PFA-0002
    r = post(client, "/invoicing/issued/2/credit", {**credit_data({1: "1"}), "issued_id": "1"})
    assert r.status_code == 303 and "reversing invoice PFA-0002" in flash(r)["message"]


# Income

def test_deleting_a_credited_invoice_from_income_warns_and_then_deletes_it(issued):
    client = issued
    post(client, "/invoicing/issue", invoice([("1", "1", "")]))                     # PFA-0002: not credited
    credit(client, 1, {1: "1"})                                                       # PFA-0003
    html = unescape(client.get("/income").text)
    warning = ("Delete this invoice? " + services.GENERATED_DELETE_NOTE + " It has credit notes (PFA-0003): they "
               "stay in Income, so their negative amounts will count without this invoice.")
    assert html.count(warning) == 1                                                   # PFA-0001 only
    assert html.count("Delete this invoice? " + services.GENERATED_DELETE_NOTE) == 3
    r = post(client, "/income/1/delete", {})
    assert r.status_code == 303 and flash(r)["message"].endswith(
        "its number is not given out again. Its credit notes (PFA-0003) stay in Income.")
    rows = unescape(client.get("/income").text).split("<tbody>")[1].split("</tbody>")[0]
    assert "PFA-0003" in rows and "PFA-0001" not in rows


def test_a_credit_notes_income_row_is_edited_like_an_issued_invoice_but_stays_negative(issued):
    client = issued
    credit(client, 1, {1: "1"})                                                       # Income row 2
    edit = unescape(client.get("/income/2/edit").text)
    assert services.GENERATED_NOTE + " " + services.CREDIT_NOTE_EDIT_NOTE in edit
    assert services.CREDIT_NOTE_EDIT_NOTE not in unescape(client.get("/income/1/edit").text)
    row = db.get("invoices", 2)
    fields = {"date": row["date"], "payment_date": "", "client": row["client"], "number": row["number"],
              "currency": "RON"}
    for amount in ("150,50", "0"):
        r = post(client, "/income/2/edit", {**fields, "amount": amount})
        assert r.status_code == 200
        assert "Invoice not changed: PFA-0002 is a credit note, so its amount must stay negative." in r.text
        assert f'value="{amount}"' in r.text                                           # what was typed stays
    assert db.get("invoices", 2)["amount"] == "-150.5"
    r = post(client, "/income/2/edit", {**fields, "amount": "-140", "payment_date": TODAY_ISO})
    assert r.status_code == 303 and flash(r)["message"] == "Invoice changed."
    assert (db.get("invoices", 2)["amount"], db.get("invoices", 2)["payment_date"]) == ("-140", TODAY_ISO)
