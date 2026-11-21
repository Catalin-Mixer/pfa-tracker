"""Editing stored invoices and expenses in the desktop app: the Edit button of a row loads it into the tab's
form, which then saves the changes (checked and converted again like a new entry) or is cancelled."""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QDate, QEvent  # noqa: E402

from conftest import LAST_YEAR, TODAY, YEAR  # noqa: E402
from files import PDF, PNG  # noqa: E402
from tracker import db, services  # noqa: E402
from tracker.config import ATTACHMENTS_DIR  # noqa: E402


class ScriptedDialogs:
    def __init__(self):
        self.files, self.answers, self.asked = [], [], []

    def open_file(self, title, file_filter):
        return self.files.pop(0) if self.files else None

    def save_file(self, title, suggested, file_filter):
        return None

    def confirm(self, title, text):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else False

    def open_path(self, path):
        pass


@pytest.fixture(scope="module")
def qapp():
    from tracker.desktop.common import STYLE
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def weekday(text: str) -> str:
    """The given day, or the next weekday (the test rates exist on weekdays only)."""
    d = date.fromisoformat(text)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


DAY = weekday(f"{LAST_YEAR}-05-04")
LATER = weekday(f"{LAST_YEAR}-06-08")
THIS_YEAR_DAY = weekday(f"{YEAR}-01-05")


def go_tab(win, name):
    win.tabs.setCurrentIndex(["Overview", "Income", "Expenses", "Invoicing", "Settings"].index(name))
    QtWidgets.QApplication.processEvents()


def qd(text):
    y, m, d = map(int, text.split("-"))
    return QDate(y, m, d)


def shown(win) -> str:
    return win.message.text() if win.message.isVisible() else ""


def write_file(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def row_button(table, row: int, text: str):
    """A button (Edit, delete) in the last column of a table row."""
    cell = table.cellWidget(row, table.columnCount() - 1)
    return next(b for b in cell.findChildren(QtWidgets.QPushButton) if b.text() == text)


def row_of(table, column: int, text: str) -> int:
    return next(r for r in range(table.rowCount()) if table.item(r, column).text() == text)


def add_invoice(win, day=DAY, client="Acme", number="A-7", amount="100", currency="RON", paid=None, proof=None):
    tab = win.income
    if proof:
        win.dialogs.files.append(proof)
        tab.proof.choose()
    tab.date.setDate(qd(day))
    tab.paid_check.setChecked(paid is not None)
    if paid:
        tab.paid.setDate(qd(paid))
    tab.client.setText(client)
    tab.number.setText(number)
    tab.amount.setText(amount)
    tab.currency.setCurrentText(currency)
    tab.add_button.click()
    assert shown(win).startswith("Invoice added"), shown(win)


def add_expense(win, day=DAY, description="Laptop", amount="250", currency="RON", category="", proof=None):
    tab = win.expenses
    if proof:
        win.dialogs.files.append(proof)
        tab.proof.choose()
    tab.date.setDate(qd(day))
    tab.amount.setText(amount)
    tab.currency.setCurrentText(currency)
    tab.description.setText(description)
    tab.category.setText(category)
    tab.add_button.click()
    assert shown(win).startswith("Expense added"), shown(win)


def edit_invoice(win, client="Acme"):
    go_tab(win, "Income")
    row_button(win.income.table, row_of(win.income.table, 2, client), "Edit").click()


def edit_expense(win, description="Laptop"):
    go_tab(win, "Expenses")
    row_button(win.expenses.table, row_of(win.expenses.table, 1, description), "Edit").click()


def invoice_files() -> list[str]:
    folder = ATTACHMENTS_DIR / "invoices"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def expense_files() -> list[str]:
    folder = ATTACHMENTS_DIR / "expenses"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def in_add_mode(tab, title, button) -> bool:
    return (tab.editing is None and tab.form.title() == title and tab.add_button.text() == button
            and not tab.cancel_button.isVisible() and not tab.proof.current_line.isVisible())


# Invoices

def test_edit_loads_the_invoice_into_the_form(win, tmp_path):
    go_tab(win, "Income")
    assert in_add_mode(win.income, "Add an invoice by hand", "Add invoice")
    add_invoice(win, amount="1234.5", currency="EUR", paid=LATER, proof=write_file(tmp_path, "scan.pdf", PDF))
    edit_invoice(win)
    tab = win.income
    assert tab.editing == 1 and tab.form.title() == "Edit invoice"
    assert tab.add_button.text() == "Save changes" and tab.cancel_button.isVisible()
    assert tab.date.date() == qd(DAY) and tab.paid_check.isChecked() and tab.paid.isEnabled() and tab.paid.date() == qd(LATER)
    assert (tab.client.text(), tab.number.text(), tab.amount.text(), tab.currency.currentText()) == ("Acme", "A-7", "1.234,50", "EUR")
    assert tab.proof.current.text() == "Current proof: A-7_Acme.pdf" and tab.proof.remove_check.isVisible()
    assert tab.proof.name.text() == "no file chosen: the current proof is kept" and not tab.proof.remove_check.isChecked()


def test_an_invoice_without_payment_date_or_proof_is_shown_as_such(win):
    go_tab(win, "Income")
    add_invoice(win, paid=LATER, client="Paid")
    add_invoice(win, client="Unpaid", number="A-8")
    edit_invoice(win, "Paid")
    edit_invoice(win, "Unpaid")                             # another Edit replaces what the form shows
    tab = win.income
    assert tab.editing == 2 and not tab.paid_check.isChecked() and not tab.paid.isEnabled()
    assert tab.client.text() == "Unpaid" and tab.number.text() == "A-8"
    assert tab.proof.current.text() == "No proof stored yet." and not tab.proof.remove_check.isVisible()


def test_saving_changes_the_invoice_converts_it_again_and_moves_to_its_new_year(win):
    if date.fromisoformat(THIS_YEAR_DAY) > TODAY:
        pytest.skip("no weekday of this year before today")
    go_tab(win, "Income")
    add_invoice(win, amount="100", currency="RON")
    assert win.year == LAST_YEAR
    edit_invoice(win)
    tab = win.income
    tab.amount.setText("1.000,00")
    tab.currency.setCurrentText("EUR")
    tab.paid_check.setChecked(True)
    tab.paid.setDate(qd(THIS_YEAR_DAY))
    tab.client.setText("Acme GmbH")
    tab.add_button.click()
    assert shown(win) == "Invoice changed."
    row = db.get("invoices", 1)
    assert (row["date"], row["payment_date"], row["income_date"]) == (DAY, THIS_YEAR_DAY, THIS_YEAR_DAY)
    assert (row["client"], row["amount"], row["currency"], row["amount_ron"]) == ("Acme GmbH", "1000", "EUR", "5000.00")
    assert win.year == YEAR and tab.heading.text() == f"Income {YEAR}"
    assert tab.table.rowCount() == 1 and tab.table.item(0, 2).text() == "Acme GmbH"
    assert in_add_mode(tab, "Add an invoice by hand", "Add invoice") and tab.client.text() == "" and tab.amount.text() == ""
    assert db.connect().execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1


def test_removing_the_payment_date_moves_the_invoice_back(win):
    if date.fromisoformat(THIS_YEAR_DAY) > TODAY:
        pytest.skip("no weekday of this year before today")
    go_tab(win, "Income")
    add_invoice(win, paid=THIS_YEAR_DAY, amount="10", currency="USD")
    assert win.year == YEAR
    edit_invoice(win)
    win.income.paid_check.setChecked(False)
    win.income.add_button.click()
    row = db.get("invoices", 1)
    assert row["payment_date"] == "" and row["income_date"] == DAY and row["amount_ron"] == "40.00"
    assert win.year == LAST_YEAR


def test_making_an_invoice_identical_to_another_is_refused_and_the_form_stays(win):
    go_tab(win, "Income")
    add_invoice(win, client="Acme", number="A-1", amount="100")
    add_invoice(win, client="Acme", number="A-2", amount="100,00")        # the same amount, typed differently
    go_tab(win, "Income")
    row_button(win.income.table, row_of(win.income.table, 3, "A-2"), "Edit").click()
    tab = win.income
    assert tab.editing == 2
    before = db.get("invoices", 2)
    tab.number.setText("A-1")
    tab.add_button.click()
    assert shown(win) == ("Invoice not changed: another stored invoice already has this date, client, number, "
                          "amount and currency.")
    assert tab.editing == 2 and tab.form.title() == "Edit invoice" and tab.add_button.text() == "Save changes"
    assert tab.number.text() == "A-1" and tab.client.text() == "Acme"
    assert db.get("invoices", 2) == before
    assert db.connect().execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 2


def test_an_error_keeps_the_form_in_edit_mode_with_what_was_typed(win):
    go_tab(win, "Income")
    add_invoice(win)
    edit_invoice(win)
    tab = win.income
    tab.amount.setText("abc")
    tab.client.setText("Changed")
    tab.add_button.click()
    assert shown(win) == "Invoice not changed: unrecognised amount 'abc'"
    assert tab.editing == 1 and tab.form.title() == "Edit invoice" and tab.cancel_button.isVisible()
    assert tab.amount.text() == "abc" and tab.client.text() == "Changed"
    assert db.get("invoices", 1)["client"] == "Acme"
    tab.amount.setText("200")                               # fixed: saved
    tab.add_button.click()
    assert shown(win) == "Invoice changed." and db.get("invoices", 1)["amount_ron"] == "200.00"
    assert in_add_mode(tab, "Add an invoice by hand", "Add invoice")


def test_cancel_leaves_the_invoice_unchanged(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win, proof=write_file(tmp_path, "scan.pdf", PDF))
    before = db.get("invoices", 1)
    edit_invoice(win)
    tab = win.income
    tab.client.setText("Someone else")
    tab.amount.setText("999")
    tab.proof.remove_check.setChecked(True)
    tab.cancel_button.click()
    assert db.get("invoices", 1) == before and invoice_files() == ["A-7_Acme.pdf"]
    assert in_add_mode(tab, "Add an invoice by hand", "Add invoice")
    assert tab.client.text() == "" and tab.amount.text() == "" and shown(win) == ""


def test_the_proof_is_kept_and_renamed_when_number_or_client_change(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win, proof=write_file(tmp_path, "scan.pdf", PDF))
    edit_invoice(win)
    win.income.amount.setText("150")                        # same number and client: same name
    win.income.add_button.click()
    assert db.get("invoices", 1)["attachment"] == "A-7_Acme.pdf" and invoice_files() == ["A-7_Acme.pdf"]
    edit_invoice(win)
    win.income.number.setText("B-9")
    win.income.client.setText("Beta")
    win.income.add_button.click()
    assert shown(win) == "Invoice changed."
    assert db.get("invoices", 1)["attachment"] == "B-9_Beta.pdf" and invoice_files() == ["B-9_Beta.pdf"]
    assert (ATTACHMENTS_DIR / "invoices" / "B-9_Beta.pdf").read_bytes() == PDF
    assert services.proof("invoices", 1)[0].name == "B-9_Beta.pdf"


def test_a_new_file_replaces_the_proof(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win, proof=write_file(tmp_path, "scan.pdf", PDF))
    edit_invoice(win)
    tab = win.income
    win.dialogs.files.append(write_file(tmp_path, "photo.png", PNG))
    tab.proof.choose()
    assert tab.proof.name.text() == "photo.png (replaces the current proof)" and not tab.proof.remove_check.isEnabled()
    tab.add_button.click()
    assert shown(win) == "Invoice changed."
    assert db.get("invoices", 1)["attachment"] == "A-7_Acme.png" and invoice_files() == ["A-7_Acme.png"]
    assert (ATTACHMENTS_DIR / "invoices" / "A-7_Acme.png").read_bytes() == PNG


def test_an_invoice_without_proof_gets_one_when_edited(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win)
    edit_invoice(win)
    win.dialogs.files.append(write_file(tmp_path, "scan.pdf", PDF))
    win.income.proof.choose()
    assert win.income.proof.name.text() == "scan.pdf"
    win.income.add_button.click()
    assert db.get("invoices", 1)["attachment"] == "A-7_Acme.pdf" and invoice_files() == ["A-7_Acme.pdf"]


def test_the_proof_can_be_removed(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win, proof=write_file(tmp_path, "scan.pdf", PDF))
    edit_invoice(win)
    tab = win.income
    tab.proof.remove_check.setChecked(True)
    assert tab.proof.name.text() == "the current proof will be removed" and not tab.proof.button.isEnabled()
    tab.add_button.click()
    assert shown(win) == "Invoice changed."
    assert db.get("invoices", 1)["attachment"] == "" and invoice_files() == []
    assert tab.table.cellWidget(0, 9) is None                # no Open button any more


def test_a_refused_new_proof_leaves_everything_as_it_was(win, tmp_path):
    go_tab(win, "Income")
    add_invoice(win, proof=write_file(tmp_path, "scan.pdf", PDF))
    before = db.get("invoices", 1)
    edit_invoice(win)
    win.dialogs.files.append(write_file(tmp_path, "fake.pdf", PNG))
    win.income.proof.choose()
    win.income.amount.setText("300")
    win.income.add_button.click()
    assert shown(win) == "Invoice not changed: attachment is not a PDF file"
    assert db.get("invoices", 1) == before and invoice_files() == ["A-7_Acme.pdf"] and win.income.editing == 1


def test_an_invoice_that_disappeared_cannot_be_edited(win):
    go_tab(win, "Income")
    add_invoice(win)
    services.delete("invoices", 1)                          # e.g. deleted in the browser meanwhile
    row_button(win.income.table, 0, "Edit").click()         # the row is still listed here
    assert shown(win) == "Invoice not changed: it no longer exists."
    assert in_add_mode(win.income, "Add an invoice by hand", "Add invoice") and win.income.table.rowCount() == 0


def test_saving_an_invoice_deleted_meanwhile_shows_the_error_and_stays_in_edit_mode(win):
    go_tab(win, "Income")
    add_invoice(win)
    edit_invoice(win)
    services.delete("invoices", 1)
    win.income.add_button.click()
    assert shown(win) == "Invoice not changed: it no longer exists."
    assert win.income.editing == 1 and win.income.client.text() == "Acme"
    assert db.connect().execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    win.income.cancel_button.click()
    assert in_add_mode(win.income, "Add an invoice by hand", "Add invoice")


def test_deleting_the_invoice_being_edited_ends_the_edit(win):
    go_tab(win, "Income")
    add_invoice(win)
    edit_invoice(win)
    win.dialogs.answers.append(True)
    row_button(win.income.table, 0, "Delete").click()
    assert shown(win) == "Invoice deleted." and in_add_mode(win.income, "Add an invoice by hand", "Add invoice")


def test_the_edit_stays_while_moving_between_tabs(win):
    go_tab(win, "Income")
    add_invoice(win)
    edit_invoice(win)
    win.income.client.setText("Typed")
    go_tab(win, "Overview")
    go_tab(win, "Income")
    assert win.income.editing == 1 and win.income.client.text() == "Typed"


# Expenses

def test_edit_loads_the_expense_into_the_form(win, tmp_path):
    go_tab(win, "Expenses")
    assert in_add_mode(win.expenses, "Add a deductible expense", "Add expense")
    add_expense(win, amount="99.9", currency="USD", category="equipment", proof=write_file(tmp_path, "bill.pdf", PDF))
    edit_expense(win)
    tab = win.expenses
    assert tab.editing == 1 and tab.form.title() == "Edit expense" and tab.add_button.text() == "Save changes"
    assert tab.cancel_button.isVisible() and tab.date.date() == qd(DAY)
    assert (tab.amount.text(), tab.currency.currentText(), tab.description.text(), tab.category.text()) == (
        "99,90", "USD", "Laptop", "equipment")
    assert tab.proof.current.text() == "Current proof: bill.pdf" and tab.proof.remove_check.isVisible()


def test_saving_changes_the_expense_converts_it_again_and_moves_to_its_new_year(win, tmp_path):
    if date.fromisoformat(THIS_YEAR_DAY) > TODAY:
        pytest.skip("no weekday of this year before today")
    go_tab(win, "Expenses")
    add_expense(win, proof=write_file(tmp_path, "bill.pdf", PDF))
    assert win.year == LAST_YEAR
    edit_expense(win)
    tab = win.expenses
    tab.date.setDate(qd(THIS_YEAR_DAY))
    tab.amount.setText("20")
    tab.currency.setCurrentText("EUR")
    tab.description.setText("Monitor")
    tab.category.setText("equipment")
    tab.add_button.click()
    assert shown(win) == "Expense changed."
    row = db.get("expenses", 1)
    assert (row["date"], row["description"], row["category"], row["amount"], row["currency"], row["amount_ron"]) == (
        THIS_YEAR_DAY, "Monitor", "equipment", "20", "EUR", "100.00")
    assert row["attachment"] and expense_files() == [row["attachment"]]          # the proof stays
    assert win.year == YEAR and tab.table.rowCount() == 1 and tab.table.item(0, 1).text() == "Monitor"
    assert in_add_mode(tab, "Add a deductible expense", "Add expense") and tab.description.text() == ""
    assert tab.date.date() == QDate(TODAY.year, TODAY.month, TODAY.day)


def test_expense_proof_replaced_and_removed(win, tmp_path):
    go_tab(win, "Expenses")
    add_expense(win, proof=write_file(tmp_path, "bill.pdf", PDF))
    edit_expense(win)
    win.dialogs.files.append(write_file(tmp_path, "receipt.png", PNG))
    win.expenses.proof.choose()
    win.expenses.add_button.click()
    assert shown(win) == "Expense changed."
    stored = db.get("expenses", 1)["attachment"]
    assert stored.endswith("receipt.png") and expense_files() == [stored]
    edit_expense(win)
    assert win.expenses.proof.current.text() == "Current proof: receipt.png"
    win.expenses.proof.remove_check.setChecked(True)
    win.expenses.add_button.click()
    assert db.get("expenses", 1)["attachment"] == "" and expense_files() == []


def test_expense_errors_keep_edit_mode_and_cancel_changes_nothing(win):
    go_tab(win, "Expenses")
    add_expense(win)
    before = db.get("expenses", 1)
    edit_expense(win)
    tab = win.expenses
    tab.description.setText("   ")
    tab.amount.setText("300")
    tab.add_button.click()
    assert shown(win) == "Expense not changed: description is empty"
    assert tab.editing == 1 and tab.form.title() == "Edit expense" and tab.amount.text() == "300"
    assert db.get("expenses", 1) == before
    go_tab(win, "Overview")                                  # the form keeps the edit across tabs and years
    go_tab(win, "Expenses")
    assert tab.editing == 1 and tab.amount.text() == "300"
    tab.cancel_button.click()
    assert db.get("expenses", 1) == before
    assert in_add_mode(tab, "Add a deductible expense", "Add expense") and tab.amount.text() == "" and shown(win) == ""


def test_an_expense_that_disappeared_cannot_be_edited(win):
    go_tab(win, "Expenses")
    add_expense(win)
    services.delete("expenses", 1)
    row_button(win.expenses.table, 0, "Edit").click()
    assert shown(win) == "Expense not changed: it no longer exists."
    assert win.expenses.editing is None and win.expenses.table.rowCount() == 0


def test_deleting_the_expense_being_edited_ends_the_edit(win):
    go_tab(win, "Expenses")
    add_expense(win)
    edit_expense(win)
    win.dialogs.answers.append(True)
    row_button(win.expenses.table, 0, "Delete").click()
    assert shown(win) == "Expense deleted." and in_add_mode(win.expenses, "Add a deductible expense", "Add expense")
