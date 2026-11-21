"""Settings tab: profiles, firm, activity, salaried job, theme, tax values, files, backup and restore."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QCompleter, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLineEdit, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from .. import formatting, services
import html

from .common import PALETTE, fill_row, label, money, set_warn, small_button, table

FIELDS = [("name", "Name of the firm", "e.g. Popescu Ion PFA"), ("cui", "Identification code (CUI)", "12345678 or RO12345678"),
          ("trade_register", "Trade Register number", "F40/1234/2019"), ("phone", "Phone", "0721 234 567"),
          ("email", "Email", "name@example.ro"), ("iban", "Bank account (IBAN)", "RO49 AAAA 1B31 0075 9384 0000"),
          ("address", "Address", "Str. Exemplu nr. 1, București, sector 1")]
APP = "desktop"          # each app has its own theme; this one is the desktop app's
COLOR_FIELDS = (("accent", "Accent"), ("background", "Background"), ("surface", "Cards and boxes"), ("text", "Text"))


class SettingsTab(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setWidgetResizable(True)
        inner = QWidget()
        self.setWidget(inner)
        box = QVBoxLayout(inner)
        self.heading = label("Settings", "pageTitle")
        box.addWidget(self.heading)
        self.settings_error = label("", "error")
        box.addWidget(self.settings_error)

        # Profiles first, compact: they can be seen and added while the firm below is being filled in
        self.profiles_group = QGroupBox("Profiles")
        pbox = QVBoxLayout(self.profiles_group)
        self.profiles_table = table(["Profile", "Firm", "CUI", "Invoices", "Expenses", "In use"], {3, 4}, stretch=1)
        self.profiles_table.setSelectionMode(self.profiles_table.SelectionMode.SingleSelection)
        self.profiles_table.cellDoubleClicked.connect(lambda row, _col: self.switch_profile(row))
        self.profiles_table.itemSelectionChanged.connect(self.show_switch_state)
        pbox.addWidget(self.profiles_table)
        pbuttons = QHBoxLayout()
        self.switch_profile_button = QPushButton("Switch to this profile")
        self.switch_profile_button.clicked.connect(lambda: self.switch_profile())
        self.new_profile_button = QPushButton("New profile…")
        self.rename_profile_button = QPushButton("Rename…")
        self.delete_profile_button = QPushButton("Delete…")
        self.new_profile_button.clicked.connect(self.new_profile)
        self.rename_profile_button.clicked.connect(self.rename_profile)
        self.delete_profile_button.clicked.connect(self.delete_profile)
        self.new_profile_button.setToolTip("A new profile starts with an empty firm and the tax values of the profile in use.")
        self.rename_profile_button.setToolTip("Renames the profile in use.")
        for b in (self.switch_profile_button, self.new_profile_button, self.rename_profile_button, self.delete_profile_button):
            pbuttons.addWidget(b)
        self.profiles_note = label("", "muted")
        pbuttons.addWidget(self.profiles_note, 1)
        pbox.addLayout(pbuttons)
        box.addWidget(self.profiles_group)
        self.profile_ids: list[str] = []

        columns = QHBoxLayout()
        firm = QGroupBox("Firm")
        self.firm_group = firm
        form = QFormLayout(firm)
        self.inputs = {}
        self.cui_note, self.register_note = label("", "muted"), label("", "muted")
        self.firm_form = form
        for key, title, hint in FIELDS:
            self.inputs[key] = QLineEdit(placeholderText=hint)
            form.addRow(title, self.inputs[key])
            if key == "cui":
                form.addRow("", self.cui_note)          # each note sits right under its own field
            if key == "trade_register":
                form.addRow("", self.register_note)
        form.addRow(label("All fields are optional; what you fill in must be in a valid format.", "muted"))
        columns.addWidget(firm, 1)

        right = QVBoxLayout()
        activity = QGroupBox("Activity")
        aform = QFormLayout(activity)
        self.activity_form = aform
        self.caen_code = QLineEdit(placeholderText="e.g. 6210")
        self.caen_revision = QComboBox()
        self.caen_revision.addItem("Rev.3", "3")
        self.caen_revision.addItem("Rev.2", "2")
        self.caen_note = label("", "muted")
        self.secondary = QLineEdit(placeholderText="e.g. 6220, 7022")
        self.secondary_note = label("", "muted")
        aform.addRow("Main activity (CAEN code)", self.caen_code)
        aform.addRow("CAEN version", self.caen_revision)
        aform.addRow("", self.caen_note)
        aform.addRow("Secondary activities", self.secondary)
        aform.addRow("", self.secondary_note)
        aform.addRow(label("Only the main activity is used for the income tax exclusion check.", "muted"))
        right.addWidget(activity)

        salaried = QGroupBox("Salaried job")
        self.salaried_box = QVBoxLayout(salaried)
        self.salaried_now = QCheckBox("I have a salaried job")
        self.salaried_box.addWidget(self.salaried_now)
        self.salaried_box.addWidget(label("Tick it if you are also employed and health-insured through that job. It "
                                          "applies to the current year: the CASS floor then does not apply to the PFA "
                                          "income. Past years are not changed by it.", "muted"))
        self.earlier_title = label("")
        self.salaried_box.addWidget(self.earlier_title)
        self.earlier_grid = QGridLayout()
        self.salaried_box.addLayout(self.earlier_grid)
        self.earlier_note = label("", "muted")
        self.salaried_box.addWidget(self.earlier_note)
        self.salaried_box.addStretch(1)
        self.earlier_checks: dict[str, QCheckBox] = {}
        right.addWidget(salaried)
        right.addStretch(1)
        columns.addLayout(right, 1)
        box.addLayout(columns)

        self.save_button = QPushButton("Save settings")
        self.save_button.clicked.connect(self.save)
        box.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignLeft)

        look = QGroupBox("Theme")
        self.theme_group_box = look
        lbox = QVBoxLayout(look)
        self.theme_buttons: dict[str, QRadioButton] = {}
        self.theme_group = QButtonGroup(self)
        choices = QHBoxLayout()
        for name in services.theme_view(APP)["themes"]:
            button = QRadioButton(name.capitalize())
            self.theme_group.addButton(button)
            self.theme_buttons[name] = button
            choices.addWidget(button)
        self.theme_in_use = label("", "muted", wrap=False)
        choices.addSpacing(12)
        choices.addWidget(self.theme_in_use)
        choices.addStretch(1)
        lbox.addLayout(choices)

        # the custom colours and the saved themes: shown only while Custom is chosen
        self.custom_panel = QWidget()
        cbox = QVBoxLayout(self.custom_panel)
        cbox.setContentsMargins(0, 0, 0, 0)
        swatches = QHBoxLayout()
        self.theme_colors: dict[str, str] = {}
        self.color_buttons: dict[str, QPushButton] = {}
        for key, title in COLOR_FIELDS:
            button = small_button(title)
            button.setIconSize(QSize(22, 16))
            button.clicked.connect(lambda _=False, k=key: self.pick_color(k))
            self.color_buttons[key] = button
            swatches.addWidget(button)
        swatches.addStretch(1)
        cbox.addLayout(swatches)
        saved = QHBoxLayout()
        saved.addWidget(label("Saved themes", wrap=False))
        self.saved_box = QComboBox()
        self.saved_box.setMinimumContentsLength(18)
        self.saved_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        saved.addWidget(self.saved_box)
        self.use_saved_button = small_button("Use")
        self.delete_saved_button = small_button("Delete…")
        self.save_as_button = small_button("Save as…")
        self.use_saved_button.clicked.connect(self.use_saved_theme)
        self.delete_saved_button.clicked.connect(self.delete_saved_theme)
        self.save_as_button.clicked.connect(self.save_custom_theme)
        for b in (self.use_saved_button, self.delete_saved_button):
            saved.addWidget(b)
        saved.addSpacing(12)
        saved.addWidget(self.save_as_button)
        saved.addStretch(1)
        cbox.addLayout(saved)
        cbox.addWidget(label("The text must stay readable on the background and on the cards. Save as… keeps these "
                             "colours under a name and uses them; saved themes are offered in the browser version "
                             "too.", "muted"))
        lbox.addWidget(self.custom_panel)
        self.theme_group.buttonToggled.connect(lambda *_: self.show_custom_panel())

        lbox.addWidget(label("The theme is for this desktop app only: the browser version has its own.", "muted"))
        self.theme_button = QPushButton("Apply theme")
        self.theme_button.clicked.connect(self.save_theme)
        lbox.addWidget(self.theme_button, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addWidget(look)

        box.addWidget(label("Tax values per year", "pageTitle"))
        self.tax_table = table(["Year", "Minimum wage", "CAS", "CAS bands", "CASS", "CASS floor – ceiling", "Income tax", "Status"],
                               {1, 2, 3, 4, 5, 6}, stretch=7)

        box.addWidget(self.tax_table)
        self.tax_note = label("", "muted")
        box.addWidget(self.tax_note)

        info = QHBoxLayout()
        self.caen_info, self.rates_info = label(""), label("")
        self.caen_info.setTextFormat(Qt.TextFormat.PlainText)
        for title, widget in (("CAEN list", self.caen_info), ("Exchange rates", self.rates_info)):
            group = QGroupBox(title)
            gbox = QVBoxLayout(group)
            gbox.addWidget(widget)
            gbox.addWidget(label("Open the file in LibreOffice or Excel and write yes in the “Excluded from income tax” "
                                 "column to mark a code. Its “Read me” sheet says where the lists come from."
                                 if title == "CAEN list" else
                                 "Read on every import. Invoices and expenses already stored keep the rate they were "
                                 "converted with.", "muted"))
            gbox.addStretch(1)
            info.addWidget(group)
        box.addLayout(info)

        data = QGroupBox("Your data, backup and moving to another computer")
        dbox = QVBoxLayout(data)
        self.data_info = label("", wrap=False)
        dbox.addWidget(self.data_info)
        buttons = QHBoxLayout()
        self.backup_button = QPushButton("Save a backup…")
        self.restore_button = QPushButton("Restore a backup…")
        self.backup_button.clicked.connect(self.save_backup)
        self.restore_button.clicked.connect(self.restore_backup)
        buttons.addWidget(self.backup_button)
        buttons.addWidget(self.restore_button)
        buttons.addStretch(1)
        dbox.addLayout(buttons)
        dbox.addWidget(label("A backup is one zip file with the settings, all invoices and expenses, the proofs, the rates file and\n"
                             "the CAEN list — the same file the browser version makes and reads. Restoring replaces everything\n"
                             "here (this profile only); a copy of the current state is saved in the profile's data/backups/ first.", "muted", wrap=False))
        box.addWidget(data)

        box.addStretch(1)
        self.completer_rev = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self) -> None:
        """Makes the page as tall as its content at the current width, not as tall as its wrapped texts would be
        if squeezed to their narrowest (which left a large empty space at the bottom)."""
        inner = self.widget()
        inner.setMinimumHeight(inner.layout().totalHeightForWidth(self.viewport().width()))

    # filling the form

    def refresh(self, view: dict | None = None, form: dict | None = None, salary: dict | None = None) -> None:
        shown_again = form is not None     # a form shown again after an error keeps the theme being chosen as well
        v = services.settings_view(form, salary)
        f = v["form"]
        self.settings_error.setText(v["settings_err"] or "")
        self.settings_error.setVisible(bool(v["settings_err"]))
        for key, _, _ in FIELDS:
            self.inputs[key].setText(f.get(key, ""))
        self.cui_note.setText("" if v["cui_ok"] is None else ("Check digit is valid." if v["cui_ok"] else "Check digit does not match."))
        set_warn(self.cui_note, v["cui_ok"] is False)
        self.register_note.setText(f"Registered in {v['registered']}." if v["registered"] else "")
        self.caen_code.setText(f.get("caen_code", ""))
        self.caen_revision.setCurrentIndex(1 if v["rev"] == "2" else 0)
        if self.completer_rev != v["rev"]:
            codes = v["caen"].get(v["rev"], {})
            completer = QCompleter([f"{c} – {e['title']}" for c, e in codes.items()], self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.activated.connect(lambda text: self.caen_code.setText(text[:4]))
            self.caen_code.setCompleter(completer)
            self.completer_rev = v["rev"]
        self.caen_note.setText(self.caen_text(v, f))
        set_warn(self.caen_note, bool(f.get("caen_code")) and (not v["caen_entry"] or v["caen_entry"]["excluded"]))
        self.secondary.setText(", ".join(f.get("secondary_caen", [])))
        self.secondary_note.setText("\n".join(
            f"{s['code']} – {s['entry']['title'] if s['entry'] else 'not a CAEN Rev.' + v['rev'] + ' code in the list'}"
            for s in v["secondary"]))
        set_warn(self.secondary_note, any(not s["entry"] for s in v["secondary"]))

        self.salaried_now.setChecked(bool(v["salary"].get(v["this_year"])))
        for cb in self.earlier_checks.values():
            cb.setParent(None)
        self.earlier_checks = {}
        for i, y in enumerate(v["earlier"]):
            cb = QCheckBox(y + ("" if y in v["lifetime"] else "  (outside the PFA's lifetime — ignored)"))
            cb.setProperty("warn", "true" if y not in v["lifetime"] else "false")
            cb.setChecked(bool(v["salary"].get(y)))
            self.earlier_grid.addWidget(cb, i // 4, i % 4)
            self.earlier_checks[y] = cb
        if v["earlier"]:
            self.earlier_title.setText("Earlier years with a salaried job" + (f" (the PFA exists since {v['registered']})" if v["registered"] else ""))
            self.earlier_note.setText("" if v["lifetime"] else "Enter the Trade Register number to mark other years.")
        else:
            self.earlier_title.setText("")
            self.earlier_note.setText("The PFA was registered this year, so there are no earlier years to mark." if v["registered"]
                                      else "Enter the Trade Register number to mark earlier years: only years since the PFA's registration can be chosen.")

        rows = v["tax_years"]
        self.tax_table.setRowCount(len(rows))
        for i, y in enumerate(rows):
            t = y["tax"]
            if t:
                status = ("no values for " + y["year"] + " — uses " + y["source"]) if not y["own"] else ("checked" if t["checked"] else "not checked")
                fill_row(self.tax_table, i, [y["year"], money(t["minimum_wage"]), formatting.percent(t["cas_rate"]),
                                             f"{formatting.number(t['cas_low_wages'])} / {formatting.number(t['cas_high_wages'])} wages",
                                             formatting.percent(t["cass_rate"]),
                                             f"{formatting.number(t['cass_floor_wages'])} – {formatting.number(t['cass_ceiling_wages'])} wages",
                                             formatting.percent(t["income_tax_rate"]), status], {1, 2, 3, 4, 5, 6},
                         warn={7} if status != "checked" else set())
            else:
                self.tax_table.setSpan(i, 1, 1, 7)
                fill_row(self.tax_table, i, [y["year"], y["error"]], warn={1})
        self.tax_table.setFixedHeight(self.tax_table.horizontalHeader().height() + 32 * max(len(rows), 1) + 6)
        self.tax_note.setText(f"These values are edited in {v['settings_path']} (see the README).")
        for note in (self.cui_note, self.register_note, self.caen_note, self.secondary_note, self.earlier_title, self.earlier_note):
            note.setVisible(bool(note.text()))
        for form, note in ((self.firm_form, self.cui_note), (self.firm_form, self.register_note),
                           (self.activity_form, self.caen_note), (self.activity_form, self.secondary_note)):
            form.setRowVisible(note, bool(note.text()))

        if v["caen_err"]:
            self.caen_info.setText(f"File: {v['caen_path']}\n{v['caen_err']}")
        else:
            counts = "; ".join(f"Rev.{r}: {len(c)} codes" for r, c in sorted(v["caen"].items(), reverse=True))
            excluded = "".join(f"\n• Rev.{c['rev']} {c['code']} – {c['title']}{self.window_text(c)}" for c in v["caen_excluded"])
            self.caen_info.setText(f"File: {v['caen_path']}\n{counts}. {len(v['caen_excluded'])} marked as excluded from income tax.{excluded}")
        r = v["rates"]
        self.rates_info.setText(f"File: {r['path']}\n" + (r["error"] or "\n".join(
            f"{c['code']}: {c['count']} daily rates, {c['first']} to {c['last']}" for c in r["currencies"])))
        c = v["counts"]
        self.data_info.setText(
            "Everything you enter is saved on this computer the moment you save it, and is there again the next time\n"
            "the app starts — in either version, browser or desktop, which share the same files:\n"
            f"• settings: {v['settings_path']}\n• invoices and expenses: {v['db_path']}"
            + (f" ({c['invoices']} invoice(s), {c['expenses']} expense(s))" if c else "")
            + f"\n• proofs: {v['data_dir']}/attachments/")
        if not shown_again:
            self.refresh_theme()
        self.refresh_profiles()
        self.fit_height()

    def refresh_theme(self) -> None:
        look = services.theme_view(APP)
        self.theme_buttons[look["theme"]].setChecked(True)
        self.theme_colors = dict(look["colors"])
        self.show_colors()
        self.refresh_saved_themes(look, keep_choice=False)
        self.show_custom_panel()

    def refresh_saved_themes(self, look: dict | None = None, keep_choice: bool = True) -> None:
        """The saved themes (the browser version may add some) and which one is in use; the choice being made
        with the radio buttons and colour buttons is left alone."""
        look = look or services.theme_view(APP)
        chosen = (self.saved_box.currentData() or look["saved_name"]) if keep_choice else \
            (look["saved_name"] or self.saved_box.currentData())
        self.saved_box.clear()
        for t in look["saved"]:
            self.saved_box.addItem(t["name"], t["name"])
        if not look["saved"]:
            self.saved_box.addItem("none saved yet", None)
        self.saved_box.setCurrentIndex(max(0, self.saved_box.findData(chosen)))
        self.saved_box.setEnabled(bool(look["saved"]))
        self.use_saved_button.setEnabled(bool(look["saved"]))
        self.delete_saved_button.setEnabled(bool(look["saved"]))
        self.theme_in_use.setText(f"In use: saved theme “{look['saved_name']}”" if look["saved_name"] else "")

    def show_custom_panel(self) -> None:
        custom = self.theme_buttons["custom"].isChecked()
        if self.custom_panel.isVisibleTo(self) != custom:
            self.custom_panel.setVisible(custom)
            self.fit_height()

    def show_colors(self) -> None:
        for key, title in COLOR_FIELDS:
            swatch = QPixmap(22, 16)
            swatch.fill(QColor(PALETTE["muted"]))            # a thin frame, so that a pale colour still shows
            painter = QPainter(swatch)
            painter.fillRect(1, 1, 20, 14, QColor(self.theme_colors[key]))
            painter.end()
            self.color_buttons[key].setIcon(QIcon(swatch))
            self.color_buttons[key].setText(f"{title} {self.theme_colors[key]}")

    def refresh_profiles(self) -> None:
        pv = services.profiles_view()
        self.profiles_group.setVisible(pv["enabled"])
        self.heading.setText(f"Settings — {pv['current_name']}" if pv["enabled"] else "Settings")
        if not pv["enabled"]:
            return
        self.profiles_note.setText(f"Using {pv['current_name']} — to switch, choose a profile in the table and click "
                                   f"“Switch to this profile” (or double-click it). Each profile has its own firm, "
                                   f"invoices, expenses and proofs; the rates file and the CAEN list are shared by all "
                                   f"profiles.")
        rows = pv["profiles"]
        self.profile_ids = [p["id"] for p in rows]
        self.profiles_table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            fill_row(self.profiles_table, i, [p["name"], p["firm"], p["cui"], p["invoices"], p["expenses"],
                                              "in use" if p["current"] else ""], {3, 4})
        # compact: up to four rows show at once, more scroll inside the table
        self.profiles_table.setFixedHeight(self.profiles_table.horizontalHeader().height() + 32 * min(max(len(rows), 1), 4) + 6)
        self.delete_profile_button.setEnabled(len(rows) > 1)
        self.delete_profile_button.setToolTip(
            "Deletes the profile chosen in the table, after saving a backup of it in the deleted-profiles folder."
            if len(rows) > 1 else "The only profile cannot be deleted.")
        self.show_switch_state()

    @staticmethod
    def window_text(e: dict) -> str:
        """' (2020–2030) — basis', as the browser shows after an excluded code."""
        years = f" ({e['from_year'] or '…'}–{e['to_year'] or '…'})" if e["from_year"] or e["to_year"] else ""
        return years + (f" — {e['basis']}" if e["basis"] else "")

    @classmethod
    def caen_text(cls, v: dict, f: dict) -> str:
        if not f.get("caen_code"):
            return ""
        e = v["caen_entry"]
        if e:
            text = f"Rev.{v['rev']} {e['code']} – {e['title']}"
            if e["excluded"]:
                text += (f"\nMarked as excluded from income tax{cls.window_text(e)}. Income tax is shown as 0 for those "
                         "years; CAS and CASS are still due.")
            else:
                text += "\nNot marked as excluded from income tax."
            return text
        other = v["caen_other"]
        return (f"{f['caen_code']} is not a CAEN Rev.{v['rev']} code in the list"
                + (f" — it exists in Rev.{other['rev']} ({other['title']}); check the version" if other else "") + ".")

    # actions

    def values(self) -> dict:
        values = {key: self.inputs[key].text() for key, _, _ in FIELDS}
        values.update(caen_code=self.caen_code.text(), caen_revision=self.caen_revision.currentData(),
                      secondary_caen=self.secondary.text(), salaried_now=self.salaried_now.isChecked(),
                      salaried_years=[y for y, cb in self.earlier_checks.items() if cb.isChecked()],
                      salaried_shown=list(self.earlier_checks))
        return values

    def save(self) -> None:
        outcome = services.save_settings(self.values())
        if outcome.ok:
            self.window.after_action(outcome)
            self.refresh()
        else:
            self.window.show_outcome(outcome)
            self.refresh(form=outcome.form, salary=outcome.extra.get("salary"))

    def chosen_theme(self) -> str:
        return next(name for name, button in self.theme_buttons.items() if button.isChecked())

    def pick_color(self, key: str) -> None:
        title = dict(COLOR_FIELDS)[key]
        picked = self.window.dialogs.pick_color(f"Theme colour: {title.lower()}", self.theme_colors[key])
        if picked:
            self.theme_colors[key] = picked
            self.theme_buttons["custom"].setChecked(True)
            self.show_colors()

    def save_theme(self) -> None:
        name = self.chosen_theme()
        look = services.theme_view(APP)
        in_use = next((t for t in look["saved"] if t["name"] == look["saved_name"]), None)
        if name == "custom" and in_use and in_use["colors"] == self.theme_colors:
            outcome = services.use_saved_theme(APP, in_use["name"])      # unchanged: it stays the saved theme in use
        else:
            outcome = services.save_theme(APP, name, dict(self.theme_colors) if name == "custom" else None)
        self.theme_done(outcome)

    def theme_done(self, outcome: services.Outcome) -> None:
        """After a theme action: show the theme in use, or keep what was chosen so that it can be corrected."""
        if outcome.ok:
            self.window.update_theme()
            self.refresh_theme()
        else:
            self.refresh_saved_themes()
        self.window.show_outcome(outcome)

    def save_custom_theme(self) -> None:
        current = services.theme_view(APP)["saved_name"] or ""
        name = self.window.dialogs.ask_text(
            "Save the custom theme", "Name for these colours (a saved theme with the same name is replaced):", current)
        if name is None:
            return
        self.theme_done(services.save_custom_theme(APP, name, dict(self.theme_colors)))

    def use_saved_theme(self) -> None:
        name = self.saved_box.currentData()
        if not name:
            self.window.show_outcome(services.Outcome(False, error="Theme not changed: there is no saved theme yet."))
            return
        self.theme_done(services.use_saved_theme(APP, name))

    def delete_saved_theme(self) -> None:
        name = self.saved_box.currentData()
        if not name:
            self.window.show_outcome(services.Outcome(False, error="Theme not deleted: there is no saved theme yet."))
            return
        if not self.window.dialogs.confirm("Delete a saved theme", f"Delete the saved theme {name}? It is removed "
                                           "from the browser version too; the colours in use do not change."):
            self.window.show_outcome(services.Outcome(False, error="Theme not deleted: you chose not to delete it."))
            return
        outcome = services.delete_saved_theme(name)
        self.refresh_saved_themes()
        self.window.show_outcome(outcome)

    def selected_profile(self) -> str | None:
        rows = self.profiles_table.selectionModel().selectedRows()
        return self.profile_ids[rows[0].row()] if rows and rows[0].row() < len(self.profile_ids) else None

    def show_switch_state(self) -> None:
        """The switch button works for a chosen profile other than the one in use."""
        chosen = self.selected_profile()
        current = services.profiles_view()["current"] if chosen else None
        self.switch_profile_button.setEnabled(bool(chosen) and chosen != current)
        self.switch_profile_button.setToolTip(
            "Choose a profile in the table first." if not chosen else
            "This is the profile in use." if chosen == current else "Shows this profile's firm, invoices and expenses.")

    def switch_profile(self, row: int | None = None) -> None:
        """Switches to the profile chosen in the table (or to the one in the given row, when double-clicked)."""
        if row is None:
            profile_id = self.selected_profile()
        else:
            profile_id = self.profile_ids[row] if 0 <= row < len(self.profile_ids) else None
        if profile_id is None:
            self.window.show_outcome(services.Outcome(False, error="Profile not changed: choose it in the table first."))
            return
        self.window.choose_profile(profile_id)

    def new_profile(self) -> None:
        name = self.window.dialogs.ask_text("New profile", "Name of the new profile (for example the firm's name):", "")
        if name is not None:
            self.window.profile_changed(services.create_profile(name))

    def rename_profile(self) -> None:
        pv = services.profiles_view()
        name = self.window.dialogs.ask_text("Rename the profile", f"New name for the profile {pv['current_name']}:",
                                            pv["current_name"])
        if name is None:
            return
        self.window.show_outcome(services.rename_profile(pv["current"], name))
        self.window.refresh(keep_settings_form=True)

    def delete_profile(self) -> None:
        profile_id = self.selected_profile()
        if profile_id is None:
            self.window.show_outcome(services.Outcome(False, error="Profile not deleted: choose it in the table first."))
            return
        p = next(p for p in services.profiles_view()["profiles"] if p["id"] == profile_id)
        confirmed = self.window.dialogs.confirm(
            "Delete a profile", f"Delete the profile {p['name']}"
            + (f" (firm {p['firm']})" if p["firm"] else "")
            + f" with its settings, {p['invoices']} invoice(s), {p['expenses']} expense(s) and their proofs? "
            "A backup of it is saved first in the deleted-profiles folder.")
        if not confirmed:
            self.window.show_outcome(services.Outcome(False, error="Profile not deleted: you chose not to delete it."))
            return
        self.window.profile_changed(services.delete_profile(profile_id, True), reset=p["current"])

    def save_backup(self) -> None:
        name, data = services.export_backup()
        path = self.window.dialogs.save_file("Save a backup", name, "Backup (*.zip)")
        if path:
            path.write_bytes(data)
            self.window.show_outcome(services.Outcome(True, message=f"Backup saved: {path}"))

    def restore_backup(self) -> None:
        path = self.window.dialogs.open_file("Restore a backup", "Backup (*.zip);;All files (*)")
        if not path:
            return
        confirmed = self.window.dialogs.confirm(
            "Restore a backup", "Everything here (settings, invoices, expenses, proofs, clients, products, series, rates, CAEN list) will be replaced "
            "by the backup's content. A copy of the current state is saved first. Continue?")
        if not confirmed:
            self.window.show_outcome(services.Outcome(False, error="Not restored: you chose not to replace the data here."))
            return
        outcome = services.restore_backup(path.read_bytes(), True)
        if outcome.ok:                                # the entries being edited may not exist any more
            self.window.income.reset_form()
            self.window.expenses.end_edit()
            self.window.invoicing.reset_forms()       # a chosen client or product may now be another one
        self.window.after_action(outcome)
        self.refresh()
