"""Rough edges found by attacking the app: absurd dates, zero-byte proofs, huge ids, odd settings keys."""
import json

import pytest

from conftest import LAST_YEAR, csv_upload
from tracker.parsing import parse_date


@pytest.mark.parametrize("text", ["1999-12-31", "0001-01-01", "01.01.1000", "31.12.1999"])
def test_dates_before_2000_are_refused(text):
    with pytest.raises(ValueError, match="before 2000"):
        parse_date(text)


def test_expense_dated_in_year_one_is_refused(client):
    r = client.post("/expenses", data={"date": "0001-01-01", "description": "x", "amount": "1", "currency": "RON"})
    assert r.status_code == 200 and "before 2000" in r.text


def test_zero_byte_proof_is_refused_not_silently_dropped(client):
    r = client.post("/income/add", data={"date": f"{LAST_YEAR}-05-05", "client": "A", "amount": "1", "currency": "RON"},
                    files={"attachment": ("proof.pdf", b"", "application/pdf")})
    assert r.status_code == 200 and "attachment is empty" in r.text


def test_huge_row_ids_do_not_crash(client):
    huge = "99999999999999999999999"
    assert client.get(f"/income/{huge}/attachment").status_code == 422
    assert client.get(f"/expenses/{huge}/attachment").status_code == 422
    assert client.post(f"/income/{huge}/delete").status_code == 422
    assert client.post("/expenses/-1/delete").status_code == 422


def test_non_ascii_or_odd_year_keys_in_settings_are_never_offered(client, settings_path):
    data = json.loads(settings_path.read_text())
    for key in ("２０２０", "1999", "2999", "abcd"):
        data["years"][key] = dict(data["years"][LAST_YEAR])
    settings_path.write_text(json.dumps(data, ensure_ascii=False))
    html = client.get("/").text
    for key in ("２０２０", ">1999<", ">2999<", "abcd"):
        assert key not in html


def test_csv_rows_with_payment_before_invoice_are_listed_as_problems(client):
    text = f"date,client,amount,currency,payment_date\n{LAST_YEAR}-05-05,A,1,RON,{LAST_YEAR}-05-04\n"
    r = client.post("/income/import", files=csv_upload(text))
    assert "1 row(s) with problems" in r.text and "is before the invoice date" in r.text
