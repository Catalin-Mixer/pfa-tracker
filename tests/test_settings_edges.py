"""Earlier-year salaried marks are never lost by a Trade Register typo and never applied invisibly."""
import json
from datetime import date

YEAR = date.today().year
Y2, Y1 = str(YEAR - 2), str(YEAR - 1)


def flags(settings_path):
    return {y: b.get("has_salary") for y, b in json.loads(settings_path.read_text())["years"].items()}


def test_a_typo_in_the_trade_register_does_not_wipe_earlier_salaried_years(client, settings_path):
    good = f"F40/1/{YEAR - 3}"
    client.post("/settings", data={"trade_register": good, "salaried_years": [Y2, Y1]})
    assert flags(settings_path)[Y2] is True
    # a typo: the page comes back with an error; the earlier-year boxes must still be there, ticked
    r = client.post("/settings", data={"trade_register": good.replace("/1/", "/1x/"), "salaried_years": [Y2, Y1]})
    assert "Not saved" in r.text
    assert f'name="salaried_years" value="{Y2}" checked' in r.text


def test_earlier_salaried_years_outside_a_changed_lifetime_are_not_applied_invisibly(client, settings_path):
    from tracker import settings
    client.post("/settings", data={"trade_register": f"F40/1/{YEAR - 3}", "salaried_years": [Y1]})
    client.post("/settings", data={"trade_register": f"F40/1/{YEAR}"})   # registration moved to this year
    page = client.get("/settings").text
    # either the flag is cleared, or it is still shown somewhere the user can untick it
    assert not settings.has_salary(Y1) or f'value="{Y1}"' in page.split("Tax values")[0]


# A missing settings.json is made by the first save (e.g. PFA_DB set, so the tracker uses settings.json in its folder)

def test_saving_settings_makes_a_missing_settings_file(client, settings_path):
    from tracker import config
    settings_path.unlink()
    assert "settings file not found" in client.get("/settings").text           # said until something is saved
    r = client.post("/settings", data={"name": "Test PFA", "cui": "RO14399840"}, follow_redirects=False)
    assert r.status_code == 303
    data = json.loads(settings_path.read_text())
    assert (data["firm"]["name"], data["firm"]["cui"]) == ("Test PFA", "RO14399840")
    # it starts like a new profile: the default tax values per year, no salaried job marked
    template = json.loads(config.default_settings_template().read_text(encoding="utf-8"))["years"]
    assert set(data["years"]) == set(template) | {str(YEAR)}
    for y, block in template.items():
        assert {k: v for k, v in data["years"][y].items() if k != "has_salary"} == \
               {k: v for k, v in block.items() if k != "has_salary"}
    assert not any(b.get("has_salary") for b in data["years"].values())
    page = client.get("/settings").text
    assert "settings file not found" not in page and "Settings saved." in page


def test_a_settings_file_that_cannot_be_read_is_never_replaced(client, settings_path):
    settings_path.write_text("{ not json", encoding="utf-8")
    r = client.post("/settings", data={"name": "Test PFA"})
    assert "Not saved" in r.text and "is not valid JSON" in r.text
    assert settings_path.read_text(encoding="utf-8") == "{ not json"


def test_a_missing_settings_folder_is_made_too(tmp_path, monkeypatch):
    from tracker import services
    target = tmp_path / "new" / "folder" / "settings.json"
    monkeypatch.setenv("PFA_SETTINGS", str(target))
    assert services.save_settings({"name": "Test PFA"}).ok
    assert json.loads(target.read_text())["firm"]["name"] == "Test PFA"
