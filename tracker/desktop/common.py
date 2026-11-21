"""Small building blocks shared by the desktop tabs."""
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QColorDialog, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import formatting, services

money = formatting.money

# The colours in use (a palette from tracker/theme.py). Widgets take them from the style sheet; the few
# colours set in code (rich text, table cells) read them here, so they follow the theme too.
PALETTE: dict = dict(services.LIGHT_PALETTE)


def luminance(color: str) -> float:
    """Relative luminance (WCAG) of a #rrggbb colour."""
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    q = QColor(color)
    return 0.2126 * channel(q.red()) + 0.7152 * channel(q.green()) + 0.0722 * channel(q.blue())


def readable_on(background: str, p: dict) -> str:
    """The palette's text or surface colour, whichever reads better on the given colour."""
    def contrast(a: str, b: str) -> float:
        la, lb = sorted((luminance(a), luminance(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)
    return max((p["text"], p["surface"]), key=lambda c: contrast(c, background))


def mix(a: str, b: str, t: float) -> str:
    """Colour a moved towards colour b by t (0 to 1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(*(round(x + (y - x) * t) for x, y in ((ca.red(), cb.red()), (ca.green(), cb.green()),
                                                          (ca.blue(), cb.blue())))).name()


def check_mark(color: str) -> str:
    """A tick in the given colour as a small PNG (for ticked check boxes), made once per colour; '' if it cannot be."""
    folder = Path(tempfile.gettempdir()) / "pfa-tracker-look"
    path = folder / f"tick-{color.lstrip('#')}.png"
    if not path.is_file():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            image = QImage(32, 32, QImage.Format.Format_ARGB32)
            image.fill(0)                      # transparent
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(color), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline([QPointF(7, 16.5), QPointF(13.5, 23), QPointF(25, 9)])
            painter.end()
            if not image.save(str(path)):
                return ""
        except OSError:
            return ""
    return path.as_posix()


def build_style(p: dict, tick: str = "") -> str:
    """The window's style sheet for a palette (light, dark or custom colours); tick is the image of a ticked box."""
    on_selection = readable_on(p["selection"], p)
    disabled = mix(p["accent"], p["surface"], 0.55)
    box_border = mix(p["input_border"], p["muted"], 0.5)
    return f"""
QWidget {{ font-size: 10.5pt; color: {p['text']}; }}
QMainWindow, QDialog, QScrollArea > QWidget > QWidget, QTabWidget > QStackedWidget > QWidget {{ background: {p['background']}; }}
QTabWidget::pane {{ border: 1px solid {p['line']}; background: {p['background']}; }}
QLabel#title {{ font-size: 15pt; font-weight: 600; }}
QLabel#firm, QLabel.muted, QLabel#muted {{ color: {p['muted']}; }}
QLabel#pageTitle {{ font-size: 14pt; font-weight: 600; }}
QLabel#profileName {{ font-weight: 600; }}
QFrame#card {{ background: {p['surface']}; border: 1px solid {p['line']}; border-radius: 8px; }}
QFrame#card[accent="true"] {{ background: {p['accent_soft']}; border-color: {p['accent']}; }}
QLabel#cardValue {{ font-size: 17pt; font-weight: 600; }}
QFrame#card[accent="true"] QLabel#cardValue {{ color: {p['accent']}; }}
QLabel#cardLabel, QLabel#cardSub {{ color: {p['muted']}; font-size: 9pt; }}
QGroupBox {{ background: {p['surface']}; border: 1px solid {p['line']}; border-radius: 8px; margin-top: 14px; padding: 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p['text']}; }}
QPushButton {{ background: {p['accent']}; color: {p['accent_text']}; border: 0; border-radius: 5px; padding: 6px 14px; }}
QPushButton:disabled {{ background: {disabled}; color: {p['accent_text']}; }}
QPushButton#link {{ background: {p['surface']}; color: {p['bad']}; border: 1px solid {p['bad']}; border-radius: 5px; padding: 2px 10px; }}
QPushButton#plain {{ background: {p['surface']}; color: {p['accent']}; border: 1px solid {p['accent']}; border-radius: 5px; padding: 2px 10px; }}
QPushButton#link:hover {{ background: {p['bad']}; color: {p['surface']}; }}
QPushButton#plain:hover {{ background: {p['accent']}; color: {p['accent_text']}; }}
QPushButton#link:disabled, QPushButton#plain:disabled {{ color: {p['muted']}; }}
QPushButton#swatch {{ border: 1px solid {p['input_border']}; border-radius: 4px; padding: 0; }}
QLabel#message {{ background: {p['ok_bg']}; border: 1px solid {p['ok_border']}; border-radius: 6px; padding: 8px; color: {p['text']}; }}
QLabel#message[kind="error"] {{ background: {p['err_bg']}; border: 1px solid {p['err_border']}; color: {p['bad']}; }}
QLabel[warn="true"], QCheckBox[warn="true"] {{ color: {p['warn']}; }}
QLabel#error {{ background: {p['err_bg']}; border: 1px solid {p['err_border']}; border-radius: 6px; padding: 8px; color: {p['bad']}; }}
QLabel#warn {{ color: {p['warn']}; }}
QLabel#tag {{ background: {p['accent_soft']}; color: {p['accent']}; border: 1px solid {p['accent']}; border-radius: 7px;
    padding: 0 6px; font-size: 8.5pt; }}
QLineEdit, QComboBox, QDateEdit, QAbstractSpinBox {{ background: {p['surface']}; color: {p['text']}; border: 1px solid {p['input_border']};
    border-radius: 4px; padding: 3px 6px; selection-background-color: {p['selection']}; selection-color: {on_selection}; }}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{ background: {p['code_bg']}; color: {p['muted']}; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {p['accent']}; }}
QAbstractItemView {{ background: {p['surface']}; color: {p['text']}; selection-background-color: {p['selection']};
    selection-color: {on_selection}; outline: 0; }}
QTableWidget {{ background: {p['surface']}; gridline-color: {p['line']}; border: 1px solid {p['line']}; }}
QHeaderView::section {{ background: {p['surface_alt']}; color: {p['muted']}; border: 0; border-bottom: 1px solid {p['line']}; padding: 4px; font-weight: 600; }}
QTableCornerButton::section {{ background: {p['surface_alt']}; border: 0; }}
QTabBar::tab {{ padding: 8px 18px; background: {p['background']}; color: {p['muted']}; border: 0; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['accent']}; font-weight: 600; border-bottom: 2px solid {p['accent']}; }}
QToolTip {{ background: {p['surface']}; color: {p['text']}; border: 1px solid {p['line']}; padding: 4px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {box_border}; background: {p['surface']}; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p['accent']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']};{f' image: url({tick});' if tick else ''} }}
QRadioButton::indicator:checked {{ border-color: {p['accent']}; background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
    stop: 0 {p['accent']}, stop: 0.5 {p['accent']}, stop: 0.6 {p['surface']}, stop: 1 {p['surface']}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {p['code_bg']}; border-color: {p['line']}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {p['background']}; border: 0; }}
QScrollBar:vertical {{ width: 12px; }}
QScrollBar:horizontal {{ height: 12px; }}
QScrollBar::handle {{ background: {p['input_border']}; border-radius: 5px; min-height: 24px; min-width: 24px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {p['surface_alt']}; }}
QCalendarWidget QToolButton {{ color: {p['text']}; background: transparent; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {p['muted']}; }}
"""


def qt_palette(p: dict) -> QPalette:
    """The same colours for what the style sheet does not reach (check boxes, dialogs, menus)."""
    pal = QPalette()
    roles = {QPalette.ColorRole.Window: "background", QPalette.ColorRole.WindowText: "text",
             QPalette.ColorRole.Base: "surface", QPalette.ColorRole.AlternateBase: "surface_alt",
             QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "surface",
             QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.ToolTipBase: "surface",
             QPalette.ColorRole.ToolTipText: "text", QPalette.ColorRole.PlaceholderText: "muted",
             QPalette.ColorRole.Highlight: "selection", QPalette.ColorRole.Link: "accent",
             QPalette.ColorRole.Mid: "line", QPalette.ColorRole.Midlight: "line", QPalette.ColorRole.Dark: "input_border"}
    for role, key in roles.items():
        pal.setColor(role, QColor(p[key]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(readable_on(p["selection"], p)))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(p["bad"]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p["muted"]))
    return pal


WARN_ROLE = Qt.ItemDataRole.UserRole + 7     # marks table cells shown in the warning colour


def apply_palette(p: dict) -> None:
    """Shows the whole application in these colours (table cells in the warning colour included)."""
    PALETTE.clear()
    PALETTE.update(p)
    app = QApplication.instance()
    style = build_style(p, check_mark(p["accent_text"]))
    if app is None or app.styleSheet() == style:      # restyling every widget is slow: only when it changes
        return
    app.setPalette(qt_palette(p))
    app.setStyleSheet(style)
    for widget in app.allWidgets():
        if isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for col in range(widget.columnCount()):
                    item = widget.item(row, col)
                    if item is not None and item.data(WARN_ROLE):
                        item.setForeground(QColor(p["warn"]))


STYLE = build_style(services.LIGHT_PALETTE)


class Dialogs:
    """File pickers, confirmations and opening files. Tests replace this object with a scripted one."""

    def __init__(self, parent: QWidget):
        self.parent = parent

    def open_file(self, title: str, file_filter: str) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(self.parent, title, str(Path.home()), file_filter)
        return Path(path) if path else None

    def save_file(self, title: str, suggested: str, file_filter: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self.parent, title, str(Path.home() / suggested), file_filter)
        return Path(path) if path else None

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self.parent, title, text) == QMessageBox.StandardButton.Yes

    def open_path(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def ask_text(self, title: str, text: str, default: str = "") -> str | None:
        value, ok = QInputDialog.getText(self.parent, title, text, text=default)
        return value if ok else None

    def pick_color(self, title: str, initial: str) -> str | None:
        color = QColorDialog.getColor(QColor(initial), self.parent, title)
        return color.name() if color.isValid() else None


def label(text: str = "", name: str | None = None, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    if wrap:   # let wrapped text take the height it needs instead of being clipped
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        lbl.setObjectName(name)
    return lbl


class Card(QFrame):
    def __init__(self, title: str, accent: bool = False):
        super().__init__()
        self.setObjectName("card")
        self.setProperty("accent", "true" if accent else "false")
        box = QVBoxLayout(self)
        self.title = label(title, "cardLabel")
        self.value = label("", "cardValue")
        self.sub = label("", "cardSub")
        for w in (self.title, self.value, self.sub):
            box.addWidget(w)

    def set(self, value: str, sub: str = "", title: str | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        if title is not None:
            self.title.setText(title)


def table(headers: list[str], numeric: set[int] = frozenset(), stretch: int | None = None,
          all_equal: bool = False) -> QTableWidget:
    """A read-only table; 'stretch' is the column that takes the spare width (wrapping its text)."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setWordWrap(True)
    header = t.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch if all_equal else QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(False)
    if stretch is not None:
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


def fill_row(t: QTableWidget, row: int, values: list, numeric: set[int] = frozenset(), tips: dict | None = None,
             warn: set[int] = frozenset()) -> None:
    for col, value in enumerate(values):
        if isinstance(value, QWidget):
            t.setCellWidget(row, col, value)
            continue
        if t.cellWidget(row, col) is not None:   # e.g. an Open button left from the row shown here before
            t.removeCellWidget(row, col)
        item = QTableWidgetItem("" if value is None else str(value))
        if col in numeric:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if tips and col in tips:
            item.setToolTip(tips[col])
        if col in warn:
            item.setForeground(QColor(PALETTE["warn"]))
            item.setData(WARN_ROLE, True)
        t.setItem(row, col, item)


def qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def text_of(d: QDate) -> str:
    return f"{d.year():04d}-{d.month():02d}-{d.day():02d}"


def small_button(text: str, name: str = "plain") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def row_buttons(*buttons: QPushButton) -> QWidget:
    """Several small buttons side by side in one table cell (e.g. Edit and delete)."""
    cell = QWidget()
    row = QHBoxLayout(cell)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    return cell


def hbox(*widgets, stretch: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    for w in widgets:
        row.addWidget(w) if isinstance(w, QWidget) else row.addLayout(w)
    if stretch:
        row.addStretch(1)
    return row


def set_kind(lbl: QLabel, kind: str) -> None:
    """Switches a message label between the green ("message") and red ("error") look."""
    lbl.setProperty("kind", kind)
    lbl.style().unpolish(lbl)
    lbl.style().polish(lbl)


def set_warn(lbl: QLabel, warn: bool) -> None:
    """Amber text, like the browser's warnings."""
    lbl.setProperty("warn", "true" if warn else "false")
    lbl.style().unpolish(lbl)
    lbl.style().polish(lbl)


LEGEND = ("Rate = RON per 1 unit of currency. A date in amber under “Rate of” means there was no rate for that day "
          "(weekend or holiday), so the latest earlier rate was used. “Open” shows the proof.")
