import io

import pytest
from PIL import Image

from app.images import compress_jpeg


def make_png(size=(2000, 1500), color=(10, 200, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_compress_downscales_and_shrinks():
    out = compress_jpeg(make_png())
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 1280
    assert len(out) <= 200 * 1024


def test_compress_rejects_garbage():
    with pytest.raises(ValueError):
        compress_jpeg(b"not an image at all")


def test_compress_small_image_stays_valid_jpeg():
    out = compress_jpeg(make_png(size=(100, 100)))
    img = Image.open(io.BytesIO(out))
    img.load()
    assert img.format == "JPEG"
    assert img.size == (100, 100)
    assert len(out) <= 200 * 1024
