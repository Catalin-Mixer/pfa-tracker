"""Damaged images must be refused, not only renamed ones: cut-short files, garbage pixels, oversized images."""
import io
import struct
import zlib

import pytest
from PIL import Image

from tracker import attachments


def _noise(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((200, 200), 64).convert("RGB").save(buf, fmt)
    return buf.getvalue()


def _png_with_garbage_pixels() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", b"not zlib data") + chunk(b"IEND", b""))


JPEG = _noise("JPEG")
GIF = _noise("GIF")


@pytest.mark.parametrize("name, data", [
    ("cut.jpg", JPEG[: int(len(JPEG) * 0.7)]),
    ("cut.jpg", JPEG[: int(len(JPEG) * 0.95)]),
    ("cut.gif", GIF[: int(len(GIF) * 0.6)]),
    ("garbage.png", _png_with_garbage_pixels()),
], ids=["jpeg-cut-70%", "jpeg-cut-95%", "gif-cut-60%", "png-garbage-pixels"])
def test_damaged_images_are_refused(name, data):
    with pytest.raises(ValueError):
        attachments.check(name, data)


def test_whole_images_still_pass():
    assert attachments.check("ok.jpg", JPEG) == "ok.jpg"
    assert attachments.check("ok.gif", GIF) == "ok.gif"


def test_huge_images_are_refused_before_decoding():
    buf = io.BytesIO()
    Image.new("1", (10000, 10000)).save(buf, "PNG")      # 100 million pixels, tiny file
    with pytest.raises(ValueError, match="too large"):
        attachments.check("huge.png", buf.getvalue())
