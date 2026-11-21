"""Where the tracker keeps its files, the same in every mode (browser, desktop window, .exe).

All data lives in one "home" folder:
- run from source (python3 launcher.py, run.sh, uvicorn app:app): the project folder;
- run as the built executable (PFA-Tax-Tracker / PFA-Tax-Tracker.exe): the folder that holds
  the executable, so keep it in the pfa-tracker folder;
- in both cases the PFA_HOME environment variable, if set, names another folder.
The single files can still be moved one by one with PFA_DB, PFA_SETTINGS, PFA_RATES and PFA_CAEN.

The executable carries default copies of settings.json, rates.csv, caen_codes.xlsx and samples/;
they are copied into the home folder on first start when it lacks them, never over existing files.
"""
import contextvars
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent

# Files seeded into an empty home folder from the bundled defaults.
SEED_FILES = ("settings.json", "rates.csv", "caen_codes.xlsx")
SEED_DIRS = ("samples",)


def is_frozen() -> bool:
    """True when running as the executable built by PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """The folder with the read-only program files: templates/, static/ and the default data files."""
    if is_frozen() and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return SOURCE_ROOT


def home_dir() -> Path:
    """The folder that holds the user's data (settings, rates, CAEN list, data/)."""
    if os.environ.get("PFA_HOME"):
        return Path(os.environ["PFA_HOME"]).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def seed_home(home: Path, bundle: Path, targets: dict | None = None) -> list[Path]:
    """Copies the bundled default files into the home folder where they are missing.

    targets maps a seed file name to the path it should be copied to (default: home / name);
    a target of None skips that file. Existing files are never overwritten.
    Returns the paths that were created."""
    targets = targets or {}
    created = []
    if home.resolve() == bundle.resolve():
        return created
    for name in SEED_FILES:
        dst = targets.get(name, home / name)
        if dst is None:
            continue
        src, dst = bundle / name, Path(dst)
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            created.append(dst)
    for name in SEED_DIRS:
        src, dst = bundle / name, home / name
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst)
            created.append(dst)
    return created


HOME = home_dir()
BUNDLE = bundle_dir()
TEMPLATES_DIR = BUNDLE / "templates"
STATIC_DIR = BUNDLE / "static"
LOG_PATH = HOME / "data" / "server.log"


# Profiles: each profile (one firm) has its own folder profiles/<id>/ with settings.json and
# data/ (pfa.sqlite, attachments/, backups/). rates.csv and caen_codes.xlsx are shared.
# The profile in use is held per request (browser) or per window (desktop) in a context variable.

ACTIVE_PROFILE: contextvars.ContextVar[str | None] = contextvars.ContextVar("pfa_profile", default=None)


def single_file_mode() -> bool:
    """True when PFA_DB or PFA_SETTINGS name the files directly: then there are no profiles."""
    return "PFA_DB" in os.environ or "PFA_SETTINGS" in os.environ


def profiles_dir() -> Path:
    return home_dir() / "profiles"


def profile_dir(profile_id: str | None = None) -> Path:
    from . import profiles   # late import: profiles uses this module
    return profiles_dir() / (profile_id or profiles.current_id())


class ActivePath(os.PathLike):
    """A path that is looked up each time it is used, so that it follows the profile in use.

    It behaves like a pathlib.Path (/, .parent, .read_text(), str(), open(), sqlite3.connect())."""

    def __init__(self, resolve):
        self._resolve = resolve

    def path(self) -> Path:
        return Path(self._resolve())

    def __fspath__(self) -> str:
        return str(self.path())

    def __str__(self) -> str:
        return str(self.path())

    def __repr__(self) -> str:
        return f"ActivePath({self.path()!s})"

    def __truediv__(self, other) -> Path:
        return self.path() / other

    def __eq__(self, other) -> bool:
        return self.path() == (other.path() if isinstance(other, ActivePath) else Path(other))

    def __hash__(self) -> int:
        return hash(self.path())

    def __getattr__(self, name):
        return getattr(self.path(), name)


def _env_or(var: str, default):
    return Path(os.environ[var]) if var in os.environ else default()


def _data_root() -> Path:
    """profiles/<id>/ normally; the home folder itself in single-file mode."""
    return home_dir() if single_file_mode() else profile_dir()


DB_PATH = ActivePath(lambda: _env_or("PFA_DB", lambda: _data_root() / "data" / "pfa.sqlite"))
SETTINGS_PATH = ActivePath(lambda: _env_or("PFA_SETTINGS", lambda: _data_root() / "settings.json"))
ATTACHMENTS_DIR = ActivePath(lambda: DB_PATH.path().parent / "attachments")
RATES_PATH = ActivePath(lambda: _env_or("PFA_RATES", lambda: home_dir() / "rates.csv"))
CAEN_PATH = ActivePath(lambda: _env_or("PFA_CAEN", lambda: home_dir() / "caen_codes.xlsx"))


def ui_settings_path() -> Path:
    """The look of each app (browser and desktop, chosen separately) and the saved custom themes.
    Kept outside the profiles: it belongs to the app on this computer, not to a firm."""
    if "PFA_UI" in os.environ:
        return Path(os.environ["PFA_UI"])
    if single_file_mode():
        return SETTINGS_PATH.path().parent / "ui-settings.json"
    return home_dir() / "ui-settings.json"


def default_settings_template() -> Path:
    """The settings a new profile starts from when there is no other profile to copy tax values from."""
    bundled = bundle_dir() / "settings.json"
    if is_frozen() and bundled.is_file():
        return bundled
    return SOURCE_ROOT / "packaging" / "defaults" / "settings.json"


if HOME != BUNDLE:
    # Only the paths not moved by an environment variable are seeded; a seeded settings.json becomes
    # the first profile's settings (see profiles.ensure()).
    try:
        seed_home(HOME, BUNDLE, {name: None if var in os.environ else HOME / name for name, var in (
            ("settings.json", "PFA_SETTINGS"), ("rates.csv", "PFA_RATES"), ("caen_codes.xlsx", "PFA_CAEN"))}
                  | ({"settings.json": None} if (HOME / "profiles.json").exists() else {}))
    except OSError as exc:  # a read-only folder: the app reports the missing files itself
        print(f"PFA tax tracker: could not copy the default files to {HOME}: {exc}", file=sys.stderr)

MAX_RATE_LOOKBACK_DAYS = 10
CENT = Decimal("0.01")
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "xml"}
