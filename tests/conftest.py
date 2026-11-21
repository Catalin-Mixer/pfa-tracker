"""Shared fixtures: every test runs against scratch files in a temporary folder, never the real data."""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="pfa-tests-"))
os.environ["PFA_DB"] = str(SCRATCH / "data" / "test.sqlite")
os.environ["PFA_SETTINGS"] = str(SCRATCH / "settings.json")
os.environ["PFA_RATES"] = str(SCRATCH / "rates.csv")
os.environ["PFA_CAEN"] = str(SCRATCH / "caen_codes.xlsx")
sys.path.insert(0, str(ROOT))

from tracker import db  # noqa: E402  (after the environment is set)
from tracker.config import ATTACHMENTS_DIR  # noqa: E402

TODAY = date.today()
YEAR = str(TODAY.year)
LAST_YEAR = str(TODAY.year - 1)

# Round numbers so that expected results can be worked out by hand:
#   12 wages = 48.000, 24 wages = 96.000, CASS floor = 24.000, ceiling = 240.000 (60) or 288.000 (72)
YEAR_VALUES = {"minimum_wage": 4000, "cas_rate": 0.25, "cas_low_wages": 12, "cas_high_wages": 24,
               "cass_rate": 0.10, "cass_floor_wages": 6, "cass_ceiling_wages": 60, "income_tax_rate": 0.10,
               "has_salary": False, "checked": True, "note": ""}
DEFAULT_SETTINGS = {
    "firm": {"name": "Test PFA", "cui": "RO14399840", "caen_code": "6210", "caen_revision": "3"},
    "years": {LAST_YEAR: dict(YEAR_VALUES), YEAR: dict(YEAR_VALUES, cass_ceiling_wages=72)},
}

# Rates: EUR 5, USD 4 on every weekday; one odd Friday so the weekend fallback is visible.
ODD_FRIDAY = date(TODAY.year - 1, 3, 1)
while ODD_FRIDAY.weekday() != 4:
    ODD_FRIDAY += timedelta(days=1)
ODD_SATURDAY = ODD_FRIDAY + timedelta(days=1)


def write_rates(path: Path) -> None:
    lines = ["date,EUR,USD"]
    d = date(TODAY.year - 2, 1, 1)
    while d <= TODAY:
        if d.weekday() < 5:
            eur = "4.5000" if d == ODD_FRIDAY else "5.0000"
            lines.append(f"{d.isoformat()},{eur},4.0000")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_caen(path: Path, excluded: dict | None = None) -> None:
    """A small CAEN workbook; excluded = {code: (from_year, to_year, basis)} marks codes as excluded."""
    from openpyxl import Workbook
    excluded = excluded or {}
    wb = Workbook()
    head = ["Code", "Activity", "Activity (English)", "Excluded from income tax (yes/no)", "Valid from (year)",
            "Valid to (year)", "Legal basis / source", "Notes"]
    ws = wb.active
    ws.title = "CAEN Rev.3"
    ws.append(head)
    for code, title in (("6210", "Activități de programare informatică"), ("6920", "Activități de contabilitate"),
                        ("0111", "Cultivarea cerealelor")):
        frm, to, basis = excluded.get(code, ("", "", ""))
        ws.append([code, title, title, "yes" if code in excluded else "no", frm, to, basis, ""])
    ws2 = wb.create_sheet("CAEN Rev.2")
    ws2.append(["Code", "Activity", "Rev.3 code(s)"] + head[3:])
    ws2.append(["6201", "Computer programming activities", "6210", "no", "", "", "", ""])
    wb.save(path)


@pytest.fixture(autouse=True)
def fresh_files():
    """Before each test: empty database, default settings, rates and CAEN list, no attachments."""
    import json
    Path(os.environ["PFA_DB"]).unlink(missing_ok=True)
    db.init_db()
    shutil.rmtree(ATTACHMENTS_DIR, ignore_errors=True)
    shutil.rmtree(Path(os.environ["PFA_DB"]).parent / "backups", ignore_errors=True)
    (Path(os.environ["PFA_SETTINGS"]).parent / "ui-settings.json").unlink(missing_ok=True)
    Path(os.environ["PFA_SETTINGS"]).write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding="utf-8")
    write_rates(Path(os.environ["PFA_RATES"]))
    write_caen(Path(os.environ["PFA_CAEN"]))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app import app
    return TestClient(app)


@pytest.fixture
def settings_path() -> Path:
    return Path(os.environ["PFA_SETTINGS"])


@pytest.fixture
def rates_path() -> Path:
    return Path(os.environ["PFA_RATES"])


@pytest.fixture
def caen_path() -> Path:
    return Path(os.environ["PFA_CAEN"])


def csv_upload(text: str, name: str = "invoices.csv"):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


def sample_csv(year: str = LAST_YEAR) -> str:
    return (
        "date,client,number,amount,currency\n"
        f"{year}-01-15,Nordwind GmbH,A-1,1000.00,EUR\n"      # 5.000 RON
        f"{year}-02-14,Blue Harbor Inc,A-2,250.00,USD\n"      # 1.000 RON
        f"{year}-03-03,Carpathia SRL,A-3,700.00,RON\n"        # 700 RON
    )
