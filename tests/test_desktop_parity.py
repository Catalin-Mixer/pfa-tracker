"""The desktop app behaves like the browser version in the details a parity check found different at first:
error colours, escaped text, CAEN exclusion details, typed settings kept, messages shown once."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402

from conftest import YEAR, write_caen  # noqa: E402
from tracker import services  # noqa: E402
from tracker.config import CAEN_PATH  # noqa: E402


class ScriptedDialogs:
    def __init__(self):
        self.files, self.answers = [], []

    def open_file(self, title, file_filter):
        return self.files.pop(0) if self.files else None

    def confirm(self, title, text):
        return self.answers.pop(0) if self.answers else False

    def open_path(self, path):
        pass


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


def background_is_red(widget) -> bool:
    """The browser shows errors on a red background (msg err) and successes on green."""
    img = widget.grab().toImage()
    c = img.pixelColor(img.width() - 6, img.height() // 2)
    return c.red() > c.green()


def test_an_error_message_is_shown_in_the_error_style_not_the_success_style(win):
    go_tab(win, "Settings")
    win.settings.inputs["email"].setText("bad@")
    win.settings.save()
    QtWidgets.QApplication.processEvents()
    assert win.message.text().startswith("Not saved")
    assert background_is_red(win.message)


def test_an_import_with_problem_rows_is_shown_in_the_error_style(win, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("date,client,amount,currency\n2026-01-05,A,abc,RON\n", encoding="utf-8")
    go_tab(win, "Income")
    win.dialogs.files = [p]
    win.income.import_csv()
    QtWidgets.QApplication.processEvents()
    assert "row(s) with problems" in win.income.import_result.text()
    assert background_is_red(win.income.import_result)


def test_import_problem_rows_show_the_row_text_as_written(win, tmp_path):
    """The browser escapes the row; the desktop label is rich text, so '<Ltd>' disappears."""
    from PySide6.QtGui import QTextDocument
    p = tmp_path / "bad.csv"
    p.write_text("date,client,amount,currency\n2026-01-05,Tom & Jerry <Ltd>,abc,RON\n", encoding="utf-8")
    go_tab(win, "Income")
    win.dialogs.files = [p]
    win.income.import_csv()
    doc = QTextDocument()
    doc.setHtml(win.income.import_result.text())
    assert "Tom & Jerry <Ltd>" in doc.toPlainText()


def test_the_caen_exclusion_shows_its_years_and_legal_basis_like_the_browser(win):
    write_caen(CAEN_PATH, excluded={"6210": (2020, 2030, "Test basis art. 1")})
    go_tab(win, "Settings")
    assert "(2020–2030) — Test basis art. 1" in win.settings.caen_note.text()
    assert "(2020–2030) — Test basis art. 1" in win.settings.caen_info.text()


def test_unsaved_settings_are_not_wiped_when_the_window_is_activated_again(win):
    go_tab(win, "Settings")
    win.settings.inputs["name"].setText("Typed, not saved yet")
    QCoreApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    assert win.settings.inputs["name"].text() == "Typed, not saved yet"


def test_a_message_is_shown_once_like_the_browser(win):
    go_tab(win, "Expenses")
    win.expenses.amount.setText("10")
    win.expenses.description.setText("Pen")
    win.expenses.add()
    assert win.message.isVisible() and win.message.text() == "Expense added."
    go_tab(win, "Overview")
    assert not win.message.isVisible()
