"""Profiles: each one holds one firm with its own settings, invoices, expenses and proofs.

profiles.json in the home folder lists them:
    {"profiles": [{"id": "p1", "name": "Popescu Ion PFA", "created": "..."}], "last_used": "p1"}
and each lives in profiles/<id>/ (settings.json, data/pfa.sqlite, data/attachments/, data/backups/).
"""
import contextlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config

ID = re.compile(r"p[0-9]{1,6}")
MAX_NAME = 60


class ProfileError(Exception):
    pass


def registry_path() -> Path:
    return config.home_dir() / "profiles.json"


def _read() -> dict:
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise ValueError
    except (OSError, ValueError):
        data = {"profiles": [], "last_used": None}
    data["profiles"] = [p for p in data["profiles"] if isinstance(p, dict) and ID.fullmatch(str(p.get("id", "")))
                        and (config.profiles_dir() / p["id"]).is_dir()]
    for p in data["profiles"]:
        p["name"] = str(p.get("name") or p["id"])
    # profile folders missing from the list (a lost or damaged profiles.json) are listed again
    listed = {p["id"] for p in data["profiles"]}
    found = sorted((d.name for d in config.profiles_dir().glob("p*") if d.is_dir() and ID.fullmatch(d.name)
                    and d.name not in listed and (d / "settings.json").is_file()), key=lambda i: int(i[1:]))
    for pid in found:
        name = str(firm_of(pid).get("name") or "") or f"Profile {pid[1:]}"
        data["profiles"].append({"id": pid, "name": name[:MAX_NAME], "created": ""})
    return data


def _write(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ProfileError("the profile needs a name")
    if len(name) > MAX_NAME:
        raise ProfileError(f"the profile name can have at most {MAX_NAME} characters")
    if any(ord(c) < 32 for c in name):
        raise ProfileError("the profile name is not readable")
    return name


def _new_id(data: dict) -> str:
    used = {p["id"] for p in data["profiles"]} | {d.name for d in config.profiles_dir().glob("p*")}
    n = 1
    while f"p{n}" in used:
        n += 1
    return f"p{n}"


def _starting_settings(copy_from: str | None) -> dict:
    """A new profile's settings: an empty firm and the tax values per year (no salaried marks)."""
    source = config.profiles_dir() / copy_from / "settings.json" if copy_from else config.default_settings_template()
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
        years = data.get("years", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        years = {}
    years = {y: {k: v for k, v in b.items() if k != "has_salary"} for y, b in years.items() if isinstance(b, dict)}
    return {"firm": {"name": "", "cui": "", "caen_code": "", "caen_revision": "3"}, "years": years}


def ensure() -> None:
    """Makes sure there is at least one profile. The first time, the existing settings and data
    (from before profiles existed) become the first profile."""
    if config.single_file_mode():
        return
    data = _read()
    if data["profiles"]:
        return
    home = config.home_dir()
    legacy_settings, legacy_data = home / "settings.json", home / "data"
    profile_id = _new_id(data)
    target = config.profiles_dir() / profile_id
    (target / "data").mkdir(parents=True, exist_ok=True)
    name = "My PFA"
    if legacy_settings.is_file():
        try:
            firm_name = json.loads(legacy_settings.read_text(encoding="utf-8")).get("firm", {}).get("name", "")
            name = _clean_name(firm_name) if firm_name else name
        except (ValueError, AttributeError, ProfileError):
            pass
        shutil.move(str(legacy_settings), target / "settings.json")
    else:
        (target / "settings.json").write_text(json.dumps(_starting_settings(None), indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    for item in ("pfa.sqlite", "attachments", "backups"):
        if (legacy_data / item).exists():
            shutil.move(str(legacy_data / item), target / "data" / item)
    data["profiles"].append({"id": profile_id, "name": name, "created": datetime.now().isoformat(timespec="seconds")})
    data["last_used"] = profile_id
    _write(data)


def all_profiles() -> list[dict]:
    ensure()
    return _read()["profiles"]


def exists(profile_id: str | None) -> bool:
    return bool(profile_id) and any(p["id"] == profile_id for p in all_profiles())


def current_id() -> str:
    """The profile in use: the one chosen for this request/window, else the last one used."""
    chosen = config.ACTIVE_PROFILE.get()
    profiles = all_profiles()
    ids = [p["id"] for p in profiles]
    if chosen in ids:
        return chosen
    last = _read().get("last_used")
    if last in ids:
        return last
    if ids:
        return ids[0]
    raise ProfileError("there is no profile")


def current() -> dict:
    pid = current_id()
    return next(p for p in all_profiles() if p["id"] == pid)


def activate(profile_id: str) -> None:
    """Uses this profile from now on (in this request or window), and remembers it as the last used."""
    if not exists(profile_id):
        raise ProfileError("there is no such profile")
    config.ACTIVE_PROFILE.set(profile_id)
    data = _read()
    data["last_used"] = profile_id
    _write(data)


@contextlib.contextmanager
def using(profile_id: str | None):
    """Runs a block with a given profile in use (without changing the last used one)."""
    token = config.ACTIVE_PROFILE.set(profile_id if exists(profile_id) else None)
    try:
        yield
    finally:
        config.ACTIVE_PROFILE.reset(token)


def create(name: str) -> str:
    """A new profile with an empty firm; the tax values per year are copied from the profile in use."""
    name = _clean_name(name)
    data = _read()
    if any(p["name"].lower() == name.lower() for p in data["profiles"]):
        raise ProfileError(f"there is already a profile named {name}")
    copy_from = current_id() if data["profiles"] else None
    profile_id = _new_id(data)
    target = config.profiles_dir() / profile_id
    (target / "data").mkdir(parents=True)
    (target / "settings.json").write_text(json.dumps(_starting_settings(copy_from), indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    data["profiles"].append({"id": profile_id, "name": name, "created": datetime.now().isoformat(timespec="seconds")})
    _write(data)
    return profile_id


def rename(profile_id: str, name: str) -> None:
    name = _clean_name(name)
    data = _read()
    if any(p["name"].lower() == name.lower() and p["id"] != profile_id for p in data["profiles"]):
        raise ProfileError(f"there is already a profile named {name}")
    for p in data["profiles"]:
        if p["id"] == profile_id:
            p["name"] = name
            _write(data)
            return
    raise ProfileError("there is no such profile")


def delete(profile_id: str) -> Path:
    """Deletes a profile after saving a backup of it; returns the backup's path. The last profile stays."""
    from . import backup
    data = _read()
    if not any(p["id"] == profile_id for p in data["profiles"]):
        raise ProfileError("there is no such profile")
    if len(data["profiles"]) == 1:
        raise ProfileError("the only profile cannot be deleted")
    name = next(p["name"] for p in data["profiles"] if p["id"] == profile_id)
    with using(profile_id):
        from . import db
        db.init_db()
        bundle_name, bundle = backup.export_bundle()
    kept = config.home_dir() / "deleted-profiles"
    kept.mkdir(parents=True, exist_ok=True)
    safety = kept / f"{datetime.now():%Y-%m-%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9-]+', '-', name).strip('-') or profile_id}-{bundle_name}"
    safety.write_bytes(bundle)
    shutil.rmtree(config.profiles_dir() / profile_id)
    data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
    if data.get("last_used") == profile_id:
        data["last_used"] = data["profiles"][0]["id"]
    _write(data)
    return safety


def firm_of(profile_id: str) -> dict:
    try:
        f = json.loads((config.profiles_dir() / profile_id / "settings.json").read_text(encoding="utf-8")).get("firm", {})
        return f if isinstance(f, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}
