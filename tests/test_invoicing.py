"""Invoicing in the core: the lists of clients, products/services and series, and issuing an invoice
that is numbered from its series, printed as a PDF and added to Income in one step."""
import io
import json
import threading
from decimal import Decimal

import pytest

from conftest import LAST_YEAR, TODAY, YEAR
from tracker import attachments, config, db, invoicing, profiles, services
from tracker.config import ATTACHMENTS_DIR

D1 = f"{LAST_YEAR}-05-05"      # a weekday-independent date inside the test rates (EUR 5, USD 4)


@pytest.fixture
def firm(settings_path):
    """The test firm with the address an invoice needs."""
    data = json.loads(settings_path.read_text())
    data["firm"].update({"address": "Str. Științei 1, Iași", "iban": "RO49AAAA1B31007593840000",
                         "email": "pfa@example.ro"})
    settings_path.write_text(json.dumps(data), encoding="utf-8")


def client_fields(**over):
    return {"name": "Carpathia SRL", "tax_id": "RO14399840", "reg_number": "J40/1234/2020",
            "address": "Bd. Unirii 10", "city": "București", "country": "RO", "email": "Facturi@Carpathia.ro",
            "phone": "0721 234 567", **over}


def add_client(**over) -> int:
    out = services.save_client(client_fields(**over))
    assert out.ok, out.error
    return out.extra["id"]


def add_product(**over) -> int:
    out = services.save_product({"name": "Programare", "description": "Dezvoltare software", "unit": "hour",
                                 "unit_price": "100", "currency": "RON", **over})
    assert out.ok, out.error
    return out.extra["id"]


def add_series(name="PFA", next_number="1", **over) -> int:
    out = services.save_series({"name": name, "next_number": next_number, **over})
    assert out.ok, out.error
    return out.extra["id"]


def issue(series_id, client_id, lines, **over):
    return services.generate_invoice({"series_id": series_id, "client_id": client_id, "date": D1,
                                      "lines": lines, **over})


def income_rows():
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM invoices ORDER BY id")]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


# Clients

def test_a_client_needs_name_tax_id_address_city_and_email():
    out = services.save_client({"country": "RO"})
    assert not out.ok
    for words in ("name is missing", "tax identification number (CUI) is missing", "address is missing",
                  "city is missing", "email address is missing"):
        assert words in out.error
    assert invoicing.list_clients() == []


def test_client_values_are_cleaned():
    add_client()
    c = invoicing.list_clients()[0]
    assert (c["tax_id"], c["email"], c["phone"], c["reg_number"]) == ("RO14399840", "facturi@carpathia.ro",
                                                                      "+40721234567", "J40/1234/2020")


@pytest.mark.parametrize("tax_id, ok", [
    ("14399840", True), ("RO 143 998 40", True), ("ro14399840", True), ("14399841", False), ("1", False),
    ("RO1800101221144", False),          # a CNP never has the RO prefix
    ("1800101221144", True), ("1800101221145", False), ("ABC", False), ("", False)])
def test_romanian_tax_ids_are_checked(tax_id, ok):
    assert services.save_client(client_fields(tax_id=tax_id)).ok is ok


@pytest.mark.parametrize("country, tax_id, ok", [
    ("DE", "DE123456789", True), ("DE", "FR123456789", False), ("GR", "EL123456789", True),
    ("GR", "GR123456789", False),
    ("US", "12-3456789", True), ("US", "XX", False), ("GB", "GB 123 4567 89", True), ("XX", "DE123456789", False)])
def test_foreign_tax_ids_are_checked(country, tax_id, ok):
    assert services.save_client(client_fields(country=country, tax_id=tax_id)).ok is ok


def test_the_same_firm_cannot_be_added_twice_even_written_differently():
    add_client(tax_id="RO14399840")
    out = services.save_client(client_fields(name="Other", tax_id="14399840"))
    assert not out.ok and "already has the tax number" in out.error
    assert services.save_client(client_fields(name="Same tax no., other country", country="DE",
                                              tax_id="DE14399840")).ok


def test_absurd_client_names():
    assert services.save_client(client_fields(name="Ωmega Ștefănescu & Fiii SRL")).ok
    for bad in ("12345", "x" * 201, "Bad\x00Name", "   "):
        assert not services.save_client(client_fields(name=bad, tax_id="18547290")).ok


@pytest.mark.parametrize("kind, fields", [
    ("client", {"name": "🦄 Unicorn SRL"}), ("client", {"address": "Str. Rachetei 🚀 1"}), ("client", {"city": "Iași 🛫"}),
    ("product", {"name": "Support 🛠"}), ("product", {"description": "line one\nline 🎉"})])
def test_what_the_invoice_font_cannot_print_is_refused(kind, fields):
    if kind == "client":
        out = services.save_client(client_fields(**fields))
    else:
        out = services.save_product({"name": "P", "unit": "day", "unit_price": "1", "currency": "RON", **fields})
    assert not out.ok and "cannot be printed on the invoice" in out.error


def test_emoji_in_the_notes_are_refused(firm):
    out = issue(add_series(), add_client(), [{"product_id": add_product()}], notes="Mulțumim! 🙏")
    assert not out.ok and "these characters in the notes cannot be printed on the invoice: 🙏" in out.error


def test_a_client_can_be_changed_and_deleted():
    cid = add_client()
    out = services.save_client(client_fields(email="new@carpathia.ro"), cid)
    assert out.ok and services.client_for_edit(cid)["email"] == "new@carpathia.ro"
    assert services.delete_client(cid).ok
    assert services.client_for_edit(cid) is None
    assert not services.delete_client(cid).ok
    assert not services.save_client(client_fields(), cid).ok
    assert not services.save_client(client_fields(), "abc").ok


def test_a_failed_client_save_gives_the_form_back():
    out = services.save_client(client_fields(email="not an email"))
    assert not out.ok and out.form["email"] == "not an email" and out.form["name"] == "Carpathia SRL"


# Products and services

def test_a_product_needs_a_name_unit_price_and_currency():
    out = services.save_product({})
    assert not out.ok
    for words in ("name is missing", "choose the unit", "price per unit", "choose the currency"):
        assert words in out.error


@pytest.mark.parametrize("price, ok", [("100", True), ("1.234,56", True), ("0,0001", True), ("0", False),
                                       ("-5", False), ("1,23456", False), ("abc", False), ("1e9", False),
                                       ("€5", False), ("5 RON", False), ("999.999.999,9999", True),
                                       ("1.000.000.000", False)])
def test_product_prices(price, ok):
    assert services.save_product({"name": f"P {price}", "unit": "day", "unit_price": price,
                                  "currency": "EUR"}).ok is ok


def test_product_currency_must_have_rates_and_unit_must_be_known():
    assert not services.save_product({"name": "A", "unit": "day", "unit_price": "1", "currency": "JPY"}).ok
    assert not services.save_product({"name": "A", "unit": "parsec", "unit_price": "1", "currency": "EUR"}).ok
    assert services.save_product({"name": "A", "unit": "day", "unit_price": "1", "currency": "eur"}).ok
    assert invoicing.list_products()[0]["currency"] == "EUR"


def test_product_names_are_unique_ignoring_case():
    pid = add_product(name="Consultanță")
    assert not services.save_product({"name": "CONSULTANȚĂ", "unit": "day", "unit_price": "1", "currency": "RON"}).ok
    assert services.save_product({"name": "Consultanță", "unit": "day", "unit_price": "2", "currency": "RON"}, pid).ok
    assert services.product_for_edit(pid)["unit_price"] == "2,00"
    assert services.delete_product(pid).ok and invoicing.list_products() == []


# Series

def test_series_names_and_numbers_are_checked():
    for name, number in (("", "1"), ("1AB", "1"), ("ABCDEFGHIJK", "1"), ("A-B", "1"), ("PFA", "0"),
                         ("PFA", "abc"), ("PFA", "10000000"), ("PFA", "-1"), ("PFA", "1.5")):
        assert not services.save_series({"name": name, "next_number": number}).ok, (name, number)
    assert services.save_series({"name": " pfa ", "next_number": ""}).ok
    assert invoicing.list_series()[0]["name"] == "PFA" and invoicing.list_series()[0]["next"] == "PFA-0001"
    assert not services.save_series({"name": "Pfa", "next_number": "5"}).ok


def test_the_first_series_is_the_default_and_the_default_can_move():
    a = add_series("A")
    b = add_series("B")
    assert [s["is_default"] for s in invoicing.list_series()] == [True, False]
    add_series("C", is_default="1")
    assert [s["name"] for s in invoicing.list_series() if s["is_default"]] == ["C"]
    services.delete_series(invoicing.list_series()[2]["id"])
    assert [s["name"] for s in invoicing.list_series() if s["is_default"]] == ["A"]
    assert services.invoicing_view()["default_series"] == a and b


# Issuing

def test_issuing_numbers_the_invoice_adds_it_to_income_and_stores_the_pdf(firm):
    s, c, p = add_series(), add_client(), add_product()
    out = issue(s, c, [{"product_id": p, "quantity": "10"}, {"product_id": "", "quantity": ""},
                       {"product_id": p, "quantity": "2,5", "price": "200"}], due_date=f"{LAST_YEAR}-05-20")
    assert out.ok, out.error
    assert out.extra["number"] == "PFA-0001" and out.year == LAST_YEAR
    assert "PFA-0001" in out.message and "1.500,00 RON" in out.message
    row = income_rows()[0]
    assert (row["number"], row["client"], row["amount"], row["currency"], row["source"]) == (
        "PFA-0001", "Carpathia SRL", "1500", "RON", "invoicing")
    assert row["attachment"] == "PFA-0001_Carpathia_SRL.pdf"
    stored = (ATTACHMENTS_DIR / "invoices" / row["attachment"]).read_bytes()
    text = pdf_text(stored)
    for words in ("FACTURĂ", "PFA", "0001", "Carpathia SRL", "RO14399840", "Test PFA", "Științei", "1.500,00",
                  "Neplătitor de TVA", "Programare", "Dezvoltare software"):
        assert words in text, words
    assert services.invoice_for_edit(row["id"])["generated"] is True
    assert invoicing.list_series()[0]["next"] == "PFA-0002"


def test_numbers_go_up_per_series_and_follow_a_preset_start(firm):
    a, b = add_series("A"), add_series("B", "100")
    c, p = add_client(), add_product()
    numbers = [issue(s, c, [{"product_id": p, "quantity": "1"}]).extra["number"] for s in (a, a, b, a, b)]
    assert numbers == ["A-0001", "A-0002", "B-0100", "A-0003", "B-0101"]
    assert [i["full_number"] for i in services.invoicing_view()["issued"]] == ["B-0101", "A-0003", "B-0100",
                                                                             "A-0002", "A-0001"]


def test_the_next_number_cannot_go_back_over_issued_invoices(firm):
    s, c, p = add_series(), add_client(), add_product()
    issue(s, c, [{"product_id": p}])
    issue(s, c, [{"product_id": p}])
    out = services.save_series({"name": "PFA", "next_number": "2"}, s)
    assert not out.ok and "PFA-0002 was already issued" in out.error
    assert services.save_series({"name": "PFA", "next_number": "50"}, s).ok
    assert issue(s, c, [{"product_id": p}]).extra["number"] == "PFA-0050"
    # a deleted series re-created under the same name continues after its invoices
    services.delete_series(s)
    assert not services.save_series({"name": "PFA", "next_number": "1"}).ok


def test_income_follows_the_payment_date_and_its_rate(firm):
    s, c = add_series(), add_client()
    p = add_product(name="Support", unit="month", unit_price="1000", currency="EUR")
    out = issue(s, c, [{"product_id": p, "quantity": "1,5"}], date=f"{LAST_YEAR}-12-20", payment_date=f"{YEAR}-01-06")
    assert out.ok, out.error and out.year == YEAR
    row = income_rows()[0]
    assert (row["amount"], row["currency"], row["income_date"], row["amount_ron"]) == ("1500", "EUR", f"{YEAR}-01-06", "7500.00")
    assert "Curs BNR" in pdf_text(services.issued_pdf(out.extra["issued_id"])[1])
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    vat = issue(s, c, [{"product_id": p, "quantity": "1"}], date=f"{YEAR}-01-06")
    assert "TVA / VAT: 1.050,00 RON" in pdf_text(services.issued_pdf(vat.extra["issued_id"])[1])


def test_a_vat_payer_invoice_adds_only_the_amount_without_vat_to_income(firm):
    assert services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "30"}).ok
    s, c, p = add_series(), add_client(), add_product(unit_price="1000")
    out = issue(s, c, [{"product_id": p, "quantity": "1"}])
    assert out.ok and "1.210,00 RON" in out.message and "without VAT: 1.000,00" in out.message
    assert income_rows()[0]["amount"] == "1000"
    text = pdf_text(services.issued_pdf(out.extra["issued_id"])[1])
    assert "TVA / VAT 21%" in text and "1.210,00" in text and "Neplătitor" not in text


@pytest.mark.parametrize("values", [{"vat_rate": "0"}, {"vat_rate": "100"}, {"vat_rate": "abc"}, {"vat_rate": "1e1"},
                                    {"vat_rate": "19,555"}, {"due_days": "-1"}, {"due_days": "366"}])
def test_invoice_settings_are_checked(values):
    assert not services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15", **values}).ok


def test_the_default_due_date_follows_the_payment_term():
    services.save_invoicing_settings({"vat_payer": "", "vat_rate": "21", "due_days": "0"})
    assert services.invoicing_view()["default_due"] == ""
    services.save_invoicing_settings({"vat_payer": "", "vat_rate": "21", "due_days": "10"})
    assert services.invoicing_view()["default_due"] > services.invoicing_view()["today"]


@pytest.mark.parametrize("change, words", [
    ({"lines": []}, "add at least one product"),
    ({"lines": [{"product_id": "", "quantity": "3"}]}, "line 1: choose a product"),
    ({"lines": [{"product_id": "999", "quantity": "1"}]}, "no longer exists"),
    ({"lines": [{"product_id": "P", "quantity": "0"}]}, "quantity must be more than 0"),
    ({"lines": [{"product_id": "P", "quantity": "1,2345"}]}, "at most 3 decimals"),
    ({"lines": [{"product_id": "P", "quantity": "1", "price": "-1"}]}, "price must be more than 0"),
    ({"lines": [{"product_id": "P", "quantity": "1", "price": "0,0001"}]}, "rounds to 0"),
    ({"date": ""}, "invoice date is missing"),
    ({"date": (TODAY.replace(year=TODAY.year + 1)).isoformat()}, "in the future"),
    ({"date": "1999-12-31"}, "before 2000"),
    ({"due_date": f"{LAST_YEAR}-05-01"}, "due date is before"),
    ({"payment_date": f"{LAST_YEAR}-05-01"}, "payment is before"),
    ({"series_id": ""}, "choose a series"),
    ({"client_id": "abc"}, "choose a client"),
    ({"notes": "x" * 1001}, "notes"),
])
def test_bad_invoices_are_refused_and_use_no_number(firm, change, words):
    s, c, p = add_series(), add_client(), add_product()
    fields = {"lines": [{"product_id": p, "quantity": "1"}], **change}
    fields["lines"] = [{**line, "product_id": p if line.get("product_id") == "P" else line.get("product_id")}
                       for line in fields["lines"]]
    out = issue(change.get("series_id", s), change.get("client_id", c), fields.pop("lines"),
                **{k: v for k, v in fields.items() if k not in ("series_id", "client_id")})
    assert not out.ok and words in out.error, out.error
    assert out.form["lines"] == [{k: str(v or "") for k, v in line.items()} | {"price": str(line.get("price", "") or "")}
                                 for line in out.form["lines"]]
    assert income_rows() == [] and invoicing.list_series()[0]["next"] == "PFA-0001"
    assert not (ATTACHMENTS_DIR / "invoices").exists() or not any((ATTACHMENTS_DIR / "invoices").iterdir())


@pytest.mark.parametrize("quantity, value", [("1,250", "125"), ("0,001", "0.1"), ("2", "200"), ("1.5", "150"),
                                             ("1.250", "125000"), ("1.234,5", "123450"), ("1 000", "100000")])
def test_a_single_comma_in_a_quantity_is_always_the_decimal_mark(firm, quantity, value):
    s, c, p = add_series(), add_client(), add_product()
    out = issue(s, c, [{"product_id": p, "quantity": quantity}])
    assert out.ok, out.error
    assert income_rows()[0]["amount"] == value


def test_a_form_sent_twice_issues_one_invoice(firm):
    s, c, p = add_series(), add_client(), add_product()
    token = services.new_invoice_token()
    first = issue(s, c, [{"product_id": p}], token=token)
    again = issue(s, c, [{"product_id": p}], token=token)
    assert first.ok and not again.ok and again.extra["already_issued"]
    assert "already used to issue invoice PFA-0001" in again.error and again.extra["issued_id"] == first.extra["issued_id"]
    assert len(income_rows()) == 1
    assert issue(s, c, [{"product_id": p}], token=services.new_invoice_token()).extra["number"] == "PFA-0002"
    assert not issue(s, c, [{"product_id": p}], token="bad token!").ok


def test_a_line_price_with_a_currency_and_huge_quantities_are_refused(firm):
    s, c, p = add_series(), add_client(), add_product(currency="EUR")
    assert "without a currency" in issue(s, c, [{"product_id": p, "price": "5 RON"}]).error
    assert "less than 1.000.000" in issue(s, c, [{"product_id": p, "quantity": "1000000"}]).error
    assert "total should be less than" in issue(s, c, [{"product_id": p, "quantity": "999999,999",
                                                        "price": "999.999.999,9999"}]).error
    assert issue(s, c, [{"product_id": p, "quantity": "999,999", "price": "999.999.999,9999"}]).ok


def test_deleted_list_entries_never_give_their_id_to_a_new_one():
    first = add_client()
    services.delete_client(first)
    assert add_client(name="Another SRL", tax_id="18547290") != first
    p = add_product()
    services.delete_product(p)
    assert add_product(name="Other") != p
    s = add_series("A")
    services.delete_series(s)
    assert add_series("B") != s


def test_lines_in_different_currencies_are_refused(firm):
    s, c = add_series(), add_client()
    a, b = add_product(name="A"), add_product(name="B", currency="EUR")
    out = issue(s, c, [{"product_id": a}, {"product_id": b}])
    assert not out.ok and "same currency" in out.error
    t = services.invoice_totals({"series_id": s, "client_id": c, "date": D1,
                                 "lines": [{"product_id": a}, {"product_id": b}]})
    assert (t["total"], t["currency"]) == ("", "") and [x["line"] for x in t["lines"]] == [1, 2]


def test_fifty_lines_are_fine_fifty_one_are_not(firm):
    s, c, p = add_series(), add_client(), add_product(unit_price="0,0001")
    assert not issue(s, c, [{"product_id": p, "quantity": "1"}] * 51).ok
    out = issue(s, c, [{"product_id": p, "quantity": "100"}] * 50)
    assert out.ok, out.error
    assert income_rows()[0]["amount"] == "0.5"


def test_an_invoice_needs_the_firm_details_from_settings():
    s, c, p = add_series(), add_client(), add_product()
    out = issue(s, c, [{"product_id": p}])
    assert not out.ok and "fill in your firm's address in Settings first" in out.error
    assert services.invoicing_view()["firm_problems"]
    assert invoicing.seller_problems({}) == ["fill in your firm's name, CUI and address in Settings first"]
    assert invoicing.seller_problems({"name": "X"}) == ["fill in your firm's CUI and address in Settings first"]


def test_a_failure_while_printing_leaves_nothing_behind(firm, monkeypatch):
    s, c, p = add_series(), add_client(), add_product()

    def broken(inv):
        raise RuntimeError("printer on fire")
    monkeypatch.setattr(invoicing, "render_pdf", broken)
    with pytest.raises(RuntimeError):
        issue(s, c, [{"product_id": p}])
    assert income_rows() == [] and invoicing.list_issued() == []
    assert invoicing.list_series()[0]["next"] == "PFA-0001"


def test_a_failure_after_the_pdf_is_stored_removes_the_file(firm, monkeypatch):
    s, c, p = add_series(), add_client(), add_product()
    real_dumps = json.dumps
    monkeypatch.setattr(invoicing.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        issue(s, c, [{"product_id": p}])
    monkeypatch.setattr(invoicing.json, "dumps", real_dumps)
    assert income_rows() == [] and not any((ATTACHMENTS_DIR / "invoices").iterdir())
    assert issue(s, c, [{"product_id": p}]).extra["number"] == "PFA-0001"


def test_two_invoices_issued_at_the_same_moment_get_different_numbers(firm):
    s, c, p = add_series(), add_client(), add_product()
    results = []
    threads = [threading.Thread(target=lambda: results.append(issue(s, c, [{"product_id": p}]))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.ok for r in results), [r.error for r in results]
    assert sorted(r.extra["number"] for r in results) == [f"PFA-000{i}" for i in range(1, 7)]


def test_an_issued_invoice_keeps_the_client_and_product_as_they_were(firm):
    s, c, p = add_series(), add_client(), add_product()
    out = issue(s, c, [{"product_id": p, "quantity": "1"}])
    services.save_client(client_fields(name="Renamed SRL"), c)
    services.save_product({"name": "Renamed", "unit": "day", "unit_price": "5", "currency": "RON"}, p)
    services.delete_client(c)
    name, data = services.issued_pdf(out.extra["issued_id"])
    assert name == "PFA-0001_Carpathia_SRL.pdf"
    text = pdf_text(data)
    assert "Carpathia SRL" in text and "Programare" in text and "Renamed" not in text
    assert services.issued_pdf(999) is None and services.issued_pdf("x") is None


def test_an_invoice_deleted_from_income_stays_issued_and_its_number_is_never_reused(firm):
    s, c, p = add_series(), add_client(), add_product()
    first = issue(s, c, [{"product_id": p}])
    second = issue(s, c, [{"product_id": p}])
    out = services.delete("invoices", second.extra["income_id"])
    assert out.ok and "PFA-0002 deleted from Income" in out.message and "not given out again" in out.message
    issued = {i["full_number"]: i for i in invoicing.list_issued()}
    assert issued["PFA-0002"]["in_income"] is False and issued["PFA-0001"]["in_income"] is True
    assert services.issued_pdf(second.extra["issued_id"]) is not None
    assert not services.save_series({"name": "PFA", "next_number": "2"}, s).ok
    # the next Income row may get the deleted row's id: it must not be taken for the deleted invoice
    third = issue(s, c, [{"product_id": p}])
    assert third.extra["number"] == "PFA-0003"
    issued = {i["full_number"]: i for i in invoicing.list_issued()}
    assert issued["PFA-0002"]["in_income"] is False and issued["PFA-0003"]["in_income"] is True
    assert first.ok
    assert services.delete("invoices", first.extra["income_id"]).message.startswith("Invoice PFA-0001 deleted")


def test_invoices_of_a_series_follow_the_order_of_their_dates(firm):
    a, b, c, p = add_series("A"), add_series("B"), add_client(), add_product()
    assert issue(a, c, [{"product_id": p}], date=f"{LAST_YEAR}-05-10").ok
    assert issue(a, c, [{"product_id": p}], date=f"{LAST_YEAR}-05-10").ok          # the same day is fine
    out = issue(a, c, [{"product_id": p}], date=f"{LAST_YEAR}-05-09")
    assert not out.ok and f"A-0002 of series A is dated {LAST_YEAR}-05-10" in out.error
    assert issue(b, c, [{"product_id": p}], date=f"{LAST_YEAR}-05-09").ok          # another series is separate
    t = services.invoice_totals({"series_id": a, "client_id": c, "date": f"{LAST_YEAR}-01-01",
                                 "lines": [{"product_id": p}]})
    assert not t["ok"] and "cannot be dated earlier" in t["error"]
    # an invoice deleted from Income still counts: its number and date were given out
    services.delete("invoices", income_rows()[1]["id"])
    assert not issue(a, c, [{"product_id": p}], date=f"{LAST_YEAR}-05-09").ok


def test_a_damaged_date_in_an_issued_record_does_not_block_the_series(firm):
    s, c, p = add_series(), add_client(), add_product()
    issue(s, c, [{"product_id": p}])
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET date='garbage'")
    assert issue(s, c, [{"product_id": p}]).ok


def test_a_damaged_issued_record_does_not_break_the_lists(firm):
    s, c, p = add_series(), add_client(), add_product()
    out = issue(s, c, [{"product_id": p}])
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data='{not json'")
    assert services.invoicing_view()["issued"][0]["full_number"] == "PFA-0001"
    assert services.issued_pdf(out.extra["issued_id"]) is None
    with db.connect() as con:
        con.execute("""UPDATE issued_invoices SET data='{"full_number": "PFA-0001", "client": 5}'""")
    assert services.issued_pdf(out.extra["issued_id"]) is None


def test_invoice_totals_preview_saves_nothing(firm):
    s, c, p = add_series(), add_client(), add_product(unit_price="33,3333")
    t = services.invoice_totals({"series_id": s, "client_id": c, "date": D1,
                                 "lines": [{"product_id": p, "quantity": "3"}]})
    assert t["ok"] and t["total"] == "100,00" and t["next_number"] == "PFA-0001"
    assert t["lines"][0]["value_display"] == "100,00"
    bad = services.invoice_totals({"series_id": s, "client_id": c, "date": D1, "lines": []})
    assert not bad["ok"] and "at least one" in bad["error"]
    early = services.invoice_totals({"lines": [{"product_id": p, "quantity": "6"}]})
    assert not early["ok"] and early["total"] == "200,00" and "choose a series" in early["error"]
    assert income_rows() == []


def test_the_lists_go_into_backups_and_come_back(firm, client):
    s, c, p = add_series(), add_client(), add_product()
    issue(s, c, [{"product_id": p}])
    name, data = services.export_backup()
    services.delete_client(c)
    services.delete_product(p)
    services.delete("invoices", income_rows()[0]["id"])
    assert services.restore_backup(data, True).ok
    view = services.invoicing_view()
    assert [x["name"] for x in view["clients"]] == ["Carpathia SRL"] and len(view["products"]) == 1
    assert [i["full_number"] for i in view["issued"]] == ["PFA-0001"] and view["series"][0]["next"] == "PFA-0002"
    assert (ATTACHMENTS_DIR / "invoices" / "PFA-0001_Carpathia_SRL.pdf").is_file()


def test_an_old_database_gets_the_invoicing_tables():
    import sqlite3
    with sqlite3.connect(config.DB_PATH) as con:
        for t in ("clients", "products", "invoice_series", "issued_invoices", "invoicing_settings"):
            con.execute(f"DROP TABLE {t}")
    db.init_db()
    assert services.invoicing_view()["clients"] == []


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("PFA_DB", raising=False)
    monkeypatch.delenv("PFA_SETTINGS", raising=False)
    monkeypatch.setenv("PFA_HOME", str(tmp_path))
    token = config.ACTIVE_PROFILE.set(None)
    services.start()
    yield tmp_path
    config.ACTIVE_PROFILE.reset(token)


def test_each_profile_has_its_own_lists_and_numbering(home):
    first = profiles.current_id()
    add_client()
    add_series("PFA", "7")
    assert services.create_profile("Second firm").ok
    view = services.invoicing_view()
    assert view["clients"] == [] and view["series"] == []
    add_series("PFA")
    assert invoicing.list_series()[0]["next"] == "PFA-0001"
    services.switch_profile(first)
    assert invoicing.list_series()[0]["next"] == "PFA-0007" and len(invoicing.list_clients()) == 1
