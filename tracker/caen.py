from .config import CAEN_PATH
from . import settings

YES = ("yes", "da", "y", "true", "1", "x")


def _revision(sheet_title: str) -> str | None:
    name = sheet_title.lower().replace(" ", "")
    if "rev.3" in name or "rev3" in name:
        return "3"
    if "rev.2" in name or "rev2" in name:
        return "2"
    return None


def load_caen() -> tuple[dict[str, dict[str, dict]], str | None]:
    """Reads caen_codes.xlsx into {"3": {code: entry}, "2": {code: entry}}."""
    if not CAEN_PATH.exists():
        return {}, f"CAEN list not found: {CAEN_PATH}"
    try:
        from openpyxl import load_workbook
        wb = load_workbook(CAEN_PATH, read_only=True, data_only=True)
        out: dict[str, dict[str, dict]] = {}
        for ws in wb.worksheets:
            rev = _revision(ws.title)
            if rev is None:
                continue
            rows = ws.iter_rows(values_only=True)
            header = [str(h or "").strip().lower() for h in next(rows, ())]

            def col(*prefixes):
                return next((i for p in prefixes for i, h in enumerate(header) if h.startswith(p)), None)

            ic, it, ix = col("code"), col("activity"), col("excluded")
            ifrom, ito, ibasis, inote = col("valid from"), col("valid to"), col("legal basis", "source"), col("note")
            codes = out.setdefault(rev, {})
            for r in rows:
                def cell(i):
                    return "" if i is None or i >= len(r) or r[i] is None else str(r[i]).strip()
                code = cell(ic).replace(".", "")
                if not code:
                    continue
                code = code.zfill(4) if code.isdigit() else code
                codes[code] = {
                    "code": code, "rev": rev, "title": cell(it),
                    "excluded": cell(ix).lower() in YES,
                    "from_year": cell(ifrom).split(".")[0], "to_year": cell(ito).split(".")[0],
                    "basis": cell(ibasis), "note": cell(inote),
                }
        wb.close()
    except Exception as e:  # a damaged or reshaped workbook must not stop the app
        return {}, f"could not read {CAEN_PATH.name}: {e}"
    if not out:
        return {}, f"{CAEN_PATH.name} has no sheet named 'CAEN Rev.3' or 'CAEN Rev.2'"
    return out, None


def exemption(year: str) -> str | None:
    """Reason the firm's CAEN code excludes this year's income from income tax, or None."""
    f = settings.firm()
    if not f["caen_code"]:
        return None
    entry = load_caen()[0].get(f["caen_revision"], {}).get(f["caen_code"])
    if not entry or not entry["excluded"]:
        return None
    if entry["from_year"].isdigit() and int(year) < int(entry["from_year"]):
        return None
    if entry["to_year"].isdigit() and int(year) > int(entry["to_year"]):
        return None
    basis = f" ({entry['basis']})" if entry["basis"] else ""
    return f"CAEN Rev.{f['caen_revision']} code {f['caen_code']} is marked as excluded from income tax in {CAEN_PATH.name}{basis}"
