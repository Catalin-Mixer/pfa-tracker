"""Packaging: where the files are looked for in every mode, first-start seeding, and the launcher's options."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tracker import config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import launcher  # noqa: E402


# --- home and bundle folders ---------------------------------------------------------------------

def test_from_source_home_and_bundle_are_the_project_folder(monkeypatch):
    monkeypatch.delenv("PFA_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not config.is_frozen()
    assert config.home_dir() == ROOT
    assert config.bundle_dir() == ROOT


def test_frozen_home_is_the_executable_folder_and_bundle_is_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv("PFA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pfa" / "PFA-Tax-Tracker.exe"))
    assert config.is_frozen()
    assert config.home_dir() == (tmp_path / "pfa").resolve()
    assert config.bundle_dir() == tmp_path / "bundle"


def test_pfa_home_overrides_the_executable_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "PFA-Tax-Tracker"))
    monkeypatch.setenv("PFA_HOME", str(tmp_path / "home"))
    assert config.home_dir() == (tmp_path / "home").resolve()


def test_the_test_suite_overrides_still_apply():
    assert config.DB_PATH == Path(os.environ["PFA_DB"])
    assert config.SETTINGS_PATH == Path(os.environ["PFA_SETTINGS"])
    assert config.RATES_PATH == Path(os.environ["PFA_RATES"])
    assert config.CAEN_PATH == Path(os.environ["PFA_CAEN"])
    assert config.ATTACHMENTS_DIR == Path(os.environ["PFA_DB"]).parent / "attachments"


def _clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PFA_")}
    env.update(extra)
    return env


DEFAULT_SETTINGS = ROOT / "packaging" / "defaults" / "settings.json"


def _fake_bundle(tmp_path: Path) -> Path:
    """What the executable unpacks: the default data files as the spec bundles them."""
    bundle = tmp_path / "bundle"
    if not bundle.exists():
        bundle.mkdir()
        shutil.copy2(DEFAULT_SETTINGS, bundle / "settings.json")
        shutil.copy2(ROOT / "rates.csv", bundle / "rates.csv")
        shutil.copy2(ROOT / "caen_codes.xlsx", bundle / "caen_codes.xlsx")
        shutil.copytree(ROOT / "samples", bundle / "samples")
    return bundle


def _frozen_paths(tmp_path: Path, env: dict) -> dict:
    """Imports tracker.config in a new Python that pretends to be the built executable, with
    tmp_path/bundle as the unpacked bundle and tmp_path/exe as the executable's folder."""
    bundle = _fake_bundle(tmp_path)
    code = textwrap.dedent(f"""
        import json, sys
        sys.frozen = True
        sys._MEIPASS = {str(bundle)!r}
        sys.executable = {str(tmp_path / "exe" / "PFA-Tax-Tracker")!r}
        sys.path.insert(0, {str(ROOT)!r})
        from tracker import config as c
        print(json.dumps({{k: str(getattr(c, k)) for k in ("HOME", "DB_PATH", "SETTINGS_PATH", "RATES_PATH",
              "CAEN_PATH", "TEMPLATES_DIR", "STATIC_DIR", "ATTACHMENTS_DIR")}}))
    """)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_frozen_paths_and_first_start_seeding(tmp_path):
    (tmp_path / "exe").mkdir()
    paths = _frozen_paths(tmp_path, _clean_env())
    home = tmp_path / "exe"
    assert paths["HOME"] == str(home.resolve())
    # the seeded settings become the first profile's settings
    assert paths["DB_PATH"] == str(home.resolve() / "profiles" / "p1" / "data" / "pfa.sqlite")
    assert paths["SETTINGS_PATH"] == str(home.resolve() / "profiles" / "p1" / "settings.json")
    assert paths["TEMPLATES_DIR"] == str(tmp_path / "bundle" / "templates")
    assert paths["STATIC_DIR"] == str(tmp_path / "bundle" / "static")
    assert (home / "profiles" / "p1" / "settings.json").read_bytes() == DEFAULT_SETTINGS.read_bytes()
    assert not (home / "settings.json").exists() and (home / "profiles.json").is_file()
    for name in ("rates.csv", "caen_codes.xlsx"):
        assert (home / name).read_bytes() == (ROOT / name).read_bytes()
    assert (home / "samples" / "sample_invoices.csv").is_file()


def test_frozen_with_pfa_home_seeds_there_and_keeps_existing_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text('{"mine": true}', encoding="utf-8")
    paths = _frozen_paths(tmp_path, _clean_env(PFA_HOME=str(home)))
    assert paths["SETTINGS_PATH"] == str(home.resolve() / "profiles" / "p1" / "settings.json")
    assert (home / "profiles" / "p1" / "settings.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert (home / "rates.csv").is_file() and (home / "caen_codes.xlsx").is_file()
    assert not (tmp_path / "exe" / "settings.json").exists()


def test_frozen_env_override_is_not_seeded(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    moved = tmp_path / "other" / "settings.json"
    paths = _frozen_paths(tmp_path, _clean_env(PFA_HOME=str(home), PFA_SETTINGS=str(moved)))
    assert paths["SETTINGS_PATH"] == str(moved)
    assert not moved.exists() and not (home / "settings.json").exists()
    assert (home / "rates.csv").is_file()


def test_seed_home_never_overwrites(tmp_path):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    (bundle / "samples").mkdir(parents=True)
    for name in config.SEED_FILES:
        (bundle / name).write_text("default " + name, encoding="utf-8")
    (bundle / "samples" / "a.csv").write_text("x", encoding="utf-8")
    home.mkdir()
    (home / "rates.csv").write_text("my rates", encoding="utf-8")

    created = config.seed_home(home, bundle)
    assert sorted(p.name for p in created) == ["caen_codes.xlsx", "samples", "settings.json"]
    assert (home / "rates.csv").read_text(encoding="utf-8") == "my rates"
    assert (home / "settings.json").read_text(encoding="utf-8") == "default settings.json"

    (home / "settings.json").write_text("changed", encoding="utf-8")
    assert config.seed_home(home, bundle) == []
    assert (home / "settings.json").read_text(encoding="utf-8") == "changed"


def test_seed_home_does_nothing_when_home_is_the_bundle(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    assert config.seed_home(tmp_path, tmp_path) == []


def test_seed_home_skips_targets_set_to_none(tmp_path):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    bundle.mkdir()
    for name in config.SEED_FILES:
        (bundle / name).write_text(name, encoding="utf-8")
    config.seed_home(home, bundle, {"settings.json": None, "rates.csv": tmp_path / "r" / "rates.csv"})
    assert not (home / "settings.json").exists()
    assert (tmp_path / "r" / "rates.csv").is_file() and not (home / "rates.csv").exists()
    assert (home / "caen_codes.xlsx").is_file()


# --- launcher ------------------------------------------------------------------------------------

def test_launcher_defaults_to_the_desktop_window():
    args = launcher.parse_args([])
    assert (args.web, args.port, args.no_browser) == (False, 8765, False)


def test_launcher_web_and_port():
    args = launcher.parse_args(["--web", "--port", "8790"])
    assert (args.web, args.port) == (True, 8790)
    assert launcher.parse_args(["--web", "--no-browser"]).no_browser


@pytest.mark.parametrize("argv", [["--port", "8790"], ["--web", "--port", "0"], ["--web", "--port", "70000"],
                                  ["--web", "--port", "abc"], ["--bogus"]])
def test_launcher_refuses_bad_options(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(argv)
    assert exc.value.code == 2


def test_launcher_help(capsys):
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--web" in out and "--port" in out


def test_launcher_web_when_the_port_is_taken_by_another_program(monkeypatch):
    monkeypatch.setattr(launcher, "port_in_use", lambda port, host=launcher.HOST: True)
    monkeypatch.setattr(launcher, "tracker_running", lambda port, host=launcher.HOST: False)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    assert launcher.run_web(8790) == 1
    assert opened == []


def test_launcher_web_when_the_tracker_already_runs_opens_the_browser(monkeypatch):
    monkeypatch.setattr(launcher, "port_in_use", lambda port, host=launcher.HOST: True)
    monkeypatch.setattr(launcher, "tracker_running", lambda port, host=launcher.HOST: True)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    assert launcher.run_web(8790) == 0
    assert opened == ["http://127.0.0.1:8790/"]


def test_launcher_tracker_running_is_false_on_a_free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert not launcher.tracker_running(port)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="libxcb-cursor is a Linux matter")
def test_launcher_finds_libxcb_cursor():
    assert launcher.load_xcb_cursor() is not None


def test_spec_bundles_the_program_and_default_files():
    spec = (ROOT / "packaging" / "pfa-tracker.spec").read_text(encoding="utf-8")
    for item in ('"templates"', '"static"', '"samples"', '"rates.csv"', '"caen_codes.xlsx"',
                 '"libxcb-cursor.so.0"', 'name="PFA-Tax-Tracker"', "launcher.py",
                 '(os.path.join(SPECPATH, "defaults", "settings.json"), ".")'):
        assert item in spec
    assert '(os.path.join(ROOT, "settings.json")' not in spec  # never the user's own settings


def test_default_settings_have_no_firm_and_no_salary():
    defaults = json.loads(DEFAULT_SETTINGS.read_text(encoding="utf-8"))
    assert defaults["firm"] == {"name": "", "cui": "", "caen_code": "", "caen_revision": "3"}
    assert defaults["years"]
    for values in defaults["years"].values():
        assert values["has_salary"] is False
        assert "minimum_wage" in values and "note" in values
