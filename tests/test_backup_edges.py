"""Rough edges found by attacking restore: databases the tracker cannot read, two restores in a
minute, duplicate zip entries, odd manifests, proofs that are not real files, a failed restore."""
import io
import json
import sqlite3
import zipfile

import pytest

from conftest import LAST_YEAR
from tracker import backup
from tracker.config import DB_PATH
from test_persistence_and_backup import bundle_with, fill, snapshot


def sqlite_with_tracker_table_names_but_other_columns() -> bytes:
    path = DB_PATH.parent / "odd.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, x TEXT)")
        con.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY, x TEXT)")
    data = path.read_bytes()
    path.unlink()
    return data


def sqlite_with_a_row_that_is_not_numbers() -> bytes:
    path = DB_PATH.parent / "odd.sqlite"
    path.write_bytes(backup._database_bytes())
    with sqlite3.connect(path) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(invoices)")][1:]
        con.execute(f"INSERT INTO invoices ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})", [""] * len(cols))
    data = path.read_bytes()
    path.unlink()
    return data


@pytest.mark.parametrize("make", [sqlite_with_tracker_table_names_but_other_columns, sqlite_with_a_row_that_is_not_numbers])
def test_a_database_the_tracker_cannot_read_is_refused_before_anything_is_replaced(client, make):
    fill(client)
    before = snapshot()
    r = client.post("/restore", files={"file": ("b.zip", bundle_with({"pfa.sqlite": make()}), "application/zip")},
                    data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 200 and "Not restored" in r.text, r.status_code
    assert snapshot() == before
    for page in ("/", "/income", "/expenses", "/settings"):
        assert client.get(page).status_code == 200, page


def test_two_restores_in_the_same_minute_keep_both_safety_copies(client):
    fill(client)
    original = snapshot()
    first = bundle_with({"settings.json": json.dumps({"firm": {"name": "Firm A", "cui": "RO14399840"}, "years": {}})})
    second = bundle_with({"settings.json": json.dumps({"firm": {"name": "Firm B", "cui": "RO14399840"}, "years": {}})})
    for bundle in (first, second):
        r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"},
                        follow_redirects=False)
        assert r.status_code == 303
    copies = sorted((DB_PATH.parent / "backups").glob("before-restore-*.zip"))
    assert len(copies) == 2, [c.name for c in copies]
    firms = {json.loads(zipfile.ZipFile(io.BytesIO(c.read_bytes())).read("settings.json"))["firm"]["name"] for c in copies}
    assert firms == {original["settings"]["firm"]["name"], "Firm A"}


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_duplicate_entries_in_the_zip_are_refused(client):
    fill(client)
    base = backup.export_bundle()[1]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        src = zipfile.ZipFile(io.BytesIO(base))
        for name in src.namelist():
            z.writestr(name, src.read(name))
        z.writestr("settings.json", json.dumps({"firm": {"name": "DUP"}, "years": {}}))
    r = client.post("/restore", files={"file": ("b.zip", out.getvalue(), "application/zip")}, data={"confirm": "1"})
    assert "the same file twice" in r.text and snapshot()["settings"]["firm"]["name"] == "Popescu Ion PFA"


@pytest.mark.parametrize("manifest", [
    {"app": "pfa-tracker", "format": 1, "created": 12345, "firm": {"name": ["x"], "cui": None}, "counts": "many"},
    {"app": "pfa-tracker", "format": 1},
    {"app": "pfa-tracker", "format": 1, "created": "x" * 100000, "firm": {"name": "A" * 5000}},
])
def test_odd_manifest_values_never_break_the_restore_message(client, manifest):
    fill(client)
    bundle = bundle_with({"manifest.json": json.dumps(manifest)})
    r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "Backup restored" in client.get("/settings").text


def test_proofs_in_the_backup_must_be_real_files(client):
    fill(client)
    before = snapshot()
    bundle = bundle_with({"attachments/invoices/A-7_Acme.pdf": b"<script>alert(1)</script>"})
    r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"})
    assert "proof A-7_Acme.pdf in the backup is not a readable file" in r.text and snapshot() == before


def test_a_failed_restore_puts_the_previous_state_back(client, monkeypatch):
    fill(client)
    before = snapshot()
    bundle = bundle_with({"settings.json": json.dumps({"firm": {"name": "Firm B"}, "years": {}})})
    calls = {"n": 0}
    real = backup._replace

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == 3:                      # fails in the middle of putting the files in place
            raise OSError("disk full")
        real(path, data)
    monkeypatch.setattr(backup, "_replace", flaky)
    r = client.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"})
    assert "the restore failed (disk full); the previous state was put back" in r.text
    monkeypatch.setattr(backup, "_replace", real)
    assert snapshot() == before
    for page in ("/", "/income", "/expenses", "/settings"):
        assert client.get(page).status_code == 200
