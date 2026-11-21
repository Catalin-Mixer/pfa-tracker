"""Income tab: import invoices from CSV, add one by hand, list the year's invoices."""
import html
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from .. import services
from .common import LEGEND, Card, fill_row, hbox, label, money, qdate, row_buttons, set_kind, small_button, table, text_of


ISSUED_SOURCE = "invoicing"        # the source of an Income row added by issuing an invoice in Invoicing


def issued_tag(number: str) -> QWidget:
    """The invoice number with a small "issued" tag: the invoice was issued in Invoicing."""
    cell = QWidget()
    row = QHBoxLayout(cell)
    row.setContentsMargins(4, 0, 4, 0)
    row.setSpacing(6)
    row.addWidget(label(number, wrap=False))
    tag = label("issued", "tag", wrap=False)
    tag.setToolTip("Issued in Invoicing: its PDF is the proof. Deleting it here also removes it from the issued invoices.")
    row.addWidget(tag)
    row.addStretch(1)
    cell.setToolTip(number)
    return cell


class ProofPicker(QWidget):
    """'Choose file…' with the chosen file's name; holds (name, bytes) or None.

    When an entry is being edited it also shows the proof stored with it: that proof is kept, replaced by a
    chosen file, or removed with the "Remove the current proof" box."""

    NOTHING_CHOSEN = "no file chosen (optional: PDF or image)"

    def __init__(self, window, file_filter: str = "Proofs (*.pdf *.png *.jpg *.jpeg *.gif *.webp *.txt *.xml)"):
        super().__init__()
        self.window, self.filter, self.upload, self.stored = window, file_filter, None, ""
        # the stored proof and its remove box: a line of its own, placed by the tab (shown only while editing)
        current_row = QHBoxLayout()
        self.current = label("", "muted", wrap=False)
        self.remove_check = QCheckBox("Remove the current proof")
        self.remove_check.toggled.connect(self.show_state)
        current_row.addWidget(self.current)
        current_row.addWidget(self.remove_check)
        current_row.addStretch(1)
        self.current_line = QWidget()
        self.current_line.setLayout(current_row)
        current_row.setContentsMargins(0, 0, 0, 0)
        self.current_line.setVisible(False)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.button = small_button("Choose file…")
        self.name = label(self.NOTHING_CHOSEN, "muted", wrap=False)
        self.clear_button = small_button("✕", "link")
        self.clear_button.setVisible(False)
        self.button.clicked.connect(self.choose)
        self.clear_button.clicked.connect(self.clear)
        for w in (self.button, self.name, self.clear_button):
            row.addWidget(w)
        row.addStretch(1)

    def choose(self) -> None:
        path = self.window.dialogs.open_file("Choose a proof", self.filter)
        if path:
            self.upload = (path.name, path.read_bytes())
            self.show_state()

    def clear(self) -> None:
        self.upload = None
        self.show_state()

    def show_stored(self, editing: bool, stored_name: str = "") -> None:
        """Add mode (editing False), or edit mode for an entry whose proof is shown as stored_name ('' = none)."""
        self.upload, self.stored = None, stored_name if editing else ""
        self.remove_check.setChecked(False)
        self.current_line.setVisible(editing)
        self.current.setText(f"Current proof: {stored_name}" if stored_name else "No proof stored yet.")
        self.remove_check.setVisible(bool(self.stored))
        self.show_state()

    def remove_requested(self) -> bool:
        return bool(self.stored) and self.upload is None and self.remove_check.isChecked()

    def show_state(self) -> None:
        removing = bool(self.stored) and self.remove_check.isChecked()
        if self.upload:
            text = self.upload[0] + (" (replaces the current proof)" if self.stored else "")
        elif removing:
            text = "the current proof will be removed"
        elif self.stored:
            text = "no file chosen: the current proof is kept"
        else:
            text = self.NOTHING_CHOSEN
        self.name.setText(text)
        self.clear_button.setVisible(self.upload is not None)
        self.button.setEnabled(not removing)
        self.remove_check.setEnabled(self.upload is None)


class IncomeTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        box = QVBoxLayout(self)
        self.heading = label("", "pageTitle")
        box.addWidget(self.heading)

        top = QHBoxLayout()
        self.total_card, self.count_card = Card("Gross income", accent=True), Card("Invoices")
        top.addWidget(self.total_card, 1)
        top.addWidget(self.count_card, 1)
        importer = QGroupBox("Import invoices from CSV")
        ibox = QVBoxLayout(importer)
        self.import_button = QPushButton("Import a CSV file…")
        self.import_button.clicked.connect(self.import_csv)
        button_row = QHBoxLayout()
        button_row.addWidget(self.import_button)
        button_row.addStretch(1)
        ibox.addLayout(button_row)
        ibox.addWidget(label("Columns: date, client, amount, currency; optional number and payment_date. Comma or "
                             "semicolon separated; dates as 2026-03-15 or 15.03.2026, not after today. Invoices "
                             "already stored are skipped.", "muted"))
        top.addWidget(importer, 2)
        box.addLayout(top)
        self.import_result = label("", "message")
        self.import_result.setTextFormat(Qt.TextFormat.RichText)
        self.import_result.setVisible(False)
        box.addWidget(self.import_result)

        form = QGroupBox("Add an invoice by hand")
        self.form = form
        self.editing: int | None = None          # the id of the invoice being edited, None when adding
        grid = QGridLayout(form)
        self.date = QDateEdit(calendarPopup=True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.paid_check = QCheckBox("Paid on")
        self.paid = QDateEdit(calendarPopup=True)
        self.paid.setDisplayFormat("yyyy-MM-dd")
        self.paid.setEnabled(False)
        self.paid_check.toggled.connect(self.paid.setEnabled)
        self.date.dateChanged.connect(lambda d: self.paid.setMinimumDate(d))   # payment never before the invoice
        self.client = QLineEdit(placeholderText="e.g. Nordwind Software GmbH")
        self.number = QLineEdit(placeholderText="e.g. PFA-2026-012 (optional)")
        self.amount = QLineEdit(placeholderText="4.200,00")
        self.currency = QComboBox(editable=True)
        self.currency.lineEdit().setMaxLength(3)
        self.proof = ProofPicker(window)
        self.add_button = QPushButton("Add invoice")
        self.add_button.clicked.connect(self.add)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Leaves the invoice as it is stored.")
        self.cancel_button.clicked.connect(self.cancel_edit)
        cells = [("Invoice date", self.date), (None, self.paid_check), ("", self.paid), ("Client", self.client),
                 ("Number", self.number), ("Amount", self.amount), ("Currency", self.currency)]
        col = 0
        for title, widget in cells:
            if title is not None:
                grid.addWidget(QLabel(title), 0, col)
            grid.addWidget(widget, 1, col)
            col += 1
        grid.addWidget(QLabel("Proof"), 2, 0)
        grid.addWidget(self.proof.current_line, 2, 1, 1, 6)
        grid.addWidget(self.proof, 3, 0, 1, 5)
        grid.addLayout(hbox(self.add_button, self.cancel_button, stretch=False), 3, 5, 1, 2,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.form_help = label("The income counts in the year the money was received, so when a date of payment is "
                               "given it sets the year and the exchange rate; otherwise the invoice date does. An invoice "
                               "stored without a date of payment gets one when you add it again with the date of payment.",
                               "muted")
        grid.addWidget(self.form_help, 4, 0, 1, 7)
        # in its place while an invoice issued in Invoicing is edited (the window has no room for both)
        self.generated_note = label(services.GENERATED_NOTE, "warn")
        self.generated_note.setVisible(False)
        grid.addWidget(self.generated_note, 4, 0, 1, 7)
        box.addWidget(form)

        self.list_title = label("", "pageTitle")
        box.addWidget(self.list_title)
        self.table = table(["Invoice date", "Paid on", "Client", "No.", "Amount", "Cur.", "Rate", "Rate of", "RON", "Proof", ""],
                           {4, 6, 8}, stretch=2)
        box.addWidget(self.table, 1)
        self.total = label("")
        box.addWidget(self.total)
        self.empty = label("", "muted")
        box.addWidget(self.empty)
        box.addWidget(label(LEGEND + " An invoice stored without a date of payment gets one when you add it again with "
                            "the date of payment filled in.", "muted"))
        self.reset_form()

    def reset_form(self) -> None:
        today = services.today()
        self.date.setMaximumDate(qdate(today))
        self.paid.setMaximumDate(qdate(today))
        self.date.setMinimumDate(QDate(2000, 1, 1))
        self.date.setDate(qdate(today))
        self.paid.setDate(qdate(today))
        self.paid_check.setChecked(False)
        for w in (self.client, self.number, self.amount):
            w.clear()
        self.show_mode(None)

    def show_mode(self, row_id: int | None, stored_proof: str = "", generated: bool = False,
                  credit_note: bool = False) -> None:
        """The form adds a new invoice (row_id None) or edits the stored invoice row_id
        (generated: it was issued in Invoicing; credit_note: it is a credit note, whose amount stays negative)."""
        self.editing = row_id
        editing = row_id is not None
        self.generated_note.setText(services.GENERATED_NOTE + (" " + services.CREDIT_NOTE_EDIT_NOTE if credit_note else ""))
        self.generated_note.setVisible(editing and generated)
        self.form_help.setVisible(not (editing and generated))
        self.form.setTitle("Edit invoice" if editing else "Add an invoice by hand")
        self.add_button.setText("Save changes" if editing else "Add invoice")
        self.cancel_button.setVisible(editing)
        self.proof.show_stored(editing, stored_proof)

    def edit(self, row_id: int) -> None:
        """Loads a stored invoice into the form to change it."""
        found = services.invoice_for_edit(row_id)
        if found is None:
            self.window.after_action(services.Outcome(False, error="Invoice not changed: it no longer exists."))
            return
        self.date.setMinimumDate(min(QDate(2000, 1, 1), qdate(date.fromisoformat(found["date"]))))
        self.date.setDate(qdate(date.fromisoformat(found["date"])))
        self.paid_check.setChecked(bool(found["payment_date"]))
        self.paid.setDate(qdate(date.fromisoformat(found["payment_date"])) if found["payment_date"] else self.date.date())
        for field, value in ((self.client, found["client"]), (self.number, found["number"])):
            field.setText(value)
            field.setCursorPosition(0)            # a long name shows from its start
        self.amount.setText(found["amount"])
        self.currency.setCurrentText(found["currency"])
        self.show_mode(row_id, services.proof_name("invoices", found["attachment"]) if found["attachment"] else "",
                       generated=found.get("generated", False), credit_note=bool(found.get("credit_note")))
        self.window.clear_message()
        self.client.setFocus()

    def cancel_edit(self) -> None:
        self.reset_form()
        self.window.clear_message()

    def refresh(self, view: dict) -> None:
        year, cur = view["year"], view["current"]
        self.heading.setText(f"Income {year}")
        self.total_card.set(money(cur["income"]), "RON", f"Gross income {year}")
        self.count_card.set(str(cur["n_invoices"]), f"in {year}")
        current_currency = self.currency.currentText() or "RON"
        self.currency.clear()
        self.currency.addItems(services.currencies())
        self.currency.setCurrentText(current_currency)
        self.list_title.setText(f"Invoices {year}")
        rows = view["invoices"]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            proof = small_button("Open") if r["attachment"] else ""
            if proof:
                proof.setToolTip(services.proof_name("invoices", r["attachment"]))
                proof.clicked.connect(lambda _=False, rid=r["id"]: self.window.open_proof("invoices", rid))
            change = small_button("Edit")
            change.setToolTip("Change this invoice in the form above.")
            change.clicked.connect(lambda _=False, rid=r["id"]: self.edit(rid))
            remove = small_button("Delete", "link")
            remove.clicked.connect(lambda _=False, rid=r["id"]: self.delete(rid))
            fallback = r["currency"] != "RON" and r["rate_date"] != r["income_date"]
            number = issued_tag(r["number"]) if r["source"] == ISSUED_SOURCE else r["number"]
            fill_row(self.table, i, [r["date"], r["payment_date"], r["client"], number, money(r["amount"]),
                                     r["currency"], r["rate"], r["rate_date"] if r["currency"] != "RON" else "",
                                     money(r["amount_ron"]), proof, row_buttons(change, remove)], {4, 6, 8},
                     tips={7: f"No rate for {r['income_date']}; the latest earlier rate was used"} if fallback else None,
                     warn={7} if fallback else set())
        self.table.setVisible(True)
        self.total.setText(f"<b>Total {year}: {money(cur['income'])} RON</b>" if rows else "")
        self.empty.setText("" if rows else f"No invoices for {year}.")

    def import_csv(self) -> None:
        path = self.window.dialogs.open_file("Import invoices from CSV", "CSV files (*.csv *.txt);;All files (*)")
        if not path:
            return
        outcome = services.import_csv(path.read_bytes(), path.name)
        self.show_import(outcome)
        self.window.after_action(outcome, keep_message=True)

    def show_import(self, outcome: services.Outcome) -> None:
        imp = outcome.extra.get("import")
        if outcome.error or not imp:
            self.import_result.setVisible(False)
            if outcome.error:
                self.window.show_outcome(outcome)
            return
        e = html.escape
        if imp["fatal"]:
            text, ok = f"<b>Import of {e(imp['source'])} failed:</b> {e(imp['fatal'])}", False
        else:
            text = (f"<b>Imported {e(imp['source'])}:</b> {len(imp['added'])} invoice(s) added, "
                    + (f"{len(imp['paid'])} stored invoice(s) got their date of payment, " if imp["paid"] else "")
                    + f"{len(imp['duplicates'])} already stored (skipped), {len(imp['errors'])} row(s) with problems (not imported).")
            if imp["errors"]:
                text += "<ul>" + "".join(f"<li>Line {err['line']}: {e(err['error'])} — {e(' | '.join(err['row'].values()))}</li>"
                                         for err in imp["errors"]) + "</ul>"
                text += "Fix those rows and import the same file again — invoices already stored are recognised and skipped."
            ok = not imp["errors"]
        set_kind(self.import_result, "message" if ok else "error")
        self.import_result.setText(text)
        self.import_result.setVisible(True)

    def add(self) -> None:
        fields = {"date": text_of(self.date.date()), "payment_date": text_of(self.paid.date()) if self.paid_check.isChecked() else "",
                  "client": self.client.text(), "number": self.number.text(), "amount": self.amount.text(),
                  "currency": self.currency.currentText()}
        if self.editing is not None:
            outcome = services.update_invoice(self.editing, fields, self.proof.upload, self.proof.remove_requested())
        else:
            outcome = services.add_invoice(fields, self.proof.upload)
        if outcome.ok:
            self.reset_form()
        self.window.after_action(outcome)

    def delete(self, row_id: int) -> None:
        note = services.delete_warning(row_id)          # issued in Invoicing, and its credit notes (if any)
        if self.window.dialogs.confirm("Delete invoice", "Delete this invoice (and its proof)?" + (" " + note if note else "")):
            outcome = services.delete("invoices", row_id)
            if outcome.ok and row_id == self.editing:
                self.reset_form()                  # the invoice being edited is gone
            self.window.after_action(outcome)
