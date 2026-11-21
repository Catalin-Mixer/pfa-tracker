"""Profiles (one firm each, with its own settings, invoices, expenses, proofs and theme) and the
light/dark/custom theme, through the core and the browser version."""
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from conftest import LAST_YEAR, YEAR, csv_upload, sample_csv
from files import PDF
from tracker import config, db, profiles, services, theme

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "packaging" / "defaults" / "settings.json"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Profiles switched on: a fresh home folder, no single-file environment variables."""
    monkeypatch.delenv("PFA_DB", raising=False)
    monkeypatch.delenv("PFA_SETTINGS", raising=False)
    monkeypatch.setenv("PFA_HOME", str(tmp_path))
    token = config.ACTIVE_PROFILE.set(None)
    yield tmp_path
    config.ACTIVE_PROFILE.reset(token)


@pytest.fixture
def web(home):
    from fastapi.testclient import TestClient
    from app import app
    services.start()
    return TestClient(app)


def firm_name(http) -> str:
    return json.loads(config.SETTINGS_PATH.read_text())["firm"]["name"]


# The first profile

def test_the_first_start_makes_a_profile_from_the_existing_data(home):
    shutil.copy(DEFAULTS, home / "settings.json")
    data = json.loads(DEFAULTS.read_text())
    data["firm"]["name"] = "Old Firm PFA"
    (home / "settings.json").write_text(json.dumps(data))
    (home / "data" / "attachments" / "invoices").mkdir(parents=True)
    (home / "data" / "attachments" / "invoices" / "x.pdf").write_bytes(PDF)
    import sqlite3
    with sqlite3.connect(home / "data" / "pfa.sqlite") as con:
        con.execute("CREATE TABLE marker (x)")
    services.start()
    assert [p["name"] for p in profiles.all_profiles()] == ["Old Firm PFA"]
    p1 = home / "profiles" / "p1"
    assert (p1 / "settings.json").is_file() and not (home / "settings.json").exists()
    assert (p1 / "data" / "attachments" / "invoices" / "x.pdf").read_bytes() == PDF
    with sqlite3.connect(p1 / "data" / "pfa.sqlite") as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='marker'").fetchone()
    assert str(config.DB_PATH) == str(p1 / "data" / "pfa.sqlite")


def test_without_any_data_the_first_profile_starts_empty(home):
    services.start()
    (p,) = profiles.all_profiles()
    assert p["name"] == "My PFA" and profiles.firm_of(p["id"])["name"] == ""


def test_single_file_mode_has_no_profiles(tmp_path):
    assert config.single_file_mode() and not services.profiles_enabled()
    assert services.profiles_view()["enabled"] is False


# Managing profiles

def test_a_new_profile_has_an_empty_firm_and_copies_the_tax_values_without_salaried_marks(web):
    web.post("/settings", data={"name": "Firm A", "cui": "RO14399840", "salaried_now": "1"})
    a = json.loads(config.SETTINGS_PATH.read_text())
    r = web.post("/profiles/new", data={"name": "Second firm"}, follow_redirects=False)
    assert r.headers["location"] == "/settings" and web.cookies.get("pfa_profile") == "p2"
    with profiles.using("p2"):
        b = json.loads(config.SETTINGS_PATH.read_text())
    assert b["firm"] == {"name": "", "cui": "", "caen_code": "", "caen_revision": "3"}
    assert set(b["years"]) == set(a["years"]) and not any("has_salary" in y for y in b["years"].values())
    assert b["years"][YEAR]["minimum_wage"] == a["years"][YEAR]["minimum_wage"]


@pytest.mark.parametrize("name, reason", [("", "needs a name"), ("   ", "needs a name"), ("x" * 61, "at most 60"),
                                          ("a\x00b", "not readable"), ("My PFA", "already a profile named")])
def test_bad_profile_names_are_refused(web, name, reason):
    r = web.post("/profiles/new", data={"name": name}, follow_redirects=True)
    assert "Profile not created" in r.text and reason in r.text and len(profiles.all_profiles()) == 1


def test_each_profile_sees_only_its_own_entries_settings_and_proofs(web):
    web.post("/settings", data={"name": "Firm A"})
    web.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)))
    web.post("/profiles/new", data={"name": "B"})
    web.post("/settings", data={"name": "Firm B"})
    web.post("/expenses", data={"date": f"{YEAR}-01-05", "description": "only in B", "amount": "5", "currency": "RON"},
             files={"attachment": ("b.pdf", PDF, "application/pdf")})
    html_b = web.get("/").text
    assert "Firm B" in html_b and "Firm A" not in html_b and "0 invoice(s)" in html_b
    assert "only in B" in web.get("/expenses").text
    web.post("/profile", data={"profile": "p1", "page": "expenses"})
    assert "only in B" not in web.get("/expenses").text
    assert "Firm A" in web.get("/").text
    web.post("/year", data={"year": LAST_YEAR, "page": "income"})
    assert "3 invoice(s)" in web.get("/").text or "Nordwind" in web.get("/income").text
    assert (config.home_dir() / "profiles" / "p2" / "data" / "attachments" / "expenses" / "1_b.pdf").is_file()
    assert not (config.home_dir() / "profiles" / "p1" / "data" / "attachments" / "expenses").exists()
    assert web.get("/expenses/1/attachment").status_code == 404          # B's proof is not reachable from A


def test_one_firm_belongs_to_one_profile(web):
    web.post("/settings", data={"name": "Firm A", "cui": "RO14399840"})
    web.post("/profiles/new", data={"name": "B"})
    for cui in ("RO14399840", "14399840", "ro 14399840"):
        r = web.post("/settings", data={"name": "Firm B", "cui": cui})
        assert "already belongs to the profile My PFA" in r.text
    assert web.post("/settings", data={"name": "Firm A again"}, follow_redirects=False).status_code == 303


def test_two_browsers_can_use_different_profiles_at_the_same_time(web):
    from fastapi.testclient import TestClient
    from app import app
    web.post("/settings", data={"name": "Firm A"})
    web.post("/profiles/new", data={"name": "B"})
    web.post("/settings", data={"name": "Firm B"})
    other = TestClient(app)
    other.post("/profile", data={"profile": "p1", "page": "overview"})
    assert "Firm A" in other.get("/").text and "Firm B" in web.get("/").text


def test_without_a_cookie_the_last_used_profile_is_shown(web):
    web.post("/profiles/new", data={"name": "B"})
    web.post("/settings", data={"name": "Firm B"})
    from fastapi.testclient import TestClient
    from app import app
    assert "Firm B" in TestClient(app).get("/").text


@pytest.mark.parametrize("cookie", ["p99", "../p1", "p1/../../etc", "", "<script>", "P1", "p1 "])
def test_a_tampered_profile_cookie_falls_back_safely(web, cookie):
    web.post("/settings", data={"name": "Firm A"})
    web.cookies.set("pfa_profile", cookie)
    r = web.get("/")
    assert r.status_code == 200 and "Firm A" in r.text


@pytest.mark.parametrize("profile", ["p99", "../p1", "", "p1/..", "x"])
def test_switching_to_a_profile_that_does_not_exist_is_refused(web, profile):
    r = web.post("/profile", data={"profile": profile, "page": "overview"}, follow_redirects=True)
    assert "Profile not changed: there is no such profile" in r.text


def test_rename(web):
    web.post("/profiles/new", data={"name": "B"})
    r = web.post("/profiles/p2/rename", data={"name": "  Beta   PFA "}, follow_redirects=True)
    assert "Profile renamed." in r.text and [p["name"] for p in profiles.all_profiles()] == ["My PFA", "Beta PFA"]
    r = web.post("/profiles/p2/rename", data={"name": "my pfa"}, follow_redirects=True)
    assert "already a profile named" in r.text
    assert "no such profile" in web.post("/profiles/p9/rename", data={"name": "Z"}, follow_redirects=True).text


def test_delete_needs_confirmation_keeps_a_backup_and_never_removes_the_last_profile(web):
    web.post("/profiles/new", data={"name": "B"})
    web.post("/expenses", data={"date": f"{YEAR}-01-05", "description": "in B", "amount": "5", "currency": "RON"})
    r = web.post("/profiles/p2/delete", data={}, follow_redirects=True)
    assert "the deletion was not confirmed" in r.text and len(profiles.all_profiles()) == 2
    r = web.post("/profiles/p2/delete", data={"confirm": "1"}, follow_redirects=True)
    assert "Profile B deleted" in r.text and [p["id"] for p in profiles.all_profiles()] == ["p1"]
    assert not (config.home_dir() / "profiles" / "p2").exists()
    (kept,) = (config.home_dir() / "deleted-profiles").iterdir()
    from tracker import backup
    assert backup.inspect_bundle(kept.read_bytes())["counts"]["expenses"] == 1
    assert web.cookies.get("pfa_profile") in (None, "")
    r = web.post("/profiles/p1/delete", data={"confirm": "1"}, follow_redirects=True)
    assert "the only profile cannot be deleted" in r.text


def test_a_backup_restores_into_the_profile_in_use_only(web):
    web.post("/settings", data={"name": "Firm A"})
    web.post("/expenses", data={"date": f"{YEAR}-01-05", "description": "A thing", "amount": "5", "currency": "RON"})
    bundle = web.get("/backup").content
    web.post("/profiles/new", data={"name": "B"})
    r = web.post("/restore", files={"file": ("b.zip", bundle, "application/zip")}, data={"confirm": "1"},
                 follow_redirects=True)
    assert "Backup restored: Firm A" in r.text and "A thing" in web.get("/expenses").text
    web.post("/profile", data={"profile": "p1", "page": "overview"})
    assert "A thing" in web.get("/expenses").text        # A still has its own
    assert len(profiles.all_profiles()) == 2


@pytest.mark.parametrize("damage", ["{ broken", "[]", '{"profiles": "x"}', None])
def test_a_lost_or_damaged_profile_list_is_rebuilt_from_the_folders(web, home, damage):
    web.post("/settings", data={"name": "Firm A"})
    web.post("/profiles/new", data={"name": "B"})
    web.post("/settings", data={"name": "Firm B"})
    if damage is None:
        (home / "profiles.json").unlink()
    else:
        (home / "profiles.json").write_text(damage)
    assert [(p["id"], p["name"]) for p in profiles.all_profiles()] == [("p1", "Firm A"), ("p2", "Firm B")]
    assert not (home / "profiles" / "p3").exists()


# Theme (one per app; saved custom themes shared)

def ui_file():
    return config.ui_settings_path()


def test_the_default_theme_is_light(web):
    view = services.theme_view("web")
    assert view["theme"] == "light" and view["palette"] == theme.LIGHT and view["saved"] == []
    assert "--bg: #f4f6f9" in web.get("/").text


def test_dark_theme_applies_to_every_page_and_is_stored_outside_the_profiles(web):
    r = web.post("/theme", data={"theme": "dark"}, follow_redirects=True)
    assert "Theme set to dark." in r.text
    for path in ("/", "/income", "/expenses", "/settings"):
        page = web.get(path).text
        assert "--bg: #14181d" in page and "color-scheme: dark" in page
    assert json.loads(ui_file().read_text())["apps"]["web"]["theme"] == "dark"
    assert "ui" not in json.loads(config.SETTINGS_PATH.read_text())


def test_the_browser_and_desktop_themes_are_independent(web):
    web.post("/theme", data={"theme": "dark"})
    assert services.theme_view("desktop")["theme"] == "light"
    services.save_theme("desktop", "custom", {"accent": "#0a7a4b", "background": "#ffffff", "surface": "#ffffff", "text": "#111111"})
    assert services.theme_view("web")["theme"] == "dark" and "--bg: #14181d" in web.get("/").text
    web.post("/theme", data={"theme": "light"})
    assert services.theme_view("desktop")["colors"]["accent"] == "#0a7a4b"


def test_switching_profile_keeps_the_theme(web):
    web.post("/theme", data={"theme": "dark"})
    web.post("/profiles/new", data={"name": "B"})
    assert "--bg: #14181d" in web.get("/").text


def test_custom_colours_are_only_shown_when_custom_is_chosen(web):
    html = web.get("/settings").text
    assert '<div id="custom-panel" hidden>' in html
    web.post("/theme", data={"theme": "custom", **theme.DEFAULT_CUSTOM})
    html = web.get("/settings").text
    assert '<div id="custom-panel" >' in html and 'type="color" name="accent"' in html
    assert "showCustom()" in html


def test_custom_colours(web):
    r = web.post("/theme", data={"theme": "custom", "accent": "#7A3FB0", "background": "#F6F3FA", "surface": "#FFFFFF",
                                 "text": "#221B2B"}, follow_redirects=True)
    assert "Theme set to custom." in r.text and "--accent: #7a3fb0" in r.text and "--ink: #221b2b" in r.text
    web.post("/theme", data={"theme": "light"})
    assert services.theme_view("web")["colors"]["accent"] == "#7a3fb0"        # kept for later


def test_a_custom_theme_can_be_saved_by_name_and_used_again_in_both_apps(web):
    colors = {"accent": "#0a7a4b", "background": "#f4faf6", "surface": "#ffffff", "text": "#10251a"}
    r = web.post("/theme", data={"theme": "custom", **colors, "action": "save_as", "saved_name": "  Forest  green "},
                 follow_redirects=True)
    assert "Custom theme “Forest green” saved and in use." in r.text and "using the saved theme “Forest green”" in r.text
    web.post("/theme", data={"theme": "light"})
    assert [s["name"] for s in services.theme_view("desktop")["saved"]] == ["Forest green"]   # offered in the desktop too
    r = web.post("/theme/use", data={"name": "forest green"}, follow_redirects=True)
    assert "Theme “Forest green” in use." in r.text and "--accent: #0a7a4b" in r.text
    assert services.theme_view("desktop")["theme"] == "light"                                 # but not used there


def test_saving_under_the_same_name_replaces(web):
    base = {"theme": "custom", "action": "save_as", "saved_name": "Mine", **theme.DEFAULT_CUSTOM}
    web.post("/theme", data=base)
    r = web.post("/theme", data={**base, "saved_name": "MINE", "accent": "#0a7a4b"}, follow_redirects=True)
    assert "updated" in r.text
    assert [(s["name"], s["colors"]["accent"]) for s in services.theme_view("web")["saved"]] == [("MINE", "#0a7a4b")]


@pytest.mark.parametrize("name, reason", [("", "give the theme a name"), ("   ", "give the theme a name"),
                                          ("x" * 41, "at most 40"), ("a\x01b", "not readable")])
def test_bad_theme_names_are_refused(web, name, reason):
    r = web.post("/theme", data={"theme": "custom", "action": "save_as", "saved_name": name, **theme.DEFAULT_CUSTOM})
    assert "Theme not saved" in r.text and reason in r.text and services.theme_view("web")["saved"] == []


def test_at_most_thirty_saved_themes(web):
    for i in range(30):
        assert services.save_custom_theme("web", f"T{i}", theme.DEFAULT_CUSTOM).ok
    out = services.save_custom_theme("web", "one more", theme.DEFAULT_CUSTOM)
    assert not out.ok and "at most 30" in out.error


def test_deleting_a_saved_theme(web):
    services.save_custom_theme("web", "Mine", theme.DEFAULT_CUSTOM)
    r = web.post("/theme/delete", data={"name": "mine"}, follow_redirects=True)
    assert "Saved theme “mine” deleted." in r.text and services.theme_view("web")["saved"] == []
    assert services.theme_view("web")["theme"] == "custom" and services.theme_view("web")["saved_name"] is None
    assert "no saved theme" in web.post("/theme/delete", data={"name": "mine"}, follow_redirects=True).text
    assert "no saved theme" in web.post("/theme/use", data={"name": "ghost"}, follow_redirects=True).text


@pytest.mark.parametrize("colors, reason", [
    ({"text": "#fefefe", "background": "#ffffff"}, "too hard to read on the background"),
    ({"text": "#777777", "background": "#ffffff", "surface": "#888888"}, "too hard to read"),
    ({"accent": "#fefefe", "surface": "#ffffff"}, "accent colour is too close"),
    ({"accent": "red"}, "must look like #1d5fa8"),
    ({"background": "#12345"}, "must look like"),
    ({"text": "javascript:alert(1)"}, "must look like"),
    ({"surface": "#ffffff; background:url(x)"}, "must look like"),
])
@pytest.mark.parametrize("action", ["apply", "save_as"])
def test_unreadable_or_malformed_custom_colours_are_refused(web, colors, reason, action):
    data = {"theme": "custom", **theme.DEFAULT_CUSTOM, **colors, "action": action, "saved_name": "X"}
    r = web.post("/theme", data=data)
    assert r.status_code == 200 and "Theme not saved" in r.text and reason in r.text
    assert services.theme_view("web")["theme"] == "light" and services.theme_view("web")["saved"] == []
    assert 'id="custom-panel" >' in r.text                    # the refused choice stays on screen


def test_unknown_theme_names_are_refused(web):
    assert "choose light, dark or custom" in web.post("/theme", data={"theme": "neon"}).text


@pytest.mark.parametrize("content", ["{ broken", "[]", '{"apps": {"web": {"theme": "custom", "colors": {"text": "#fff"}}}}',
                                     '{"apps": {"web": {"theme": 5}}, "saved_themes": [1, {"name": "x", "colors": {"text": "#ffffff", "background": "#ffffff"}}]}'])
def test_a_hand_broken_ui_settings_file_falls_back_to_light(web, content):
    ui_file().write_text(content)
    view = services.theme_view("web")
    assert view["theme"] == "light" and view["saved"] == [] and web.get("/settings").status_code == 200


def test_custom_palette_is_derived_readably():
    p = theme.palette("custom", {"accent": "#0a7a4b", "background": "#101418", "surface": "#1a2027", "text": "#f0f4f8"})
    assert p["bad"] == theme.DARK["bad"]                                   # dark base for a dark background
    assert theme.contrast(p["text"], p["background"]) >= 4.5 and theme.contrast(p["accent_text"], p["accent"]) >= 3
    assert all(theme.HEX.fullmatch(v) for v in p.values())


def test_built_in_themes_are_readable():
    for p in (theme.LIGHT, theme.DARK):
        assert theme.contrast(p["text"], p["background"]) >= 7 and theme.contrast(p["text"], p["surface"]) >= 7
        assert theme.contrast(p["muted"], p["surface"]) >= 4.5 and theme.contrast(p["accent_text"], p["accent"]) >= 4.5
        assert theme.contrast(p["bad"], p["err_bg"]) >= 4.5


def test_the_profiles_section_is_at_the_top_of_the_settings_page(web):
    html = web.get("/settings").text
    assert html.index("<h3>Profiles</h3>") < html.index("<h3>Firm</h3>") < html.index("<h3>Theme</h3>")
    assert 'action="/profiles/new"' in html[:html.index("<h3>Firm</h3>")]



# Switching only from Settings → Profiles

def test_the_header_shows_the_profile_but_cannot_switch_it(web):
    web.post("/profiles/new", data={"name": "Second"})
    for path in ("/", "/income", "/expenses", "/settings"):
        html = web.get(path).text
        header = html.split("<main>")[0]
        assert "Profile: <b>Second</b>" in header and "<select" not in header and 'action="/profile"' not in header


def test_each_other_profile_has_a_switch_button_in_settings(web):
    web.post("/profiles/new", data={"name": "Second"})
    html = web.get("/settings").text
    section = html[html.index("<h3>Profiles</h3>"):html.index("<h3>Firm</h3>")]
    assert section.count('action="/profile"') == 1 and 'name="profile" value="p1"' in section
    assert '<button class="secondary" type="submit">Switch</button>' in section and "in use" in section
    r = web.post("/profile", data={"profile": "p1", "page": "settings"}, follow_redirects=False)
    assert r.headers["location"] == "/settings" and "Profile: <b>My PFA</b>" in web.get("/settings").text


def test_profile_deletion_asks_in_a_pop_up_not_with_a_tick_box(web):
    web.post("/profiles/new", data={"name": "Second"})
    html = web.get("/settings").text
    section = html[html.index("<h3>Profiles</h3>"):html.index("<h3>Firm</h3>")]
    assert "sure" not in section and 'type="checkbox"' not in section
    assert section.count('onsubmit="return confirm(') == 2 and "Delete the profile Second with its 0 invoice(s)" in section
    assert section.count('<input type="hidden" name="confirm" value="1">') == 2
    assert section.count('<button class="danger" type="submit">Delete</button>') == 2


def test_a_profile_name_with_quotes_cannot_break_the_pop_up(web):
    import html as html_lib
    name = 'O\'Brien "&" <Co>'
    web.post("/profiles/new", data={"name": name})
    page = web.get("/settings").text
    assert "<Co>" not in page.split("<main>")[1]
    handlers = [html_lib.unescape(h) for h in re.findall(r'onsubmit="([^"]*)"', page) if "Delete the profile" in h]
    expected = "Delete the profile " + name + " with its 0 invoice(s) and 0 expense(s)? A backup of it is saved first."
    assert any(json.loads(h.removeprefix("return confirm(").removesuffix(")")) == expected for h in handlers)
