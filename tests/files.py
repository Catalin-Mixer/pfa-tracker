"""Small but real sample files for the proof checks, made on the fly."""
import io

from PIL import Image


def _image(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


def _pdf(padding: int = 0) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if padding:
        writer.add_metadata({"/Padding": "x" * padding})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


PDF = _pdf()
PNG = _image("PNG")
JPEG = _image("JPEG")
GIF = _image("GIF")
WEBP = _image("WEBP")
XML = b'<?xml version="1.0" encoding="UTF-8"?><Invoice><ID>F-1</ID><Total currency="EUR">100.00</Total></Invoice>'


def big_pdf(size: int) -> bytes:
    """A valid one-page PDF of exactly the given size (padded through its metadata)."""
    padding = max(size - len(_pdf(1)), 1)
    for _ in range(5):
        data = _pdf(padding)
        if len(data) == size:
            return data
        padding += size - len(data)
    raise AssertionError("could not build a PDF of that size")
