"""The desktop app's theme (light, dark, custom colours, saved custom themes; its own, apart from the browser's)
and profiles (one firm each, with its own settings, invoices, expenses and proofs), driven through the window's
widgets without a screen."""
import contextvars
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QDate, QEvent  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

from conftest import TODAY, YEAR  # noqa: E402
from tracker import config, services, theme  # noqa: E402
from tracker.config import SETTINGS_PATH  # noqa: E402

DESKTOP = Path(__file__).resolve().parent.parent / "tracker" / "desktop"


class ScriptedDialogs:
    """Answers queued by the test: files, confirmations, typed names and picked colours."""

    def __init__(self):
        self.files, self.answers, self.texts, self.colors, self.asked = [], [], [], [], []

    def open_file(self, title, file_filter):
        return self.files.pop(0) if self.files else None

    def save_file(self, title, suggested, file_filter):
        return None

    def confirm(self, title, text):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else False

    def ask_text(self, title, text, default=""):
        self.asked.append(text)
        return self.texts.pop(0) if self.texts else None

    def pick_color(self, title, initial):
        return self.colors.pop(0) if self.colors else None

    def open_path(self, path):
        pass


@pytest.fixture(scope="module")
def qapp():
    from tracker.desktop.common import STYLE
    from PySide6.QtCore import QEvent
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # windows other test files closed
    app.setStyleSheet(STYLE)
    return app


def make_window(qapp):
    from tracker.desktop.app import MainWindow
    w = MainWindow()
    w.dialogs = ScriptedDialogs()
    w.show()
    return w


def close_window(qapp, w):
    from PySide6.QtCore import QEvent
    w.close()
    w.deleteLater()
    qapp.processEvents()
    # really delete it now: every window left alive would be restyled again by the next theme change
    QtWidgets.QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def win(qapp):
    """A window in single-file mode (the test files of conftest, no profiles)."""
    w = make_window(qapp)
    yield w
    close_window(qapp, w)


@pytest.fixture
def home(monkeypatch, tmp_path):
    """Profiles switched on: a fresh home folder, no profile chosen yet (rates and CAEN list stay the test ones)."""
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


def go_tab(win, name):
    win.tabs.setCurrentIndex(["Overview", "Income", "Expenses", "Invoicing", "Settings"].index(name))
    QtWidgets.QApplication.processEvents()


def shown(win) -> str:
    return win.message.text() if win.message.isVisible() else ""


def style() -> str:
    return QtWidgets.QApplication.instance().styleSheet()


def window_color(win) -> str:
    """The colour actually drawn at the window's edge (its background)."""
    img = win.grab().toImage()
    return img.pixelColor(2, img.height() - 3).name()


def header_profile(win) -> str:
    """What the header shows about the profile (only its name: profiles are switched in Settings)."""
    return win.profile_label.text() if win.profile_label.isVisible() else ""


def profile_names(win) -> list[str]:
    return [r[0] for r in profile_rows(win)]


def switch_in_settings(win, name):
    go_tab(win, "Settings")
    win.settings.profiles_table.selectRow(profile_names(win).index(name))
    win.settings.switch_profile_button.click()
    QtWidgets.QApplication.processEvents()


def profile_rows(win) -> list[list[str]]:
    t = win.settings.profiles_table
    return [[t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())] for r in range(t.rowCount())]


def choose_theme(win, name, colors=None):
    go_tab(win, "Settings")
    s = win.settings
    s.theme_buttons[name].setChecked(True)
    for key, value in (colors or {}).items():
        win.dialogs.colors.append(value)
        s.color_buttons[key].click()
    s.theme_button.click()


def ui_text() -> str:
    """The file holding each app's theme and the saved themes (empty if nothing was chosen yet)."""
    path = config.ui_settings_path()
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def saved_names(win) -> list[str]:
    box = win.settings.saved_box
    return [box.itemData(i) for i in range(box.count()) if box.itemData(i)]


def save_as(win, name, colors=None):
    """Chooses Custom, picks the colours and saves them under a name with Save as…"""
    choose_colors(win, colors)
    win.dialogs.texts.append(name)
    win.settings.save_as_button.click()


def choose_colors(win, colors=None):
    go_tab(win, "Settings")
    win.settings.theme_buttons["custom"].setChecked(True)
    for key, value in (colors or {}).items():
        win.dialogs.colors.append(value)
        win.settings.color_buttons[key].click()


def activate(win):
    """Coming back to the window (after using the browser version, for example)."""
    QtWidgets.QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    QtWidgets.QApplication.processEvents()


SUNNY = {"accent": "#b5432b", "background": "#fdf6e3", "surface": "#fffdf7", "text": "#2b2118"}
NIGHT = {"accent": "#6fb3ff", "background": "#101820", "surface": "#1b2733", "text": "#e8eef5"}


def add_invoice(win, client="Acme", amount="100"):
    go_tab(win, "Income")
    tab = win.income
    tab.date.setDate(QDate(TODAY.year, TODAY.month, TODAY.day))
    tab.paid_check.setChecked(False)
    tab.client.setText(client)
    tab.number.setText(f"N-{client}")
    tab.amount.setText(amount)
    tab.currency.setCurrentText("RON")
    tab.add_button.click()
    assert shown(win) == "Invoice added."


def add_expense(win, description="Laptop", amount="50"):
    go_tab(win, "Expenses")
    tab = win.expenses
    tab.date.setDate(QDate(TODAY.year, TODAY.month, TODAY.day))
    tab.amount.setText(amount)
    tab.currency.setCurrentText("RON")
    tab.description.setText(description)
    tab.add_button.click()
    assert not shown(win).startswith("Expense not added"), shown(win)


def save_firm(win, name, cui=""):
    go_tab(win, "Settings")
    win.settings.inputs["name"].setText(name)
    win.settings.inputs["cui"].setText(cui)
    win.settings.save_button.click()


def column(table, col) -> list[str]:
    return [table.item(r, col).text() if table.item(r, col) else "" for r in range(table.rowCount())]


# Theme

def test_no_colour_is_written_in_the_desktop_code():
    """Every colour comes from the theme's palette, so light, dark and custom reach every part of the window."""
    for path in DESKTOP.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", text), path.name
        assert "GlobalColor" not in text, path.name


def test_the_style_uses_every_colour_of_the_palette():
    from tracker.desktop.common import build_style
    for palette in (theme.LIGHT, theme.DARK, theme.palette("custom", theme.DEFAULT_CUSTOM)):
        sheet = build_style(palette)
        missing = [k for k, v in palette.items() if v.lower() not in sheet.lower()]
        assert not missing, missing


def test_light_is_the_default_and_dark_and_custom_restyle_the_window(win):
    assert theme.LIGHT["background"] in style() and window_color(win) == theme.LIGHT["background"]
    go_tab(win, "Settings")
    assert [n for n, b in win.settings.theme_buttons.items() if b.isChecked()] == ["light"]

    choose_theme(win, "dark")
    assert shown(win) == "Theme set to dark."
    assert theme.DARK["background"] in style() and theme.LIGHT["background"] not in style()
    assert window_color(win) == theme.DARK["background"]
    app_palette = QtWidgets.QApplication.instance().palette()
    assert app_palette.color(QPalette.ColorRole.Base).name() == theme.DARK["surface"]
    assert json.loads(ui_text())["apps"]["desktop"]["theme"] == "dark"
    assert "ui" not in json.loads(SETTINGS_PATH.read_text())           # not in the firm's settings any more

    custom = {"accent": "#b5432b", "background": "#fdf6e3", "surface": "#fffdf7", "text": "#2b2118"}
    choose_theme(win, "custom", custom)
    assert shown(win) == "Theme set to custom."
    assert all(v in style() for v in custom.values()) and theme.DARK["background"] not in style()
    assert window_color(win) == custom["background"]
    assert win.settings.color_buttons["accent"].text() == "Accent #b5432b"
    assert services.theme_view("desktop")["colors"] == custom

    choose_theme(win, "light")
    assert theme.LIGHT["background"] in style() and window_color(win) == theme.LIGHT["background"]
    assert services.theme_view("desktop")["colors"] == custom   # the custom colours are kept for next time


def test_choosing_a_colour_selects_custom_and_a_cancelled_choice_changes_nothing(win):
    go_tab(win, "Settings")
    s = win.settings
    before = dict(s.theme_colors)
    s.color_buttons["text"].click()                           # the colour dialog is cancelled
    assert s.theme_colors == before and s.theme_buttons["light"].isChecked()
    win.dialogs.colors.append("#102030")
    s.color_buttons["text"].click()
    assert s.theme_colors["text"] == "#102030" and s.theme_buttons["custom"].isChecked()
    assert theme.LIGHT["background"] in style()               # nothing applied before "Apply theme"


def test_unreadable_custom_colours_are_refused_and_the_choice_is_kept(win):
    old = ui_text()
    choose_theme(win, "custom", {"background": "#ffffff", "surface": "#ffffff", "text": "#eeeeee"})
    assert shown(win).startswith("Theme not saved: the text colour is too hard to read on the background colour")
    assert win.message.property("kind") == "error"
    assert theme.LIGHT["background"] in style() and ui_text() == old
    s = win.settings
    assert s.theme_buttons["custom"].isChecked() and s.theme_colors["text"] == "#eeeeee"   # to correct, not retype

    choose_theme(win, "custom", {"text": "#1a1a1a", "accent": "#fafafa"})
    assert shown(win) == "Theme not saved: the accent colour is too close to the surface colour."
    choose_theme(win, "custom", {"accent": "#0057b8"})
    assert shown(win) == "Theme set to custom." and "#0057b8" in style()


def test_the_custom_colours_show_only_when_custom_is_chosen(win):
    go_tab(win, "Settings")
    s = win.settings
    custom_parts = [*s.color_buttons.values(), s.saved_box, s.use_saved_button, s.delete_saved_button, s.save_as_button]
    assert s.theme_buttons["light"].isChecked()
    assert not s.custom_panel.isVisible() and not any(w.isVisible() for w in custom_parts)
    assert s.theme_button.isVisible()
    s.theme_buttons["custom"].setChecked(True)                 # shown at once, before anything is applied
    assert s.custom_panel.isVisible() and all(w.isVisible() for w in custom_parts)
    s.theme_buttons["dark"].setChecked(True)
    assert not s.custom_panel.isVisible()
    s.theme_buttons["custom"].setChecked(True)
    s.theme_buttons["light"].setChecked(True)
    assert not s.custom_panel.isVisible() and theme.LIGHT["background"] in style()

    choose_theme(win, "custom", SUNNY)
    assert shown(win) == "Theme set to custom."
    go_tab(win, "Overview")
    go_tab(win, "Settings")                                    # shown again: Custom is in use, so are its colours
    assert s.theme_buttons["custom"].isChecked() and s.custom_panel.isVisible()
    choose_theme(win, "dark")
    assert not s.custom_panel.isVisible()


def test_a_custom_theme_saved_under_a_name_is_used_and_kept_after_a_restart(qapp):
    w = make_window(qapp)
    try:
        go_tab(w, "Settings")
        s = w.settings
        assert saved_names(w) == [] and not s.use_saved_button.isEnabled() and s.theme_in_use.text() == ""
        choose_colors(w, SUNNY)
        s.save_as_button.click()                               # the name dialog is cancelled: nothing happens
        assert shown(w) == "" and ui_text() == "" and theme.LIGHT["background"] in style()
        assert w.dialogs.asked[-1] == "Name for these colours (a saved theme with the same name is replaced):"
        w.dialogs.texts.append("  Sunny  ")
        s.save_as_button.click()
        assert shown(w) == "Custom theme “Sunny” saved and in use."
        assert saved_names(w) == ["Sunny"] and s.saved_box.currentText() == "Sunny"
        assert s.theme_in_use.text() == "In use: saved theme “Sunny”"
        assert window_color(w) == SUNNY["background"] and all(v in style() for v in SUNNY.values())
        look = services.theme_view("desktop")
        assert look["saved_name"] == "Sunny" and look["saved"] == [{"name": "Sunny", "colors": SUNNY}]
    finally:
        close_window(qapp, w)

    w = make_window(qapp)                                      # the app started again
    try:
        assert window_color(w) == SUNNY["background"]
        go_tab(w, "Settings")
        s = w.settings
        assert s.theme_buttons["custom"].isChecked() and s.custom_panel.isVisible()
        assert saved_names(w) == ["Sunny"] and s.saved_box.currentText() == "Sunny"
        assert s.theme_in_use.text() == "In use: saved theme “Sunny”"
        assert s.color_buttons["background"].text() == f"Background {SUNNY['background']}"
    finally:
        close_window(qapp, w)


def test_saving_under_the_same_name_replaces_the_saved_theme(win):
    save_as(win, "Sunny", SUNNY)
    save_as(win, "sunny", {"accent": "#0057b8"})
    assert shown(win) == "Custom theme “sunny” updated and in use."
    assert saved_names(win) == ["sunny"]
    assert services.theme_view("desktop")["saved"] == [{"name": "sunny", "colors": dict(SUNNY, accent="#0057b8")}]
    assert "#0057b8" in style() and SUNNY["accent"] not in style()
    assert win.dialogs.asked[-1] == "Name for these colours (a saved theme with the same name is replaced):"


def test_using_a_saved_theme(win):
    save_as(win, "Sunny", SUNNY)
    save_as(win, "Night", NIGHT)
    assert window_color(win) == NIGHT["background"]
    choose_theme(win, "light")
    s = win.settings
    assert saved_names(win) == ["Sunny", "Night"] and s.theme_in_use.text() == ""
    assert window_color(win) == theme.LIGHT["background"] and not s.custom_panel.isVisible()

    s.theme_buttons["custom"].setChecked(True)
    s.saved_box.setCurrentIndex(s.saved_box.findData("Sunny"))
    s.use_saved_button.click()
    assert shown(win) == "Theme “Sunny” in use."
    assert window_color(win) == SUNNY["background"] and s.theme_buttons["custom"].isChecked()
    assert s.theme_colors == SUNNY and s.theme_in_use.text() == "In use: saved theme “Sunny”"
    assert services.theme_view("desktop")["saved_name"] == "Sunny"

    s.theme_button.click()                                     # applied again unchanged: still the saved theme
    assert services.theme_view("desktop")["saved_name"] == "Sunny"
    choose_theme(win, "custom", {"accent": "#0057b8"})         # changed colours are no longer the saved theme
    assert shown(win) == "Theme set to custom."
    assert services.theme_view("desktop")["saved_name"] is None and s.theme_in_use.text() == ""
    assert services.theme_view("desktop")["saved"][0] == {"name": "Sunny", "colors": SUNNY}


def test_deleting_a_saved_theme(win):
    save_as(win, "Sunny", SUNNY)
    s = win.settings
    s.delete_saved_button.click()                              # not confirmed
    assert shown(win) == "Theme not deleted: you chose not to delete it." and saved_names(win) == ["Sunny"]
    assert "Delete the saved theme Sunny?" in win.dialogs.asked[-1]
    win.dialogs.answers.append(True)
    s.delete_saved_button.click()
    assert shown(win) == "Saved theme “Sunny” deleted."
    assert saved_names(win) == [] and s.saved_box.currentText() == "none saved yet"
    assert not s.use_saved_button.isEnabled() and not s.delete_saved_button.isEnabled()
    assert s.theme_in_use.text() == ""
    assert window_color(win) == SUNNY["background"] and s.theme_buttons["custom"].isChecked()   # the colours stay
    s.use_saved_theme()
    assert shown(win) == "Theme not changed: there is no saved theme yet."


def test_a_bad_name_or_unreadable_colours_are_not_saved(win):
    old = ui_text()
    for name, error in (("   ", "Theme not saved: give the theme a name."),
                        ("x" * 41, "Theme not saved: a theme name can have at most 40 characters.")):
        save_as(win, name, SUNNY)
        assert shown(win) == error and win.message.property("kind") == "error"
    save_as(win, "Unreadable", {"background": "#ffffff", "surface": "#ffffff", "text": "#eeeeee"})
    assert shown(win).startswith("Theme not saved: the text colour is too hard to read on the background colour")
    assert ui_text() == old and saved_names(win) == [] and theme.LIGHT["background"] in style()
    s = win.settings
    assert s.theme_buttons["custom"].isChecked() and s.custom_panel.isVisible()
    assert s.theme_colors == dict(SUNNY, background="#ffffff", surface="#ffffff", text="#eeeeee")   # kept to correct


def test_the_desktop_theme_is_apart_from_the_browser_theme(win):
    choose_theme(win, "dark")
    services.save_theme("web", "custom", SUNNY)                # the browser version chooses its own
    activate(win)
    assert window_color(win) == theme.DARK["background"] and win.settings.theme_buttons["dark"].isChecked()
    assert services.theme_view("desktop")["theme"] == "dark" and services.theme_view("web")["theme"] == "custom"

    services.save_theme("web", "dark")
    choose_theme(win, "light")
    assert shown(win) == "Theme set to light." and window_color(win) == theme.LIGHT["background"]
    assert services.theme_view("web")["theme"] == "dark"

    # a theme saved in the browser is offered here too, without being used here
    services.save_custom_theme("web", "From the browser", NIGHT)
    activate(win)
    assert saved_names(win) == ["From the browser"] and window_color(win) == theme.LIGHT["background"]
    assert services.theme_view("web")["saved_name"] == "From the browser"
    assert services.theme_view("desktop")["saved_name"] is None
    win.settings.theme_buttons["custom"].setChecked(True)
    win.settings.use_saved_button.click()
    assert window_color(win) == NIGHT["background"] and services.theme_view("web")["theme"] == "custom"


def test_warning_colours_in_tables_and_text_follow_the_theme(qapp):
    data = json.loads(SETTINGS_PATH.read_text())
    data["years"][YEAR]["checked"] = False
    data["years"][YEAR]["note"] = "A note"
    SETTINGS_PATH.write_text(json.dumps(data))
    w = make_window(qapp)
    w.dialogs = ScriptedDialogs()
    try:
        go_tab(w, "Settings")
        status = w.settings.tax_table.item(column(w.settings.tax_table, 0).index(YEAR), 7)
        assert status.text() == "not checked" and status.foreground().color().name() == theme.LIGHT["warn"]
        choose_theme(w, "dark")
        assert status.foreground().color().name() == theme.DARK["warn"]
        go_tab(w, "Overview")
        html = w.overview.values.text()
        assert f"color:{theme.DARK['warn']}" in html and f"color:{theme.DARK['muted']}" in html
        assert theme.LIGHT["warn"] not in html
    finally:
        close_window(qapp, w)


# Profiles

def test_without_profiles_there_is_no_profile_list(win):
    assert not win.profile_label.isVisible() and not hasattr(win, "profile_box")
    go_tab(win, "Settings")
    assert not win.settings.profiles_group.isVisible() and win.settings.heading.text() == "Settings"


def test_the_first_start_makes_one_profile_listed_in_the_header_and_in_settings(pwin, home):
    assert (home / "profiles.json").is_file() and (home / "profiles" / "p1" / "settings.json").is_file()
    assert header_profile(pwin) == "Profile: My PFA"
    go_tab(pwin, "Settings")
    assert header_profile(pwin) == "Profile: My PFA"          # the year picker hides on Settings, the profile name not
    assert pwin.settings.profiles_group.isVisible() and pwin.settings.heading.text() == "Settings — My PFA"
    assert profile_rows(pwin) == [["My PFA", "", "", "0", "0", "in use"]]
    assert not pwin.settings.delete_profile_button.isEnabled()
    assert "rates file and the CAEN list are shared" in pwin.settings.profiles_note.text()


def test_each_profile_shows_only_its_own_firm_invoices_and_expenses(pwin, home):
    w = pwin
    save_firm(w, "Alpha PFA", "RO14399840")
    assert shown(w) == "Settings saved." and "Alpha PFA" in w.firm_line.text()
    add_invoice(w, "Acme")

    w.dialogs.texts.append("Beta")
    go_tab(w, "Settings")
    w.settings.new_profile_button.click()
    assert shown(w) == "Profile Beta created — fill in its firm below."
    assert header_profile(w) == "Profile: Beta"
    assert w.settings.heading.text() == "Settings — Beta"
    assert w.settings.inputs["name"].text() == "" and w.settings.inputs["cui"].text() == ""
    assert w.firm_line.text() == ""
    assert [r[0] for r in profile_rows(w)] == ["My PFA", "Beta"] and profile_rows(w)[1][5] == "in use"
    assert profile_rows(w)[0][1:5] == ["Alpha PFA", "RO14399840", "1", "0"]
    go_tab(w, "Income")
    assert w.income.table.rowCount() == 0 and w.year == YEAR
    add_expense(w, "Laptop")
    save_firm(w, "Beta PFA")
    assert shown(w) == "Settings saved."

    switch_in_settings(w, "My PFA")
    assert shown(w) == "Now showing the profile My PFA."
    assert "Alpha PFA" in w.firm_line.text()
    go_tab(w, "Income")
    assert column(w.income.table, 2) == ["Acme"]
    go_tab(w, "Expenses")
    assert w.expenses.table.rowCount() == 0
    go_tab(w, "Settings")
    assert w.settings.inputs["name"].text() == "Alpha PFA"

    switch_in_settings(w, "Beta")
    assert "Beta PFA" in w.firm_line.text()
    go_tab(w, "Expenses")
    assert column(w.expenses.table, 1) == ["Laptop"]
    go_tab(w, "Income")
    assert w.income.table.rowCount() == 0
    assert (home / "profiles" / "p1" / "data" / "pfa.sqlite").is_file() and (home / "profiles" / "p2" / "data" / "pfa.sqlite").is_file()


def test_switching_profile_clears_what_was_typed_and_resets_the_year(pwin):
    w = pwin
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Income")
    w.income.client.setText("typed for Beta")
    switch_in_settings(w, "My PFA")
    assert w.income.client.text() == "" and w.year == YEAR


def test_a_firm_belongs_to_one_profile_only(pwin):
    w = pwin
    save_firm(w, "Alpha PFA", "RO14399840")
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    save_firm(w, "Copy", "14399840")
    assert "already belongs to the profile My PFA" in shown(w)
    assert w.settings.inputs["name"].text() == "Copy"          # the form is kept to be corrected


def test_switching_profile_keeps_the_desktop_theme(pwin, home):
    w = pwin
    choose_theme(w, "dark")
    assert window_color(w) == theme.DARK["background"]
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Settings")
    assert w.settings.heading.text() == "Settings — Beta"
    assert window_color(w) == theme.DARK["background"] and w.settings.theme_buttons["dark"].isChecked()
    switch_in_settings(w, "My PFA")
    assert window_color(w) == theme.DARK["background"]
    choose_theme(w, "light")
    switch_in_settings(w, "Beta")
    assert window_color(w) == theme.LIGHT["background"] and w.settings.theme_buttons["light"].isChecked()
    # kept for the app, next to profiles.json, not in a profile
    assert json.loads((home / "ui-settings.json").read_text())["apps"]["desktop"]["theme"] == "light"


def test_the_profiles_are_the_first_group_on_the_settings_page(pwin):
    w = pwin
    w.resize(1024, 740)
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Settings")
    s = w.settings
    inner = s.widget()
    groups = sorted((g for g in inner.findChildren(QtWidgets.QGroupBox) if g.isVisible()), key=lambda g: g.y())
    assert groups[0] is s.profiles_group and groups[1] is s.firm_group
    assert s.heading.geometry().bottom() < s.profiles_group.y()
    assert s.profiles_group.geometry().bottom() < s.firm_group.y()
    # the profiles, their buttons and the firm's first fields are all in view at once, without scrolling
    assert s.verticalScrollBar().value() == 0
    view_height = s.viewport().height()
    for widget in (s.profiles_table, s.new_profile_button, s.delete_profile_button, s.inputs["name"], s.inputs["cui"]):
        assert widget.mapTo(inner, widget.rect().bottomLeft()).y() < view_height, widget
    assert [r[0] for r in profile_rows(w)] == ["My PFA", "Beta"]


def test_renaming_the_profile_in_use(pwin):
    w = pwin
    go_tab(w, "Settings")
    w.settings.rename_profile_button.click()                  # cancelled
    assert header_profile(w) == "Profile: My PFA" and profile_names(w) == ["My PFA"] and shown(w) == ""
    w.dialogs.texts.append("Popescu Ion")
    w.settings.rename_profile_button.click()
    assert w.dialogs.asked[-1] == "New name for the profile My PFA:"
    assert shown(w) == "Profile renamed."
    assert header_profile(w) == "Profile: Popescu Ion" and profile_rows(w)[0][0] == "Popescu Ion"
    assert w.settings.heading.text() == "Settings — Popescu Ion"
    w.dialogs.texts.append("Other")
    w.settings.new_profile()
    w.dialogs.texts.append("popescu ion")
    w.settings.rename_profile_button.click()
    assert shown(w) == "Profile not renamed: there is already a profile named popescu ion."
    assert profile_names(w) == ["Popescu Ion", "Other"] and header_profile(w) == "Profile: Other"


def test_deleting_a_profile_asks_first_and_never_deletes_the_last(pwin, home):
    w = pwin
    go_tab(w, "Settings")
    s = w.settings
    s.profiles_table.selectRow(0)
    w.dialogs.answers.append(True)
    s.delete_profile()                                        # the button is disabled; the core refuses too
    assert shown(w) == "Profile not deleted: the only profile cannot be deleted."
    assert profile_names(w) == ["My PFA"]

    add_invoice(w, "Acme")
    w.dialogs.texts.append("Beta")
    s.new_profile()
    go_tab(w, "Settings")
    assert s.delete_profile_button.isEnabled()
    s.profiles_table.clearSelection()
    s.delete_profile_button.click()
    assert shown(w) == "Profile not deleted: choose it in the table first."

    s.profiles_table.selectRow(0)
    w.dialogs.answers.append(False)
    s.delete_profile_button.click()
    assert "Delete the profile My PFA with its settings, 1 invoice(s), 0 expense(s)" in w.dialogs.asked[-1]
    assert shown(w) == "Profile not deleted: you chose not to delete it." and len(profile_names(w)) == 2

    s.profiles_table.selectRow(0)
    w.dialogs.answers.append(True)
    s.delete_profile_button.click()
    assert shown(w).startswith("Profile My PFA deleted. A backup of it was saved in ")
    assert profile_names(w) == ["Beta"] and header_profile(w) == "Profile: Beta"
    assert [p.name for p in (home / "deleted-profiles").iterdir()][0].endswith(".zip")
    assert not (home / "profiles" / "p1").exists()
    assert not s.delete_profile_button.isEnabled()


def test_deleting_the_profile_in_use_shows_another_one(pwin):
    w = pwin
    save_firm(w, "Alpha PFA")
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Settings")
    w.settings.profiles_table.selectRow(1)                    # Beta, in use
    w.dialogs.answers.append(True)
    w.settings.delete_profile_button.click()
    assert shown(w).startswith("Profile Beta deleted.")
    assert profile_names(w) == ["My PFA"] and header_profile(w) == "Profile: My PFA"
    assert w.settings.inputs["name"].text() == "Alpha PFA"
    assert "Alpha PFA" in w.firm_line.text() and services.profiles_view()["current"] == "p1"


def test_the_window_keeps_its_profile_when_the_browser_switches(pwin):
    w = pwin
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    switch_in_settings(w, "My PFA")
    # the browser (another request, another context) switches to Beta: the last used profile changes
    contextvars.Context().run(services.switch_profile, "p2")
    assert json.loads((Path(os.environ["PFA_HOME"]) / "profiles.json").read_text())["last_used"] == "p2"
    w.refresh()
    assert header_profile(w) == "Profile: My PFA" and services.profiles_view()["current"] == "p1"


def test_the_header_only_names_the_profile_and_switching_is_in_settings(pwin):
    w = pwin
    head = w.centralWidget().layout().itemAt(0).layout()
    header_widgets = [head.itemAt(i).widget() for i in range(head.count()) if head.itemAt(i).widget()]
    assert [x for x in header_widgets if isinstance(x, QtWidgets.QComboBox)] == [w.year_box]     # no profile list
    assert header_profile(w) == "Profile: My PFA"
    go_tab(w, "Settings")
    s = w.settings
    assert s.switch_profile_button.text() == "Switch to this profile"
    assert "Switch to this profile" in s.profiles_note.text()
    assert s.profiles_group.findChildren(QtWidgets.QCheckBox) == []     # deleting asks in a dialog, no tick box


def test_switching_with_the_settings_button(pwin):
    w = pwin
    save_firm(w, "Alpha PFA")
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Settings")
    s = w.settings
    assert header_profile(w) == "Profile: Beta"
    s.profiles_table.clearSelection()
    assert not s.switch_profile_button.isEnabled()             # nothing chosen
    s.switch_profile()
    assert shown(w) == "Profile not changed: choose it in the table first."
    s.profiles_table.selectRow(1)                              # Beta, in use
    assert not s.switch_profile_button.isEnabled()
    s.profiles_table.selectRow(0)
    assert s.switch_profile_button.isEnabled()
    s.switch_profile_button.click()
    assert shown(w) == "Now showing the profile My PFA."
    assert header_profile(w) == "Profile: My PFA" and services.profiles_view()["current"] == "p1"
    assert "Alpha PFA" in w.firm_line.text() and s.inputs["name"].text() == "Alpha PFA"
    assert profile_rows(w)[0][5] == "in use" and profile_rows(w)[1][5] == ""
    assert w.settings.heading.text() == "Settings — My PFA"


def test_switching_by_double_clicking_a_row(pwin):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w = pwin
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    go_tab(w, "Settings")
    t = w.settings.profiles_table

    def double_click(row):
        spot = t.visualItemRect(t.item(row, 0)).center()
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot)
        QTest.mouseDClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot)
        QtWidgets.QApplication.processEvents()

    double_click(0)
    assert shown(w) == "Now showing the profile My PFA." and header_profile(w) == "Profile: My PFA"
    assert services.profiles_view()["current"] == "p1"
    double_click(0)                                            # the profile in use: nothing changes
    assert services.profiles_view()["current"] == "p1" and header_profile(w) == "Profile: My PFA"


def test_switching_resets_the_year_and_ends_an_edit(pwin):
    w = pwin
    add_invoice(w, "Acme")
    add_expense(w, "Laptop")
    w.income.edit(1)
    w.expenses.edit(1)
    w.income.client.setText("typed")
    assert w.income.editing == 1 and w.expenses.editing == 1
    w.dialogs.texts.append("Beta")
    w.settings.new_profile()
    assert w.income.editing is None and w.expenses.editing is None
    assert w.income.client.text() == "" and w.income.form.title() == "Add an invoice by hand"
    go_tab(w, "Expenses")
    assert w.expenses.form.title() == "Add a deductible expense" and w.expenses.description.text() == ""
    switch_in_settings(w, "My PFA")
    assert w.year == YEAR and w.income.editing is None
