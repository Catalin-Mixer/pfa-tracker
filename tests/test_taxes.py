"""CAS, CASS and income tax: every band edge, the salaried-job rule, settings fallback, CAEN exclusion."""
import json
from decimal import Decimal

import pytest

from conftest import DEFAULT_SETTINGS, LAST_YEAR, YEAR, write_caen
from tracker import settings, taxes

D = Decimal
Y = LAST_YEAR   # minimum wage 4.000: 12 wages 48.000, 24 wages 96.000, floor 24.000, ceiling 240.000


def calc(income, expenses="0", year=Y):
    return taxes.compute(year, D(income), D(expenses))


@pytest.mark.parametrize("net, cas, cass, tax", [
    ("0", "0.00", "2400.00", "0.00"),                 # nothing earned: still CASS on the floor
    ("10000", "0.00", "2400.00", "760.00"),           # under the floor: CASS on 24.000; tax on 10.000 - 2.400
    ("24000", "0.00", "2400.00", "2160.00"),          # exactly the floor
    ("47999.99", "0.00", "4800.00", "4320.00"),       # one cent under 12 wages: no CAS
    ("48000", "12000.00", "4800.00", "3120.00"),      # exactly 12 wages: CAS on 12 wages
    ("95999.99", "12000.00", "9600.00", "7440.00"),   # one cent under 24 wages
    ("96000", "24000.00", "9600.00", "6240.00"),      # exactly 24 wages: CAS on 24 wages
    ("240000", "24000.00", "24000.00", "19200.00"),   # exactly the ceiling
    ("1000000", "24000.00", "24000.00", "95200.00"),  # far above the ceiling: CASS capped
])
def test_bands_and_edges(net, cas, cass, tax):
    r = calc(net)
    assert (str(r["cas"]), str(r["cass"]), str(r["tax"])) == (cas, cass, tax)
    assert r["total_due"] == r["cas"] + r["cass"] + r["tax"]


def test_expenses_reduce_the_net_income():
    r = calc("100000", "4000.01")
    assert r["net"] == D("95999.99") and r["cas"] == D("12000.00")


def test_a_loss_pays_only_the_cass_floor():
    r = calc("1000", "50000")
    assert r["net"] == D("-49000") and r["cas"] == 0 and r["cass"] == D("2400.00") and r["tax"] == 0
    assert r["taxable"] == 0


def test_ceiling_can_differ_per_year():
    assert calc("1000000", year=YEAR)["cass"] == D("28800.00")      # 72 wages this year
    assert calc("1000000", year=LAST_YEAR)["cass"] == D("24000.00")  # 60 wages last year


def test_salaried_job_drops_the_floor_but_not_the_ceiling(settings_path):
    data = json.loads(settings_path.read_text())
    data["years"][Y]["has_salary"] = True
    settings_path.write_text(json.dumps(data))
    assert calc("10000")["cass"] == D("1000.00")
    assert calc("0")["cass"] == D("0.00")
    assert calc("1000000")["cass"] == D("24000.00")
    assert calc("10000", year=YEAR)["cass"] == D("2400.00")   # other year unchanged


def test_salaried_job_is_never_inherited_by_a_later_year(settings_path):
    data = json.loads(settings_path.read_text())
    data["years"] = {Y: dict(data["years"][Y], has_salary=True)}
    settings_path.write_text(json.dumps(data))
    r = calc("10000", year=YEAR)
    assert r["settings_year"] == Y and r["settings"]["has_salary"] is False and r["cass"] == D("2400.00")


def test_missing_year_uses_the_latest_earlier_year(settings_path):
    r = calc("100000", year=str(int(YEAR) + 5))
    assert r["settings_year"] == YEAR and r["settings_error"] is None and r["cas"] == D("24000.00")


def test_year_before_any_settings_falls_back_to_flat_tax(settings_path):
    r = calc("100000", year="2000")
    assert r["settings"] is None and "no settings for 2000" in r["settings_error"]
    assert r["cas"] == 0 and r["cass"] == 0 and r["tax"] == D("10000.00")


@pytest.mark.parametrize("break_it, expected", [
    (lambda d: d["years"][Y].pop("minimum_wage"), "no settings"),
    (lambda d: d["years"][Y].update(cas_rate="a quarter"), "not a number"),
    (lambda d: d["years"][Y].pop("cass_rate"), "missing"),
    (lambda d: d.update(years="nope"), "no settings"),
    (lambda d: d.update(years={Y: "nope"}), "no settings"),
])
def test_damaged_settings_give_a_message_not_a_crash(settings_path, break_it, expected):
    data = json.loads(settings_path.read_text())
    break_it(data)
    settings_path.write_text(json.dumps(data))
    r = calc("100000")
    assert r["settings"] is None and expected in r["settings_error"]


@pytest.mark.parametrize("content", ["", "{", "[]", "42", '"text"', "\x00\x01"])
def test_unreadable_settings_file_gives_a_message_not_a_crash(settings_path, content):
    settings_path.write_bytes(content.encode("latin-1"))
    r = calc("100000")
    assert r["settings"] is None and r["settings_error"]


def test_missing_settings_file_gives_a_message(settings_path):
    settings_path.unlink()
    assert "not found" in calc("1")["settings_error"]


def test_absurd_values_do_not_crash(settings_path):
    data = json.loads(settings_path.read_text())
    data["years"][Y].update(minimum_wage=0, cass_rate=0, cas_rate=1.5)
    settings_path.write_text(json.dumps(data))
    r = calc("100000")
    assert r["cas"] == D("0.00") and r["cass"] == D("0.00") and r["tax"] == D("10000.00")
    r = calc("1" + "0" * 30)
    assert r["total_due"] > 0


def test_caen_exclusion_sets_income_tax_to_zero_only_in_its_years(caen_path):
    write_caen(caen_path, {"6210": (YEAR, "", "test law")})
    this_year, last_year = calc("100000", year=YEAR), calc("100000", year=LAST_YEAR)
    assert this_year["tax"] == 0 and "6210" in this_year["tax_exempt_note"] and "test law" in this_year["tax_exempt_note"]
    assert this_year["cas"] == D("24000.00") and this_year["cass"] == D("10000.00")
    assert last_year["tax"] == D("6600.00") and last_year["tax_exempt_note"] is None


def test_caen_exclusion_with_an_end_year(caen_path):
    write_caen(caen_path, {"6210": ("", LAST_YEAR, "")})
    assert calc("100000", year=LAST_YEAR)["tax"] == 0
    assert calc("100000", year=YEAR)["tax"] > 0


def test_caen_exclusion_applies_only_to_the_chosen_revision(caen_path, settings_path):
    write_caen(caen_path, {"6210": ("", "", "")})
    data = json.loads(settings_path.read_text())
    data["firm"]["caen_revision"] = "2"
    settings_path.write_text(json.dumps(data))
    assert calc("100000")["tax"] > 0


def test_missing_or_broken_caen_file_means_no_exclusion(caen_path):
    caen_path.write_bytes(b"not a workbook")
    assert calc("100000")["tax"] == D("6600.00")
    caen_path.unlink()
    assert calc("100000")["tax"] == D("6600.00")


def test_all_years_summary_matches_the_database(client):
    from conftest import csv_upload, sample_csv
    client.post("/income/import", files=csv_upload(sample_csv(LAST_YEAR)), data={"year": YEAR})
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-01", "description": "x", "amount": "700", "currency": "RON"})
    rows = {r["year"]: r for r in taxes.all_years()}
    assert rows[LAST_YEAR]["income"] == D("6700.00") and rows[LAST_YEAR]["expenses"] == D("700.00")
    assert rows[LAST_YEAR]["net"] == D("6000.00") and rows[LAST_YEAR]["cass"] == D("2400.00")
    assert rows[LAST_YEAR]["tax"] == D("360.00")


@pytest.mark.parametrize("cui, ok", [
    ("RO14399840", True), ("14399840", True), ("ro 14399840", True), ("14399841", False), ("RO", False),
    ("", False), ("1", False), ("12345678901", False), ("abc", False), ("14399840; DROP TABLE", False),
])
def test_cui_check_digit(cui, ok):
    assert settings.cui_valid(cui) is ok
