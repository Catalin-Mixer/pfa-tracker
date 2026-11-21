"""The desktop app (Qt): every tab and action driven through its widgets, on the same files as the browser
version, with the same results and messages. Runs without a screen (Qt's offscreen mode)."""
import html
import json
import os
import re
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QDate  # noqa: E402

from conftest import LAST_YEAR, TODAY, YEAR, csv_upload, sample_csv  # noqa: E402
from files import PDF, PNG  # noqa: E402
from tracker import db, services  # noqa: E402
from tracker.config import ATTACHMENTS_DIR, DB_PATH, SETTINGS_PATH  # noqa: E402


class ScriptedDialogs:
    """Stands in for the file pickers and questions: answers are queued by the test."""

    def __init__(self):
        self.files, self.saves, self.answers, self.opened = [], [], [], []

    def open_file(self, title, file_filter):
        return self.files.pop(0) if self.files else None

    def save_file(self, title, suggested, file_filter):
        self.suggested = suggested
        return self.saves.pop(0) if self.saves else None

    def confirm(self, title, text):
        return self.answers.pop(0) if self.answers else False

    def open_path(self, path):
        self.opened.append(Path(path))


@pytest.fixture(scope="module")
def qapp():
    from tracker.desktop.common import STYLE
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture
def win(qapp):
    from tracker.desktop.app import MainWindow
    w = MainWindow()
    w.dialogs = ScriptedDialogs()
    w.show()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def go_tab(win, name):
    win.tabs.setCurrentIndex(["Overview", "Income", "Expenses", "Invoicing", "Settings"].index(name))
    QtWidgets.QApplication.processEvents()


def qd(text):
    y, m, d = map(int, text.split("-"))
    return QDate(y, m, d)


def shown(win) -> str:
    return win.message.text() if win.message.isVisible() else ""


def table_text(t) -> list[list[str]]:
    return [[t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())] for r in range(t.rowCount())]


def write_file(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def fill_invoice(win, date, client="Acme", number="A-7", amount="100", currency="RON", paid=None):
    tab = win.income
    tab.date.setDate(qd(date))
    tab.paid_check.setChecked(paid is not None)
    if paid:
        tab.paid.setDate(qd(paid))
    tab.client.setText(client)
    tab.number.setText(number)
    tab.amount.setText(amount)
    tab.currency.setCurrentText(currency)


# The window

def test_the_window_has_the_four_tabs_and_only_offers_the_current_year_when_empty(win):
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Overview", "Income", "Expenses", "Invoicing", "Settings"]
    assert [win.year_box.itemText(i) for i in range(win.year_box.count())] == [YEAR]
    assert win.overview.heading.text() == f"Overview {YEAR}" and win.overview.empty.isVisible()


def test_the_year_picker_is_hidden_on_settings(win):
    go_tab(win, "Settings")
    assert not win.year_box.isVisible()
    go_tab(win, "Income")
    assert win.year_box.isVisible()


def test_choosing_a_year_without_records_is_refused(win):
    win.choose_year("1999")
    assert "That year is not available" in shown(win) and win.year == YEAR


# Income

def test_adding_an_invoice_with_payment_date_and_proof(win, tmp_path, client):
    go_tab(win, "Income")
    win.dialogs.files.append(write_file(tmp_path, "scan.pdf", PDF))
    win.income.proof.choose()
    fill_invoice(win, f"{LAST_YEAR}-12-20", amount="1.000,00", currency="EUR", paid=f"{YEAR}-01-10")
    win.income.add_button.click()
    assert shown(win) == "Invoice added."
    row = db.get("invoices", 1)
    assert row["income_date"] == f"{YEAR}-01-10" and row["amount_ron"] == "5000.00" and row["attachment"] == "A-7_Acme.pdf"
    assert win.year == YEAR and win.income.heading.text() == f"Income {YEAR}"
    assert table_text(win.income.table)[0][:3] == [f"{LAST_YEAR}-12-20", f"{YEAR}-01-10", "Acme"]
    assert win.income.client.text() == "" and win.income.proof.upload is None       # form cleared
    assert "Acme" in client.get("/income").text                                       # the browser sees it too


def test_an_invoice_error_is_shown_and_the_form_is_kept(win):
    go_tab(win, "Income")
    fill_invoice(win, f"{LAST_YEAR}-05-05", amount="abc")
    win.income.add_button.click()
    assert "Invoice not added: unrecognised amount 'abc'" in shown(win)
    assert win.income.client.text() == "Acme" and win.income.amount.text() == "abc"
    assert db.connect().execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_dates_after_today_cannot_be_picked_and_payment_cannot_precede_the_invoice(win):
    tab = win.income
    assert tab.date.maximumDate() == QDate(TODAY.year, TODAY.month, TODAY.day)
    tab.date.setDate(qd((TODAY + timedelta(days=5)).isoformat()))
    assert tab.date.date() <= QDate(TODAY.year, TODAY.month, TODAY.day)
    tab.date.setDate(qd(f"{LAST_YEAR}-05-05"))
    tab.paid.setDate(qd(f"{LAST_YEAR}-05-01"))
    assert tab.paid.date() == qd(f"{LAST_YEAR}-05-05")
    assert win.expenses.date.maximumDate() == QDate(TODAY.year, TODAY.month, TODAY.day)


def test_a_renamed_proof_is_refused_with_the_same_message_as_in_the_browser(win, tmp_path, client):
    go_tab(win, "Income")
    win.dialogs.files.append(write_file(tmp_path, "fake.pdf", PNG))
    win.income.proof.choose()
    fill_invoice(win, f"{LAST_YEAR}-05-05")
    win.income.add_button.click()
    desktop_message = shown(win)
    web = client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "Acme", "number": "A-7",
                                           "amount": "100", "currency": "RON"},
                      files={"attachment": ("fake.pdf", PNG, "application/pdf")})
    assert desktop_message == "Invoice not added: attachment is not a PDF file" and desktop_message in html.unescape(web.text)


def test_importing_a_csv_shows_the_result_and_moves_to_its_year(win, tmp_path):
    go_tab(win, "Income")
    text = sample_csv(LAST_YEAR) + f"{LAST_YEAR}-02-30,Bad,1,RON\n"
    win.dialogs.files.append(write_file(tmp_path, "inv.csv", text.encode()))
    win.income.import_button.click()
    result = win.income.import_result.text()
    assert "3 invoice(s) added" in result and "1 row(s) with problems" in result and "Line 5" in result
    assert win.year == LAST_YEAR and len(table_text(win.income.table)) == 3
    assert [win.year_box.itemText(i) for i in range(win.year_box.count())] == [YEAR, LAST_YEAR]


def test_deleting_asks_first_and_removes_the_proof(win, tmp_path):
    go_tab(win, "Income")
    win.dialogs.files.append(write_file(tmp_path, "scan.pdf", PDF))
    win.income.proof.choose()
    fill_invoice(win, f"{LAST_YEAR}-05-05")
    win.income.add_button.click()
    win.dialogs.answers.append(False)
    win.income.delete(1)
    assert db.get("invoices", 1) is not None
    win.dialogs.answers.append(True)
    win.income.delete(1)
    assert db.get("invoices", 1) is None and shown(win) == "Invoice deleted."
    assert not any((ATTACHMENTS_DIR / "invoices").iterdir())


def test_opening_a_proof_hands_the_stored_file_to_the_system(win, tmp_path):
    win.dialogs.files.append(write_file(tmp_path, "scan.pdf", PDF))
    win.income.proof.choose()
    fill_invoice(win, f"{LAST_YEAR}-05-05")
    win.income.add_button.click()
    win.open_proof("invoices", 1)
    assert win.dialogs.opened == [ATTACHMENTS_DIR / "invoices" / "A-7_Acme.pdf"]
    win.open_proof("invoices", 99)
    assert "missing" in shown(win)


# Expenses

def test_adding_an_expense_with_a_proof_in_another_currency(win, tmp_path, client):
    go_tab(win, "Expenses")
    tab = win.expenses
    win.dialogs.files.append(write_file(tmp_path, "bon fiscal.png", PNG))
    tab.proof.choose()
    tab.date.setDate(qd(f"{LAST_YEAR}-06-06"))
    tab.amount.setText("10")
    tab.currency.setCurrentText("USD")
    tab.description.setText("Desk")
    tab.category.setText("equipment")
    tab.add_button.click()
    assert shown(win) == "Expense added." and win.year == LAST_YEAR
    rows = table_text(tab.table)
    assert rows[0][:5] == [f"{LAST_YEAR}-06-06", "Desk", "equipment", "10,00", "USD"] and rows[0][7] == "40,00"
    assert (ATTACHMENTS_DIR / "expenses" / "1_bon_fiscal.png").read_bytes() == PNG
    client.post("/year", data={"year": LAST_YEAR, "page": "expenses"})
    assert "Desk" in client.get("/expenses").text


@pytest.mark.parametrize("field, value, message", [
    ("amount", "0", "Expense not added: amount must be positive"),
    ("amount", "1e9", "Expense not added: unrecognised amount '1e9'"),
    ("description", "", "Expense not added: description is empty"),
])
def test_expense_errors_match_the_browser(win, client, field, value, message):
    tab = win.expenses
    values = {"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "100", "currency": "RON", field: value}
    tab.date.setDate(qd(values["date"]))
    tab.description.setText(values["description"])
    tab.amount.setText(values["amount"])
    tab.add_button.click()
    assert shown(win) == message
    assert message in html.unescape(client.post("/expenses", data=values).text)


# Settings

def settings_values(win, **values):
    tab = win.settings
    for key, widget in tab.inputs.items():
        widget.setText(values.get(key, ""))
    tab.caen_code.setText(values.get("caen_code", ""))
    tab.secondary.setText(values.get("secondary_caen", ""))
    tab.salaried_now.setChecked(values.get("salaried_now", False))


def test_saving_settings_writes_the_shared_file_and_the_browser_shows_them(win, client):
    go_tab(win, "Settings")
    settings_values(win, name="Popescu Ion PFA", cui="ro 14399840", trade_register="f40/1234/2019", phone="0721 234 567",
                    email="Ion@Example.RO", iban="ro49aaaa1b31007593840000", address="Str. Exemplu nr. 1, București",
                    caen_code="6210", secondary_caen="6920", salaried_now=True)
    win.settings.save_button.click()
    assert shown(win) == "Settings saved."
    saved = json.loads(SETTINGS_PATH.read_text())
    assert saved["firm"]["cui"] == "RO14399840" and saved["firm"]["iban"] == "RO49 AAAA 1B31 0075 9384 0000"
    assert saved["years"][YEAR]["has_salary"] is True
    assert "Popescu Ion PFA · CUI RO14399840 · CAEN 6210" in client.get("/").text
    assert win.firm_line.text() == "Popescu Ion PFA · CUI RO14399840 · CAEN 6210"
    assert win.settings.register_note.text() == "Registered in 2019."
    assert set(win.settings.earlier_checks) == {str(y) for y in range(2019, int(YEAR))}


def test_wrong_settings_are_all_reported_and_nothing_is_saved(win):
    before = SETTINGS_PATH.read_text()
    go_tab(win, "Settings")
    settings_values(win, name="X", trade_register="J1/1/1", phone="12", email="nope", iban="RO00")
    win.settings.save_button.click()
    message = shown(win)
    for reason in ("Not saved", "must look like F40/1234/2019", "phone number is not valid", "email address is not valid",
                   "IBAN is not valid"):
        assert reason in message
    assert SETTINGS_PATH.read_text() == before and win.settings.inputs["email"].text() == "nope"


def test_earlier_salaried_years_are_ticked_in_their_own_list(win):
    go_tab(win, "Settings")
    settings_values(win, name="X", trade_register=f"F40/1/{int(YEAR) - 3}")
    win.settings.save_button.click()
    win.settings.earlier_checks[LAST_YEAR].setChecked(True)
    win.settings.save_button.click()
    years = json.loads(SETTINGS_PATH.read_text())["years"]
    assert years[LAST_YEAR]["has_salary"] is True and years[YEAR].get("has_salary") is False
    win.settings.earlier_checks[LAST_YEAR].setChecked(False)
    win.settings.save_button.click()
    assert json.loads(SETTINGS_PATH.read_text())["years"][LAST_YEAR]["has_salary"] is False


# Backup

def test_backup_and_restore_through_the_desktop(win, tmp_path):
    go_tab(win, "Expenses")
    win.expenses.date.setDate(qd(f"{LAST_YEAR}-06-06"))
    win.expenses.description.setText("Desk")
    win.expenses.amount.setText("100")
    win.expenses.add_button.click()
    go_tab(win, "Settings")
    target = tmp_path / "backup.zip"
    win.dialogs.saves.append(target)
    win.settings.backup_button.click()
    assert win.dialogs.suggested.startswith("pfa-backup-") and "Backup saved" in shown(win)
    assert "pfa.sqlite" in zipfile.ZipFile(target).namelist()
    services.delete("expenses", 1)
    win.dialogs.files.append(target)
    win.dialogs.answers.append(False)
    win.settings.restore_button.click()
    assert "tick the box" in shown(win) or "Not restored" in shown(win)
    assert db.get("expenses", 1) is None
    win.dialogs.files.append(target)
    win.dialogs.answers.append(True)
    win.settings.restore_button.click()
    assert shown(win).startswith("Backup restored") and db.get("expenses", 1)["description"] == "Desk"


def test_a_backup_made_in_the_browser_restores_in_the_desktop(win, client, tmp_path):
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "From the browser", "amount": "5",
                                   "currency": "RON"})
    bundle = write_file(tmp_path, "b.zip", client.get("/backup").content)
    services.delete("expenses", 1)
    go_tab(win, "Settings")
    win.dialogs.files.append(bundle)
    win.dialogs.answers.append(True)
    win.settings.restore_button.click()
    assert db.get("expenses", 1)["description"] == "From the browser"


# Both modes together

def test_changes_made_in_the_browser_appear_when_the_window_is_refreshed(win, client):
    client.post("/settings", data={"name": "Browser PFA", "salary_years": ""})
    client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    win.refresh()
    assert win.firm_line.text().startswith("Browser PFA")
    assert LAST_YEAR in [win.year_box.itemText(i) for i in range(win.year_box.count())]
    win.choose_year(LAST_YEAR)
    go_tab(win, "Overview")
    assert win.overview.income_card.value.text() == "6.700,00"


def test_overview_numbers_are_the_same_as_in_the_browser(win, client):
    client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "x", "amount": "700", "currency": "RON"})
    win.refresh()
    win.choose_year(LAST_YEAR)
    go_tab(win, "Overview")
    client.post("/year", data={"year": LAST_YEAR, "page": "overview"})
    page = client.get("/").text
    for card in (win.overview.income_card, win.overview.expense_card, win.overview.net_card, win.overview.total_card):
        assert card.value.text() in page
    rows = table_text(win.overview.breakdown)
    assert rows[-1][0] == f"Total to pay for {LAST_YEAR}" and rows[-1][2] in page
