"""The desktop window: the same tabs as the browser version, over the same files."""
import sys

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from .. import services
from .common import Dialogs, apply_palette, label, set_kind
from .expenses import ExpensesTab
from .income import IncomeTab
from .invoicing_tab import InvoicingTab
from .overview import OverviewTab
from .settings_tab import SettingsTab

TABS = ("Overview", "Income", "Expenses", "Invoicing", "Settings")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        services.start()
        # The window keeps to its profile even if the browser version switches to another one meanwhile.
        self.pin_profile()
        self.palette_shown: dict | None = None
        self.update_theme()
        self.setWindowTitle("PFA tax tracker")
        screen = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        self.small_screen = bool(screen and (screen.width() < 1300 or screen.height() < 920))
        self.resize(min(1280, screen.width()) if screen else 1280, min(900, screen.height()) if screen else 900)
        self.dialogs = Dialogs(self)
        self.year: str | None = None

        central = QWidget()
        self.setCentralWidget(central)
        box = QVBoxLayout(central)
        head = QHBoxLayout()
        head.addWidget(label("PFA tax tracker", "title", wrap=False))
        self.firm_line = label("", "firm")
        head.addWidget(self.firm_line, 1)
        # only shows the profile in use; profiles are switched in Settings → Profiles
        self.profile_label = label("", "profileName", wrap=False)
        self.profile_label.setToolTip("The profile in use. To switch to another one, go to Settings → Profiles.")
        head.addWidget(self.profile_label)
        head.addSpacing(12)
        self.year_label = QLabel("Year")
        self.year_box = QComboBox()
        self.year_box.activated.connect(lambda _: self.choose_year(self.year_box.currentText()))
        head.addWidget(self.year_label)
        head.addWidget(self.year_box)
        box.addLayout(head)
        self.message = label("", "message")
        self.message.setVisible(False)
        box.addWidget(self.message)

        self.tabs = QTabWidget()
        self.overview, self.income, self.expenses = OverviewTab(self), IncomeTab(self), ExpensesTab(self)
        self.invoicing, self.settings = InvoicingTab(self), SettingsTab(self)
        for title, tab in zip(TABS, (self.overview, self.income, self.expenses, self.invoicing, self.settings)):
            self.tabs.addTab(tab, title)
        self.tabs.currentChanged.connect(self.tab_changed)
        box.addWidget(self.tabs, 1)
        self.refresh()

        self.setMinimumSize(960, 640)

    def event(self, e):
        # coming back to the window: show what the browser version may have changed meanwhile
        # (the Settings form is left alone so that nothing typed there is lost)
        if e.type() == QEvent.Type.WindowActivate:
            self.refresh(keep_settings_form=True)
        return super().event(e)

    def tab_changed(self, _index: int) -> None:
        # like opening another page in the browser: a message is shown once
        self.clear_message()
        self.income.import_result.setVisible(False)
        self.refresh()

    def current_tab(self):
        return self.tabs.currentWidget()

    def refresh(self, keep_settings_form: bool = False) -> None:
        self.update_theme()
        self.refresh_profiles()
        view = services.year_view(self.year)
        self.year = view["year"]
        self.year_box.blockSignals(True)
        self.year_box.clear()
        self.year_box.addItems(view["years"])
        self.year_box.setCurrentText(self.year)
        self.year_box.blockSignals(False)
        on_settings = self.current_tab() is self.settings
        yearless = on_settings or self.current_tab() is self.invoicing    # neither of them is about one year
        self.year_box.setVisible(not yearless)
        self.year_label.setVisible(not yearless)
        f = services.firm()
        self.firm_line.setText(" · ".join(p for p in (f["name"], f"CUI {f['cui']}" if f["cui"] else "",
                                                      f"CAEN {f['caen_code']}" if f["caen_code"] else "") if p)
                               if (f["name"] or f["cui"]) else "")
        tab = self.current_tab()
        if on_settings:
            if not keep_settings_form:
                tab.refresh()
            else:
                tab.refresh_profiles()
                tab.refresh_saved_themes()     # the browser version may have saved or deleted one
        else:
            tab.refresh(view)

    def refresh_profiles(self) -> None:
        pv = services.profiles_view()
        self.profile_label.setText(f"Profile: {pv['current_name']}" if pv["enabled"] else "")
        self.profile_label.setVisible(pv["enabled"])

    def pin_profile(self) -> None:
        if services.profiles_enabled():
            services.switch_profile(services.profiles_view()["current"])

    def update_theme(self) -> None:
        """Shows the desktop app's own theme when it changed (the browser version has its own, and the theme
        does not depend on the profile, so switching profile leaves it as it is)."""
        palette = services.theme_view("desktop")["palette"]
        if palette != self.palette_shown:
            apply_palette(palette)
            self.palette_shown = palette

    def choose_profile(self, profile_id: str | None) -> None:
        if profile_id and profile_id != services.profiles_view()["current"]:
            self.profile_changed(services.switch_profile(profile_id))

    def profile_changed(self, outcome: services.Outcome, reset: bool = True) -> None:
        """After switching, creating or deleting a profile: show the profile in use from scratch."""
        if outcome.ok and reset:
            self.pin_profile()
            self.year = None                       # the other profile has its own years
            self.income.reset_form()               # nothing typed for one firm is added to another
            self.income.import_result.setVisible(False)
            self.expenses.end_edit()               # nor is an entry of one firm edited in another
            self.invoicing.reset_forms()           # nor a client, product or invoice of one firm used in another
        self.show_outcome(outcome)
        self.refresh(keep_settings_form=not (outcome.ok and reset))

    def choose_year(self, year: str) -> None:
        if year in services.available_years():
            self.year = year
            self.clear_message()
            self.income.import_result.setVisible(False)
        else:
            self.show_outcome(services.Outcome(False, error="That year is not available."))
        self.refresh()

    def show_outcome(self, outcome: services.Outcome) -> None:
        text = outcome.error or outcome.message
        if not text:
            return self.clear_message()
        set_kind(self.message, "error" if outcome.error else "message")
        self.message.setText(text)
        self.message.setVisible(True)

    def clear_message(self) -> None:
        self.message.setVisible(False)
        self.message.setText("")

    def after_action(self, outcome: services.Outcome, keep_message: bool = False) -> None:
        """After an add/delete/save/restore: show its message and move to the year it concerns."""
        if outcome.year and outcome.year in services.available_years():
            self.year = outcome.year
        if not keep_message or outcome.error:
            self.show_outcome(outcome)
        elif not outcome.message:
            self.clear_message()
        self.refresh()

    def open_proof(self, table: str, row_id: int) -> None:
        found = services.proof(table, row_id)
        if found is None:
            self.show_outcome(services.Outcome(False, error="The proof file is missing."))
        else:
            self.dialogs.open_path(found[0])


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PFA tax tracker")
    window = MainWindow()
    window.show()
    if window.small_screen:
        # some window managers ignore a maximise asked for before the window is on screen
        QTimer.singleShot(300, window.showMaximized)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
