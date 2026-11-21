"""Dates and amounts: normal Romanian and English forms, plus absurd input that must be refused."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tracker.parsing import decode, parse_amount, parse_date, parse_rate, read_csv


@pytest.mark.parametrize("text, expected", [
    ("2025-03-15", date(2025, 3, 15)),
    ("15.03.2025", date(2025, 3, 15)),
    ("15/03/2025", date(2025, 3, 15)),
    ("15-03-2025", date(2025, 3, 15)),
    ("2025/03/15", date(2025, 3, 15)),
    ("  2025-03-15  ", date(2025, 3, 15)),
])
def test_dates_in_accepted_forms(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "yesterday", "2025-13-01", "31.02.2025", "2025-02-30", "15.03.25", "03/15/2025",
    "20250315", "1e3", "None", "null", "2025-03-15T10:00:00", "٢٠٢٥-03-15",
    "0000-01-01", "1999-12-31", "-2025-03-15", "2025-03-15; DROP TABLE invoices", "🙂",
])
def test_unreadable_dates_are_refused(text):
    with pytest.raises(ValueError):
        parse_date(text)


def test_today_is_allowed_but_tomorrow_is_not():
    assert parse_date(date.today().isoformat()) == date.today()
    with pytest.raises(ValueError, match="in the future"):
        parse_date((date.today() + timedelta(days=1)).isoformat())


@pytest.mark.parametrize("text", ["2999-12-31", "9999-01-01", "01.01.3000"])
def test_far_future_dates_are_refused(text):
    with pytest.raises(ValueError, match="in the future"):
        parse_date(text)


def test_future_dates_can_be_allowed_explicitly_for_rates_files():
    assert parse_date("2999-12-31", allow_future=True) == date(2999, 12, 31)


@pytest.mark.parametrize("text, expected", [
    ("1234.56", "1234.56"), ("1234,56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
    ("1 234,56", "1234.56"), ("1.500", "1500"), ("1,500", "1500"), ("12,5", "12.5"), ("12.5", "12.5"),
    ("0", "0"), ("-250,00", "-250.00"), ("€ 99", "99"), ("99 lei", "99"), ("1.234.567,89", "1234567.89"),
    ("1,234,567.89", "1234567.89"), ("0,01", "0.01"), ("999999999999999", "999999999999999"),
])
def test_amounts_in_accepted_forms(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", [
    "", "   ", "abc", "1..2", "1,,2", "1.2.3.4,5,6", "NaN", "nan", "Infinity", "inf", "-inf", "1e5",
    "0x10", "--5", "5-", "1/2", "one hundred", "💰", "1e308", "١٢٣",
    "12.34.56", "None", "null", "True",
])
def test_unreadable_amounts_are_refused(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_amounts_of_a_million_billion_or_more_are_refused():
    with pytest.raises(ValueError, match="too large"):
        parse_amount("1000000000000000")
    with pytest.raises(ValueError, match="too large"):
        parse_amount("-1" + "0" * 40)


def test_amounts_keep_exact_cents():
    assert parse_amount("0.1") + parse_amount("0.2") == Decimal("0.3")


@pytest.mark.parametrize("text, expected", [("4.9750", "4.9750"), ("4,9750", "4.9750"), ("5", "5"), ("1.500", "1.500")])
def test_rates_are_plain_decimals(text, expected):
    assert parse_rate(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "0", "-1", "abc", "NaN", "inf", "1.2.3"])
def test_bad_rates_are_refused(text):
    with pytest.raises(ValueError):
        parse_rate(text)


def test_csv_reader_handles_commas_semicolons_tabs_and_header_case():
    assert read_csv("Date;Client\n2025-01-01;A\n")[0] == {"date": "2025-01-01", "client": "A"}
    assert read_csv("date\tclient\n2025-01-01\tA\n")[0] == {"date": "2025-01-01", "client": "A"}
    assert read_csv("DATE , CLIENT \n 2025-01-01 , A \n")[0] == {"date": "2025-01-01", "client": "A"}


def test_csv_reader_survives_garbage():
    assert read_csv("") == []
    assert read_csv("just one word") == []
    read_csv("\x00\x01\x02")   # must not raise
    rows = read_csv("a,b\n1\n1,2,3\n")
    assert rows[0]["a"] == "1" and rows[1]["b"] == "2"


def test_decode_accepts_utf8_bom_windows_1250_and_binary_junk():
    assert decode("date,client\n2025-01-01,Ștefan".encode("utf-8-sig")).startswith("date")
    assert "Ştefan" in decode("Ştefan".encode("cp1250"))
    assert isinstance(decode(bytes(range(256))), str)
