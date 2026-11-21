"""Exchange rates: file layouts, weekend fallback, missing rates and broken files."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import ODD_FRIDAY, ODD_SATURDAY, TODAY
from tracker.rates import convert, find_rate, load_rates


def test_wide_layout_is_read():
    rates, err = load_rates()
    assert err is None
    assert set(rates) == {"EUR", "USD"}
    assert rates["EUR"][ODD_FRIDAY] == Decimal("4.5000")


def test_long_layout_is_read(rates_path):
    rates_path.write_text("date,currency,rate\n2025-01-02,EUR,4.97\n2025-01-02,chf,5.30\n", encoding="utf-8")
    rates, err = load_rates()
    assert err is None
    assert rates["CHF"][date(2025, 1, 2)] == Decimal("5.30")


def test_weekend_uses_the_latest_earlier_rate():
    rates, _ = load_rates()
    rate, day = find_rate(rates, "EUR", ODD_SATURDAY)
    assert (rate, day) == (Decimal("4.5000"), ODD_FRIDAY)
    rate, day = find_rate(rates, "eur", ODD_SATURDAY + timedelta(days=1))
    assert (rate, day) == (Decimal("4.5000"), ODD_FRIDAY)


def test_gap_longer_than_ten_days_is_an_error(rates_path):
    rates_path.write_text("date,EUR\n2025-01-02,5\n", encoding="utf-8")
    rates, _ = load_rates()
    assert find_rate(rates, "EUR", date(2025, 1, 12))[1] == date(2025, 1, 2)
    with pytest.raises(ValueError, match="no EUR rate"):
        find_rate(rates, "EUR", date(2025, 1, 13))


def test_ron_and_lei_need_no_rate():
    assert find_rate({}, "RON", TODAY) == (Decimal(1), TODAY)
    assert find_rate({}, "lei", TODAY) == (Decimal(1), TODAY)


def test_unknown_currency_is_an_error():
    rates, _ = load_rates()
    with pytest.raises(ValueError, match="no rates for GBP"):
        find_rate(rates, "GBP", TODAY)


def test_conversion_rounds_half_up_to_cents():
    rates = {"EUR": {TODAY: Decimal("4.9755")}}
    assert convert(rates, None, Decimal("1.01"), "EUR", TODAY)["amount_ron"] == "5.03"   # 5.025255 -> 5.03
    assert convert(rates, None, Decimal("0.005"), "RON", TODAY)["amount_ron"] == "0.01"


def test_conversion_reports_a_broken_rates_file_only_for_foreign_currency():
    assert convert({}, "rates file not found", Decimal(10), "RON", TODAY)["amount_ron"] == "10.00"
    with pytest.raises(ValueError, match="rates file not found"):
        convert({}, "rates file not found", Decimal(10), "EUR", TODAY)


@pytest.mark.parametrize("content, expected", [
    ("", "empty"),
    ("EUR,USD\n5,4\n", "needs a 'date' column"),
    ("date,EUR\n2025-01-02,abc\n", "could not read"),
    ("date,EUR\nnot a date,5\n", "could not read"),
    ("date,EUR\n2025-01-02,-5\n", "could not read"),
])
def test_broken_rates_files_give_a_message_not_a_crash(rates_path, content, expected):
    rates_path.write_bytes(content.encode("latin-1"))
    rates, err = load_rates()
    assert rates == {} and expected in err


def test_binary_junk_rates_file_gives_a_message(rates_path):
    rates_path.write_bytes(bytes(range(256)) * 10)
    rates, err = load_rates()
    assert rates == {} and err


def test_missing_rates_file_gives_a_message(rates_path):
    rates_path.unlink()
    rates, err = load_rates()
    assert rates == {} and "not found" in err


def test_rates_file_may_hold_future_days(rates_path):
    rates_path.write_text("date,EUR\n2999-01-01,5\n", encoding="utf-8")
    assert load_rates()[1] is None
