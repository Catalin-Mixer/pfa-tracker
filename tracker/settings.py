import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .config import SETTINGS_PATH

TAX_KEYS = ("minimum_wage", "cas_rate", "cas_low_wages", "cas_high_wages", "cass_rate",
            "cass_floor_wages", "cass_ceiling_wages", "income_tax_rate")


class SettingsError(Exception):
    pass


def read() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"), parse_float=Decimal)
    except FileNotFoundError:
        raise SettingsError(f"settings file not found: {SETTINGS_PATH}")
    except OSError as e:
        raise SettingsError(f"could not read settings file {SETTINGS_PATH}: {e}")
    except json.JSONDecodeError as e:
        raise SettingsError(f"settings file {SETTINGS_PATH} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise SettingsError(f"settings file {SETTINGS_PATH} should contain a JSON object")
    return data


def write(data: dict) -> None:
    def encode(o):
        # whole numbers stay integers (4050, 12); rates are written as plain decimals (0.25)
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() and "." not in str(o) else float(o)
        raise TypeError(type(o))
    ordered = {k: data[k] for k in ("firm", "years") if k in data} | {k: v for k, v in data.items() if k not in ("firm", "years")}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ordered, indent=2, ensure_ascii=False, default=encode) + "\n", encoding="utf-8")
    tmp.replace(SETTINGS_PATH)


def year_blocks() -> dict:
    years = read().get("years", {})
    return years if isinstance(years, dict) else {}


def has_salary(year: str) -> bool:
    """Salaried job in that year. A mark for a year before the PFA was registered is ignored."""
    if year not in marked_years():
        return False
    registered = registration_year(firm()["trade_register"])
    return not (registered and year.isdigit() and int(year) < registered)


def marked_years() -> set[str]:
    """Years marked with a salaried job in settings.json (only a real true counts)."""
    try:
        blocks = year_blocks()
    except SettingsError:
        return set()
    return {y for y, b in blocks.items() if isinstance(b, dict) and b.get("has_salary") is True}


def tax_settings(year: str) -> tuple[dict | None, str | None, str | None]:
    """Returns (values, year they come from, error).

    A year without tax values uses the latest earlier year's values, but has_salary is always
    taken from the requested year itself: it is a personal fact and is never inherited.
    """
    try:
        years = year_blocks()
    except SettingsError as e:
        return None, None, str(e)
    candidates = sorted((y for y, b in years.items()
                         if y.isdigit() and int(y) <= int(year) and isinstance(b, dict) and "minimum_wage" in b), key=int)
    if not candidates:
        return None, None, f"no settings for {year} or any earlier year in {SETTINGS_PATH.name}"
    src = candidates[-1]
    block = years[src]
    try:
        values = {k: Decimal(str(block[k])) for k in TAX_KEYS}
    except KeyError as e:
        return None, src, f"settings for {src} are missing {e}"
    except InvalidOperation:
        return None, src, f"settings for {src} contain a value that is not a number"
    values["has_salary"] = has_salary(year)
    values["checked"] = bool(block.get("checked", False))
    values["note"] = block.get("note", "")
    return values, src, None


FIRM_TEXT_FIELDS = ("name", "cui", "caen_code", "trade_register", "phone", "email", "iban", "address")


def firm() -> dict:
    try:
        f = read().get("firm", {})
    except SettingsError:
        f = {}
    f = f if isinstance(f, dict) else {}
    out = {k: str(f[k]) if isinstance(f.get(k), (str, int)) and not isinstance(f.get(k), bool) else ""
           for k in FIRM_TEXT_FIELDS}
    out["caen_revision"] = "2" if str(f.get("caen_revision")) == "2" else "3"
    extra = f.get("secondary_caen", [])
    out["secondary_caen"] = [str(c) for c in extra if isinstance(c, (str, int))] if isinstance(extra, list) else []
    return out


def registration_year(trade_register: str) -> int | None:
    """The year the PFA was registered, read from its Trade Register number (F40/1234/2019)."""
    m = TRADE_REGISTER.fullmatch(trade_register or "")
    return int(m.group("year")[-4:]) if m else None


def lifetime_years(trade_register: str, today: date | None = None) -> list[str]:
    """Past years in which the PFA existed (from its registration year to last year), newest first."""
    start = registration_year(trade_register)
    current = (today or date.today()).year
    return [str(y) for y in range(current - 1, start - 1, -1)] if start else []


def save_firm_and_salary(firm_values: dict, salary: dict[str, bool]) -> None:
    """Saves the firm and the salaried-job marks. When there is no settings.json yet, it is made, starting like a new
    profile: the default tax values per year and no salaried-job marks. A settings.json that exists but cannot be read
    is never replaced (SettingsError)."""
    if SETTINGS_PATH.exists():
        data = read()
    else:
        from .profiles import _starting_settings
        data = _starting_settings(None)
    old = data.get("firm") if isinstance(data.get("firm"), dict) else {}
    data["firm"] = {**old, **firm_values}          # keys added by hand are kept
    years = data.setdefault("years", {})
    if not isinstance(years, dict):
        raise SettingsError(f"'years' in {SETTINGS_PATH.name} should be a set of year blocks")
    for year, on in salary.items():
        if year in years and not isinstance(years[year], dict):
            raise SettingsError(f"the {year} entry in {SETTINGS_PATH.name} is not a year block")
        if year in years or on:
            years.setdefault(year, {})["has_salary"] = on
    write(data)


def cui_valid(cui: str) -> bool:
    """Checks the control digit of a Romanian CUI/CIF (key 753217532); an RO prefix is allowed."""
    s = cui.strip().upper().removeprefix("RO").strip()
    if not s.isdigit() or not 2 <= len(s) <= 10:
        return False
    body, check = s[:-1].zfill(9), int(s[-1])
    c = sum(int(d) * int(k) for d, k in zip(body, "753217532")) * 10 % 11
    return (0 if c == 10 else c) == check


# Firm details: each check returns the cleaned value or raises ValueError with a readable reason.

# F<county>/<number>/<year or registration date>: county 1-40, 51 or 52 (40 is Bucharest)
TRADE_REGISTER = re.compile(r"F(?P<county>0?[1-9]|[1-3][0-9]|40|51|52)/(?P<number>[1-9][0-9]{0,6})/"
                            r"(?P<year>(?:(?:0[1-9]|[12][0-9]|3[01])\.(?:0[1-9]|1[0-2])\.)?(?:19|20)[0-9]{2})")


def clean_trade_register(text: str, today: date | None = None) -> str:
    t = re.sub(r"\s+", "", text or "").upper()
    m = TRADE_REGISTER.fullmatch(t)
    if not m:
        raise ValueError("the Trade Register number must look like F40/1234/2019 "
                         "(F, county code 1–40, 51 or 52, order number, year or registration date)")
    year = int(m.group("year")[-4:])
    current = (today or date.today()).year
    if not 1990 <= year <= current:
        raise ValueError(f"the Trade Register year {year} is not possible (1990–{current})")
    if "." in m.group("year"):
        day, month, _ = m.group("year").split(".")
        try:
            registered = date(year, int(month), int(day))
        except ValueError:
            raise ValueError(f"the registration date {m.group('year')} does not exist")
        if registered > (today or date.today()):
            raise ValueError("the registration date is in the future")
    return t


def clean_phone(text: str) -> str:
    """Romanian numbers (07xx xxx xxx, 02x/03x landlines, +40…, 0040…) or any +country number."""
    t = re.sub(r"\+40\s*\(0\)", "+40", (text or "").strip())
    t = re.sub(r"[\s.\-()/]", "", t)
    if t.startswith("0040"):
        t = "+40" + t[4:]
    elif t.startswith("0") and not t.startswith("00"):
        t = "+40" + t[1:]
    elif t.startswith("00"):
        t = "+" + t[2:]
    if t.startswith("+40"):
        if not re.fullmatch(r"\+40[2378][0-9]{8}", t):
            raise ValueError("a Romanian phone number has 10 digits, e.g. 0721 234 567 or +40 21 123 4567")
    elif not re.fullmatch(r"\+[1-9][0-9]{7,14}", t):
        raise ValueError("the phone number is not valid (e.g. 0721 234 567 or +49 30 1234567)")
    return t


EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")


def clean_email(text: str) -> str:
    t = (text or "").strip()
    if len(t) > 254 or not EMAIL.fullmatch(t) or ".." in t or t.split("@")[0].startswith(".") or t.split("@")[0].endswith("."):
        raise ValueError("the email address is not valid (e.g. name@example.ro)")
    return t.lower()


IBAN_LENGTHS = {"RO": 24, "DE": 22, "AT": 20, "BG": 22, "HU": 28, "FR": 27, "IT": 27, "ES": 24, "NL": 18,
                "BE": 16, "GB": 22, "IE": 22, "PL": 28, "MD": 24, "CH": 21, "LU": 20, "PT": 25, "CZ": 24,
                "SK": 24, "SE": 24, "DK": 18, "FI": 18, "GR": 27}


def clean_iban(text: str) -> str:
    t = re.sub(r"[\s-]", "", text or "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", t):
        raise ValueError("the IBAN is not valid (e.g. RO49 AAAA 1B31 0075 9384 0000)")
    country = t[:2]
    if country == "RO" and not re.fullmatch(r"RO[0-9]{2}[A-Z]{4}[A-Z0-9]{16}", t):
        raise ValueError("a Romanian IBAN is RO + 2 check digits + 4-letter bank code + 16 characters (24 in all)")
    if country in IBAN_LENGTHS and len(t) != IBAN_LENGTHS[country]:
        raise ValueError(f"an IBAN from {country} has {IBAN_LENGTHS[country]} characters, this one has {len(t)}")
    digits = "".join(str(int(c, 36)) for c in t[4:] + t[:4])
    if int(digits) % 97 != 1:
        raise ValueError("the IBAN check digits do not match (a character is probably mistyped)")
    return " ".join(t[i:i + 4] for i in range(0, len(t), 4))


def clean_address(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not 5 <= len(t) <= 200:
        raise ValueError("the address should be between 5 and 200 characters")
    if not re.search(r"[A-Za-zĂÂÎȘȚăâîșțŞŢşţ]", t) or any(ord(c) < 32 for c in t):
        raise ValueError("the address is not readable")
    return t


def clean_caen_list(text: str, main_code: str) -> list[str]:
    codes = [c.replace(".", "") for c in re.split(r"[\s,;]+", text or "") if c]
    bad = [c for c in codes if not (c.isdigit() and len(c) == 4)]
    if bad:
        raise ValueError(f"secondary CAEN codes must be 4 digits each (not {', '.join(bad[:3])})")
    if main_code in codes:
        raise ValueError(f"{main_code} is already the main activity")
    if len(codes) > 30:
        raise ValueError("at most 30 secondary CAEN codes")
    return list(dict.fromkeys(codes))
