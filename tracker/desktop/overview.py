"""Overview tab: the year's totals, the tax breakdown and all years side by side."""
import html
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import formatting, services
from .common import PALETTE, Card, fill_row, label, money, small_button, table


class OverviewTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        box = QVBoxLayout(self)
        self.heading = label("", "pageTitle")
        box.addWidget(self.heading)
        self.warning = label("", "error")
        box.addWidget(self.warning)
        cards = QHBoxLayout()
        self.income_card, self.expense_card = Card("Gross income"), Card("Deductible expenses")
        self.net_card, self.total_card = Card("Net income"), Card("Total to pay", accent=True)
        for c in (self.income_card, self.expense_card, self.net_card, self.total_card):
            cards.addWidget(c)
        for card, tab in ((self.income_card, 1), (self.expense_card, 2)):
            details = small_button("details")
            details.clicked.connect(lambda _=False, t=tab: self.window.tabs.setCurrentIndex(t))
            card.layout().addWidget(details)
        box.addLayout(cards)

        box.addWidget(label("Taxes and contributions", "pageTitle"))
        row = QHBoxLayout()
        self.breakdown = table(["", "Amount", "To pay"], {1, 2}, stretch=0)
        self.breakdown.setMinimumHeight(230)
        row.addWidget(self.breakdown, 2)
        self.values = label("")
        self.values.setObjectName("card")
        self.values.setMinimumWidth(300)
        row.addWidget(self.values, 1)
        box.addLayout(row)
        box.addWidget(label("Estimate only — check the final figures for Declarația unică with your accountant. "
                            "Special cases (pensioners, people insured elsewhere, opting in to CAS below the threshold) "
                            "are not handled.", "muted"))
        box.addWidget(label("All years", "pageTitle"))
        self.all_years = table(["Year", "Income", "Expenses", "Net income", "CAS", "CASS", "Taxable", "Income tax",
                                "Total to pay"], set(range(1, 9)), all_equal=True)
        self.all_years.cellDoubleClicked.connect(self.open_year)
        box.addWidget(self.all_years, 1)
        self.empty = label("No data yet. Import invoices on the Income tab and add expenses on the Expenses tab.", "muted")
        box.addWidget(self.empty)

    def refresh(self, view: dict) -> None:
        year, cur = view["year"], view["current"]
        s = cur.get("settings")
        self.heading.setText(f"Overview {year}")
        warnings = []
        if cur.get("settings_error"):
            warnings.append(f"Tax settings: {cur['settings_error']}. CAS and CASS are not calculated; "
                            "income tax is shown at 10% of net income.")
        if s and cur.get("settings_year") != year:
            warnings.append(f"There are no tax values for {year} in {services.settings_view()['settings_path']}, so the {cur['settings_year']} "
                            f"values are used. Add a {year} block to the file.")
        self.warning.setText("\n".join(warnings))
        self.warning.setVisible(bool(warnings))

        self.income_card.set(money(cur["income"]), f"RON · {cur['n_invoices']} invoice(s)")
        self.expense_card.set(money(cur["expenses"]), f"RON · {cur['n_expenses']} expense(s)")
        self.net_card.set(money(cur["net"]), "RON · income − expenses")
        self.total_card.set(money(cur["total_due"]), "RON · CAS + CASS + income tax (estimate)")

        rows = [("Net income", money(cur["net"]), "")]
        if s:
            rows += [
                (f"CAS – pension, {formatting.percent(s['cas_rate'])} of the base\n{cur['cas_note']}",
                 f"base {money(cur['cas_base'])}", money(cur["cas"])),
                (f"CASS – health, {formatting.percent(s['cass_rate'])} of the base\n{cur['cass_note']}",
                 f"base {money(cur['cass_base'])}", money(cur["cass"])),
                ("Taxable income\nnet income − CAS − CASS, not below 0", money(cur["taxable"]), ""),
                (f"Income tax, {formatting.percent(s['income_tax_rate'])} of taxable income"
                 + (f"\nNot charged: {cur['tax_exempt_note']}." if cur.get("tax_exempt_note") else ""), "", money(cur["tax"])),
            ]
        else:
            rows.append(("Income tax, 10% of net income", "", money(cur["tax"])))
        rows.append((f"Total to pay for {year}", "", money(cur["total_due"])))
        self.breakdown.setRowCount(len(rows))
        for i, r in enumerate(rows):
            fill_row(self.breakdown, i, list(r), {1, 2})
        self.breakdown.resizeRowsToContents()

        if s:
            mw = s["minimum_wage"]
            text = (f"<b>Values used{'' if cur['settings_year'] == year else ' (from ' + cur['settings_year'] + ')'}</b><ul>"
                    f"<li>Minimum wage: {money(mw)} RON</li>"
                    f"<li>CAS {formatting.percent(s['cas_rate'])}: base {formatting.number(s['cas_low_wages'])} wages from "
                    f"{money(s['cas_low_wages'] * mw)}, {formatting.number(s['cas_high_wages'])} wages from {money(s['cas_high_wages'] * mw)}</li>"
                    f"<li>CASS {formatting.percent(s['cass_rate'])}: floor {money(s['cass_floor_wages'] * mw)}"
                    f"{' (not applied)' if s['has_salary'] else ''}, ceiling {money(s['cass_ceiling_wages'] * mw)}</li>"
                    f"<li>Income tax {formatting.percent(s['income_tax_rate'])}</li>"
                    f"<li>Salaried job in {year}: <b>{'yes' if s['has_salary'] else 'no'}</b></li></ul>")
            if not s["checked"]:
                text += f"<div style='color:{PALETTE['warn']}'>Not yet marked as checked against an official source.</div>"
            if s["note"]:
                text += f"<div style='color:{PALETTE['muted']}'>{html.escape(str(s['note']))}</div>"
        else:
            text = ""
        text += ("<div style='margin-top:6px'>Firm and salaried job: Settings tab. Minimum wage and thresholds: "
                 "settings.json (see the README).</div>")
        self.values.setText(text)

        summaries = view["summaries"]
        self.all_years.setRowCount(len(summaries))
        for i, y in enumerate(summaries):
            label_year = y["year"] + (" (salaried)" if y.get("settings") and y["settings"]["has_salary"] else "")
            fill_row(self.all_years, i, [label_year, money(y["income"]), money(y["expenses"]), money(y["net"]),
                                         money(y["cas"]), money(y["cass"]), money(y["taxable"]), money(y["tax"]),
                                         money(y["total_due"])], set(range(1, 9)))
            if y["year"] == year:
                self.all_years.selectRow(i)
        self.all_years.setVisible(bool(summaries))
        self.empty.setVisible(not summaries)

    def open_year(self, row: int, _col: int) -> None:
        item = self.all_years.item(row, 0)
        if item:
            self.window.choose_year(item.text()[:4])
