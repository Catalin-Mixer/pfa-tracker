"""Firm details (Trade Register number, phone, email, IBAN, address, secondary CAEN codes), the salaried-job
rule over the PFA's lifetime, invoice proof names (<number>_<client>) and the same proof checks on both tabs,
using a real PDF (tests/fixtures/invoice.pdf)."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

from conftest import LAST_YEAR, TODAY, YEAR
from files import JPEG, PDF, PNG
from tracker import db, settings, taxes
from tracker.config import ATTACHMENTS_DIR

REAL_PDF = (Path(__file__).parent / "fixtures" / "invoice.pdf").read_bytes()
BEFORE = str(int(YEAR) - 3)


def save(http, **fields):
    data = {"name": "Test PFA", "caen_code": "6210", "caen_revision": "3"}
    data.update(fields)
    return http.post("/settings", data=data, follow_redirects=False)


def saved(settings_path):
    return json.loads(settings_path.read_text())


# Firm fields

@pytest.mark.parametrize("text, expected", [
    ("F40/1234/2019", "F40/1234/2019"), ("f40/1234/2019", "F40/1234/2019"), (" F 5 / 12 / 2020 ", "F5/12/2020"),
    ("F01/7/1990", "F01/7/1990"), ("F52/99/15.03.2021", "F52/99/15.03.2021"), ("F51/1234567/2020", "F51/1234567/2020"),
])
def test_trade_register_numbers_accepted(text, expected):
    assert settings.clean_trade_register(text) == expected


@pytest.mark.parametrize("text, reason", [
    ("J40/1234/2019", "must look like"), ("F41/1/2019", "must look like"), ("F0/1/2019", "must look like"),
    ("F40/0/2019", "must look like"), ("F40/12345678/2019", "must look like"), ("F40/1/19", "must look like"),
    ("F40-1234-2019", "must look like"), ("40/1234/2019", "must look like"), ("", "must look like"),
    ("F40/1/1989", "not possible"), ("F40/1/2999", "must look like"), (f"F40/1/{int(YEAR) + 1}", "not possible"), ("F40/1/31.02.2020", "does not exist"),
    ("F40/1/2019; DROP TABLE", "must look like"), ("Ｆ40/1/2019", "must look like"),
])
def test_trade_register_numbers_refused(text, reason):
    with pytest.raises(ValueError, match=reason):
        settings.clean_trade_register(text)


def test_registration_date_in_the_future_is_refused():
    future = date(TODAY.year, 12, 31)
    if future > TODAY:
        with pytest.raises(ValueError, match="in the future"):
            settings.clean_trade_register(f"F40/1/{future:%d.%m.%Y}")


@pytest.mark.parametrize("text, expected", [
    ("0721 234 567", "+40721234567"), ("0721.234.567", "+40721234567"), ("+40 721 234 567", "+40721234567"),
    ("0040721234567", "+40721234567"), ("021 123 45 67", "+40211234567"), ("0256-123-456", "+40256123456"),
    ("+49 30 1234567", "+49301234567"), ("0049 30 1234567", "+49301234567"), ("(0721) 234-567", "+40721234567"),
    ("+40 (0)721 234 567", "+40721234567"), ("0800 800 800", "+40800800800"),
])
def test_phone_numbers_accepted(text, expected):
    assert settings.clean_phone(text) == expected


@pytest.mark.parametrize("text", ["0721 234 56", "0721 234 5678", "0121234567", "123", "abc", "+", "+40",
                                  "+0 123 456 789", "++40721234567", "0721-ABC-567", "+1234567890123456"])
def test_phone_numbers_refused(text):
    with pytest.raises(ValueError):
        settings.clean_phone(text)


@pytest.mark.parametrize("text", ["ion.popescu@example.ro", "a+b@mail.co.uk", "X@Y.IO", "first_last@sub.domain-x.com"])
def test_email_addresses_accepted(text):
    assert settings.clean_email(text) == text.lower()


@pytest.mark.parametrize("text", ["", "plain", "@example.ro", "a@", "a@b", "a@b.", "a@.ro", "a..b@x.ro", ".a@x.ro",
                                  "a.@x.ro", "a b@x.ro", "a@x_y.ro", "a@-x.ro", "a@x.r", "<script>@x.ro",
                                  "a@" + "x" * 250 + ".ro"])
def test_email_addresses_refused(text):
    with pytest.raises(ValueError):
        settings.clean_email(text)


@pytest.mark.parametrize("text, expected", [
    ("RO49 AAAA 1B31 0075 9384 0000", "RO49 AAAA 1B31 0075 9384 0000"),
    ("ro49aaaa1b31007593840000", "RO49 AAAA 1B31 0075 9384 0000"),
    ("RO49-AAAA-1B31-0075-9384-0000", "RO49 AAAA 1B31 0075 9384 0000"),
    ("DE89 3704 0044 0532 0130 00", "DE89 3704 0044 0532 0130 00"),
    ("GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 32"),
])
def test_ibans_accepted(text, expected):
    assert settings.clean_iban(text) == expected


@pytest.mark.parametrize("text, reason", [
    ("RO49 AAAA 1B31 0075 9384 0001", "check digits do not match"),
    ("RO48 AAAA 1B31 0075 9384 0000", "check digits do not match"),
    ("RO49 AAAA 1B31 0075 9384 000", "Romanian IBAN"),
    ("RO49 1111 1B31 0075 9384 0000", "Romanian IBAN"),
    ("DE89 3704 0044 0532 0130 0", "has 22 characters"),
    ("", "not valid"), ("RO", "not valid"), ("1234567890123456", "not valid"), ("RO49 AAAA 1B31 0075 9384 00Ö0", "not valid"),
])
def test_ibans_refused(text, reason):
    with pytest.raises(ValueError, match=reason):
        settings.clean_iban(text)


@pytest.mark.parametrize("text, ok", [
    ("Str. Exemplu nr. 1, București, sector 1", True), ("  Bd.   Unirii 5,  Iași  ", True), ("Ap 1", False),
    ("12345", False), ("x" * 201, False), ("Str.\x00 1, Cluj", False),
])
def test_addresses(text, ok):
    if ok:
        assert settings.clean_address(text) == " ".join(text.split())
    else:
        with pytest.raises(ValueError):
            settings.clean_address(text)


def test_secondary_caen_codes_are_cleaned_and_checked():
    assert settings.clean_caen_list("6220, 70.22;6220  7022", "6210") == ["6220", "7022"]
    assert settings.clean_caen_list("", "6210") == []
    for bad, reason in (("62", "4 digits"), ("abcd", "4 digits"), ("6210", "already the main"),
                        (", ".join(str(1000 + i) for i in range(31)), "at most 30")):
        with pytest.raises(ValueError, match=reason):
            settings.clean_caen_list(bad, "6210")


def test_all_firm_fields_are_saved_cleaned_and_shown(client, settings_path):
    r = save(client, trade_register="f40/1234/2019", phone="0721 234 567", email="Ion@Example.RO",
             iban="ro49aaaa1b31007593840000", address="Str. Exemplu nr. 1,  București", secondary_caen="6220, 69.20")
    assert r.status_code == 303
    firm = saved(settings_path)["firm"]
    assert firm["trade_register"] == "F40/1234/2019" and firm["phone"] == "+40721234567"
    assert firm["email"] == "ion@example.ro" and firm["iban"] == "RO49 AAAA 1B31 0075 9384 0000"
    assert firm["address"] == "Str. Exemplu nr. 1, București" and firm["secondary_caen"] == ["6220", "6920"]
    html = client.get("/settings").text
    assert "Registered in 2019." in html and "6920 – Activități de contabilitate" in html
    assert 'value="RO49 AAAA 1B31 0075 9384 0000"' in html


def test_every_wrong_field_is_reported_at_once_and_nothing_is_saved(client, settings_path):
    before = settings_path.read_text()
    r = save(client, trade_register="J1/1/1", phone="12", email="nope", iban="RO00", address="x", secondary_caen="62")
    assert r.status_code == 200 and "Not saved" in r.text
    for reason in ("must look like F40/1234/2019", "phone number is not valid", "email address is not valid",
                   "IBAN is not valid", "address should be", "4 digits each"):
        assert reason in r.text, reason
    assert 'value="nope"' in r.text and settings_path.read_text() == before


def test_empty_optional_fields_are_fine(client, settings_path):
    assert save(client).status_code == 303
    assert saved(settings_path)["firm"]["iban"] == "" and saved(settings_path)["firm"]["secondary_caen"] == []


def test_unknown_secondary_code_is_saved_with_a_warning(client):
    save(client, secondary_caen="9999")
    assert "not a CAEN Rev.3 code in the list" in client.get("/settings").text


# Salaried job: a year-less checkbox for now, earlier years only within the PFA's lifetime

def test_the_checkbox_has_no_year_and_sets_only_the_current_year(client, settings_path):
    data = saved(settings_path)
    data["years"][LAST_YEAR]["has_salary"] = True
    settings_path.write_text(json.dumps(data))
    html = client.get("/settings").text
    assert "I have a salaried job</label>" in html and f"salaried job in {YEAR}" not in html.lower()
    save(client, salaried_now="1")
    years = saved(settings_path)["years"]
    assert years[YEAR]["has_salary"] is True and years[LAST_YEAR]["has_salary"] is True     # untouched
    save(client)
    years = saved(settings_path)["years"]
    assert years[YEAR]["has_salary"] is False and years[LAST_YEAR]["has_salary"] is True    # still untouched


def test_earlier_years_are_offered_only_within_the_pfa_lifetime(client):
    html = client.get("/settings").text
    assert 'name="salaried_years"' not in html and "Enter the Trade Register number" in html
    save(client, trade_register=f"F40/1/{YEAR}")
    html = client.get("/settings").text
    assert 'name="salaried_years"' not in html and "registered this year" in html
    save(client, trade_register=f"F40/1/{BEFORE}")
    html = client.get("/settings").text
    offered = [y for y in (str(y) for y in range(int(BEFORE) - 2, int(YEAR) + 1))
               if f'name="salaried_years" value="{y}"' in html]
    assert offered == [str(y) for y in range(int(BEFORE), int(YEAR))]


def test_earlier_years_are_saved_and_change_the_tax_of_those_years(client, settings_path):
    save(client, trade_register=f"F40/1/{BEFORE}", salaried_years=[LAST_YEAR])
    assert saved(settings_path)["years"][LAST_YEAR]["has_salary"] is True
    assert taxes.compute(LAST_YEAR, 10000, 0)["cass"] == 1000           # floor dropped in that year
    assert taxes.compute(YEAR, 10000, 0)["cass"] == 2400                # not in the current year
    shown = ",".join(str(y) for y in range(int(BEFORE), int(YEAR)))
    save(client, trade_register=f"F40/1/{BEFORE}", salaried_shown=shown)          # the box unticked in the page
    assert saved(settings_path)["years"][LAST_YEAR]["has_salary"] is False


def test_years_not_shown_in_the_page_are_never_changed(client, settings_path):
    save(client, trade_register=f"F40/1/{BEFORE}", salaried_years=[LAST_YEAR])
    save(client, trade_register=f"F40/1/{BEFORE}")                                 # no list sent at all
    assert saved(settings_path)["years"][LAST_YEAR]["has_salary"] is True


def test_a_mark_before_the_registration_year_is_shown_as_ignored_and_can_be_removed(client, settings_path):
    save(client, trade_register=f"F40/1/{BEFORE}", salaried_years=[LAST_YEAR])
    save(client, trade_register=f"F40/1/{YEAR}")                                   # registration moved to this year
    assert settings.has_salary(LAST_YEAR) is False                                # ignored in the taxes
    html = client.get("/settings").text
    assert f'name="salaried_years" value="{LAST_YEAR}" checked' in html and "outside the PFA's lifetime" in html
    save(client, trade_register=f"F40/1/{YEAR}", salaried_shown=LAST_YEAR)        # unticked
    assert saved(settings_path)["years"][LAST_YEAR]["has_salary"] is False


def test_hand_edited_true_strings_do_not_count(settings_path):
    data = saved(settings_path)
    data["years"][LAST_YEAR]["has_salary"] = "false"
    settings_path.write_text(json.dumps(data))
    assert settings.has_salary(LAST_YEAR) is False


def test_broken_year_blocks_give_a_message_not_a_crash(client, settings_path):
    data = saved(settings_path)
    data["years"][LAST_YEAR] = "x"
    settings_path.write_text(json.dumps(data))
    r = save(client, trade_register=f"F40/1/{BEFORE}", salaried_years=[LAST_YEAR])
    assert r.status_code == 200 and "Not saved" in r.text and "not a year block" in r.text


@pytest.mark.parametrize("years", [[str(int(BEFORE) - 1)], [YEAR], [str(int(YEAR) + 1)], ["abc"], ["1999", LAST_YEAR]])
def test_years_outside_the_lifetime_are_refused(client, settings_path, years):
    before = settings_path.read_text()
    r = save(client, trade_register=f"F40/1/{BEFORE}", salaried_years=years)
    assert r.status_code == 200 and "years in which the PFA existed" in r.text
    assert settings_path.read_text() == before


def test_earlier_years_need_a_valid_trade_register_number(client, settings_path):
    before = settings_path.read_text()
    r = save(client, trade_register="nonsense", salaried_years=[LAST_YEAR])
    assert "Not saved" in r.text and "must look like F40/1234/2019" in r.text
    assert settings_path.read_text() == before
    r = save(client, salaried_years=[LAST_YEAR])                                   # no number at all
    assert "years in which the PFA existed (none yet)" in r.text


# Invoice proof names: <number>_<client>

def add_invoice(http, number="PFA-2026-001", client="Nordwind Software GmbH", name="scan.pdf", data=PDF, **extra):
    fields = {"date": f"{LAST_YEAR}-05-05", "client": client, "number": number, "amount": "100", "currency": "RON"}
    fields.update(extra)
    return http.post("/income/add", data=fields, files={"attachment": (name, data, "application/pdf")},
                     follow_redirects=False)


@pytest.mark.parametrize("number, client_name, expected", [
    ("PFA-2026-001", "Nordwind Software GmbH", "PFA-2026-001_Nordwind_Software_GmbH.pdf"),
    ("F 12/2025", "Ștefan & Fiii SRL", "F_12_2025_Stefan_Fiii_SRL.pdf"),
    ("", "Acme", "no-number_Acme.pdf"),
    ("../../etc", "passwd", "etc_passwd.pdf"),
    ("1", "Ţară Ăîâ", "1_Tara_Aia.pdf"),
    ("CON", "x", "CON_x.pdf"),
    ("🙂", "🙂", "no-number_client.pdf"),
    ("N" * 200, "Client", "N" * 40 + "_Client.pdf"),
])
def test_invoice_proofs_are_named_number_client(client, number, client_name, expected):
    r = client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": client_name, "number": number,
                                         "amount": "100", "currency": "RON"},
                    files={"attachment": ("whatever.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    assert os.listdir(ATTACHMENTS_DIR / "invoices") == [expected]
    dl = client.get("/income/1/attachment")
    assert dl.content == PDF and f'filename="{expected}"' in dl.headers["content-disposition"]


def test_same_number_and_client_twice_gets_a_suffix(client):
    add_invoice(client)
    add_invoice(client, date=f"{LAST_YEAR}-06-06", name="other.png", data=PNG)
    assert sorted(os.listdir(ATTACHMENTS_DIR / "invoices")) == ["PFA-2026-001_Nordwind_Software_GmbH.pdf",
                                                                "PFA-2026-001_Nordwind_Software_GmbH.png"]
    add_invoice(client, date=f"{LAST_YEAR}-07-07")
    assert "PFA-2026-001_Nordwind_Software_GmbH-2.pdf" in os.listdir(ATTACHMENTS_DIR / "invoices")


def test_names_differing_only_in_case_do_not_collide_on_any_system(client):
    add_invoice(client, number="a1", client="acme")
    add_invoice(client, number="A1", client="ACME")
    assert sorted(os.listdir(ATTACHMENTS_DIR / "invoices")) == ["A1_ACME-2.pdf", "a1_acme.pdf"]


def test_windows_reserved_names_are_avoided():
    from tracker import attachments
    assert attachments.invoice_base_name("", "x").startswith("no-number")
    assert attachments.save("invoices", 1, "a.pdf", PDF, base_name="CON") == "proof_CON.pdf"
    assert attachments.save("invoices", 2, "a.pdf", PDF, base_name="nul.txt") .startswith("proof_")


def test_very_long_client_names_are_shortened(client):
    add_invoice(client, client="C" * 500)
    assert os.listdir(ATTACHMENTS_DIR / "invoices") == ["PFA-2026-001_" + "C" * 70 + ".pdf"]


def test_expense_proofs_keep_their_own_names(client):
    client.post("/expenses", data={"date": f"{LAST_YEAR}-06-06", "description": "Desk", "amount": "1", "currency": "RON"},
                files={"attachment": ("bon fiscal.pdf", PDF, "application/pdf")})
    assert os.listdir(ATTACHMENTS_DIR / "expenses") == ["1_bon_fiscal.pdf"]
    assert 'filename="bon_fiscal.pdf"' in client.get("/expenses/1/attachment").headers["content-disposition"]


# The same proof checks on both tabs, with the real PDF from the user

def post_proof(http, tab, name, data):
    if tab == "income":
        fields, url = {"date": f"{LAST_YEAR}-05-05", "client": "Acme", "number": "F1", "amount": "1", "currency": "RON"}, "/income/add"
    else:
        fields, url = {"date": f"{LAST_YEAR}-05-05", "description": "Desk", "amount": "1", "currency": "RON"}, "/expenses"
    return http.post(url, data=fields, files={"attachment": (name, data, "application/pdf")}, follow_redirects=False)


def rows(table):
    with db.connect() as con:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("tab", ["income", "expenses"])
def test_the_real_pdf_is_accepted_on_both_tabs(client, tab):
    assert post_proof(client, tab, "invoice.pdf", REAL_PDF).status_code == 303
    url = "/income/1/attachment" if tab == "income" else "/expenses/1/attachment"
    assert client.get(url).content == REAL_PDF


@pytest.mark.parametrize("tab", ["income", "expenses"])
@pytest.mark.parametrize("name, data, reason", [
    ("invoice.pdf", REAL_PDF[: len(REAL_PDF) // 2], "damaged or unreadable PDF"),
    ("invoice.pdf", REAL_PDF[:1024], "damaged or unreadable PDF"),
    ("invoice.pdf", b"junk" + REAL_PDF, "not a PDF file"),
    ("invoice.png", REAL_PDF, "not a readable PNG image"),
    ("invoice.jpg", REAL_PDF, "not a readable JPEG image"),
    ("invoice.txt", REAL_PDF, "not a UTF-8 text file"),
    ("invoice.xml", REAL_PDF, "not well-formed XML"),
    ("invoice.pdf", JPEG, "not a PDF file"),
    ("invoice.pdf.exe", REAL_PDF, "not an allowed type"),
    ("invoice.pdf", b"", "attachment is empty"),
    ("invoice.pdf", REAL_PDF.replace(b"/Type /Page", b"/Type /Gone").replace(b"/Type/Page", b"/Type/Gone"), "damaged or unreadable PDF"),
])
def test_the_same_proof_checks_apply_on_both_tabs(client, tab, name, data, reason):
    r = post_proof(client, tab, name, data)
    assert r.status_code == 200 and reason in r.text
    assert rows("invoices" if tab == "income" else "expenses") == 0
    folder = ATTACHMENTS_DIR / ("invoices" if tab == "income" else "expenses")
    assert not folder.exists() or os.listdir(folder) == []
