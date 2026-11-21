"""Data stays on disk between runs, and a backup zip carries a complete set (settings, invoices,
expenses, proofs, rates, CAEN list) to another computer that has the tracker; bad backups are refused."""
import io
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from conftest import LAST_YEAR, YEAR, csv_upload, sample_csv
from files import PDF, PNG
from tracker import backup, db, settings
from tracker.config import ATTACHMENTS_DIR, CAEN_PATH, DB_PATH, RATES_PATH, SETTINGS_PATH


def fill(client):
    """A small but complete set of data: settings, invoices (one with a proof), an expense with a proof."""
    client.post("/settings", data={"name": "Popescu Ion PFA", "cui": "RO14399840", "caen_code": "6210", "caen_revision": "3",
                                   "trade_register": f"F40/1234/{int(YEAR) - 3}", "iban": "RO49AAAA1B31007593840000",
                                   "salaried_now": "1", "salaried_years": [LAST_YEAR]})
    client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "Acme", "number": "A-7", "amount": "100",
                                     "currency": "RON"}, files={"attachment": ("scan.pdf", PDF, "application/pdf")})
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "250", "currency": "RON"},
                files={"attachment": ("bon.png", PNG, "image/png")})


def snapshot() -> dict:
    """Everything that should survive: settings, rows, proofs (with their bytes)."""
    with db.connect() as con:
        rows = {t: [tuple(r)[1:] for r in con.execute(f"SELECT * FROM {t} ORDER BY id")] for t in ("invoices", "expenses")}
    proofs = {str(p.relative_to(ATTACHMENTS_DIR)): p.read_bytes() for p in sorted(ATTACHMENTS_DIR.rglob("*")) if p.is_file()}
    return {"settings": json.loads(SETTINGS_PATH.read_text()), "rows": rows, "proofs": proofs,
            "rates": RATES_PATH.read_bytes(), "caen": CAEN_PATH.read_bytes()}


def fresh_app():
    """A brand-new app instance and client, as after closing and reopening the tracker."""
    import importlib
    from fastapi.testclient import TestClient
    import tracker.web
    importlib.reload(tracker.web)
    return TestClient(tracker.web.app)


# Persistence

def test_everything_is_on_disk_and_still_there_after_a_restart(client):
    fill(client)
    before = snapshot()
    assert before["rows"]["invoices"] and before["rows"]["expenses"] and len(before["proofs"]) == 2
    assert before["settings"]["firm"]["name"] == "Popescu Ion PFA"
    reopened = fresh_app()
    assert snapshot() == before
    html = reopened.get("/").text
    assert "Popescu Ion PFA · CUI RO14399840 · CAEN 6210" in html and "4 invoice(s)" in html
    assert "Acme" in reopened.get("/income").text and "Desk" in reopened.get("/expenses").text
    assert 'value="RO49 AAAA 1B31 0075 9384 0000"' in reopened.get("/settings").text
    assert reopened.get("/income/4/attachment").content == PDF and reopened.get("/expenses/1/attachment").content == PNG


def test_settings_and_data_live_together_in_the_data_files(client):
    fill(client)
    assert SETTINGS_PATH.is_file() and DB_PATH.is_file()
    with sqlite3.connect(DB_PATH) as con:
        assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 4
    assert settings.firm()["cui"] == "RO14399840" and settings.has_salary(YEAR) and settings.has_salary(LAST_YEAR)


def test_the_year_selection_survives_a_restart_too(client):
    fill(client)
    client.post("/year", data={"year": LAST_YEAR, "page": "overview"})
    reopened = fresh_app()
    reopened.cookies = client.cookies
    assert f"Overview {LAST_YEAR}" in reopened.get("/").text


# Backup

def test_backup_is_a_zip_with_everything_and_a_manifest(client):
    fill(client)
    r = client.get("/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="pfa-backup-RO14399840-')
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"manifest.json", "pfa.sqlite", "settings.json", "rates.csv", "caen_codes.xlsx",
                                 "attachments/invoices/A-7_Acme.pdf", "attachments/expenses/1_bon.png"}
    m = json.loads(z.read("manifest.json"))
    assert m["app"] == "pfa-tracker" and m["format"] == 1 and m["firm"] == {"name": "Popescu Ion PFA", "cui": "RO14399840"}
    assert m["counts"] == {"expenses": 1, "invoices": 4, "attachments": 2}
    assert z.read("attachments/invoices/A-7_Acme.pdf") == PDF
    assert json.loads(z.read("settings.json"))["firm"]["cui"] == "RO14399840"


def test_backup_of_an_empty_tracker_still_works(client, settings_path):
    settings_path.write_text("{}")
    name, data = backup.export_bundle()
    assert name.startswith("pfa-backup-pfa-") and backup.inspect_bundle(data)["counts"]["invoices"] == 0


def test_restore_on_another_computer_gives_the_same_data(client):
    fill(client)
    before = snapshot()
    bundle = client.get("/backup").content
    # "the other computer": the same tracker with nothing in it (the autouse fixture resets everything)
    with db.connect() as con:
        con.execute("DELETE FROM invoices")
        con.execute("DELETE FROM expenses")
    shutil.rmtree(ATTACHMENTS_DIR)
    client.post("/settings", data={"name": "Other", "salary_years": ""})
    RATES_PATH.write_text("date,EUR\n2025-01-02,1\n")
    assert snapshot() != before
    r = client.post("/restore", files={"file": ("pfa-backup.zip", bundle, "application/zip")}, data={"confirm": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/settings").text
    assert "Backup restored: Popescu Ion PFA · RO14399840" in page and "4 invoice(s), 1 expense(s), 2 proof(s)" in page
    after = snapshot()
    assert after == before
    assert client.get("/income/4/attachment").content == PDF
    reopened = fresh_app()
    assert "4 invoice(s)" in reopened.get("/").text


def test_restore_keeps_a_safety_copy_of_what_was_there(client):
    fill(client)
    bundle = client.get("/backup").content
    client.post("/expenses", data={"date": f"{LAST_YEAR}-07-07", "description": "Lamp", "amount": "50", "currency": "RON"})
    client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"})
    copies = list((DB_PATH.parent / "backups").glob("before-restore-*.zip"))
    assert len(copies) == 1
    saved = backup.inspect_bundle(copies[0].read_bytes())
    assert saved["counts"]["expenses"] == 2                           # the Lamp was in the safety copy
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_restore_replaces_proofs_that_are_not_in_the_backup(client):
    fill(client)
    bundle = client.get("/backup").content
    client.post("/expenses", data={"date": f"{LAST_YEAR}-07-07", "description": "Lamp", "amount": "50", "currency": "RON"},
                files={"attachment": ("lamp.pdf", PDF, "application/pdf")})
    assert (ATTACHMENTS_DIR / "expenses" / "2_lamp.pdf").exists()
    client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"})
    assert sorted(p.name for p in (ATTACHMENTS_DIR / "expenses").iterdir()) == ["1_bon.png"]


def test_restore_needs_the_confirmation_box(client):
    fill(client)
    bundle = client.get("/backup").content
    with db.connect() as con:
        con.execute("DELETE FROM expenses")
    r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")})
    assert r.status_code == 200 and "tick the box" in r.text
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0
    assert not (DB_PATH.parent / "backups").exists()


def bundle_with(changes: dict, base: bytes | None = None, drop: tuple = ()) -> bytes:
    """A copy of a valid backup with some entries replaced, added or dropped."""
    base = base if base is not None else backup.export_bundle()[1]
    src = zipfile.ZipFile(io.BytesIO(base))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            if name not in changes and name not in drop:
                z.writestr(name, src.read(name))
        for name, data in changes.items():
            z.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize("make, reason", [
    (lambda: b"", "file is empty"),
    (lambda: b"not a zip at all", "not a zip file"),
    (lambda: PDF, "not a zip file"),
    (lambda: bundle_with({}, drop=("manifest.json",)), "no manifest.json"),
    (lambda: bundle_with({"manifest.json": b"{broken"}), "manifest is unreadable"),
    (lambda: bundle_with({"manifest.json": b'{"app": "other"}'}), "not made by the PFA tax tracker"),
    (lambda: bundle_with({"manifest.json": b'{"app": "pfa-tracker", "format": 99}'}), "format 99"),
    (lambda: bundle_with({"manifest.json": b"[1, 2]"}), "not made by the PFA tax tracker"),
    (lambda: bundle_with({}, drop=("pfa.sqlite",)), "no database"),
    (lambda: bundle_with({}, drop=("settings.json",)), "no settings.json"),
    (lambda: bundle_with({"settings.json": b"{ broken"}), "settings.json is not valid"),
    (lambda: bundle_with({"settings.json": b"[]"}), "settings.json is not valid"),
    (lambda: bundle_with({"pfa.sqlite": b"not a database"}), "not a SQLite file"),
    (lambda: bundle_with({"pfa.sqlite": other_sqlite()}), "tables missing"),
    (lambda: bundle_with({"pfa.sqlite": damaged_sqlite()}), "damaged"),
    (lambda: bundle_with({"../../etc/passwd": b"x"}), "does not belong there"),
    (lambda: bundle_with({"/etc/passwd": b"x"}), "does not belong there"),
    (lambda: bundle_with({"attachments/invoices/../../x.pdf": b"x"}), "does not belong there"),
    (lambda: bundle_with({"attachments/other/x.pdf": b"x"}), "does not belong there"),
    (lambda: bundle_with({"attachments/invoices/evil.exe": b"MZ"}), "does not belong there"),
    (lambda: bundle_with({"attachments/invoices/sub/x.pdf": b"x"}), "does not belong there"),
    (lambda: bundle_with({"extra.txt": b"x"}), "does not belong there"),
    (lambda: bundle_with({"run.sh": b"rm -rf /"}), "does not belong there"),
])
def test_bad_backups_are_refused_and_nothing_changes(client, make, reason):
    fill(client)
    before = snapshot()
    r = client.post("/restore", files={"file": ("b.zip", make(), "application/zip")}, data={"confirm": "1"})
    assert r.status_code == 200 and "Not restored" in r.text and reason in r.text, r.text[-2000:]
    assert snapshot() == before and not (DB_PATH.parent / "backups").exists()


def other_sqlite() -> bytes:
    path = DB_PATH.parent / "other.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
    data = path.read_bytes()
    path.unlink()
    return data


def damaged_sqlite() -> bytes:
    data = bytearray(backup._database_bytes())
    for i in range(4096, min(len(data), 4096 * 8), 7):      # scribble over the pages after the header
        data[i] ^= 0xFF
    return bytes(data)


def test_zip_bomb_is_refused_by_its_unpacked_size(client):
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"app": "pfa-tracker", "format": 1}))
        z.writestr("settings.json", "{}")
        z.writestr("pfa.sqlite", b"\0" * (backup.MAX_BUNDLE_BYTES + 1))
    r = client.post("/restore", files={"file": ("b.zip", big.getvalue(), "application/zip")}, data={"confirm": "1"})
    assert "larger than 1 GB" in r.text


def test_a_backup_from_another_firm_is_restored_but_named_in_the_message(client):
    fill(client)
    bundle = client.get("/backup").content
    client.post("/settings", data={"name": "Second PFA", "cui": "", "salary_years": ""})
    client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"},
                follow_redirects=False)
    html = client.get("/settings").text
    assert "Backup restored: Popescu Ion PFA · RO14399840" in html and "Popescu Ion PFA · CUI RO14399840" in html


def test_backup_is_consistent_while_the_app_is_in_use(client):
    fill(client)
    name, data = backup.export_bundle()
    m = backup.inspect_bundle(data)
    assert m["counts"]["invoices"] == 4
    client.post("/expenses", data={"date": f"{LAST_YEAR}-07-07", "description": "Lamp", "amount": "50", "currency": "RON"})
    assert backup.inspect_bundle(backup.export_bundle()[1])["counts"]["expenses"] == 2
    assert m["counts"]["expenses"] == 1                                  # the earlier bundle is unchanged


def test_restore_runs_the_database_upgrade_on_old_backups(client):
    old = DB_PATH.parent / "old.sqlite"
    with sqlite3.connect(old) as con:
        con.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, date TEXT NOT NULL, client TEXT NOT NULL, "
                    "number TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL, currency TEXT NOT NULL, rate TEXT NOT NULL, "
                    "rate_date TEXT NOT NULL, amount_ron TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
                    "UNIQUE (date, client, number, amount, currency))")
        con.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL, "
                    "category TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL, currency TEXT NOT NULL, rate TEXT NOT NULL, "
                    "rate_date TEXT NOT NULL, amount_ron TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.execute(f"INSERT INTO invoices (date, client, amount, currency, rate, rate_date, amount_ron, created_at) "
                    f"VALUES ('{LAST_YEAR}-01-05', 'Old', '10', 'RON', '1', '{LAST_YEAR}-01-05', '10.00', 'x')")
    bundle = bundle_with({"pfa.sqlite": old.read_bytes()})
    old.unlink()
    r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.get("invoices", 1)["income_date"] == f"{LAST_YEAR}-01-05"
    client.post("/year", data={"year": LAST_YEAR, "page": "income"})
    assert "Old" in client.get("/income").text
