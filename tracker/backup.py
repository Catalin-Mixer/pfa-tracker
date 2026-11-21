"""Backup and restore: one zip file holds everything the tracker knows, so it can be moved to
another computer that has the tracker installed.

The zip contains manifest.json, settings.json, rates.csv, caen_codes.xlsx, pfa.sqlite and the
proofs under attachments/invoices and attachments/expenses.
"""
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import attachments, db
from .config import ATTACHMENTS_DIR, ATTACHMENT_EXTENSIONS, CAEN_PATH, DB_PATH, RATES_PATH, SETTINGS_PATH

FORMAT = 1
APP = "pfa-tracker"
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024          # 1 GB unpacked
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,200}")
TABLES = {"invoices", "expenses"}
REQUIRED_COLUMNS = {
    "invoices": {"date", "client", "number", "amount", "currency", "rate", "rate_date", "amount_ron", "source", "created_at"},
    "expenses": {"date", "description", "category", "amount", "currency", "rate", "rate_date", "amount_ron", "created_at"},
}


class BackupError(Exception):
    pass


def _firm() -> dict:
    from . import settings
    f = settings.firm()
    return {"name": f["name"], "cui": f["cui"]}


def _counts() -> dict:
    with db.connect() as con:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(TABLES)}


def _database_bytes() -> bytes:
    """A consistent copy of the database, even while the app is running."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "pfa.sqlite"
        with sqlite3.connect(DB_PATH) as src, sqlite3.connect(copy) as dst:
            src.backup(dst)
        return copy.read_bytes()


def _attachments() -> list[tuple[str, Path]]:
    out = []
    for table in ("invoices", "expenses"):
        folder = ATTACHMENTS_DIR / table
        if folder.is_dir():
            out += [(f"attachments/{table}/{p.name}", p) for p in sorted(folder.iterdir()) if p.is_file()]
    return out


def export_bundle() -> tuple[str, bytes]:
    """Returns (suggested file name, zip bytes)."""
    db.init_db()
    firm = _firm()
    created = datetime.now()
    attachments = _attachments()
    manifest = {
        "app": APP, "format": FORMAT, "created": created.isoformat(timespec="seconds"), "firm": firm,
        "counts": {**_counts(), "attachments": len(attachments)},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("pfa.sqlite", _database_bytes())
        for name, path in (("settings.json", SETTINGS_PATH), ("rates.csv", RATES_PATH), ("caen_codes.xlsx", CAEN_PATH)):
            if path.is_file():
                z.write(path, name)
        for name, path in attachments:
            z.write(path, name)
    label = re.sub(r"[^A-Za-z0-9-]+", "-", firm["cui"] or firm["name"] or "pfa").strip("-") or "pfa"
    return f"pfa-backup-{label}-{created:%Y-%m-%d-%H%M}.zip", buf.getvalue()


def inspect_bundle(data: bytes) -> dict:
    """Checks a backup file thoroughly and returns its manifest; raises BackupError."""
    if not data:
        raise BackupError("the file is empty")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("this is not a backup file made by the tracker (not a zip file)")
    names = z.namelist()
    if len(set(names)) != len(names):
        raise BackupError("the backup holds the same file twice")
    if "manifest.json" not in names:
        raise BackupError("this zip file is not a tracker backup (no manifest.json inside)")
    try:
        manifest = json.loads(z.read("manifest.json"))
    except (ValueError, UnicodeDecodeError):
        raise BackupError("the backup's manifest is unreadable")
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise BackupError("this zip file was not made by the PFA tax tracker")
    if manifest.get("format") != FORMAT:
        raise BackupError(f"the backup uses format {manifest.get('format')}, this version of the tracker reads format {FORMAT}")
    if sum(i.file_size for i in z.infolist()) > MAX_BUNDLE_BYTES:
        raise BackupError("the backup is larger than 1 GB unpacked")
    for name in names:
        if name in ("manifest.json", "pfa.sqlite", "settings.json", "rates.csv", "caen_codes.xlsx"):
            continue
        parts = name.split("/")
        ok = (len(parts) == 3 and parts[0] == "attachments" and parts[1] in ("invoices", "expenses")
              and SAFE_NAME.fullmatch(parts[2]) and parts[2].rsplit(".", 1)[-1].lower() in ATTACHMENT_EXTENSIONS
              and ".." not in parts[2])
        if not ok:
            raise BackupError(f"the backup holds a file that does not belong there: {name[:80]}")
    if "pfa.sqlite" not in names:
        raise BackupError("the backup has no database (pfa.sqlite) inside")
    if "settings.json" not in names:
        raise BackupError("the backup has no settings.json inside")
    try:
        settings_data = json.loads(z.read("settings.json"))
        if not isinstance(settings_data, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        raise BackupError("the backup's settings.json is not valid")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "check.sqlite"
        path.write_bytes(z.read("pfa.sqlite"))
        _check_database(path)
    for name in names:
        if name.startswith("attachments/"):
            try:
                attachments.check(name.rsplit("/", 1)[1], z.read(name))
            except ValueError as e:
                raise BackupError(f"the proof {name.rsplit('/', 1)[1]} in the backup is not a readable file ({e})")
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    firm = manifest.get("firm") if isinstance(manifest.get("firm"), dict) else {}
    manifest["counts"] = {k: v for k, v in counts.items() if isinstance(v, int)}
    manifest["firm"] = {k: str(firm.get(k) or "") for k in ("name", "cui")}
    manifest["created"] = str(manifest.get("created") or "")
    manifest["attachments"] = [n for n in names if n.startswith("attachments/")]
    return manifest


def _check_database(path: Path) -> None:
    """The database must be sound, have the tracker's tables and columns, and hold readable rows."""
    from decimal import Decimal, InvalidOperation
    try:
        with sqlite3.connect(path) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("the backup's database is damaged")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not TABLES <= tables:
                raise BackupError("the backup's database is not a tracker database (tables missing)")
            for table, needed in REQUIRED_COLUMNS.items():
                columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if not needed <= columns:
                    raise BackupError(f"the backup's database is not a tracker database ({table} columns missing)")
                for row in con.execute(f"SELECT id, date, amount, amount_ron FROM {table}"):
                    try:
                        Decimal(row[2]), Decimal(row[3])
                        ok = isinstance(row[1], str) and len(row[1]) == 10
                    except (InvalidOperation, TypeError, ValueError):
                        ok = False
                    if not ok:
                        raise BackupError(f"the backup's database holds an unreadable {table[:-1]} (id {row[0]})")
    except sqlite3.DatabaseError:
        raise BackupError("the backup's database is not a SQLite file")


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".restoring")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _apply(z: zipfile.ZipFile) -> None:
    """Puts a checked bundle's files in place."""
    names = z.namelist()
    _replace(DB_PATH, z.read("pfa.sqlite"))
    _replace(SETTINGS_PATH, z.read("settings.json"))
    for name, path in (("rates.csv", RATES_PATH), ("caen_codes.xlsx", CAEN_PATH)):
        if name in names:
            _replace(path, z.read(name))
    for table in ("invoices", "expenses"):
        folder = ATTACHMENTS_DIR / table
        if folder.is_dir():
            for p in folder.iterdir():
                if p.is_file():
                    p.unlink()
    for name in names:
        if name.startswith("attachments/"):
            _replace(ATTACHMENTS_DIR / name.split("/", 1)[1], z.read(name))
    db.init_db()


def _safety_path(name: str) -> Path:
    backups = DB_PATH.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stem = f"before-restore-{datetime.now():%Y-%m-%d-%H%M%S}-{name.removeprefix('pfa-backup-')}"
    path, n = backups / stem, 1
    while path.exists():
        n += 1
        path = backups / stem.replace(".zip", f"-{n}.zip")
    return path


def restore_bundle(data: bytes) -> dict:
    """Replaces everything with the backup's content. A safety copy of the current state is written
    to data/backups/ first; if putting the files in place fails, that copy is put back."""
    manifest = inspect_bundle(data)
    safety_name, safety = export_bundle()
    safety_path = _safety_path(safety_name)
    safety_path.write_bytes(safety)
    try:
        _apply(zipfile.ZipFile(io.BytesIO(data)))
    except Exception as e:
        try:
            _apply(zipfile.ZipFile(io.BytesIO(safety)))
        except Exception:
            raise BackupError(f"the restore failed halfway ({e}) and the previous state could not be put back "
                              f"automatically; it is saved in {safety_path}")
        raise BackupError(f"the restore failed ({e}); the previous state was put back")
    manifest["safety_copy"] = str(safety_path)
    manifest["restored_counts"] = {**_counts(), "attachments": len(manifest["attachments"])}
    return manifest
