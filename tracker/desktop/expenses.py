"""Expenses tab: add a deductible expense, list the year's expenses."""
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QComboBox, QDateEdit, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import services
from .common import LEGEND, Card, fill_row, hbox, label, money, qdate, row_buttons, small_button, table, text_of
from .income import ProofPicker


class ExpensesTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        box = QVBoxLayout(self)
        self.heading = label("", "pageTitle")
        box.addWidget(self.heading)
        top = QHBoxLayout()
        self.total_card, self.count_card = Card("Deductible expenses", accent=True), Card("Expenses")
        top.addWidget(self.total_card, 1)
        top.addWidget(self.count_card, 1)
        top.addWidget(label("Add each deductible expense with the form below. Amounts in another currency are "
                            "converted to RON with the rate of that day, like invoices.", "muted"), 2)
        box.addLayout(top)

        form = QGroupBox("Add a deductible expense")
        self.form = form
        self.editing: int | None = None          # the id of the expense being edited, None when adding
        grid = QGridLayout(form)
        self.date = QDateEdit(calendarPopup=True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.amount = QLineEdit(placeholderText="250,00")
        self.currency = QComboBox()
        self.description = QLineEdit(placeholderText="e.g. laptop, accounting fee, internet")
        self.category = QLineEdit(placeholderText="e.g. equipment, rent (optional)")
        self.proof = ProofPicker(window)
        self.add_button = QPushButton("Add expense")
        self.add_button.clicked.connect(self.add)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Leaves the expense as it is stored.")
        self.cancel_button.clicked.connect(self.cancel_edit)
        for col, (title, widget) in enumerate([("Date", self.date), ("Amount", self.amount), ("Currency", self.currency),
                                               ("Description", self.description), ("Category", self.category)]):
            grid.addWidget(QLabel(title), 0, col)
            grid.addWidget(widget, 1, col)
        for col, weight in enumerate((0, 1, 0, 3, 2)):
            grid.setColumnStretch(col, weight)
        grid.addWidget(QLabel("Proof"), 2, 0)
        grid.addWidget(self.proof.current_line, 2, 1, 1, 4)
        grid.addWidget(self.proof, 3, 0, 1, 4)
        grid.addLayout(hbox(self.add_button, self.cancel_button, stretch=False), 3, 4,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        box.addWidget(form)

        self.list_title = label("", "pageTitle")
        box.addWidget(self.list_title)
        self.table = table(["Date", "Description", "Category", "Amount", "Cur.", "Rate", "Rate of", "RON", "Proof", ""], {3, 5, 7}, stretch=1)
        box.addWidget(self.table, 1)
        self.total = label("")
        box.addWidget(self.total)
        self.empty = label("", "muted")
        box.addWidget(self.empty)
        box.addWidget(label(LEGEND, "muted"))
        self.shown_year = None
        self.reset_form(str(services.today().year))

    def reset_form(self, year: str) -> None:
        today = services.today()
        self.date.setMinimumDate(QDate(2000, 1, 1))
        self.date.setMaximumDate(qdate(today))
        # today in the current year; the last day of a past year otherwise
        self.date.setDate(qdate(today) if year == str(today.year) else QDate(int(year), 12, 31))
        for w in (self.amount, self.description, self.category):
            w.clear()
        self.show_mode(None)

    def show_mode(self, row_id: int | None, stored_proof: str = "") -> None:
        """The form adds a new expense (row_id None) or edits the stored expense row_id."""
        self.editing = row_id
        editing = row_id is not None
        self.form.setTitle("Edit expense" if editing else "Add a deductible expense")
        self.add_button.setText("Save changes" if editing else "Add expense")
        self.cancel_button.setVisible(editing)
        self.proof.show_stored(editing, stored_proof)

    def end_edit(self) -> None:
        """Back to adding: the form is filled in afresh for the year shown at the next refresh."""
        if self.editing is not None:
            self.show_mode(None)
        self.shown_year = None

    def edit(self, row_id: int) -> None:
        """Loads a stored expense into the form to change it."""
        found = services.expense_for_edit(row_id)
        if found is None:
            self.window.after_action(services.Outcome(False, error="Expense not changed: it no longer exists."))
            return
        day = qdate(date.fromisoformat(found["date"]))
        self.date.setMinimumDate(min(QDate(2000, 1, 1), day))
        self.date.setDate(day)
        self.amount.setText(found["amount"])
        self.show_currency(found["currency"])
        for field, value in ((self.description, found["description"]), (self.category, found["category"])):
            field.setText(value)
            field.setCursorPosition(0)
        self.show_mode(row_id, services.proof_name("expenses", found["attachment"]) if found["attachment"] else "")
        self.window.clear_message()
        self.description.setFocus()

    def show_currency(self, code: str) -> None:
        if self.currency.findText(code) < 0:     # e.g. no longer in the rates file: shown, so it is not changed unseen
            self.currency.addItem(code)
        self.currency.setCurrentText(code)

    def cancel_edit(self) -> None:
        self.end_edit()
        self.window.clear_message()
        self.window.refresh()

    def refresh(self, view: dict) -> None:
        year, cur = view["year"], view["current"]
        if year != self.shown_year:
            if self.editing is None:              # what is being edited stays in the form
                self.reset_form(year)
            self.shown_year = year
        self.heading.setText(f"Expenses {year}")
        self.total_card.set(money(cur["expenses"]), "RON", f"Deductible expenses {year}")
        self.count_card.set(str(cur["n_expenses"]), f"in {year}")
        chosen = self.currency.currentText() or "RON"
        self.currency.clear()
        self.currency.addItems(services.currencies())
        if self.editing is not None:
            self.show_currency(chosen)
        else:
            self.currency.setCurrentText(chosen)
        self.list_title.setText(f"Expenses {year}")
        rows = view["expenses"]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            proof = small_button("Open") if r["attachment"] else ""
            if proof:
                proof.setToolTip(services.proof_name("expenses", r["attachment"]))
                proof.clicked.connect(lambda _=False, rid=r["id"]: self.window.open_proof("expenses", rid))
            change = small_button("Edit")
            change.setToolTip("Change this expense in the form above.")
            change.clicked.connect(lambda _=False, rid=r["id"]: self.edit(rid))
            remove = small_button("Delete", "link")
            remove.clicked.connect(lambda _=False, rid=r["id"]: self.delete(rid))
            fallback = r["currency"] != "RON" and r["rate_date"] != r["date"]
            fill_row(self.table, i, [r["date"], r["description"], r["category"], money(r["amount"]), r["currency"],
                                     r["rate"], r["rate_date"] if r["currency"] != "RON" else "", money(r["amount_ron"]),
                                     proof, row_buttons(change, remove)], {3, 5, 7}, warn={6} if fallback else set(),
                     tips={6: f"No rate for {r['date']}; the latest earlier rate was used"} if fallback else None)
        self.table.setVisible(True)
        self.total.setText(f"<b>Total {year}: {money(cur['expenses'])} RON</b>" if rows else "")
        self.empty.setText("" if rows else f"No expenses for {year}.")

    def add(self) -> None:
        fields = {"date": text_of(self.date.date()), "description": self.description.text(),
                  "category": self.category.text(), "amount": self.amount.text(), "currency": self.currency.currentText()}
        if self.editing is not None:
            outcome = services.update_expense(self.editing, fields, self.proof.upload, self.proof.remove_requested())
        else:
            outcome = services.add_expense(fields, self.proof.upload)
        if outcome.ok:
            self.end_edit()
        self.window.after_action(outcome)

    def delete(self, row_id: int) -> None:
        if self.window.dialogs.confirm("Delete expense", "Delete this expense (and its proof)?"):
            outcome = services.delete("expenses", row_id)
            if outcome.ok and row_id == self.editing:
                self.end_edit()                    # the expense being edited is gone
            self.window.after_action(outcome)
