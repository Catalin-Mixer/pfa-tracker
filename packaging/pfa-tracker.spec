# PyInstaller spec for the PFA tax tracker: one executable that opens the desktop window,
# or the browser version with --web.
#
#   Linux:   sh packaging/build_linux.sh        -> PFA-Tax-Tracker   (copied to the project folder)
#   Windows: packaging\build_windows.bat        -> dist\PFA-Tax-Tracker.exe
#
# Run from any folder: python -m PyInstaller --noconfirm --clean packaging/pfa-tracker.spec
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Program files (read-only, unpacked next to the code) and the default data files that are
# copied into the home folder on first start when it lacks them (see tracker/config.py).
datas = [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "samples"), "samples"),
    (os.path.join(ROOT, "fonts"), "fonts"),          # DejaVu Sans for the invoice PDFs (ă, â, î, ș, ț)
    # a clean settings.json (no firm details, no salaried job), not the user's own file
    (os.path.join(SPECPATH, "defaults", "settings.json"), "."),
    (os.path.join(ROOT, "rates.csv"), "."),
    (os.path.join(ROOT, "caen_codes.xlsx"), "."),
]

binaries = []
if IS_LINUX:
    # Stand-in for libxcb-cursor.so.0, which Qt needs on Linux; launcher.py loads it when the
    # system has no libxcb-cursor. Not used on Windows.
    binaries.append((os.path.join(ROOT, "desktop_support", "libxcb-cursor.so.0"), "desktop_support"))

hiddenimports = (
    collect_submodules("tracker")           # includes tracker.desktop, imported lazily by launcher.py
    + collect_submodules("uvicorn")         # uvicorn picks its loop/protocol modules at run time
    + collect_submodules("reportlab")       # the invoice PDFs; reportlab loads font data modules at run time
    + ["python_multipart", "multipart", "anyio._backends._asyncio",
       "jinja2", "openpyxl", "pypdf", "PIL.Image", "defusedxml", "defusedxml.ElementTree"]
)

# Qt and other modules the tracker does not use; leaving them out keeps the executable smaller.
excludes = [
    "tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy", "pandas",
    # optional extras pulled in by other installed packages, not used by the tracker
    "rich", "pygments", "markdown_it", "mdurl", "lxml", "yaml", "bs4", "soupsieve", "dotenv",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickTest", "PySide6.QtQmlFeatures", "PySide6.QtLabsStyleKit",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtConcurrent",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
]

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PFA-Tax-Tracker",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=not IS_WINDOWS,   # windowed on Windows (no black console box); a console on Linux
)
