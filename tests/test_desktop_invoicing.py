"""The desktop app's Invoicing tab: clients, products and services, series and VAT settings kept in the app,
and invoices issued from them straight into Income — driven through the window's widgets without a screen."""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QDate, QEvent  # noqa: E402

from conftest import LAST_YEAR, TODAY, YEAR  # noqa: E402
from tracker import config, services  # noqa: E402
from tracker.config import SETTINGS_PATH  # noqa: E402

TABS = ["Overview", "Income", "Expenses", "Invoicing", "Settings"]


class ScriptedDialogs:
    """Stands in for the file pickers and questions: answers are queued by the test."""

    def __init__(self):
        self.files, self.saves, self.answers, self.opened, self.asked = [], [], [], [], []
        self.suggested = None

    def open_file(self, title, file_filter):
        return self.files.pop(0) if self.files else None

    def save_file(self, title, suggested, file_filter):
        self.suggested = suggested
        return self.saves.pop(0) if self.saves else None

    def confirm(self, title, text):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else False

    def ask_text(self, title, text, default=""):
        return None

    def open_path(self, path):
        self.opened.append(Path(path))


@pytest.fixture(scope="module")
def qapp():
    from tracker.desktop.common import STYLE
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.setStyleSheet(STYLE)
    return app


def make_window(qapp):
    from tracker.desktop.app import MainWindow
    w = MainWindow()
    w.dialogs = ScriptedDialogs()
    w.show()
    return w


def close_window(qapp, w):
    w.close()
    w.deleteLater()
    qapp.processEvents()
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def win(qapp):
    w = make_window(qapp)
    yield w
    close_window(qapp, w)


def settle():
    QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # replaced row buttons really go


def go_tab(win, name):
    win.tabs.setCurrentIndex(TABS.index(name))
    settle()


def section(win, name):
    from tracker.desktop.invoicing_tab import SECTIONS
    go_tab(win, "Invoicing")
    win.invoicing.sections.setCurrentIndex(SECTIONS.index(name))
    settle()


def shown(win) -> str:
    return win.message.text() if win.message.isVisible() else ""


def table_text(t) -> list[list[str]]:
    return [[t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())] for r in range(t.rowCount())]


def cell_button(t, row, text):
    from PySide6.QtWidgets import QPushButton
    for col in range(t.columnCount()):
        w = t.cellWidget(row, col)
        if w is not None:
            for b in ([w] if isinstance(w, QPushButton) else w.findChildren(QPushButton)):
                if b.text() == text:
                    return b
    raise AssertionError(f"no {text} button in row {row}")


def firm_with_address(address="Str. Științei 1, Iași"):
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    data["firm"]["address"] = address
    SETTINGS_PATH.write_text(json.dumps(data), encoding="utf-8")


CLIENT = {"name": "Ωmega Ștefănescu SRL", "tax_id": "RO14399840", "reg_number": "", "address": "Bd. Unirii 10",
          "city": "București", "country": "RO", "email": "facturi@tara.ro", "phone": ""}


def fill_form(editor, values: dict):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QPlainTextEdit
    for key, value in values.items():
        w = editor.fields[key]
        if isinstance(w, QLineEdit):
            w.setText(value)
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(value)
        elif isinstance(w, QComboBox):
            assert w.findData(value) >= 0, (key, value)
            w.setCurrentIndex(w.findData(value))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))


def ready(win, *, price="150,5055", currency="RON", address=True):
    """A client, a product and a series stored; the window shows them."""
    if address:
        firm_with_address()
    client = services.save_client(CLIENT).extra["id"]
    product = services.save_product({"name": "Consultanță IT", "description": "Dezvoltare software", "unit": "hour",
                                     "unit_price": price, "currency": currency}).extra["id"]
    series = services.save_series({"name": "PFA", "next_number": "1"}).extra["id"]
    win.refresh()
    return client, product, series


def choose(combo, value):
    i = combo.findData(value)
    assert i >= 0, value
    combo.setCurrentIndex(i)


def set_line(tab, row, product_id, quantity="", price=""):
    w = tab.line_widgets[row]
    choose(w["product"], product_id)
    w["quantity"].setText(quantity)
    w["price"].setText(price)


def new_invoice(win, client, product, quantity="10", price=""):
    section(win, "New invoice")
    tab = win.invoicing
    choose(tab.client_box, client)
    set_line(tab, 0, product, quantity, price)
    return tab


# The tab and its sections

def test_invoicing_sits_before_settings_with_five_sections_and_no_year_picker(win):
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == TABS
    go_tab(win, "Invoicing")
    s = win.invoicing.sections
    assert [s.tabText(i).replace("&&", "&") for i in range(s.count())] == [
        "New invoice", "Issued invoices", "Clients", "Products & services", "Series & VAT"]
    assert not win.year_box.isVisible()
    go_tab(win, "Income")
    assert win.year_box.isVisible()


def test_empty_lists_say_what_to_do_and_the_missing_firm_address_is_pointed_out(win):
    go_tab(win, "Invoicing")
    tab = win.invoicing
    assert tab.clients.empty.text() == "No clients yet — add one below."
    assert tab.products_editor.empty.text() == "No products or services yet — add one below."
    assert tab.series.empty.text() == "No series yet — add one below."
    assert tab.issued_empty.text().startswith("No invoices issued yet")
    assert tab.firm_warning_line.isVisible() and "address" in tab.firm_warning.text()
    assert "Settings" in tab.firm_warning.text()
    assert "No clients yet" in tab.client_box.itemText(0) and "No series yet" in tab.series_box.itemText(0)
    tab.to_settings.click()
    settle()
    assert win.current_tab() is win.settings
    firm_with_address()
    go_tab(win, "Invoicing")
    assert not tab.firm_warning_line.isVisible()


# Clients

def test_a_client_is_added_changed_and_deleted_from_the_app(win):
    section(win, "Clients")
    ed = win.invoicing.clients
    assert ed.fields["country"].currentData() == "RO"            # Romania is the default
    fill_form(ed, {**CLIENT, "reg_number": "J40/1234/2020", "phone": "0721 234 567"})
    ed.save_button.click()
    settle()
    assert "Client Ωmega Ștefănescu SRL added" in shown(win)
    rows = table_text(ed.table)
    assert rows[0][:5] == ["Ωmega Ștefănescu SRL", "RO14399840", "J40/1234/2020", "Bd. Unirii 10, București", "Romania"]
    assert ed.fields["name"].text() == ""                          # the form is empty again
    # it is offered on a new invoice at once
    assert any("Ωmega Ștefănescu SRL" in win.invoicing.client_box.itemText(i) for i in range(win.invoicing.client_box.count()))

    cell_button(ed.table, 0, "Edit").click()
    assert ed.form.title() == "Edit client" and ed.fields["name"].text() == "Ωmega Ștefănescu SRL"
    ed.fields["city"].setText("Cluj-Napoca")
    ed.save_button.click()
    settle()
    assert "changed" in shown(win) and table_text(ed.table)[0][3] == "Bd. Unirii 10, Cluj-Napoca"
    assert ed.form.title() == "Add a client"

    win.dialogs.answers = [False]
    cell_button(ed.table, 0, "Delete").click()
    settle()
    assert "Ωmega Ștefănescu SRL" in win.dialogs.asked[-1] and ed.table.rowCount() == 1     # not confirmed: kept
    win.dialogs.answers = [True]
    cell_button(ed.table, 0, "Delete").click()
    settle()
    assert "deleted" in shown(win) and services.invoicing_view()["clients"] == []
    assert ed.empty.isVisible()


def test_a_client_with_problems_is_refused_and_what_was_typed_stays(win):
    section(win, "Clients")
    ed = win.invoicing.clients
    fill_form(ed, {"name": "X", "tax_id": "123", "email": "not-an-email", "address": "", "city": ""})
    ed.save_button.click()
    settle()
    err = shown(win)
    assert err.startswith("Client not saved") and "CUI 123 is not valid" in err and "email" in err
    assert ed.fields["tax_id"].text() == "123" and ed.fields["email"].text() == "not-an-email"
    assert services.invoicing_view()["clients"] == []


def test_a_foreign_client_chooses_its_country_from_the_list(win):
    section(win, "Clients")
    ed = win.invoicing.clients
    fill_form(ed, {"name": "Acme GmbH", "tax_id": "DE123456789", "address": "Hauptstr. 5", "city": "Berlin",
                   "country": "DE", "email": "bill@acme.de"})
    ed.save_button.click()
    settle()
    assert table_text(ed.table)[0][4] == "Germany"


def test_editing_a_client_deleted_meanwhile_says_so(win):
    cid = services.save_client(CLIENT).extra["id"]
    section(win, "Clients")
    ed = win.invoicing.clients
    cell_button(ed.table, 0, "Edit").click()
    services.delete_client(cid)                                  # e.g. in the browser version
    ed.save_button.click()
    settle()
    assert "no longer exists" in shown(win)


# Products and services

def test_a_product_with_a_four_decimal_price_is_added_changed_and_deleted(win):
    section(win, "Products & services")
    ed = win.invoicing.products_editor
    assert [ed.fields["currency"].itemText(i) for i in range(ed.fields["currency"].count())][0] == "RON"
    fill_form(ed, {"name": "Traducere Ωmega", "description": "EN → RO\npe pagină", "unit": "page",
                   "unit_price": "12,3456", "currency": "EUR"})
    ed.save_button.click()
    settle()
    assert "Traducere Ωmega added" in shown(win)
    row = table_text(ed.table)[0]
    assert row[0] == "Traducere Ωmega" and row[2] == "pagină / page" and row[3] == "12,3456" and row[4] == "EUR"
    cell_button(ed.table, 0, "Edit").click()
    assert ed.fields["unit_price"].text() == "12.3456" or ed.fields["unit_price"].text().replace(".", ",") == "12,3456"
    assert ed.fields["description"].toPlainText() == "EN → RO\npe pagină"
    assert ed.fields["unit"].currentData() == "page" and ed.fields["currency"].currentData() == "EUR"
    ed.fields["unit_price"].setText("1.000,50")
    ed.save_button.click()
    settle()
    assert table_text(ed.table)[0][3] == "1.000,50"
    win.dialogs.answers = [True]
    cell_button(ed.table, 0, "Delete").click()
    settle()
    assert "deleted" in shown(win) and ed.table.rowCount() == 0


def test_a_product_without_a_price_is_refused(win):
    section(win, "Products & services")
    ed = win.invoicing.products_editor
    fill_form(ed, {"name": "Support", "unit_price": ""})
    ed.save_button.click()
    settle()
    assert shown(win).startswith("Product not saved") and ed.fields["name"].text() == "Support"


# Series and VAT

def test_a_series_is_added_made_default_and_deleted(win):
    section(win, "Series & VAT")
    ed = win.invoicing.series
    fill_form(ed, {"name": "pfa", "next_number": "7"})
    ed.save_button.click()
    settle()
    assert "the next invoice will be PFA-0007" in shown(win)
    fill_form(ed, {"name": "AB2026", "next_number": "", "is_default": True})
    ed.save_button.click()
    settle()
    rows = {r[0]: r for r in table_text(ed.table)}
    assert rows["AB2026"][1] == "AB2026-0001" and rows["AB2026"][3] == "yes" and rows["PFA"][3] == ""
    section(win, "New invoice")
    assert win.invoicing.series_box.currentText().startswith("AB2026")      # the default is chosen
    section(win, "Series & VAT")
    win.dialogs.answers = [True]
    cell_button(ed.table, [r[0] for r in table_text(ed.table)].index("AB2026"), "Delete").click()
    settle()
    assert "Series AB2026 deleted" in shown(win) and [r[0] for r in table_text(ed.table)] == ["PFA"]


def test_a_bad_series_name_is_refused(win):
    section(win, "Series & VAT")
    ed = win.invoicing.series
    fill_form(ed, {"name": "1-ABC", "next_number": "0"})
    ed.save_button.click()
    settle()
    assert shown(win).startswith("Series not saved") and ed.fields["name"].text() == "1-ABC"


def test_vat_settings_are_saved_and_the_totals_add_vat(win):
    client, product, _ = ready(win, price="100")
    section(win, "Series & VAT")
    tab = win.invoicing
    assert not tab.vat_payer.isChecked() and tab.vat_rate.text() == "21" and tab.due_days.text() == "15"
    tab.vat_payer.click()
    tab.vat_rate.setText("19")
    tab.due_days.setText("30")
    tab.vat_button.click()
    settle()
    assert shown(win) == "Invoice settings saved."
    new_invoice(win, client, product, "2")
    assert tab.total_card.value.text() == "238,00 RON" and "VAT 19% 38,00" in tab.total_card.sub.text()


def test_bad_vat_settings_are_refused_and_stay_in_the_form(win):
    section(win, "Series & VAT")
    tab = win.invoicing
    tab.vat_rate.setText("150")
    tab.vat_rate.textEdited.emit("150")
    tab.vat_button.click()
    settle()
    assert shown(win).startswith("Not saved") and tab.vat_rate.text() == "150"
    win.refresh()                                           # e.g. coming back to the window
    assert tab.vat_rate.text() == "150"


# New invoice

def test_the_new_invoice_starts_with_today_the_usual_due_date_and_the_default_series(win):
    ready(win)
    section(win, "New invoice")
    tab = win.invoicing
    assert tab.date.date() == QDate(TODAY.year, TODAY.month, TODAY.day)
    due = TODAY + timedelta(days=15)
    assert tab.due_check.isChecked() and tab.due.date() == QDate(due.year, due.month, due.day)
    assert not tab.paid_check.isChecked() and tab.series_box.currentText().startswith("PFA")
    assert tab.client_box.currentData() is None and len(tab.line_widgets) == 1
    assert tab.number_card.value.text() == "PFA-0001"
    # the due date follows an earlier invoice date until it is set by hand
    earlier = TODAY - timedelta(days=40)
    tab.date.setDate(QDate(earlier.year, earlier.month, earlier.day))
    follows = earlier + timedelta(days=15)
    assert tab.due.date() == QDate(follows.year, follows.month, follows.day)


def test_live_totals_follow_every_change(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "10")
    assert tab.total_card.value.text() == "1.505,06 RON" and table_text(tab.lines)[0][5] == "1.505,06"
    assert table_text(tab.lines)[0][2] == "oră / hour" and table_text(tab.lines)[0][4] == "RON"
    tab.add_line()
    set_line(tab, 1, product, "2,5", "200")
    assert tab.total_card.value.text() == "2.005,06 RON" and table_text(tab.lines)[1][5] == "500,00"
    assert tab.problems.text() == "Ready to be issued."
    cell_button(tab.lines, 0, "Remove").click()
    settle()
    assert len(tab.line_widgets) == 1 and tab.total_card.value.text() == "500,00 RON"
    tab.line_widgets[0]["quantity"].setText("1,250")                # a comma is the decimal mark in a quantity
    assert table_text(tab.lines)[0][5] == "250,00"
    tab.line_widgets[0]["quantity"].setText("abc")
    assert "line 1" in tab.problems.text() and tab.total_card.value.text() == "0,00 RON"


def test_issuing_adds_the_invoice_to_income_with_its_pdf(win, tmp_path):
    client, product, series = ready(win)
    tab = new_invoice(win, client, product, "10")
    tab.notes.setPlainText("Mulțumim! „Ghilimele” ©")
    tab.issue_button.click()
    settle()
    msg = shown(win)
    assert msg.startswith("Invoice PFA-0001 issued to Ωmega Ștefănescu SRL for 1.505,06 RON and added to Income for " + YEAR)
    assert tab.result.isVisible() and tab.result.title() == "Invoice PFA-0001 issued"
    assert tab.to_income_button.text() == f"Show in Income {YEAR}"
    # the form is ready for the next one
    assert tab.client_box.currentData() is None and tab.notes.toPlainText() == ""
    assert tab.number_card.value.text() == "PFA-0002" and len(tab.line_widgets) == 1

    tab.open_pdf_button.click()
    opened = win.dialogs.opened[-1]
    assert opened.name == "PFA-0001_Tara_SRL.pdf" or opened.suffix == ".pdf"
    assert opened.read_bytes().startswith(b"%PDF")
    target = tmp_path / "saved.pdf"
    win.dialogs.saves = [target]
    tab.save_pdf_button.click()
    assert win.dialogs.suggested.endswith(".pdf") and target.read_bytes().startswith(b"%PDF")
    assert f"Invoice saved: {target}" in shown(win)

    section(win, "Issued invoices")
    row = table_text(tab.issued_table)[0]
    due = (TODAY + timedelta(days=15)).isoformat()
    assert row[:7] == ["PFA-0001", TODAY.isoformat(), due, "Ωmega Ștefănescu SRL", "1.505,06", "RON", f"yes · {YEAR}"]
    assert services.invoicing_view()["series"][0]["next"] == "PFA-0002"

    tab.to_income_button.click()
    settle()
    assert win.current_tab() is win.income
    number_cell = win.income.table.cellWidget(0, 3)
    texts = [lbl.text() for lbl in number_cell.findChildren(QtWidgets.QLabel)]
    assert texts == ["PFA-0001", "issued"]
    # the Overview shows it too
    go_tab(win, "Overview")
    assert "1.505,06" in win.overview.income_card.value.text()


def test_an_invoice_paid_last_year_goes_to_that_year(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    day = QDate(int(LAST_YEAR), 12, 20)
    tab.date.setDate(day)
    tab.paid_check.setChecked(True)
    tab.paid.setDate(QDate(int(LAST_YEAR), 12, 28))
    tab.issue_button.click()
    settle()
    assert f"added to Income for {LAST_YEAR}" in shown(win) and win.year == LAST_YEAR
    tab.to_income_button.click()
    settle()
    assert win.current_tab() is win.income and win.income.heading.text() == f"Income {LAST_YEAR}"


def test_issuing_without_a_client_is_refused_and_the_form_stays(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "3")
    tab.client_box.setCurrentIndex(0)
    assert "choose a client" in tab.problems.text()
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued: choose a client")
    assert tab.line_widgets[0]["quantity"].text() == "3" and not tab.result.isVisible()
    assert services.invoicing_view()["issued"] == []


def test_issuing_is_refused_while_the_firm_has_no_address(win):
    client, product, _ = ready(win, address=False)
    tab = new_invoice(win, client, product, "1")
    assert "address" in tab.problems.text()
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued") and "address" in shown(win)


def test_a_product_deleted_while_chosen_is_not_swapped_silently(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    services.delete_product(product)                     # e.g. in the browser version, meanwhile
    win.refresh()
    combo = tab.line_widgets[0]["product"]
    assert combo.currentData() == product and combo.currentText() == "(deleted from the list — choose another)"
    assert "line 1: the product or service no longer exists" in tab.problems.text()
    tab.issue_button.click()
    settle()
    assert "no longer exists" in shown(win) and services.invoicing_view()["issued"] == []


def test_a_client_deleted_while_chosen_is_not_swapped_silently(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    services.delete_client(client)
    win.refresh()
    assert tab.client_box.currentData() == client
    assert tab.client_box.currentText() == "(deleted from the list — choose another)"
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued")


def test_lines_in_two_currencies_show_no_total(win):
    client, product, _ = ready(win)
    euro = services.save_product({"name": "Support", "unit": "month", "unit_price": "1000", "currency": "EUR"}).extra["id"]
    win.refresh()
    tab = new_invoice(win, client, product, "1")
    tab.add_line()
    set_line(tab, 1, euro, "1")
    assert tab.total_card.value.text() == "—" and "same currency" in tab.problems.text()
    assert table_text(tab.lines)[1][4] == "EUR"


def test_fifty_lines_at_most_and_an_invoice_with_all_of_them(win):
    client, product, _ = ready(win, price="0,0101")
    tab = new_invoice(win, client, product, "1")
    for _ in range(80):
        tab.add_line_button.click()
    assert len(tab.line_widgets) == 50 and not tab.add_line_button.isEnabled()
    for row in range(50):
        set_line(tab, row, product, "2,5")
    assert tab.problems.text() == "Ready to be issued."
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice PFA-0001 issued")
    issued = services.invoicing_view()["issued"][0]
    assert issued["total_display"] == "1,50"            # each line 2,5 × 0,0101 = 0,02525 → 0,03


def test_empty_lines_are_left_out(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "2")
    tab.add_line()
    tab.add_line()
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice PFA-0001 issued") and "301,01 RON" in shown(win)


def test_what_is_typed_stays_when_the_window_comes_back(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "4")
    tab.notes.setPlainText("note")
    section(win, "Clients")
    win.invoicing.clients.fields["name"].setText("Half typed")
    QtWidgets.QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    go_tab(win, "Overview")
    go_tab(win, "Invoicing")
    assert win.invoicing.clients.fields["name"].text() == "Half typed"
    assert tab.client_box.currentData() == client and tab.line_widgets[0]["quantity"].text() == "4"
    assert tab.notes.toPlainText() == "note"


# Issued invoices and Income

def test_each_issued_invoice_has_its_pdf_and_stays_listed_when_deleted_from_income(win, tmp_path):
    client, product, _ = ready(win)
    new_invoice(win, client, product, "1").issue_button.click()
    settle()
    section(win, "Issued invoices")
    t = win.invoicing.issued_table
    target = tmp_path / "again.pdf"
    win.dialogs.saves = [target]
    cell_button(t, 0, "Save PDF").click()
    assert target.read_bytes().startswith(b"%PDF")
    cell_button(t, 0, "Open").click()
    assert win.dialogs.opened[-1].read_bytes().startswith(b"%PDF")
    # refusing the save dialog writes nothing
    cell_button(t, 0, "Save PDF").click()
    go_tab(win, "Income")
    win.dialogs.answers = [True]
    cell_button(win.income.table, 0, "Delete").click()
    settle()
    assert services.GENERATED_DELETE_NOTE in win.dialogs.asked[-1]
    assert "stays in Invoicing" in shown(win)
    section(win, "New invoice")
    assert not win.invoicing.to_income_button.isVisible()          # it is not in Income any more
    assert win.invoicing.open_pdf_button.isVisible()
    section(win, "Issued invoices")
    assert t.rowCount() == 1 and table_text(t)[0][0] == "PFA-0001" and table_text(t)[0][6] == "no — deleted from Income"
    cell_button(t, 0, "Open").click()                     # its PDF is still there
    assert win.dialogs.opened[-1].read_bytes().startswith(b"%PDF")
    # its number is not given out again
    client2 = services.invoicing_view()["clients"][0]["id"]
    product2 = services.invoicing_view()["products"][0]["id"]
    new_invoice(win, client2, product2, "1").issue_button.click()
    settle()
    assert shown(win).startswith("Invoice PFA-0002 issued")


def test_the_income_edit_form_says_when_the_invoice_was_issued_here(win):
    client, product, _ = ready(win)
    new_invoice(win, client, product, "1").issue_button.click()
    settle()
    services.add_invoice({"date": TODAY.isoformat(), "payment_date": "", "client": "Manual SRL", "number": "M-1",
                          "amount": "10", "currency": "RON"}, None)
    go_tab(win, "Income")
    inc = win.income
    numbers = [inc.table.cellWidget(r, 3) for r in range(inc.table.rowCount())]
    manual = next(r for r in range(inc.table.rowCount()) if inc.table.item(r, 3) and inc.table.item(r, 3).text() == "M-1")
    issued = next(r for r, w in enumerate(numbers) if w is not None)
    cell_button(inc.table, issued, "Edit").click()
    assert inc.generated_note.isVisible() and inc.generated_note.text() == services.GENERATED_NOTE
    assert not inc.form_help.isVisible()
    cell_button(inc.table, manual, "Edit").click()
    assert not inc.generated_note.isVisible() and inc.form_help.isVisible()
    cell_button(inc.table, issued, "Edit").click()
    inc.cancel_button.click()
    assert not inc.generated_note.isVisible()


# Profiles

@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.delenv("PFA_DB")
    monkeypatch.delenv("PFA_SETTINGS")
    monkeypatch.setenv("PFA_HOME", str(tmp_path))
    token = config.ACTIVE_PROFILE.set(None)
    yield tmp_path
    config.ACTIVE_PROFILE.reset(token)


@pytest.fixture
def pwin(qapp, home):
    w = make_window(qapp)
    yield w
    close_window(qapp, w)


def test_the_lists_belong_to_the_profile_in_use(pwin):
    win = pwin
    services.save_client(CLIENT)
    win.refresh()
    section(win, "Clients")
    ed = win.invoicing.clients
    assert ed.table.rowCount() == 1
    ed.fields["name"].setText("Typed for the first firm")
    first = services.profiles_view()["current"]
    win.profile_changed(services.create_profile("Second firm 🏢"))
    settle()
    assert services.profiles_view()["current"] != first
    go_tab(win, "Invoicing")
    assert ed.table.rowCount() == 0 and ed.empty.isVisible() and ed.fields["name"].text() == ""
    assert win.invoicing.client_box.currentData() is None and win.invoicing.client_box.count() == 1
    win.choose_profile(first)
    settle()
    go_tab(win, "Invoicing")
    assert ed.table.rowCount() == 1


def test_a_new_invoice_cannot_be_dated_before_the_last_one_of_its_series(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    tab.issue_button.click()
    settle()
    tab = new_invoice(win, client, product, "1")
    earlier = TODAY - timedelta(days=3)
    tab.date.setDate(QDate(earlier.year, earlier.month, earlier.day))
    assert "cannot be dated earlier" in tab.problems.text()
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued") and "PFA-0001" in shown(win)
    assert len(services.invoicing_view()["issued"]) == 1


def test_a_dot_in_a_quantity_follows_the_amount_rule(win):
    client, product, _ = ready(win, price="1")
    tab = new_invoice(win, client, product, "1.5")
    assert table_text(tab.lines)[0][5] == "1,50"
    tab.line_widgets[0]["quantity"].setText("1.250")
    assert table_text(tab.lines)[0][5] == "1.250,00"


def test_a_pdf_that_cannot_be_printed_gives_a_message(win, monkeypatch):
    client, product, _ = ready(win)
    new_invoice(win, client, product, "1").issue_button.click()
    settle()
    section(win, "Issued invoices")
    monkeypatch.setattr(services, "issued_pdf", lambda _id: None)      # e.g. a damaged record
    cell_button(win.invoicing.issued_table, 0, "Open").click()
    assert "cannot be printed" in shown(win) and win.dialogs.opened == []



def test_a_double_click_on_issue_issues_once_and_keeps_the_success_message(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    first_token = tab.token
    tab.issue_button.click()
    tab.issue_button.click()                     # the second click reaches the fresh, empty form
    settle()
    assert shown(win).startswith("Invoice PFA-0001 issued") and len(services.invoicing_view()["issued"]) == 1
    assert tab.token and tab.token != first_token and tab.issue_button.isEnabled()
    tab.all_issued_button.click()
    settle()
    assert win.invoicing.sections.currentIndex() == 1


def test_the_same_form_sent_twice_is_issued_once(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    fields = tab.invoice_fields()
    assert services.generate_invoice(fields).ok                  # e.g. the first of two sends
    tab.issue_button.click()                                     # the same form again
    settle()
    assert shown(win).startswith("Not issued again") and "PFA-0001" in shown(win)
    assert len(services.invoicing_view()["issued"]) == 1


def test_a_refused_form_keeps_its_token_and_a_new_form_gets_a_new_one(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    tab.client_box.setCurrentIndex(0)
    token = tab.token
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued") and tab.token == token
    choose(tab.client_box, client)
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice PFA-0001 issued") and tab.token != token


def test_the_status_line_and_the_empty_choices(win):
    client, product, series = ready(win)
    section(win, "New invoice")
    tab = win.invoicing
    assert tab.series_box.itemText(0) == "— choose a series —" and tab.client_box.itemText(0) == "— choose a client —"
    assert tab.line_widgets[0]["product"].itemText(0) == "— choose —"
    assert tab.problems.text().startswith("Still needed before issuing: choose a client")
    tab.series_box.setCurrentIndex(0)
    assert "choose a series" in tab.problems.text()


def test_a_series_chosen_by_hand_and_deleted_meanwhile_is_not_swapped_silently(win):
    client, product, series = ready(win)
    other = services.save_series({"name": "B", "next_number": "1"}).extra["id"]
    win.refresh()
    tab = new_invoice(win, client, product, "1")
    i = tab.series_box.findData(other)
    tab.series_box.setCurrentIndex(i)
    tab.series_box.activated.emit(i)                             # as when the person picks it
    services.delete_series(other)
    win.refresh()
    assert tab.series_box.currentData() == other
    assert tab.series_box.currentText() == "(deleted from the list — choose another)"
    tab.issue_button.click()
    settle()
    assert shown(win).startswith("Invoice not issued") and services.invoicing_view()["issued"] == []


def test_characters_the_invoice_cannot_print_are_refused(win):
    section(win, "Clients")
    ed = win.invoicing.clients
    fill_form(ed, {**CLIENT, "name": "Rocket SRL 🚀"})
    ed.save_button.click()
    settle()
    assert shown(win).startswith("Client not saved") and "🚀" in shown(win)
    assert ed.fields["name"].text() == "Rocket SRL 🚀"


def test_a_price_with_currency_text_is_refused(win):
    section(win, "Products & services")
    ed = win.invoicing.products_editor
    for price in ("€5", "5 RON", "1.000.000.000"):
        fill_form(ed, {"name": "Support", "unit_price": price})
        ed.save_button.click()
        settle()
        assert shown(win).startswith("Product not saved"), price
    assert services.invoicing_view()["products"] == []


def test_the_notes_are_at_most_1000_characters_and_count_in_the_status(win):
    client, product, _ = ready(win)
    tab = new_invoice(win, client, product, "1")
    tab.notes.setPlainText("ă" * 1200)
    assert len(tab.notes.toPlainText()) == 1000
    assert tab.problems.text() == "Ready to be issued."
    tab.notes.setPlainText("Thanks 🙏")
    assert "cannot be printed" in tab.problems.text()
