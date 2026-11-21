"""Credit notes in the desktop app, driven through its widgets without a screen: the Credit note button of Issued
invoices, its form (each line with what is left to credit, Credit all, series, date, refund date, reason) with
live totals, refused input kept with the reason, issuing with its PDF and a way to Income, what the list says
about credit notes, the warning before deleting a credited invoice and a credit note's amount that stays negative
in Income — with the same words as the browser version."""
import io
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QDate  # noqa: E402

from conftest import TODAY, YEAR  # noqa: E402
from test_desktop_invoicing import (cell_button, go_tab, home, pwin, qapp, ready, section, settle,  # noqa: E402,F401
                                    shown, table_text, win)
from tracker import db, invoicing, services  # noqa: E402

TODAY_ISO = TODAY.isoformat()
CLIENT_NAME = "Ωmega Ștefănescu SRL"


@pytest.fixture
def issued(win):
    """Invoice PFA-0001: 10 hours at 150,50 and 4 pages at 12,3456 (1.554,38 RON), in Income for this year."""
    client, product, series = ready(win, price="150,50")
    page = services.save_product({"name": "Traducere", "unit": "page", "unit_price": "12,3456",
                                  "currency": "RON"}).extra["id"]
    out = services.generate_invoice({"series_id": series, "client_id": client, "date": TODAY_ISO,
                                     "lines": [{"product_id": product, "quantity": "10"},
                                               {"product_id": page, "quantity": "4"}]})
    assert out.ok, out.error
    win.refresh()
    return {"client": client, "product": product, "series": series, "win": win}


def credit(issued_id, quantities: dict, **fields):
    """A credit note issued meanwhile, e.g. in the browser version."""
    out = services.generate_credit_note({"issued_id": issued_id, "series_id": "", "date": TODAY_ISO,
                                         "lines": [{"line": str(n), "quantity": q} for n, q in quantities.items()],
                                         **fields})
    assert out.ok, out.error
    return out


def issued_rows(win) -> dict[str, int]:
    t = win.invoicing.issued_table
    return {t.item(r, 0).text(): r for r in range(t.rowCount())}


def has_button(t, row, text) -> bool:
    try:
        cell_button(t, row, text)
    except AssertionError:
        return False
    return True


def open_form(win, number="PFA-0001"):
    section(win, "Issued invoices")
    cell_button(win.invoicing.issued_table, issued_rows(win)[number], "Credit note").click()
    settle()
    return win.invoicing.credit_form


def income_row(win, number) -> int:
    t = win.income.table
    for r in range(t.rowCount()):
        w = t.cellWidget(r, 3)
        texts = [lbl.text() for lbl in w.findChildren(QtWidgets.QLabel)] if w else [t.item(r, 3).text()]
        if number in texts:
            return r
    raise AssertionError(f"{number} is not in Income")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


# The list

def test_each_invoice_offers_a_credit_note_unless_it_is_one_was_deleted_from_income_or_is_fully_credited(issued):
    win = issued["win"]
    for _ in range(2):                                                               # PFA-0002, PFA-0003
        assert services.generate_invoice({"series_id": issued["series"], "client_id": issued["client"],
                                          "date": TODAY_ISO, "lines": [{"product_id": issued["product"]}]}).ok
    credit(1, {1: "10", 2: "4"})                                                      # PFA-0004: all
    credit(3, {1: "0,5"})                                                             # PFA-0005: part
    services.delete("invoices", 2)
    section(win, "Issued invoices")
    t = win.invoicing.issued_table
    rows = issued_rows(win)
    credit_column = {number: table_text(t)[row][7] for number, row in rows.items()}
    assert credit_column == {"PFA-0005": "Credit note reversing PFA-0003", "PFA-0004": "Credit note reversing PFA-0001",
                             "PFA-0003": "Partly credited by PFA-0005", "PFA-0002": "",
                             "PFA-0001": "Fully credited by PFA-0004"}
    assert {n for n, row in rows.items() if has_button(t, row, "Credit note")} == {"PFA-0003"}
    assert table_text(t)[rows["PFA-0004"]][4] == "-1.554,38" and table_text(t)[rows["PFA-0005"]][4] == "-75,25"
    assert [t.horizontalHeaderItem(c).text() for c in range(8)] == [
        "Number", "Invoice date", "Due date", "Client", "Total", "Currency", "In Income", "Credit"]


# The form

def test_the_form_shows_each_line_with_what_is_left_to_credit(issued):
    win = issued["win"]
    credit(1, {2: "4"})                                                               # PFA-0002
    form = open_form(win)
    assert form.isVisible() and form.title() == "Credit note for invoice PFA-0001"
    assert form.info.text() == (f"Invoice PFA-0001 of {TODAY_ISO} · {CLIENT_NAME} · 1.554,38 RON · credited so far "
                                f"by PFA-0002")
    assert [form.lines.horizontalHeaderItem(c).text() for c in range(8)] == [
        "Product or service", "Unit", "Price per unit", "Invoiced", "Credited", "Left", "Quantity to credit", "Value"]
    rows = table_text(form.lines)
    assert rows[0][:6] == ["Consultanță IT", "oră / hour", "150,50", "10", "0", "10"]
    assert rows[1][:7] == ["Traducere", "pagină / page", "12,3456", "4", "4", "0", "fully credited"]
    assert list(form.quantities) == [1] and form.quantities[1].text() == ""
    assert form.series_box.currentText() == "PFA (next: PFA-0003)"
    assert form.date.date() == QDate(TODAY.year, TODAY.month, TODAY.day) and not form.refund_check.isChecked()
    assert form.reason.toPlainText() == "" and form.number_card.value.text() == "PFA-0003"
    assert form.problems.text() == ("Still needed before issuing: choose at least one line of the invoice and the "
                                    "quantity to credit.")
    form.cancel_button.click()
    assert not form.isVisible()


def test_a_form_that_cannot_be_used_is_not_opened_and_says_why(issued):
    win = issued["win"]
    section(win, "Issued invoices")
    credit(1, {1: "10", 2: "4"})                                       # meanwhile, in the browser version
    t = win.invoicing.issued_table
    cell_button(t, 0, "Credit note").click()                           # the button shown before that
    settle()
    assert shown(win) == "No credit note can be issued: invoice PFA-0001 is already fully credited (by PFA-0002)."
    assert not win.invoicing.credit_form.isVisible()
    assert not has_button(t, issued_rows(win)["PFA-0001"], "Credit note")     # the list is as stored now
    win.invoicing.credit_form.open(2)
    settle()
    assert shown(win) == "No credit note can be issued: PFA-0002 is a credit note; only an invoice can be credited."
    assert not win.invoicing.credit_form.isVisible()


# Live totals, Credit all

def test_live_totals_follow_what_is_typed_and_credit_all_fills_in_what_is_left(issued):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("2,5")
    assert table_text(form.lines)[0][7] == "-376,25" and form.total_card.value.text() == "-376,25 RON"
    assert form.total_card.sub.text() == "The invoice has no VAT" and form.problems.text() == "Ready to be issued."
    form.quantities[1].setText("11")
    assert form.problems.text() == ("Still needed before issuing: line 1 (Consultanță IT): only 10 left to credit, "
                                    "not 11.")
    assert table_text(form.lines)[0][7] == ""
    form.all_button.click()
    assert [form.quantities[n].text() for n in (1, 2)] == ["10", "4"]
    assert form.total_card.value.text() == "-1.554,38 RON" and form.problems.text() == "Ready to be issued."
    assert len(services.invoicing_view()["issued"]) == 1                      # nothing issued


def test_the_totals_show_the_invoices_vat(win):
    client, product, series = ready(win, price="150,50")
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    services.generate_invoice({"series_id": series, "client_id": client, "date": TODAY_ISO,
                               "lines": [{"product_id": product, "quantity": "2"}]})
    services.save_invoicing_settings({"vat_payer": "", "vat_rate": "19", "due_days": "15"})
    win.refresh()
    form = open_form(win)
    form.quantities[1].setText("1")
    assert form.total_card.value.text() == "-182,11 RON"
    assert form.total_card.sub.text() == "-150,50 RON + VAT 21% -31,61 RON"


# Issuing

def test_issuing_adds_the_credit_note_to_income_and_offers_its_pdf(issued, tmp_path):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("2,5")
    form.reason.setPlainText("Ore facturate în plus")
    form.issue_button.click()
    settle()
    assert shown(win) == (f"Credit note PFA-0002 issued to {CLIENT_NAME} for -376,25 RON, reversing invoice PFA-0001, "
                          f"and added to Income for {YEAR}. Its PDF is the credit note's proof.")
    tab = win.invoicing
    assert not form.isVisible() and tab.credit_result.isVisible()
    assert tab.credit_result.title() == "Credit note PFA-0002 issued"
    assert tab.credit_summary.text() == f"Credit note PFA-0002 · reverses PFA-0001 · {CLIENT_NAME} · -376,25 RON"
    assert tab.credit_income_button.text() == f"Show in Income {YEAR}"
    rows = issued_rows(win)
    assert table_text(tab.issued_table)[rows["PFA-0001"]][7] == "Partly credited by PFA-0002"

    tab.credit_open_button.click()
    opened = win.dialogs.opened[-1]
    assert opened.name.startswith("PFA-0002_") and opened.suffix == ".pdf"
    words = pdf_text(opened.read_bytes())
    assert "FACTURĂ STORNO / CREDIT NOTE" in words and "Ore facturate în plus" in words
    target = tmp_path / "storno.pdf"
    win.dialogs.saves = [target]
    tab.credit_save_button.click()
    assert target.read_bytes().startswith(b"%PDF") and f"Invoice saved: {target}" in shown(win)

    tab.credit_income_button.click()
    settle()
    assert win.current_tab() is win.income and win.income.heading.text() == f"Income {YEAR}"
    row = income_row(win, "PFA-0002")
    assert table_text(win.income.table)[row][4] == "-376,25"
    assert "1.178,13" in win.income.total_card.value.text()                  # 1.554,38 - 376,25


def test_a_refund_date_is_the_date_the_income_counts_on(issued):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("1")
    form.refund_check.setChecked(True)
    form.issue_button.click()
    settle()
    assert shown(win).startswith("Credit note PFA-0002 issued")
    assert db.get("invoices", 2)["payment_date"] == TODAY_ISO


def test_refused_input_stays_in_the_form_with_the_reason_and_nothing_is_issued(issued):
    win = issued["win"]
    services.save_series({"name": "ST", "next_number": "40"})
    win.refresh()
    form = open_form(win)
    token = form.token
    form.series_box.setCurrentIndex(form.series_box.findData(services.invoicing_view()["series"][1]["id"]))
    form.quantities[1].setText("12")
    form.reason.setPlainText("<b>Anulare</b> „parțială”")
    form.issue_button.click()
    settle()
    assert shown(win) == "Credit note not issued: line 1 (Consultanță IT): only 10 left to credit, not 12."
    assert form.isVisible() and form.quantities[1].text() == "12" and form.token == token
    assert form.reason.toPlainText() == "<b>Anulare</b> „parțială”" and form.series_box.currentText().startswith("ST")
    assert len(services.invoicing_view()["issued"]) == 1
    form.quantities[1].setText("10")
    form.issue_button.click()
    settle()
    assert shown(win).startswith("Credit note ST-0040 issued") and not form.isVisible()


def test_what_the_other_app_credits_meanwhile_is_shown_and_what_is_typed_stays(issued):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("3")
    form.reason.setPlainText("typed")
    credit(1, {1: "8"})                                                # meanwhile, in the browser version
    win.refresh()
    assert table_text(form.lines)[0][4:6] == ["8", "2"] and form.quantities[1].text() == "3"
    assert form.reason.toPlainText() == "typed"
    assert "line 1 (Consultanță IT): only 2 left to credit, not 3" in form.problems.text()
    assert form.info.text().endswith("credited so far by PFA-0002") and form.number_card.value.text() == "PFA-0003"


def test_a_double_click_issues_one_credit_note_and_the_same_form_sent_twice_is_issued_once(issued):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("1")
    form.issue_button.click()
    form.issue_button.click()                          # the second click of a double click: the form is closed
    settle()
    assert shown(win).startswith("Credit note PFA-0002 issued") and len(services.invoicing_view()["issued"]) == 2
    form = open_form(win)
    form.quantities[1].setText("1")
    assert services.generate_credit_note(form.fields()).ok                   # e.g. the first of two sends
    form.issue_button.click()                                                # the same form again
    settle()
    assert shown(win) == ("Not issued again: this form was already used to issue credit note PFA-0003 (it was sent "
                          "twice). Find it under Issued invoices.")
    assert not form.isVisible() and win.invoicing.credit_result.title() == "Credit note PFA-0003 issued"
    assert len(services.invoicing_view()["issued"]) == 3


def test_the_reason_is_at_most_500_characters_and_must_be_printable(issued):
    win = issued["win"]
    form = open_form(win)
    form.quantities[1].setText("1")
    form.reason.setPlainText("ă" * 600)
    assert len(form.reason.toPlainText()) == 500 and form.problems.text() == "Ready to be issued."
    form.reason.setPlainText("Anulat 🚀")
    assert "cannot be printed" in form.problems.text()


def test_a_new_profile_does_not_keep_the_form_of_another(pwin):
    win = pwin
    assert services.save_settings({"name": "Test PFA", "cui": "RO14399840", "address": "Str. Științei 1, Iași"}).ok
    client, product, series = ready(win)
    out = services.generate_invoice({"series_id": series, "client_id": client, "date": TODAY_ISO,
                                     "lines": [{"product_id": product}]})
    assert out.ok, out.error
    win.refresh()
    form = open_form(win)
    form.quantities[1].setText("1")
    first = services.profiles_view()["current"]
    win.profile_changed(services.create_profile("Second firm"))
    settle()
    assert not form.isVisible() and not win.invoicing.credit_result.isVisible()
    win.choose_profile(first)
    settle()
    assert not form.isVisible()


# Income

def test_deleting_a_credited_invoice_from_income_warns_and_then_deletes_it(issued):
    win = issued["win"]
    services.generate_invoice({"series_id": issued["series"], "client_id": issued["client"], "date": TODAY_ISO,
                               "lines": [{"product_id": issued["product"]}]})           # PFA-0002: not credited
    credit(1, {1: "1"})                                                                  # PFA-0003
    go_tab(win, "Income")
    win.dialogs.answers = [False]
    cell_button(win.income.table, income_row(win, "PFA-0002"), "Delete").click()
    assert win.dialogs.asked[-1] == "Delete this invoice (and its proof)? " + services.GENERATED_DELETE_NOTE
    win.dialogs.answers = [True]
    cell_button(win.income.table, income_row(win, "PFA-0001"), "Delete").click()
    settle()
    assert win.dialogs.asked[-1] == ("Delete this invoice (and its proof)? " + services.GENERATED_DELETE_NOTE +
                                     " It has credit notes (PFA-0003): they stay in Income, so their negative amounts "
                                     "will count without this invoice.")
    assert shown(win).endswith("its number is not given out again. Its credit notes (PFA-0003) stay in Income.")
    income_row(win, "PFA-0003")
    with pytest.raises(AssertionError):
        income_row(win, "PFA-0001")


def test_a_credit_notes_income_row_is_edited_like_an_issued_invoice_but_stays_negative(issued):
    win = issued["win"]
    credit(1, {1: "1"})                                                                  # Income row 2
    go_tab(win, "Income")
    inc = win.income
    cell_button(inc.table, income_row(win, "PFA-0001"), "Edit").click()
    assert inc.generated_note.text() == services.GENERATED_NOTE
    cell_button(inc.table, income_row(win, "PFA-0002"), "Edit").click()
    assert inc.generated_note.isVisible()
    assert inc.generated_note.text() == services.GENERATED_NOTE + " " + services.CREDIT_NOTE_EDIT_NOTE
    for amount in ("150,50", "0"):
        inc.amount.setText(amount)
        inc.add_button.click()
        settle()
        assert shown(win) == "Invoice not changed: PFA-0002 is a credit note, so its amount must stay negative."
        assert inc.amount.text() == amount and inc.editing == 2
    assert db.get("invoices", 2)["amount"] == "-150.5"
    inc.amount.setText("-140")
    inc.add_button.click()
    settle()
    assert shown(win) == "Invoice changed." and db.get("invoices", 2)["amount"] == "-140"
    assert invoicing.credit_status(1)["lines"][0]["credited_quantity"] == "1"      # the credit note is as issued
