"""Credit notes in the core: reversing all or some of one issued invoice, by quantity and at the invoice's
own prices and VAT, never more than is left to credit; numbered from a series like invoices, and added to
Income as a negative amount without VAT, dated and converted like any invoice (its refund date, or its own
date). All or nothing, a form sent twice, two at the same moment, damaged records, old databases, backups.
Its PDF: titled as a credit note, naming the invoice it reverses, negative quantities and totals, no due date
and no payment line, its reason when given, the reversed invoice's exchange rate; invoices print as before."""
import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import LAST_YEAR, ODD_FRIDAY, YEAR
from test_invoicing import (D1, add_client, add_product, add_series, client_fields, firm, income_rows, issue,  # noqa: F401
                          pdf_text)
from tracker import config, db, invoicing, services, taxes
from tracker.config import ATTACHMENTS_DIR

D2 = f"{LAST_YEAR}-06-01"


def credit(issued_id, lines=None, **over) -> dict:
    """Issues a credit note: lines as {position: quantity}, or everything left when lines is None."""
    fields = {"issued_id": issued_id, "date": D1, **over}
    if lines is None:
        fields.setdefault("everything", True)
    else:
        fields["lines"] = [{"line": str(pos), "quantity": qty} for pos, qty in lines.items()]
    return invoicing.issue_credit_note(fields)


def refused(issued_id, lines=None, **over) -> str:
    with pytest.raises(invoicing.InvoicingError) as e:
        credit(issued_id, lines, **over)
    return str(e.value)


def invoice(lines=None, **over) -> int:
    """Issues an invoice (series PFA, client Carpathia SRL) and returns its issued id."""
    series = next((s["id"] for s in invoicing.list_series() if s["name"] == "PFA"), None) or add_series()
    client = (invoicing.list_clients() or [{"id": add_client()}])[0]["id"]
    if lines is None:                                                  # 10 hours at 100 RON = 1.000 RON
        product = next((x["id"] for x in invoicing.list_products() if x["name"] == "Programare"), None)
        lines = [{"product_id": product or add_product(), "quantity": "10"}]
    out = issue(series, client, lines, **over)
    assert out.ok, out.error
    return out.extra["issued_id"]


def left(issued_id) -> list[str]:
    return [line["left_quantity"] for line in invoicing.credit_status(issued_id)["lines"]]


def proof_files() -> list[str]:
    folder = ATTACHMENTS_DIR / "invoices"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def year_income(year: str) -> Decimal:
    return next((s["income"] for s in taxes.all_years() if s["year"] == year), Decimal(0))


# Crediting a whole invoice

def test_a_full_credit_note_is_numbered_added_to_income_as_negative_and_cancels_the_invoice(firm):
    inv = invoice()
    cn = credit(inv, reason="Lucrare anulată")
    assert cn["full_number"] == "PFA-0002" and cn["kind"] == "credit_note"
    assert cn["credits"] == {"id": inv, "full_number": "PFA-0001", "date": D1, "series": "PFA"}
    assert [(line["orig_line"], line["quantity"], line["price"], line["value"]) for line in cn["lines"]] == [
        (1, "-10", "100", "-1000")]
    assert (cn["subtotal"], cn["vat"], cn["total"], cn["reason"]) == ("-1000", "0", "-1000", "Lucrare anulată")
    row = income_rows()[1]
    assert (row["number"], row["client"], row["amount"], row["amount_ron"], row["currency"], row["source"],
            row["income_date"]) == ("PFA-0002", "Carpathia SRL", "-1000", "-1000.00", "RON", "invoicing", D1)
    assert row["attachment"] == "PFA-0002_Carpathia_SRL.pdf"
    assert (ATTACHMENTS_DIR / "invoices" / row["attachment"]).read_bytes().startswith(b"%PDF")
    assert services.invoice_for_edit(row["id"])["generated"] is True
    with db.connect() as con:
        stored = con.execute("SELECT kind, credits_id, total, income_id FROM issued_invoices WHERE id=?",
                             (cn["id"],)).fetchone()
    assert tuple(stored) == ("credit_note", inv, "-1000", row["id"])
    assert year_income(LAST_YEAR) == 0
    status = invoicing.credit_status(inv)
    assert status["fully_credited"] and status["credited_by"] == ["PFA-0002"] and status["total_left"] == "0"
    assert "already fully credited (by PFA-0002)" in status["problem"]
    assert invoicing.list_series()[0]["next"] == "PFA-0003"


def test_the_year_and_its_taxes_are_as_if_the_credited_part_was_never_invoiced(firm):
    product = add_product(unit_price="10000")
    invoice([{"product_id": product, "quantity": "8"}])                       # 80.000 RON
    credit(1, {1: "3"})                                                      # -30.000 RON
    with_credit = next(s for s in taxes.all_years() if s["year"] == LAST_YEAR)
    expected = taxes.compute(LAST_YEAR, Decimal("50000"), Decimal(0))
    assert with_credit["income"] == Decimal("50000")
    assert (with_credit["cas"], with_credit["cass"], with_credit["tax"]) == (expected["cas"], expected["cass"],
                                                                            expected["tax"])


def test_crediting_everything_left_takes_what_earlier_credit_notes_left(firm):
    a, b = add_product(name="A"), add_product(name="B", unit_price="50")
    inv = invoice([{"product_id": a, "quantity": "4"}, {"product_id": b, "quantity": "2"}])
    credit(inv, {2: "1"})
    cn = credit(inv)
    assert [(line["orig_line"], line["quantity"], line["value"]) for line in cn["lines"]] == [
        (1, "-4", "-400"), (2, "-1", "-50")]
    assert left(inv) == ["0", "0"] and year_income(LAST_YEAR) == 0


# Crediting part of an invoice

def test_some_lines_and_quantities_can_be_credited_but_never_more_than_is_left(firm):
    a, b = add_product(name="A"), add_product(name="B", unit_price="50")
    inv = invoice([{"product_id": a, "quantity": "4"}, {"product_id": b, "quantity": "2,5"}])
    first = credit(inv, {2: "1,5"})
    assert [(line["orig_line"], line["name"], line["quantity"], line["value"]) for line in first["lines"]] == [
        (2, "B", "-1.5", "-75")]
    assert left(inv) == ["4", "1"]
    assert "line 2 (B): only 1 left to credit, not 2" in refused(inv, {2: "2"})
    second = credit(inv, {1: "1", 2: "1"})
    assert (second["full_number"], second["subtotal"]) == ("PFA-0003", "-150")
    assert left(inv) == ["3", "0"]
    assert "line 2 (B) is already fully credited" in refused(inv, {2: "0,5"})
    status = invoicing.credit_status(inv)
    assert not status["fully_credited"] and status["problem"] is None and status["subtotal_left"] == "300"
    assert year_income(LAST_YEAR) == Decimal("300")


def test_pieces_of_a_line_add_up_to_its_value_and_its_vat_to_the_cent(firm):
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    p = add_product(unit_price="0,3333")
    inv = invoice([{"product_id": p, "quantity": "3"}, {"product_id": p, "quantity": "10"}])
    original = invoicing.get_issued(inv)
    assert (original["subtotal"], original["vat"], original["total"]) == ("4.33", "0.91", "5.24")
    notes = [credit(inv, {1: "1"}) for _ in range(3)] + [credit(inv)]
    assert [n["subtotal"] for n in notes] == ["-0.33", "-0.34", "-0.33", "-3.33"]
    assert sum(Decimal(n["subtotal"]) for n in notes) == Decimal("-4.33")
    assert sum(Decimal(n["vat"]) for n in notes) == Decimal("-0.91")
    assert sum(Decimal(n["total"]) for n in notes) == Decimal("-5.24")
    assert year_income(LAST_YEAR) == 0


def test_what_is_left_of_a_line_can_be_closed_even_when_it_is_worth_nothing(firm):
    p = add_product(unit_price="0,0001")
    inv = invoice([{"product_id": p, "quantity": "100"}])                   # 0,01 RON
    assert credit(inv, {1: "50"})["subtotal"] == "-0.01"                   # 0,005 rounds up to 0,01
    assert "worth 0,00 RON" in refused(inv, {1: "25"})
    closing = credit(inv, {1: "50"})
    assert closing["total"] == "0" and invoicing.credit_status(inv)["fully_credited"]


@pytest.mark.parametrize("lines, words", [
    ({}, "choose at least one line"),
    ({1: "0"}, "choose at least one line"),
    ({1: "-1"}, "without a minus sign"),
    ({1: "1,2345"}, "at most 3 decimals"),
    ({1: "abc"}, "unrecognised amount"),
    ({1: "1e3"}, "unrecognised amount"),
    ({1: "99999999999999999999999999999999,5"}, "line 1"),
    ({1: "11"}, "only 10 left to credit, not 11"),
    ({2: "1"}, "has no line 2"),
    ({0: "1"}, "has no line 0"),
    ({"x": "1"}, "has no line x"),
])
def test_wrong_lines_and_quantities_are_refused(firm, lines, words):
    inv = invoice()
    assert words in refused(inv, lines)


def test_a_line_given_twice_is_refused(firm):
    inv = invoice()
    with pytest.raises(invoicing.InvoicingError, match=r"line 1 \(Programare\) is given twice"):
        invoicing.issue_credit_note({"issued_id": inv, "date": D1, "lines": [{"line": "1", "quantity": "1"},
                                                                             {"line": "1", "quantity": "2"}]})


def test_quantities_are_read_like_on_an_invoice(firm):
    inv = invoice()
    assert credit(inv, {1: "1,5"})["lines"][0]["quantity"] == "-1.5"
    assert credit(inv, {1: "0,125"})["lines"][0]["quantity"] == "-0.125"
    assert left(inv) == ["8.375"]


# VAT, currencies and the date the income counts on

def test_the_vat_of_the_invoice_is_credited_even_after_the_vat_settings_changed(firm):
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    inv = invoice()
    services.save_invoicing_settings({"vat_payer": "", "vat_rate": "19", "due_days": "15"})
    partial = credit(inv, {1: "5"})
    assert (partial["vat_payer"], partial["vat_rate"], partial["vat"], partial["total"]) == (True, "21", "-105", "-605")
    rest = credit(inv)
    assert (rest["vat"], rest["total"]) == ("-105", "-605")
    assert [r["amount"] for r in income_rows()] == ["1000", "-500", "-500"]       # never the VAT


def test_a_credit_note_in_another_currency_counts_at_the_rate_of_its_own_date(firm):
    p = add_product(name="Support", unit_price="100", currency="EUR")
    inv = invoice([{"product_id": p, "quantity": "1"}], date=ODD_FRIDAY.isoformat())     # EUR at 4,5 that day
    cn = credit(inv, date=D1)                                                           # EUR at 5
    row = income_rows()[1]
    assert (row["amount"], row["currency"], row["rate"], row["amount_ron"]) == ("-100", "EUR", "5.0000", "-500.00")
    assert year_income(LAST_YEAR) == Decimal("-50")          # the rate changed: a full credit need not net to 0
    # the credit note keeps the invoice's rate for its VAT and total in RON
    assert cn["rate"] == invoicing.get_issued(inv)["rate"] and cn["total_ron"] == "-450"


def test_the_refund_date_decides_the_year_and_the_rate(firm):
    p = add_product(name="Support", unit_price="100", currency="EUR")
    inv = invoice([{"product_id": p, "quantity": "2"}], date=f"{LAST_YEAR}-12-10")
    cn = credit(inv, {1: "1"}, date=f"{LAST_YEAR}-12-20", payment_date=f"{YEAR}-01-06")
    row = income_rows()[1]
    assert (row["date"], row["payment_date"], row["income_date"], row["amount_ron"]) == (
        f"{LAST_YEAR}-12-20", f"{YEAR}-01-06", f"{YEAR}-01-06", "-500.00")
    assert cn["payment_date"] == f"{YEAR}-01-06"
    assert year_income(LAST_YEAR) == Decimal("1000") and year_income(YEAR) == Decimal("-500")


@pytest.mark.parametrize("over, words", [
    ({"date": ""}, "credit note date is missing"),
    ({"date": "1999-12-31"}, "before 2000"),
    ({"date": f"{int(YEAR) + 1}-01-01"}, "in the future"),
    ({"date": f"{LAST_YEAR}-05-04"}, "cannot be dated before the invoice it reverses (PFA-0001 is dated"),
    ({"date": D2, "payment_date": D1}, "date of the refund is before the credit note date"),
    ({"payment_date": f"{int(YEAR) + 1}-01-01"}, "date of the refund: date"),
    ({"reason": "x" * 501}, "reason should be at most 500 characters"),
    ({"reason": "ok 🚀"}, "cannot be printed"),
    ({"series_id": "999"}, "choose a series"),
    ({"token": "bad token!"}, "bad token"),
])
def test_wrong_credit_notes_are_refused_and_change_nothing(firm, over, words):
    inv = invoice()
    before = (income_rows(), proof_files())
    assert words in refused(inv, **over)
    assert (income_rows(), proof_files()) == before
    assert invoicing.list_series()[0]["next"] == "PFA-0002" and len(invoicing.list_issued()) == 1


# What cannot be credited

def test_an_invoice_deleted_from_income_cannot_be_credited(firm):
    inv = invoice()
    services.delete("invoices", income_rows()[0]["id"])
    assert "was deleted from Income, so it cannot be credited" in refused(inv)
    assert invoicing.credit_status(inv)["in_income"] is False


def test_deleting_a_credited_invoice_from_income_keeps_its_credit_notes_but_stops_further_credit(firm):
    inv = invoice()
    credit(inv, {1: "4"})
    assert services.delete("invoices", income_rows()[0]["id"]).ok
    assert [r["number"] for r in income_rows()] == ["PFA-0002"]
    assert "deleted from Income" in refused(inv, {1: "1"})


def test_a_credit_note_deleted_from_income_still_counts_against_its_invoice(firm):
    inv = invoice()
    cn = credit(inv, {1: "4"})
    assert services.delete("invoices", cn["income_id"]).ok
    status = invoicing.credit_status(inv)
    assert status["credited_by"] == ["PFA-0002"] and left(inv) == ["6"]
    assert "only 6 left" in refused(inv, {1: "7"})


def test_a_credit_note_cannot_itself_be_credited(firm):
    inv = invoice()
    cn = credit(inv, {1: "1"})
    assert "PFA-0002 is a credit note; only an invoice can be credited" in refused(cn["id"], {1: "1"})
    assert invoicing.credit_status(cn["id"])["problem"].startswith("PFA-0002 is a credit note")


@pytest.mark.parametrize("issued_id", ["", "abc", "0", "-1", "1.0", str(2**63), "99", None, True, [1]])
def test_an_unknown_or_tampered_invoice_id_is_refused(firm, issued_id):
    invoice()
    assert "choose the invoice to credit" in refused(issued_id)
    assert invoicing.credit_status(issued_id) is None


def test_damaged_records_are_never_guessed_at(firm):
    inv = invoice()
    cn = credit(inv, {1: "1"})
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data=? WHERE id=?", (json.dumps({"full_number": "PFA-0002"}), cn["id"]))
    assert "record of credit note PFA-0002 is damaged" in refused(inv, {1: "1"})
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data=? WHERE id=?",
                    (json.dumps({**invoicing.get_issued(inv), "lines": [{"name": "x", "price": "NaN"}]}), inv))
    assert "record of invoice PFA-0001 is damaged" in refused(inv, {1: "1"})
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data='not json' WHERE id=?", (inv,))
    assert "choose the invoice to credit" in refused(inv)


def test_a_credit_note_needs_the_firm_details_from_settings(firm, settings_path):
    inv = invoice()
    data = json.loads(settings_path.read_text())
    data["firm"]["address"] = ""
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    assert "fill in your firm's address in Settings first" in refused(inv)


# Numbering

def test_credit_notes_share_the_numbers_and_the_date_order_of_their_series(firm):
    inv = invoice()
    assert credit(inv, {1: "1"}, date=D2)["full_number"] == "PFA-0002"
    out = issue(invoicing.list_series()[0]["id"], invoicing.list_clients()[0]["id"],
                [{"product_id": invoicing.list_products()[0]["id"]}], date=D1)
    assert not out.ok and ("credit note PFA-0002 of series PFA is dated " + D2 + ", so a new invoice in that series "
                           "cannot be dated earlier") in out.error
    later = invoice(date=f"{LAST_YEAR}-07-01")
    assert invoicing.get_issued(later)["full_number"] == "PFA-0003"
    assert ("invoice PFA-0003 of series PFA is dated " + f"{LAST_YEAR}-07-01" + ", so a new credit note in that series "
            "cannot be dated earlier") in refused(inv, {1: "1"}, date=D2)
    assert credit(inv, {1: "1"}, date=f"{LAST_YEAR}-07-01")["full_number"] == "PFA-0004"


def test_a_credit_note_can_take_another_series_and_needs_one_when_its_own_is_gone(firm):
    inv = invoice()
    other = add_series("ST", "50")
    assert credit(inv, {1: "1"}, series_id=str(other))["full_number"] == "ST-0050"
    services.delete_series(next(s["id"] for s in invoicing.list_series() if s["name"] == "PFA"))
    assert "series PFA of invoice PFA-0001 is no longer in the list; choose a series" in refused(inv, {1: "1"})
    assert credit(inv, {1: "1"}, series_id=other)["full_number"] == "ST-0051"


def test_a_credit_note_keeps_the_client_and_lines_as_they_were_invoiced(firm):
    inv = invoice()
    c, p = invoicing.list_clients()[0]["id"], invoicing.list_products()[0]["id"]
    services.save_client(client_fields(name="Renamed SRL"), c)
    services.delete_product(p)
    cn = credit(inv, {1: "2"})
    assert cn["client"]["name"] == "Carpathia SRL" and cn["lines"][0]["name"] == "Programare"
    assert income_rows()[1]["client"] == "Carpathia SRL"


# All or nothing, twice, at the same moment

def test_a_form_sent_twice_issues_one_credit_note(firm):
    inv = invoice()
    token = services.new_invoice_token()
    first = credit(inv, token=token)
    with pytest.raises(invoicing.AlreadyIssued) as e:
        credit(inv, token=token)                      # not "already fully credited": it was the same form
    assert (e.value.issued_id, e.value.full_number, e.value.kind) == (first["id"], "PFA-0002", "credit_note")
    assert "already used to issue credit note PFA-0002" in str(e.value)
    assert len(income_rows()) == 2


def test_a_failure_while_printing_leaves_nothing_behind(firm, monkeypatch):
    inv = invoice()
    before = (income_rows(), proof_files())
    monkeypatch.setattr(invoicing, "render_pdf", lambda data: (_ for _ in ()).throw(RuntimeError("printer on fire")))
    with pytest.raises(RuntimeError):
        credit(inv)
    assert (income_rows(), proof_files()) == before and len(invoicing.list_issued()) == 1
    assert invoicing.list_series()[0]["next"] == "PFA-0002" and left(inv) == ["10"]


def test_a_failure_after_the_pdf_is_stored_removes_the_file(firm, monkeypatch):
    inv = invoice()
    before = proof_files()
    real_dumps = json.dumps
    monkeypatch.setattr(invoicing.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        credit(inv)
    monkeypatch.setattr(invoicing.json, "dumps", real_dumps)
    assert proof_files() == before and len(income_rows()) == 1
    assert credit(inv)["full_number"] == "PFA-0002"


def test_two_credit_notes_at_the_same_moment_never_credit_more_than_the_invoice(firm):
    inv = invoice()
    results, errors = [], []

    def attempt():
        try:
            results.append(credit(inv, {1: "6"}))
        except invoicing.InvoicingError as e:
            errors.append(str(e))
    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1 and len(errors) == 3, errors
    assert all("only 4 left to credit" in e for e in errors), errors
    assert left(inv) == ["4"] and len(income_rows()) == 2


# Preview, old databases, backups

def test_the_preview_shows_totals_and_problems_and_saves_nothing(firm):
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    inv = invoice()
    p = invoicing.credit_preview({"issued_id": inv, "date": D1, "lines": [{"line": "1", "quantity": "2"}]})
    assert p["problems"] == [] and (p["subtotal"], p["vat"], p["total"], p["currency"]) == ("-200", "-42", "-242", "RON")
    bad = invoicing.credit_preview({"issued_id": inv, "date": "", "lines": [{"line": "1", "quantity": "2"}]})
    assert bad["total"] == "-242" and "the credit note date is missing" in bad["problems"]
    over = invoicing.credit_preview({"issued_id": inv, "date": D1, "lines": [{"line": "1", "quantity": "20"}]})
    assert over["lines"] == [] and any("only 10 left" in x for x in over["problems"])
    gone = invoicing.credit_preview({"issued_id": "999", "date": D1})
    assert gone["lines"] == [] and "choose the invoice to credit" in gone["problems"][0]
    assert len(income_rows()) == 1 and invoicing.list_series()[0]["next"] == "PFA-0002"


def test_an_old_database_gets_the_new_columns_and_its_invoices_can_be_credited(firm):
    inv = invoice()
    old_columns = "id, income_id, series, number, full_number, date, client, currency, total, data, token, created_at"
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute(f"CREATE TABLE old AS SELECT {old_columns} FROM issued_invoices")
        con.execute("DROP TABLE issued_invoices")
        con.execute("ALTER TABLE old RENAME TO issued_invoices")
    db.init_db()
    with db.connect() as con:
        assert tuple(con.execute("SELECT kind, credits_id FROM issued_invoices").fetchone()) == ("invoice", 0)
    assert credit(inv)["full_number"] == "PFA-0002"


def test_credit_notes_go_into_backups_and_come_back(firm):
    inv = invoice()
    credit(inv, {1: "4"})
    status = invoicing.credit_status(inv)
    _, data = services.export_backup()
    credit(inv)
    assert services.restore_backup(data, True).ok
    assert invoicing.credit_status(inv) == status
    assert "PFA-0002_Carpathia_SRL.pdf" in proof_files()
    assert credit(inv)["full_number"] == "PFA-0003"


# The PDF

def flat(text: str) -> str:
    """The PDF text with every run of spaces and line breaks as one space (long lines wrap where they fit)."""
    return " ".join(text.split())


def stored_pdf_text(income_id: int) -> str:
    """The text of the PDF stored as the proof of an Income row, read like the invoice PDF tests do."""
    row = next(r for r in income_rows() if r["id"] == income_id)
    return pdf_text((ATTACHMENTS_DIR / "invoices" / row["attachment"]).read_bytes())


def day(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def test_a_credit_note_pdf_says_what_it_is_and_which_invoice_it_reverses(firm):
    inv = invoice(due_date=f"{LAST_YEAR}-05-20")
    invoice_text = flat(pdf_text(services.issued_pdf(inv)[1]))
    assert "Scadență / Due date: 20.05." in invoice_text and "Plata în contul / Payment to:" in invoice_text
    cn = credit(inv, date=D2)
    text = flat(stored_pdf_text(cn["income_id"]))
    assert text.startswith("FACTURĂ STORNO / CREDIT NOTE Stornează factura / Reverses invoice "
                           f"PFA-0001 din / of {day(D1)}")
    for words in ("Seria / Series: PFA Nr. / No.: 0002", f"Data / Date: {day(D2)}", "Carpathia SRL", "RO14399840",
                  "Test PFA", "Științei", "Neplătitor de TVA", "Programare", "Dezvoltare software",
                  "Factura circulă fără semnătură"):
        assert words in text, words
    for absent in ("FACTURĂ / INVOICE", "Scadență", "Due date", "Plata în contul", "Payment to", "Motiv / Reason",
                   "Mențiuni / Notes"):
        assert absent not in text, absent
    # reprinted from its record, it is the same document
    assert flat(pdf_text(services.issued_pdf(cn["id"])[1])) == text
    # never a due date, even if its record held one
    forced = flat(pdf_text(invoicing.render_pdf({**invoicing.get_issued(cn["id"]), "due_date": f"{LAST_YEAR}-07-01"})))
    assert "Scadență" not in forced and "01.07." not in forced
    from pypdf import PdfReader
    import io
    meta = PdfReader(io.BytesIO(services.issued_pdf(cn["id"])[1])).metadata
    assert (meta.title, meta.subject) == ("Factura storno PFA-0002", "Credit note PFA-0002")


def test_a_credit_note_pdf_prints_its_quantities_and_totals_as_negative_numbers(firm):
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    a, b = add_product(name="Consultanță"), add_product(name="Mentenanță", unit="month", unit_price="40")
    inv = invoice([{"product_id": a, "quantity": "4"}, {"product_id": b, "quantity": "3"}])
    cn = credit(inv, {1: "2,5"})
    text = flat(stored_pdf_text(cn["income_id"]))
    assert "1 Consultanță Dezvoltare software oră / hour -2,5 100,00 -250,00" in text
    assert "Mentenanță" not in text                                # only the lines credited
    for words in ("Total fără TVA / Subtotal -250,00 RON", "TVA / VAT 21% -52,50 RON",
                  "Total de plată / Total due -302,50 RON"):
        assert words in text, words
    assert "Neplătitor" not in text


def test_the_reason_is_printed_only_when_one_was_given(firm):
    inv = invoice()
    without = credit(inv, {1: "1"})
    blank = credit(inv, {1: "1"}, reason="  \n ")
    given = credit(inv, {1: "1"}, reason="Preț greșit & anulare <parțială>\nWrong price, cancelled in part")
    assert "Motiv" not in stored_pdf_text(without["income_id"]) and "Motiv" not in stored_pdf_text(blank["income_id"])
    text = stored_pdf_text(given["income_id"])
    assert "Motiv / Reason\nPreț greșit & anulare <parțială>\nWrong price, cancelled in part" in text


def test_a_credit_note_in_another_currency_prints_the_reversed_invoices_rate(firm):
    services.save_invoicing_settings({"vat_payer": "1", "vat_rate": "21", "due_days": "15"})
    p = add_product(name="Support", unit="month", unit_price="100", currency="EUR")
    inv = invoice([{"product_id": p, "quantity": "1"}], date=ODD_FRIDAY.isoformat())      # EUR at 4,5 that day
    cn = credit(inv, date=D1)                                                          # EUR at 5 on this day
    text = flat(stored_pdf_text(cn["income_id"]))
    assert (f"Curs BNR al facturii stornate / Exchange rate of the reversed invoice ({day(ODD_FRIDAY.isoformat())}): "
            "1 EUR = 4,5000 RON · TVA / VAT: -94,50 RON · Total: -544,50 RON") in text
    assert "Total de plată / Total due -121,00 EUR" in text and "5,0000" not in text
    assert next(r for r in income_rows() if r["id"] == cn["income_id"])["amount_ron"] == "-500.00"   # Income: its own day


def test_invoices_print_exactly_as_before_credit_notes(firm):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "invoice_pdf_text.json").read_text("utf-8"))
    for name, case in fixture["invoices"].items():
        assert pdf_text(invoicing.render_pdf(case["invoice"])) == case["text"], name
    inv = invoice(due_date=f"{LAST_YEAR}-05-20")
    text = pdf_text(services.issued_pdf(inv)[1])
    assert text.startswith("FACTURĂ / INVOICE") and "STORNO" not in text and "Reverses" not in text


def test_a_damaged_credit_note_record_is_not_printed(firm):
    inv = invoice()
    cn = credit(inv, {1: "1"})
    data = invoicing.get_issued(cn["id"])
    del data["credits"]
    with db.connect() as con:
        con.execute("UPDATE issued_invoices SET data=? WHERE id=?", (json.dumps(data), cn["id"]))
    assert services.issued_pdf(cn["id"]) is None
    assert services.issued_pdf(inv) is not None
