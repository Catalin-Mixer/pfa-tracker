"""CSV import: layouts, aliases, duplicates, bad rows, payment dates and absurd files."""
from datetime import timedelta

from conftest import LAST_YEAR, ODD_FRIDAY, ODD_SATURDAY, TODAY, YEAR, sample_csv
from tracker import db
from tracker.invoices import import_invoices


def stored():
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM invoices ORDER BY id")]


def test_sample_file_is_converted_with_the_right_rates():
    res = import_invoices(sample_csv(), "s.csv")
    assert (len(res["added"]), len(res["duplicates"]), len(res["errors"]), res["fatal"]) == (3, 0, 0, None)
    rows = stored()
    assert [r["amount_ron"] for r in rows] == ["5000.00", "1000.00", "700.00"]
    assert [r["rate"] for r in rows] == ["5.0000", "4.0000", "1"]
    assert all(r["income_date"] == r["date"] and r["payment_date"] == "" for r in rows)


def test_reimporting_the_same_file_adds_nothing():
    import_invoices(sample_csv(), "s.csv")
    res = import_invoices(sample_csv(), "s.csv")
    assert (len(res["added"]), len(res["duplicates"])) == (0, 3) and len(stored()) == 3


def test_same_invoice_with_a_different_number_is_not_a_duplicate():
    import_invoices(sample_csv(), "s.csv")
    res = import_invoices(sample_csv().replace("A-1", "A-9"), "s.csv")
    assert len(res["added"]) == 1 and len(stored()) == 4


def test_semicolons_romanian_headers_and_amount_forms():
    text = (f"Data;Beneficiar;Nr;Suma;Moneda\n{LAST_YEAR}-01-15;Ştefan SRL;7;1.234,56;eur\n"
            f"15.01.{LAST_YEAR};Ştefan SRL;8;1 234,56;Lei\n")
    res = import_invoices(text, "ro.csv")
    assert len(res["added"]) == 2 and res["errors"] == []
    rows = stored()
    assert rows[0]["amount_ron"] == "6172.80" and rows[0]["currency"] == "EUR"
    assert rows[1]["amount_ron"] == "1234.56" and rows[1]["currency"] == "RON"


def test_extra_columns_are_ignored_and_blank_lines_skipped():
    text = f"id,date,client,amount,currency,comment\n,,,,,\n1,{LAST_YEAR}-05-05,A,10,RON,hello\n\n"
    res = import_invoices(text, "x.csv")
    assert len(res["added"]) == 1 and res["errors"] == []


def test_weekend_invoice_uses_the_previous_rate_and_remembers_which_day():
    text = f"date,client,amount,currency\n{ODD_SATURDAY.isoformat()},A,100,EUR\n"
    import_invoices(text, "w.csv")
    row = stored()[0]
    assert row["rate_date"] == ODD_FRIDAY.isoformat() and row["amount_ron"] == "450.00"


def test_payment_date_sets_the_year_and_the_rate():
    saturday, friday = ODD_SATURDAY.isoformat(), ODD_FRIDAY.isoformat()
    text = (f"date,client,amount,currency,payment_date\n"
            f"{int(LAST_YEAR) - 1}-12-20,A,100,EUR,{saturday}\n")
    res = import_invoices(text, "p.csv")
    assert len(res["added"]) == 1
    row = stored()[0]
    assert row["payment_date"] == saturday and row["income_date"] == saturday
    assert row["rate_date"] == friday and row["amount_ron"] == "450.00"
    assert db.totals_by_year("invoices")[0] == {LAST_YEAR: __import__("decimal").Decimal("450.00")}


def test_reimporting_with_payment_dates_completes_unpaid_invoices():
    unpaid = f"date,client,number,amount,currency\n{LAST_YEAR}-12-10,Kappa,K1,200,EUR\n{LAST_YEAR}-06-10,Beta,B1,100,RON\n"
    import_invoices(unpaid, "first.csv")
    assert stored()[0]["income_date"] == f"{LAST_YEAR}-12-10"
    paid = (f"date,client,number,amount,currency,payment_date\n{LAST_YEAR}-12-10,Kappa,K1,200,EUR,{YEAR}-01-02\n"
            f"{LAST_YEAR}-06-10,Beta,B1,100,RON,\n")
    res = import_invoices(paid, "second.csv")
    assert (len(res["added"]), len(res["paid"]), len(res["duplicates"])) == (0, 1, 1)
    rows = stored()
    assert len(rows) == 2 and rows[0]["payment_date"] == f"{YEAR}-01-02" and rows[0]["income_date"] == f"{YEAR}-01-02"
    assert rows[0]["rate_date"] >= f"{YEAR}-01-01" and rows[1]["payment_date"] == ""


def test_bad_rows_are_reported_with_their_line_and_the_good_ones_imported():
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    text = (
        "date,client,amount,currency,payment_date\n"
        f"{LAST_YEAR}-01-10,Good,10,RON,\n"
        f"{LAST_YEAR}-02-30,Bad date,10,RON,\n"
        f"{LAST_YEAR}-01-10,,10,RON,\n"
        f"{LAST_YEAR}-01-10,No currency,10,,\n"
        f"{LAST_YEAR}-01-10,Bad amount,ten,RON,\n"
        f"{LAST_YEAR}-01-10,No rate,10,GBP,\n"
        f"{tomorrow},Future,10,RON,\n"
        f"{LAST_YEAR}-01-10,Future payment,10,RON,{tomorrow}\n"
        f"2000-01-10,Too old for rates,10,EUR,\n"
    )
    res = import_invoices(text, "mix.csv")
    assert len(res["added"]) == 1 and len(stored()) == 1
    problems = {e["line"]: e["error"] for e in res["errors"]}
    assert set(problems) == {3, 4, 5, 6, 7, 8, 9, 10}
    assert "unrecognised date" in problems[3] and "empty client" in problems[4]
    assert "empty currency" in problems[5] and "unrecognised amount" in problems[6]
    assert "no rates for GBP" in problems[7] and "in the future" in problems[8]
    assert "in the future" in problems[9] and "no EUR rate" in problems[10]


def test_missing_required_columns_stop_the_import():
    res = import_invoices("date,client,amount\n2025-01-01,A,1\n", "x.csv")
    assert res["fatal"].startswith("Missing column(s): currency") and stored() == []


def test_files_without_data_rows_stop_the_import():
    for text in ("", "date,client,amount,currency\n", "\n\n", "just words"):
        assert import_invoices(text, "x.csv")["fatal"] == "The file has no data rows."
    assert stored() == []


def test_absurd_files_do_not_crash():
    assert import_invoices("\x00\x01\x02\x03" * 100, "bin.csv")["fatal"]
    assert import_invoices("date,client,amount,currency\n" + "🙂," * 4 + "\n", "emoji.csv")["errors"]
    wide = "date,client,amount,currency," + ",".join(f"c{i}" for i in range(500)) + f"\n{LAST_YEAR}-01-01,A,1,RON" + ",x" * 500 + "\n"
    assert len(import_invoices(wide, "wide.csv")["added"]) == 1
    long_client = f"date,client,amount,currency\n{LAST_YEAR}-01-01,{'A' * 10000},1,RON\n"
    assert len(import_invoices(long_client, "long.csv")["added"]) == 1
    injection = f'date,client,amount,currency\n{LAST_YEAR}-01-01,"Robert\'); DROP TABLE invoices;--",1,RON\n'
    assert len(import_invoices(injection, "sql.csv")["added"]) == 1 and len(stored()) == 3


def test_ten_thousand_rows_import():
    lines = ["date,client,amount,currency"] + [f"{LAST_YEAR}-01-{(i % 28) + 1:02d},Client {i},{i}.50,EUR" for i in range(10000)]
    res = import_invoices("\n".join(lines), "big.csv")
    assert len(res["added"]) == 10000 and res["errors"] == []


def test_old_database_without_the_new_columns_is_upgraded():
    with db.connect() as con:
        con.execute("DROP TABLE invoices")
        con.execute("DROP TABLE expenses")
        con.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, date TEXT NOT NULL, client TEXT NOT NULL, "
                    "number TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL, currency TEXT NOT NULL, rate TEXT NOT NULL, "
                    "rate_date TEXT NOT NULL, amount_ron TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
                    "created_at TEXT NOT NULL, UNIQUE (date, client, number, amount, currency))")
        con.execute("INSERT INTO invoices (date, client, amount, currency, rate, rate_date, amount_ron, created_at) "
                    f"VALUES ('{YEAR}-01-05', 'Old', '10', 'RON', '1', '{YEAR}-01-05', '10.00', 'x')")
    db.init_db()
    row = stored()[0]
    assert row["income_date"] == f"{YEAR}-01-05" and row["payment_date"] == "" and row["attachment"] == ""
    assert db.list_year("invoices", YEAR)[0]["client"] == "Old"
