import io
import re
import unicodedata
from pathlib import Path

from .config import ATTACHMENTS_DIR, ATTACHMENT_EXTENSIONS, MAX_ATTACHMENT_BYTES

IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "gif": "GIF", "webp": "WEBP"}
MAX_IMAGE_PIXELS = 40_000_000   # a photo or scan of a receipt is far smaller; bigger images are refused before decoding

FOLDERS = {"invoices": "invoices", "expenses": "expenses"}


def folder(table: str) -> Path:
    return ATTACHMENTS_DIR / FOLDERS[table]


def check(filename: str, data: bytes) -> str:
    """Returns the safe file name to store, or raises ValueError.

    The name must have an allowed extension, and the content must really be a file of that type:
    a renamed or damaged file is refused.
    """
    base = Path(filename or "").name
    stem, dot, ext = base.rpartition(".")
    ext = ext.lower()
    if not dot or ext not in ATTACHMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ATTACHMENT_EXTENSIONS))
        raise ValueError(f"attachment '{base}' is not an allowed type (allowed: {allowed})")
    if len(data) == 0:
        raise ValueError("attachment is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"attachment is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
    check_content(ext, data)
    return f"{safe_part(stem) or 'file'}.{ext}"


def safe_part(text: str, limit: int = 80) -> str:
    """Letters without diacritics, digits, dot, dash and underscore only (Ștefan SRL -> Stefan_SRL)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_text).strip("._-")[:limit]


def check_content(ext: str, data: bytes) -> None:
    """Raises ValueError unless the bytes are a readable file of the type the extension claims."""
    if ext == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("attachment is not a PDF file")
        try:
            import logging
            from pypdf import PdfReader
            logging.getLogger("pypdf").setLevel(logging.ERROR)
            if len(PdfReader(io.BytesIO(data)).pages) < 1:
                raise ValueError
        except Exception:
            raise ValueError("attachment is a damaged or unreadable PDF")
    elif ext in IMAGE_FORMATS:
        check_image(ext, data)
    elif ext == "xml":
        try:
            from defusedxml.ElementTree import fromstring
            fromstring(data)
        except Exception:
            raise ValueError("attachment is not well-formed XML")
    elif ext == "txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("attachment is not a UTF-8 text file (save it as UTF-8 and try again)")
        if any((ord(c) < 32 and c not in "\t\r\n\f") or 0x7F <= ord(c) <= 0x9F for c in text):
            raise ValueError("attachment contains binary data, not text")


def check_image(ext: str, data: bytes) -> None:
    """The image must be of the named format, of a sensible size, and decode completely."""
    import warnings
    from PIL import Image
    expected = IMAGE_FORMATS[ext]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                found, (width, height) = img.format, img.size
                if found == expected and width * height <= MAX_IMAGE_PIXELS:
                    img.load()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("attachment image is too large")
    except Exception:
        raise ValueError(f"attachment is not a readable {expected} image")
    if found != expected:
        raise ValueError(f"attachment is a {found} image but is named .{ext}")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(f"attachment image is too large ({width} × {height} pixels)")


def save(table: str, row_id: int, filename: str, data: bytes, base_name: str | None = None) -> str:
    """Stores the file in the table's own folder and returns the stored name.

    With base_name (invoices: "<number>_<client>") the file is named after it, with _2, _3… added
    if that name is taken; otherwise it is stored as <row id>_<original name>.
    """
    checked = check(filename, data)
    folder(table).mkdir(parents=True, exist_ok=True)
    if base_name:
        stem, ext = safe_part(base_name, 120) or "proof", checked.rsplit(".", 1)[1]
        if stem.split(".")[0].upper() in WINDOWS_RESERVED:
            stem = "proof_" + stem
        taken = {p.name.lower() for p in folder(table).iterdir()}
        stored, n = f"{stem}.{ext}", 1
        while stored.lower() in taken:
            n += 1
            stored = f"{stem}-{n}.{ext}"
    else:
        stored = f"{row_id}_{checked}"
    (folder(table) / stored).write_bytes(data)
    return stored


def path(table: str, stored_name: str) -> Path | None:
    """The file for a stored name, only if it lies inside the table's folder."""
    if not stored_name or Path(stored_name).name != stored_name:
        return None
    p = folder(table) / stored_name
    return p if p.is_file() else None


def remove(table: str, stored_name: str) -> None:
    p = path(table, stored_name)
    if p:
        p.unlink()


def rename_invoice_proof(stored_name: str, base_name: str) -> str:
    """Renames an invoice proof after its number or client changed; returns the new stored name."""
    old = path("invoices", stored_name)
    if old is None:
        return stored_name
    ext = stored_name.rsplit(".", 1)[-1]
    stem = safe_part(base_name, 120) or "proof"
    if stem.split(".")[0].upper() in WINDOWS_RESERVED:
        stem = "proof_" + stem
    if f"{stem}.{ext}".lower() == stored_name.lower():
        return stored_name
    taken = {p.name.lower() for p in folder("invoices").iterdir()} - {stored_name.lower()}
    new, n = f"{stem}.{ext}", 1
    while new.lower() in taken:
        n += 1
        new = f"{stem}-{n}.{ext}"
    old.rename(folder("invoices") / new)
    return new


def download_name(table: str, stored_name: str) -> str:
    """Invoice proofs keep their <number>_<client> name; expense proofs drop the <row id>_ prefix."""
    if table == "expenses" and "_" in stored_name:
        return stored_name.split("_", 1)[1]
    return stored_name


def invoice_base_name(number: str, client: str) -> str:
    """<number>_<client>, each part made safe and shortened so that neither crowds the other out."""
    return f"{safe_part(number, 40) or 'no-number'}_{safe_part(client, 70) or 'client'}"


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
