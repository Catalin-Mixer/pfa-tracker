"""Invoicing tab: issue invoices that go straight to Income, and keep the lists they are made from
(clients, products and services, series) and the VAT settings."""
import tempfile
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import services
from .common import Card, fill_row, hbox, label, qdate, row_buttons, small_button, table, text_of

NOTES_MAX = 1000
DELETED = "(deleted from the list — choose another)"
SECTIONS = ("New invoice", "Issued invoices", "Clients", "Products & services", "Series & VAT")


def scrolled(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    return area


def choose_data(combo: QComboBox, value) -> bool:
    """Selects the item holding this value; False if there is none."""
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
    return index >= 0


def compact_combo(chars: int) -> QComboBox:
    """A drop-down as wide as about this many characters, however long its items are (they show in full when open)."""
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    return combo


# ------------------------------------------------------------------ the three lists and their forms

class ListEditor(QWidget):
    """A list with Edit and Delete on each row, and under it the form that adds an entry or changes one.

    fields: (key, title, widget) — QLineEdit, QPlainTextEdit, QComboBox (the value is the item's data) or QCheckBox."""

    def __init__(self, tab, *, noun: str, add_title: str, headers: list[str], stretch: int, numeric: set[int],
                 empty: str, fields: list, save, load, delete, delete_text, row_values, note: str = ""):
        super().__init__()
        self.tab, self.window = tab, tab.window
        self.noun, self.add_title, self.empty_text = noun, add_title, empty
        self.save_fn, self.load_fn, self.delete_fn, self.delete_text, self.row_values = save, load, delete, delete_text, row_values
        self.fields = {key: widget for key, _title, widget in fields}
        self.numeric = numeric
        self.editing: int | None = None
        box = QVBoxLayout(self)
        self.table = table(headers + [""], numeric, stretch=stretch)
        self.table.setMinimumHeight(160)
        box.addWidget(self.table, 1)
        self.empty = label("", "muted")
        box.addWidget(self.empty)

        self.form = QGroupBox(add_title)
        grid = QGridLayout(self.form)
        for n, (key, title, widget) in enumerate(fields):
            row, col = divmod(n, 2)
            if isinstance(widget, QCheckBox):
                grid.addWidget(widget, row, col * 2 + 1)
            else:
                grid.addWidget(label(title, wrap=False), row, col * 2)
                grid.addWidget(widget, row, col * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        last = (len(fields) + 1) // 2
        if note:
            grid.addWidget(label(note, "muted"), last, 0, 1, 4)
        self.save_button = QPushButton(f"Add {noun}")
        self.save_button.clicked.connect(self.save)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip(f"Leaves the {noun} as it is stored.")
        self.cancel_button.clicked.connect(self.cancel_edit)
        grid.addLayout(hbox(self.save_button, self.cancel_button), last + 1, 0, 1, 4)
        box.addWidget(self.form)
        self.reset_form()

    # the form's values
    def values(self) -> dict:
        out = {}
        for key, w in self.fields.items():
            if isinstance(w, QLineEdit):
                out[key] = w.text()
            elif isinstance(w, QPlainTextEdit):
                out[key] = w.toPlainText()
            elif isinstance(w, QComboBox):
                out[key] = w.currentData() if w.currentData() is not None else ""
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
        return out

    def set_values(self, values: dict) -> None:
        for key, w in self.fields.items():
            value = values.get(key, "")
            if isinstance(w, QLineEdit):
                w.setText("" if value is None else str(value))
                w.setCursorPosition(0)
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText("" if value is None else str(value))
            elif isinstance(w, QComboBox):
                if not choose_data(w, value) and w.count():
                    w.setCurrentIndex(0)
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))

    def defaults(self) -> dict:
        return {}

    def reset_form(self) -> None:
        self.set_values(self.defaults())
        self.show_mode(None)

    def show_mode(self, row_id: int | None) -> None:
        self.editing = row_id
        editing = row_id is not None
        self.form.setTitle(f"Edit {self.noun}" if editing else self.add_title)
        self.save_button.setText("Save changes" if editing else f"Add {self.noun}")
        self.cancel_button.setVisible(editing)

    # actions
    def save(self) -> None:
        outcome = self.save_fn(self.values(), self.editing)
        if outcome.ok:
            self.reset_form()
        self.window.after_action(outcome)

    def edit(self, row_id: int) -> None:
        found = self.load_fn(row_id)
        if found is None:
            self.window.after_action(services.Outcome(False, error=f"Not changed: that {self.noun} no longer exists."))
            return
        self.set_values(found)
        self.show_mode(row_id)
        self.window.clear_message()
        next(iter(self.fields.values())).setFocus()

    def cancel_edit(self) -> None:
        self.reset_form()
        self.window.clear_message()

    def delete(self, row: dict) -> None:
        if self.window.dialogs.confirm(f"Delete {self.noun}", self.delete_text(row)):
            outcome = self.delete_fn(row["id"])
            if outcome.ok and row["id"] == self.editing:
                self.reset_form()                  # what was being edited is gone
            self.window.after_action(outcome)

    def show_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            change = small_button("Edit")
            change.setToolTip(f"Change this {self.noun} in the form below.")
            change.clicked.connect(lambda _=False, rid=r["id"]: self.edit(rid))
            remove = small_button("Delete", "link")
            remove.clicked.connect(lambda _=False, row=r: self.delete(row))
            fill_row(self.table, i, self.row_values(r) + [row_buttons(change, remove)], self.numeric)
        self.table.resizeRowsToContents()
        self.table.setVisible(bool(rows))
        self.empty.setText("" if rows else self.empty_text)
        self.empty.setVisible(not rows)


class ClientsEditor(ListEditor):
    def __init__(self, tab, view: dict):
        country = QComboBox()
        for code, name in view["countries"].items():
            country.addItem(f"{name} ({code})", code)
        inputs = [("name", "Name", QLineEdit(placeholderText="e.g. Nordwind Software SRL")),
                  ("tax_id", "CUI / tax ID", QLineEdit(placeholderText="RO14399840, or the VAT number abroad")),
                  ("reg_number", "Trade register no.", QLineEdit(placeholderText="J40/1234/2020 (optional)")),
                  ("address", "Address", QLineEdit(placeholderText="Str. Exemplu nr. 1")),
                  ("city", "City", QLineEdit(placeholderText="București")),
                  ("country", "Country", country),
                  ("email", "Email", QLineEdit(placeholderText="billing@example.ro")),
                  ("phone", "Phone", QLineEdit(placeholderText="0721 234 567 (optional)"))]
        super().__init__(
            tab, noun="client", add_title="Add a client", stretch=0, numeric=set(),
            headers=["Name", "CUI / tax ID", "Trade register no.", "Address", "Country", "Email", "Phone"],
            empty="No clients yet — add one below.", fields=inputs,
            save=services.save_client, load=services.client_for_edit, delete=services.delete_client,
            delete_text=lambda r: f"Delete the client {r['name']}? Invoices already issued to it keep its details.",
            row_values=lambda r: [r["name"], r["tax_id"], r["reg_number"], f"{r['address']}, {r['city']}",
                                  r["country_name"], r["email"], r["phone"]],
            note="Name, CUI / tax ID, address, city, country and email are needed on an invoice. For a person, "
                 "the CUI / tax ID is the CNP.")

    def defaults(self) -> dict:
        return {"country": "RO"}


class ProductsEditor(ListEditor):
    def __init__(self, tab, view: dict):
        unit, currency = QComboBox(), QComboBox()
        for key, name in view["units"].items():
            unit.addItem(name, key)
        description = QPlainTextEdit(placeholderText="Printed under the name on the invoice (optional)")
        description.setFixedHeight(60)
        inputs = [("name", "Name", QLineEdit(placeholderText="e.g. Software development")),
                  ("unit", "Unit", unit),
                  ("description", "Description", description),
                  ("unit_price", "Price per unit", QLineEdit(placeholderText="150,00")),
                  ("currency", "Currency", currency)]
        super().__init__(
            tab, noun="product or service", add_title="Add a product or service", stretch=1, numeric={3},
            headers=["Name", "Description", "Unit", "Price per unit", "Currency"],
            empty="No products or services yet — add one below.", fields=inputs,
            save=services.save_product, load=services.product_for_edit, delete=services.delete_product,
            delete_text=lambda r: f"Delete {r['name']}? Invoices already issued keep it.",
            row_values=lambda r: [r["name"], r["description"], r["unit_label"], r["price_display"], r["currency"]],
            note="Prices are typed as 1.234,56 or 1234.56. The price can be changed on each invoice line.")
        self.currency = currency
        self.fill_currencies(view["currencies"])
        self.reset_form()

    def fill_currencies(self, currencies: list[str]) -> None:
        current = self.currency.currentData()
        self.currency.blockSignals(True)
        self.currency.clear()
        for c in currencies:
            self.currency.addItem(c, c)
        if not choose_data(self.currency, current) and current:
            self.currency.addItem(current, current)           # e.g. no longer in the rates file: kept as it was
            choose_data(self.currency, current)
        self.currency.blockSignals(False)

    def defaults(self) -> dict:
        return {"unit": "hour", "currency": "RON"}


class SeriesEditor(ListEditor):
    def __init__(self, tab, view: dict):
        inputs = [("name", "Name", QLineEdit(placeholderText="e.g. PFA")),
                  ("next_number", "Next number", QLineEdit(placeholderText="1")),
                  ("is_default", "", QCheckBox("Default series (chosen first on a new invoice)"))]
        super().__init__(
            tab, noun="series", add_title="Add a series", stretch=0, numeric={1, 2},
            headers=["Series", "Next invoice", "Issued", "Default"],
            empty="No series yet — add one below.", fields=inputs,
            save=services.save_series, load=services.series_for_edit, delete=services.delete_series,
            delete_text=lambda r: (f"Delete the series {r['name']}? Invoices already issued in it stay; a new series "
                                   "with the same name continues after them."),
            row_values=lambda r: [r["name"], r["next"], str(r["issued"]), "yes" if r["is_default"] else ""],
            note="Invoices are numbered SERIES-0001, SERIES-0002, … The number goes up by itself with each invoice.")

    def defaults(self) -> dict:
        return {"next_number": "1"}


# ------------------------------------------------------------------ the tab

# ------------------------------------------------------------------ credit notes

REASON_MAX = 500
CREDIT_HELP = ("Quantity to credit: at most what is left of the line; empty means none of it. A comma is the decimal "
               "mark (1,5 = one and a half). Each line is credited at its price on the invoice, with the invoice's "
               "VAT. Credit all fills in everything that is left.")
CREDIT_ISSUE_HELP = ("Issuing gives the credit note the next number of its series, saves its PDF and adds it to Income "
                     "as a negative amount without VAT, with the PDF as proof. It counts in the year of the date of the "
                     "refund, or of its own date.")


class CreditNoteForm(QGroupBox):
    """The credit note form of one issued invoice, shown above the Issued invoices list: each line with what is
    left to credit and the quantity to credit, Credit all, the series, the date, an optional refund date and an
    optional reason, with totals that follow what is typed (worked out by services, like the browser's)."""

    def __init__(self, tab):
        super().__init__("Credit note")
        self.tab, self.window = tab, tab.window
        self.view: dict | None = None
        self.token = ""
        self.quantities: dict[int, QLineEdit] = {}
        self.series_by_hand = False
        box = QVBoxLayout(self)
        self.info = label("", "muted")
        box.addWidget(self.info)
        grid = QGridLayout()
        self.series_box = compact_combo(30)
        self.date = QDateEdit(calendarPopup=True)
        self.refund_check = QCheckBox("Date of the refund")
        self.refund = QDateEdit(calendarPopup=True)
        for d in (self.date, self.refund):
            d.setDisplayFormat("yyyy-MM-dd")
        self.refund.setEnabled(False)
        self.refund_check.toggled.connect(self.refund.setEnabled)
        self.date.dateChanged.connect(self.refund.setMinimumDate)       # no refund before the credit note
        grid.addWidget(label("Series", wrap=False), 0, 0)
        grid.addWidget(self.series_box, 1, 0)
        grid.addWidget(label("Credit note date", wrap=False), 0, 1)
        grid.addWidget(self.date, 1, 1)
        grid.addWidget(self.refund_check, 0, 2)
        grid.addWidget(self.refund, 1, 2)
        grid.setColumnStretch(3, 1)
        box.addLayout(grid)
        self.lines = table(["Product or service", "Unit", "Price per unit", "Invoiced", "Credited", "Left",
                            "Quantity to credit", "Value"], {2, 3, 4, 5, 7}, stretch=0)
        self.lines.setSelectionMode(self.lines.SelectionMode.NoSelection)
        self.lines.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.lines.setColumnWidth(6, 165)
        self.lines.verticalHeader().setDefaultSectionSize(36)
        box.addWidget(self.lines)
        self.all_button = small_button("Credit all")
        self.all_button.clicked.connect(self.credit_all)
        box.addLayout(hbox(self.all_button))
        box.addWidget(label(CREDIT_HELP, "muted"))
        box.addWidget(label("Reason", wrap=False))
        self.reason = QPlainTextEdit(placeholderText=f"Printed on the credit note (optional, at most {REASON_MAX} "
                                                     "characters)")
        self.reason.setFixedHeight(56)
        self.reason.textChanged.connect(self.reason_changed)
        box.addWidget(self.reason)
        cards = QHBoxLayout()
        self.number_card = Card("Credit note number")
        self.total_card = Card("Total", accent=True)
        cards.addWidget(self.number_card, 1)
        cards.addWidget(self.total_card, 2)
        box.addLayout(cards)
        self.problems = label("", "muted")
        box.addWidget(self.problems)
        self.issue_button = QPushButton("Issue credit note and add to Income")
        self.issue_button.clicked.connect(self.issue)
        self.cancel_button = small_button("Cancel")
        self.cancel_button.clicked.connect(self.close_form)
        box.addLayout(hbox(self.issue_button, self.cancel_button))
        box.addWidget(label(CREDIT_ISSUE_HELP, "muted"))
        self.series_box.currentIndexChanged.connect(lambda _: self.update_totals())
        self.series_box.activated.connect(self.series_chosen)
        for d in (self.date, self.refund):
            d.dateChanged.connect(lambda _: self.update_totals())
        self.refund_check.toggled.connect(lambda _: self.update_totals())
        self.setVisible(False)

    def series_chosen(self, _index: int) -> None:
        self.series_by_hand = True

    def open(self, issued_id) -> bool:
        """Opens the form for an issued invoice; if it cannot be credited, says why and stays closed."""
        outcome = services.start_credit_note(issued_id)
        if not outcome.ok:
            self.close_form()
            self.window.after_action(outcome)
            return False
        view, form = outcome.extra["view"], outcome.extra["form"]
        self.view, self.token, self.series_by_hand = view, form["token"], False
        self.setTitle(f"Credit note for invoice {view['full_number']}")
        invoice_day = qdate(date.fromisoformat(view["date"]))
        today = qdate(date.fromisoformat(view["today"]))
        self.date.blockSignals(True)
        self.date.setMinimumDate(invoice_day)
        self.date.setMaximumDate(today)
        self.date.setDate(today)
        self.date.blockSignals(False)
        self.refund.setMinimumDate(today)
        self.refund.setMaximumDate(today)
        self.refund.setDate(today)
        self.refund_check.setChecked(False)
        self.reason.blockSignals(True)
        self.reason.clear()
        self.reason.blockSignals(False)
        self.fill_series(view["own_series"])
        self.fill_lines({})
        self.tab.credit_result.setVisible(False)
        self.setVisible(True)
        self.window.clear_message()
        self.update_totals()
        self.tab.sections.widget(SECTIONS.index("Issued invoices")).verticalScrollBar().setValue(0)
        return True

    def show_info(self) -> None:
        v = self.view
        so_far = f" · credited so far by {', '.join(v['credited_by'])}" if v["credited_by"] else ""
        self.info.setText(f"Invoice {v['full_number']} of {v['date']} · {v['client']} · {v['total_display']} "
                          f"{v['currency']}{so_far}")

    def fill_series(self, chosen) -> None:
        self.series_box.blockSignals(True)
        self.series_box.clear()
        self.series_box.addItem("— choose a series —", None)
        for s in self.view["series"]:
            self.series_box.addItem(f"{s['name']} (next: {s['next']})", s["id"])
        if chosen is not None and not choose_data(self.series_box, chosen):
            self.series_box.addItem(DELETED, chosen)
            choose_data(self.series_box, chosen)
        self.series_box.blockSignals(False)

    def fill_lines(self, typed: dict[int, str]) -> None:
        """The invoice's lines with what is left to credit; typed: quantities already typed, kept."""
        self.lines.setRowCount(0)
        self.quantities = {}
        for row, line in enumerate(self.view["lines"]):
            self.lines.insertRow(row)
            if line["left_quantity"] != "0":
                qty = QLineEdit(typed.get(line["line"], ""), placeholderText="0")
                qty.setAlignment(Qt.AlignmentFlag.AlignRight)
                qty.textChanged.connect(lambda _: self.update_totals())
                self.quantities[line["line"]] = qty
                cell = qty
            else:
                cell = "fully credited"
            fill_row(self.lines, row, [line["name"], line["unit_label"], line["price_display"], line["quantity_display"],
                                       line["credited_display"], line["left_display"], cell, ""], {2, 3, 4, 5, 7})
        self.lines.setFixedHeight(min(self.lines.horizontalHeader().sizeHint().height()
                                      + 36 * len(self.view["lines"]) + 4, 420))
        self.show_info()

    def reason_changed(self) -> None:
        text = self.reason.toPlainText()
        if len(text) > REASON_MAX:                       # like the browser's maxlength: what is over the limit is cut
            cursor = self.reason.textCursor()
            position = min(cursor.position(), REASON_MAX)
            self.reason.blockSignals(True)
            self.reason.setPlainText(text[:REASON_MAX])
            self.reason.blockSignals(False)
            cursor = self.reason.textCursor()
            cursor.setPosition(position)
            self.reason.setTextCursor(cursor)
        self.update_totals()

    def credit_all(self) -> None:
        for line in services.credit_all(self.view):
            w = self.quantities.get(int(line["line"]))
            if w is not None:
                w.setText(line["quantity"])
        self.update_totals()

    def fields(self) -> dict:
        return {"issued_id": str(self.view["id"]) if self.view else "",
                "series_id": str(self.series_box.currentData() or ""), "date": text_of(self.date.date()),
                "payment_date": text_of(self.refund.date()) if self.refund_check.isChecked() else "",
                "reason": self.reason.toPlainText(), "token": self.token,
                "lines": [{"line": str(n), "quantity": w.text()} for n, w in self.quantities.items()]}

    def update_totals(self) -> None:
        if self.view is None:
            return
        t = services.credit_note_totals(self.fields())
        values = {line["line"]: line["value_display"] for line in t["lines"]}
        for row, line in enumerate(self.view["lines"]):
            fill_row_cell(self.lines, row, 7, values.get(line["line"], ""))
        cur = t["currency"]
        self.number_card.set(t["next_number"] or "—", "the next number of the series" if t["next_number"] else
                             "choose a series")
        sub = (f"{t['subtotal']} {cur} + VAT {t['vat_rate']}% {t['vat']} {cur}" if t["vat"]
               else "The invoice has no VAT")
        self.total_card.set(f"{t['total']} {cur}", sub)
        self.problems.setText(f"Still needed before issuing: {t['error']}." if t["error"] else "Ready to be issued.")

    def issue(self) -> None:
        if self.view is None or not self.isVisible():
            return            # e.g. the second click of a double click, once the form has closed
        self.issue_button.setEnabled(False)
        try:
            outcome = services.generate_credit_note(self.fields())
        finally:
            self.issue_button.setEnabled(True)
        if outcome.ok or outcome.extra.get("already_issued"):
            self.close_form()
            self.tab.show_credit_result(outcome)
        self.window.after_action(outcome)

    def close_form(self) -> None:
        self.setVisible(False)
        self.view = None
        self.quantities = {}
        self.lines.setRowCount(0)

    def refresh(self) -> None:
        """Shows what is left to credit as stored now (the other app may have credited meanwhile); what is typed
        stays."""
        if self.view is None:
            return
        view = services.credit_note_view(self.view["id"])
        if view is None:
            self.close_form()
            return
        typed = {n: w.text() for n, w in self.quantities.items()}
        chosen = self.series_box.currentData()
        self.view = view
        self.fill_series(chosen if (self.series_by_hand or chosen is not None) else view["own_series"])
        self.fill_lines(typed)
        self.update_totals()


class InvoicingTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        view = services.invoicing_view()
        self.view = view
        self.products: dict[int, dict] = {}
        self.last_issued: dict | None = None
        self.last_credit: dict | None = None
        self.vat_changed = False
        self.due_touched = False
        self.series_by_hand = False
        self.token = ""                      # one per New invoice form (see reset_new_invoice)
        self.fresh_fields: dict | None = None

        box = QVBoxLayout(self)
        box.addWidget(label("Invoicing", "pageTitle"))
        warn_row = QHBoxLayout()
        self.firm_warning = label("", "error")
        self.to_settings = small_button("Open Settings")
        self.to_settings.clicked.connect(lambda: self.window.tabs.setCurrentWidget(self.window.settings))
        warn_row.addWidget(self.firm_warning, 1)
        warn_row.addWidget(self.to_settings, 0, Qt.AlignmentFlag.AlignVCenter)
        self.firm_warning_line = QWidget()
        self.firm_warning_line.setLayout(warn_row)
        warn_row.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.firm_warning_line)

        self.sections = QTabWidget()
        self.clients = ClientsEditor(self, view)
        self.products_editor = ProductsEditor(self, view)
        self.series = SeriesEditor(self, view)
        pages = (self.build_new_invoice(), self.build_issued(), self.clients, self.products_editor,
                 self.build_series_and_vat())
        for title, page in zip(SECTIONS, pages):
            self.sections.addTab(scrolled(page), title.replace("&", "&&"))     # a lone & would underline a letter
        self.sections.currentChanged.connect(lambda _: self.window.clear_message())
        box.addWidget(self.sections, 1)
        self.reset_forms()

    # ---------------------------------------------------------- New invoice
    def build_new_invoice(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        # what was issued last: on top, where it is seen after issuing
        self.result = QGroupBox("")
        rbox = QVBoxLayout(self.result)
        self.open_pdf_button = small_button("Open PDF")
        self.save_pdf_button = small_button("Save PDF as…")
        self.to_income_button = small_button("Show in Income")
        self.all_issued_button = small_button("All issued invoices")
        self.all_issued_button.clicked.connect(lambda: self.sections.setCurrentIndex(SECTIONS.index("Issued invoices")))
        self.open_pdf_button.clicked.connect(lambda: self.open_pdf(self.last_issued["issued_id"]))
        self.save_pdf_button.clicked.connect(lambda: self.save_pdf(self.last_issued["issued_id"]))
        self.to_income_button.clicked.connect(lambda: self.show_in_income(self.last_issued["year"]))
        rbox.addLayout(hbox(self.open_pdf_button, self.save_pdf_button, self.to_income_button, self.all_issued_button))
        self.result.setVisible(False)
        box.addWidget(self.result)
        head = QGroupBox("Invoice")
        grid = QGridLayout(head)
        self.series_box = compact_combo(36)
        self.client_box = compact_combo(30)
        self.date = QDateEdit(calendarPopup=True)
        self.due_check = QCheckBox("Due date")
        self.due = QDateEdit(calendarPopup=True)
        self.paid_check = QCheckBox("Date of payment")
        self.paid = QDateEdit(calendarPopup=True)
        for d in (self.date, self.due, self.paid):
            d.setDisplayFormat("yyyy-MM-dd")
        self.due_check.toggled.connect(self.due.setEnabled)
        self.paid_check.toggled.connect(self.paid.setEnabled)
        self.date.dateChanged.connect(self.invoice_date_changed)
        self.due.dateChanged.connect(self.due_date_changed)
        self.notes = QPlainTextEdit(placeholderText="Printed at the bottom of the invoice (optional, at most "
                                                    f"{NOTES_MAX} characters)")
        self.notes.setFixedHeight(56)
        self.notes.textChanged.connect(self.notes_changed)
        grid.addWidget(label("Series", wrap=False), 0, 0)
        grid.addWidget(self.series_box, 1, 0)
        grid.addWidget(label("Client", wrap=False), 0, 1, 1, 2)
        grid.addWidget(self.client_box, 1, 1, 1, 2)
        grid.addWidget(label("Invoice date", wrap=False), 2, 0)
        grid.addWidget(self.date, 3, 0)
        grid.addWidget(self.due_check, 2, 1)
        grid.addWidget(self.due, 3, 1)
        grid.addWidget(self.paid_check, 2, 2)
        grid.addWidget(self.paid, 3, 2)
        grid.setColumnStretch(3, 1)
        grid.addWidget(label("Notes", wrap=False), 5, 0)
        grid.addWidget(self.notes, 6, 0, 1, 4)
        grid.addWidget(label("The income counts in the year the money was received: with a date of payment it goes "
                             "to Income in that year (at that day's exchange rate), otherwise in the invoice date's "
                             "year. Fill in the date of payment later in Income if it is not paid yet.", "muted"),
                       4, 0, 1, 4)
        box.addWidget(head)

        lines = QGroupBox("Products and services on the invoice")
        lbox = QVBoxLayout(lines)
        self.lines = table(["Product or service", "Quantity", "Unit", "Price per unit", "Cur.", "Value", ""],
                           {1, 3, 5}, stretch=0)
        self.lines.setSelectionMode(self.lines.SelectionMode.NoSelection)
        self.lines.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.lines.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.lines.setColumnWidth(1, 110)
        self.lines.setColumnWidth(3, 140)
        self.lines.verticalHeader().setDefaultSectionSize(36)
        self.lines.setMinimumHeight(150)
        self.line_widgets: list[dict] = []
        lbox.addWidget(self.lines)
        self.add_line_button = small_button("Add line")
        self.add_line_button.clicked.connect(lambda: self.add_line(focus=True))
        self.lines_note = label("", "muted")
        lbox.addLayout(hbox(self.add_line_button))
        lbox.addWidget(self.lines_note)
        box.addWidget(lines)

        bottom = QHBoxLayout()
        self.number_card = Card("Invoice number")
        self.total_card = Card("Total", accent=True)
        bottom.addWidget(self.number_card, 1)
        bottom.addWidget(self.total_card, 2)
        box.addLayout(bottom)
        self.problems = label("", "muted")
        box.addWidget(self.problems)
        self.issue_button = QPushButton("Issue invoice and add to Income")
        self.issue_button.clicked.connect(self.issue)
        box.addLayout(hbox(self.issue_button))

        box.addStretch(1)
        for w in (self.series_box, self.client_box):
            w.currentIndexChanged.connect(lambda _: self.update_totals())
        self.series_box.activated.connect(self.series_chosen)
        for d in (self.date, self.due, self.paid):
            d.dateChanged.connect(lambda _: self.update_totals())
        for c in (self.due_check, self.paid_check):
            c.toggled.connect(lambda _: self.update_totals())
        return page

    def notes_changed(self) -> None:
        text = self.notes.toPlainText()
        if len(text) > NOTES_MAX:                        # like the browser's maxlength: what is over the limit is cut
            cursor = self.notes.textCursor()
            position = min(cursor.position(), NOTES_MAX)
            self.notes.blockSignals(True)
            self.notes.setPlainText(text[:NOTES_MAX])
            self.notes.blockSignals(False)
            cursor = self.notes.textCursor()
            cursor.setPosition(position)
            self.notes.setTextCursor(cursor)
        self.update_totals()

    def series_chosen(self, _index: int) -> None:
        self.series_by_hand = True

    def invoice_date_changed(self, d: QDate) -> None:
        self.paid.setMinimumDate(d)                      # neither payment nor due date before the invoice
        self.due.setMinimumDate(d)
        if not self.due_touched and self.due_check.isChecked():
            # the due date follows the invoice date (by the usual payment term) until it is set by hand
            self.due.blockSignals(True)
            self.due.setDate(d.addDays(self.view["settings"]["due_days"]))
            self.due.blockSignals(False)

    def due_date_changed(self, _d: QDate) -> None:
        self.due_touched = True

    def add_line(self, focus: bool = False, product_id=None, quantity: str = "", price: str = "") -> None:
        if len(self.line_widgets) >= self.view["max_lines"]:
            return
        product = compact_combo(26)
        self.fill_products(product, product_id)
        qty = QLineEdit(quantity, placeholderText="1")
        qty.setAlignment(Qt.AlignmentFlag.AlignRight)
        cost = QLineEdit(price)
        cost.setAlignment(Qt.AlignmentFlag.AlignRight)
        remove = small_button("Remove", "link")
        remove.setToolTip("Takes this line off the invoice (nothing stored is deleted).")
        widgets = {"product": product, "quantity": qty, "price": cost, "remove": remove}
        row = len(self.line_widgets)
        self.line_widgets.append(widgets)
        self.lines.insertRow(row)
        fill_row(self.lines, row, [product, qty, "", cost, "", "", row_buttons(remove)], {1, 3, 5})
        product.currentIndexChanged.connect(lambda _: self.line_changed())
        qty.textChanged.connect(lambda _: self.update_totals())
        cost.textChanged.connect(lambda _: self.update_totals())
        remove.clicked.connect(lambda _=False, w=widgets: self.remove_line(w))
        self.show_line_count()
        self.line_changed()
        if focus:
            product.setFocus()

    def remove_line(self, widgets: dict) -> None:
        if widgets in self.line_widgets:
            row = self.line_widgets.index(widgets)
            self.line_widgets.pop(row)
            self.lines.removeRow(row)
        if not self.line_widgets:
            self.add_line()                              # always one line to fill in
        self.show_line_count()
        self.update_totals()

    def show_line_count(self) -> None:
        n, top = len(self.line_widgets), self.view["max_lines"]
        self.add_line_button.setEnabled(n < top)
        self.lines_note.setText(f"{n} of at most {top} lines." if n >= top else
                                "Choose a product or service on each line; leave the price empty to use its price "
                                "from the list. Empty lines are left out.")
        self.lines.setFixedHeight(min(self.lines.horizontalHeader().sizeHint().height() + 36 * n + 4, 420))

    def fill_products(self, combo: QComboBox, keep=None) -> None:
        """The products and services to choose from; one chosen but deleted meanwhile stays shown as deleted,
        so that the invoice is not changed without a word."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— choose —" if self.products else "None yet — add them under Products & services", None)
        for pid, p in self.products.items():
            combo.addItem(f"{p['name']} — {p['price_display']} {p['currency']} / {p['unit_label']}", pid)
        if keep is not None and not choose_data(combo, keep):
            combo.addItem(DELETED, keep)
            choose_data(combo, keep)
        combo.blockSignals(False)

    def line_changed(self) -> None:
        for row, w in enumerate(self.line_widgets):
            p = self.products.get(w["product"].currentData())
            w["price"].setPlaceholderText(p["price_display"] if p else "list price")
            fill_row(self.lines, row, [w["product"], w["quantity"], p["unit_label"] if p else "", w["price"],
                                       p["currency"] if p else ""], {1, 3})
        self.update_totals()

    def invoice_fields(self) -> dict:
        return {"series_id": str(self.series_box.currentData() or ""),
                "client_id": str(self.client_box.currentData() or ""),
                "date": text_of(self.date.date()),
                "due_date": text_of(self.due.date()) if self.due_check.isChecked() else "",
                "payment_date": text_of(self.paid.date()) if self.paid_check.isChecked() else "",
                "notes": self.notes.toPlainText(), "token": self.token,
                "lines": [{"product_id": str(w["product"].currentData() or ""), "quantity": w["quantity"].text(),
                           "price": w["price"].text()} for w in self.line_widgets]}

    def update_totals(self) -> None:
        if not hasattr(self, "total_card"):
            return
        t = services.invoice_totals(self.invoice_fields())
        values = {line["line"]: line["value_display"] for line in t["lines"]}
        for row in range(len(self.line_widgets)):
            fill_row_cell(self.lines, row, 5, values.get(row + 1, ""))
        cur = t["currency"]
        self.number_card.set(t["next_number"] or "—", "the next number of the series" if t["next_number"] else
                             "choose a series")
        if not t["total"]:                               # e.g. lines in different currencies: nothing to add up
            self.total_card.set("—", "the lines must all be in one currency")
        else:
            if t["vat"]:
                sub = f"{t['subtotal']} {cur} + VAT {t['vat_rate']}% {t['vat']} {cur}"
            else:
                sub = "Not a VAT payer: no VAT is added"
            self.total_card.set(f"{t['total']} {cur}", sub)
        self.problems.setText(f"Still needed before issuing: {t['error']}." if t["error"] else "Ready to be issued.")

    def issue(self) -> None:
        fields = self.invoice_fields()
        if self.last_issued and fields == self.fresh_fields:
            return          # a second click of a double click, on the empty form just made for the next invoice
        self.issue_button.setEnabled(False)
        try:
            outcome = services.generate_invoice(fields)
        finally:
            self.issue_button.setEnabled(True)
        if outcome.ok:
            self.last_issued = {"issued_id": outcome.extra["issued_id"], "number": outcome.extra["number"],
                                "year": outcome.year}
            self.reset_new_invoice()
            self.result.setTitle(f"Invoice {outcome.extra['number']} issued")
            self.to_income_button.setText(f"Show in Income {outcome.year}")
            self.result.setVisible(True)
            self.sections.widget(0).verticalScrollBar().setValue(0)     # the result is shown on top
        self.window.after_action(outcome)

    def reset_new_invoice(self) -> None:
        """An empty invoice: the default series, no client, today, the usual due date, one empty line."""
        view = self.view
        today = date.fromisoformat(view["today"])
        self.date.blockSignals(True)
        self.date.setMinimumDate(QDate(2000, 1, 1))
        self.date.setMaximumDate(qdate(today))
        self.date.setDate(qdate(today))
        self.date.blockSignals(False)
        self.paid.setMaximumDate(qdate(today))
        self.paid.setMinimumDate(qdate(today))
        self.paid.setDate(qdate(today))
        self.paid_check.setChecked(False)
        self.paid.setEnabled(False)
        self.due.setMinimumDate(qdate(today))
        self.due.setMaximumDate(qdate(today + timedelta(days=3660)))
        self.due.blockSignals(True)
        self.due.setDate(qdate(date.fromisoformat(view["default_due"])) if view["default_due"] else qdate(today))
        self.due.blockSignals(False)
        self.due_check.setChecked(bool(view["default_due"]))
        self.due.setEnabled(bool(view["default_due"]))
        self.due_touched = False
        self.series_by_hand = False
        self.notes.clear()
        if view["default_series"] is not None:
            choose_data(self.series_box, view["default_series"])
        self.client_box.setCurrentIndex(0)
        self.lines.setRowCount(0)
        self.line_widgets = []
        self.add_line()
        # a fresh form: a new token, so that sending the same form twice issues it only once
        self.token = services.new_invoice_token()
        self.fresh_fields = self.invoice_fields()

    # ---------------------------------------------------------- Issued invoices
    def build_issued(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        # the credit note just issued: on top, where it is seen after issuing
        self.credit_result = QGroupBox("")
        cbox = QVBoxLayout(self.credit_result)
        self.credit_summary = label("", "muted")
        cbox.addWidget(self.credit_summary)
        self.credit_open_button = small_button("Open PDF")
        self.credit_save_button = small_button("Save PDF as…")
        self.credit_income_button = small_button("Show in Income")
        self.credit_open_button.clicked.connect(lambda: self.open_pdf(self.last_credit["issued_id"]))
        self.credit_save_button.clicked.connect(lambda: self.save_pdf(self.last_credit["issued_id"]))
        self.credit_income_button.clicked.connect(lambda: self.show_in_income(self.last_credit["year"]))
        cbox.addLayout(hbox(self.credit_open_button, self.credit_save_button, self.credit_income_button))
        self.credit_result.setVisible(False)
        box.addWidget(self.credit_result)
        self.credit_form = CreditNoteForm(self)
        box.addWidget(self.credit_form)
        self.issued_table = table(["Number", "Invoice date", "Due date", "Client", "Total", "Currency", "In Income",
                                   "Credit", "PDF"], {4}, stretch=3)
        box.addWidget(self.issued_table, 1)
        self.issued_empty = label("", "muted")
        box.addWidget(self.issued_empty)
        box.addWidget(label("The PDF is printed again from what was recorded when the invoice was issued; changes made "
                            "in Income do not change it. An invoice deleted from Income stays here, marked as deleted "
                            "from Income, and its number is not given out again. Credit note reverses all or part of "
                            "an invoice; an invoice deleted from Income or fully credited has no such button.", "muted"))
        return page

    def show_credit_result(self, outcome) -> None:
        """After issuing a credit note (or finding that the same form already issued one): its PDF and Income."""
        mine = next((i for i in services.invoicing_view()["issued"] if i["id"] == outcome.extra["issued_id"]), None)
        if mine is None:
            return
        self.last_credit = {"issued_id": mine["id"], "number": mine["full_number"], "year": mine["income_year"]}
        self.credit_result.setTitle(f"Credit note {mine['full_number']} issued")
        self.credit_summary.setText(f"Credit note {mine['full_number']} · reverses {mine['credits_number']} · "
                                    f"{mine['client']} · {mine['total_display']} {mine['currency']}")
        self.credit_income_button.setText(f"Show in Income {mine['income_year']}")
        self.credit_income_button.setVisible(mine["in_income"])
        self.credit_result.setVisible(True)
        self.sections.widget(SECTIONS.index("Issued invoices")).verticalScrollBar().setValue(0)

    def show_issued(self, rows: list[dict]) -> None:
        self.issued_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            open_button = small_button("Open")
            open_button.clicked.connect(lambda _=False, iid=r["id"]: self.open_pdf(iid))
            save_button = small_button("Save PDF")
            save_button.clicked.connect(lambda _=False, iid=r["id"]: self.save_pdf(iid))
            buttons = [open_button, save_button]
            if r["can_credit"]:
                credit_button = small_button("Credit note")
                credit_button.setToolTip(f"Credit all or part of {r['full_number']}.")
                credit_button.clicked.connect(lambda _=False, iid=r["id"]: self.credit_form.open(iid))
                buttons.append(credit_button)
            where = f"yes · {r['income_year']}" if r["in_income"] else "no — deleted from Income"
            fill_row(self.issued_table, i, [r["full_number"], r["date"], r["due_date"], r["client"], r["total_display"],
                                            r["currency"], where, r["credit_info"], row_buttons(*buttons)], {4},
                     warn=set() if r["in_income"] else {6})
        self.issued_table.setVisible(bool(rows))
        self.issued_empty.setText("" if rows else "No invoices issued yet — issue one under New invoice.")
        self.issued_empty.setVisible(not rows)

    def pdf_of(self, issued_id):
        found = services.issued_pdf(issued_id)
        if found is None:
            self.window.show_outcome(services.Outcome(False, error="The PDF of that invoice cannot be printed: its "
                                                                   "record is missing or damaged."))
        return found

    def open_pdf(self, issued_id) -> None:
        found = self.pdf_of(issued_id)
        if found:
            folder = Path(tempfile.gettempdir()) / "pfa-tracker-invoices"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / found[0]
            path.write_bytes(found[1])
            self.window.dialogs.open_path(path)

    def save_pdf(self, issued_id) -> None:
        found = self.pdf_of(issued_id)
        if not found:
            return
        path = self.window.dialogs.save_file("Save the invoice as PDF", found[0], "PDF (*.pdf)")
        if path:
            try:
                path.write_bytes(found[1])
            except OSError as e:
                self.window.show_outcome(services.Outcome(False, error=f"Not saved: {e.strerror or e}."))
                return
            self.window.show_outcome(services.Outcome(True, message=f"Invoice saved: {path}"))

    def show_in_income(self, year: str | None) -> None:
        if year and year in services.available_years():
            self.window.year = year
        self.window.tabs.setCurrentWidget(self.window.income)

    # ---------------------------------------------------------- Series & VAT
    def build_series_and_vat(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        self.series.table.setMaximumHeight(200)
        self.series.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        box.addWidget(self.series)
        vat = QGroupBox("VAT and payment term")
        grid = QGridLayout(vat)
        self.vat_payer = QCheckBox("VAT payer (the firm is registered for VAT)")
        self.vat_rate = QLineEdit(placeholderText="21")
        self.due_days = QLineEdit(placeholderText="15")
        for w in (self.vat_rate, self.due_days):
            w.setMaximumWidth(120)
            w.textEdited.connect(self.vat_edited)
        self.vat_payer.clicked.connect(self.vat_edited)
        grid.addWidget(self.vat_payer, 0, 0, 1, 2)
        grid.addWidget(label("VAT rate (%)", wrap=False), 1, 0)
        grid.addWidget(self.vat_rate, 1, 1)
        grid.addWidget(label("Payment term (days)", wrap=False), 2, 0)
        grid.addWidget(self.due_days, 2, 1)
        grid.setColumnStretch(2, 1)
        grid.addWidget(label("Not a VAT payer: invoices show no VAT. VAT payer: the VAT is added to the total; Income "
                             "gets the amount without VAT. The payment term sets the due date offered on a new "
                             "invoice (0 = no due date).", "muted"), 3, 0, 1, 3)
        self.vat_button = QPushButton("Save VAT settings")
        self.vat_button.clicked.connect(self.save_vat)
        grid.addLayout(hbox(self.vat_button), 4, 0, 1, 3)
        box.addWidget(vat)
        box.addStretch(1)
        return page

    def vat_edited(self, *_):
        self.vat_changed = True

    def show_vat(self, conf: dict) -> None:
        self.vat_payer.setChecked(conf["vat_payer"])
        self.vat_rate.setText(str(conf["vat_rate"]))
        self.due_days.setText(str(conf["due_days"]))
        self.vat_changed = False

    def save_vat(self) -> None:
        outcome = services.save_invoicing_settings({"vat_payer": self.vat_payer.isChecked(),
                                                    "vat_rate": self.vat_rate.text(), "due_days": self.due_days.text()})
        if outcome.ok:
            self.vat_changed = False
        self.window.after_action(outcome)

    # ---------------------------------------------------------- showing it all
    def reset_forms(self) -> None:
        """From scratch (on start and after switching profile): nothing typed for one firm goes to another."""
        self.view = services.invoicing_view()
        self.last_issued = None
        self.last_credit = None
        self.credit_form.close_form()
        self.credit_result.setVisible(False)
        # nothing chosen for one profile is kept: the same id may be another client or product in the next one
        self.series_box.clear()
        self.client_box.clear()
        self.lines.setRowCount(0)
        self.line_widgets = []
        self.result.setVisible(False)
        for editor in (self.clients, self.products_editor, self.series):
            editor.reset_form()
        self.vat_changed = False
        self.refresh(None)
        self.reset_new_invoice()
        self.update_totals()

    def refresh(self, _year_view: dict | None) -> None:
        """Shows the lists as stored now; what is being typed in a form stays as it is."""
        view = self.view = services.invoicing_view()
        self.firm_warning.setText("Invoices cannot be issued yet: " + "; ".join(view["firm_problems"]) + "."
                                  if view["firm_problems"] else "")
        self.firm_warning_line.setVisible(bool(view["firm_problems"]))
        self.products = {p["id"]: p for p in view["products"]}

        series_id = self.series_box.currentData()
        self.series_box.blockSignals(True)
        self.series_box.clear()
        self.series_box.addItem("— choose a series —" if view["series"] else "No series yet — add one under Series & VAT",
                                None)
        for s in view["series"]:
            self.series_box.addItem(f"{s['name']} (next: {s['next']})", s["id"])
        # the series chosen by hand stays (shown as deleted if it was deleted meanwhile); otherwise the default one,
        # which may have changed meanwhile
        if self.series_by_hand and series_id is not None:
            if not choose_data(self.series_box, series_id):
                self.series_box.addItem(DELETED, series_id)
                choose_data(self.series_box, series_id)
        elif view["default_series"] is not None:
            choose_data(self.series_box, view["default_series"])
        self.series_box.blockSignals(False)

        client_id = self.client_box.currentData()
        self.client_box.blockSignals(True)
        self.client_box.clear()
        self.client_box.addItem("— choose a client —" if view["clients"] else "No clients yet — add one under Clients", None)
        for c in view["clients"]:
            self.client_box.addItem(f"{c['name']} · {c['tax_id']}", c["id"])
        if client_id is not None and not choose_data(self.client_box, client_id):
            self.client_box.addItem(DELETED, client_id)
            choose_data(self.client_box, client_id)
        self.client_box.blockSignals(False)

        for w in self.line_widgets:
            self.fill_products(w["product"], w["product"].currentData())
        self.line_changed()

        self.clients.show_rows(view["clients"])
        self.products_editor.show_rows(view["products"])
        self.products_editor.fill_currencies(view["currencies"])
        self.series.show_rows(view["series"])
        self.show_issued(view["issued"])
        if self.last_issued:            # the invoice just issued may have been deleted from Income meanwhile
            mine = next((i for i in view["issued"] if i["id"] == self.last_issued["issued_id"]), None)
            in_income = bool(mine and mine["in_income"])
            self.to_income_button.setVisible(in_income)
            if in_income:
                self.last_issued["year"] = mine["income_year"]
                self.to_income_button.setText(f"Show in Income {mine['income_year']}")
        if self.last_credit:            # the credit note just issued may have been deleted from Income meanwhile
            mine = next((i for i in view["issued"] if i["id"] == self.last_credit["issued_id"]), None)
            in_income = bool(mine and mine["in_income"])
            self.credit_income_button.setVisible(in_income)
            if in_income:
                self.last_credit["year"] = mine["income_year"]
                self.credit_income_button.setText(f"Show in Income {mine['income_year']}")
        self.credit_form.refresh()
        if not self.vat_changed:
            self.show_vat(view["settings"])



def fill_row_cell(t, row: int, col: int, text: str) -> None:
    """Sets one right-aligned cell's text, leaving the widgets in the other cells of the row alone."""
    item = t.item(row, col)
    if item is None:
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t.setItem(row, col, item)
    item.setText(text)
