import io

from PIL import Image


def compress_jpeg(data: bytes, max_side: int = 1280, target_kb: int = 200) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ValueError("undecodable image") from exc

    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))

    quality = 80
    buf = io.BytesIO()
    while quality >= 40:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= target_kb * 1024:
            return buf.getvalue()
        quality -= 10
    return buf.getvalue()
