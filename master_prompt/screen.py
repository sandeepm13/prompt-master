"""Capture the monitor the user is working on, as a JPEG the model can see."""
from __future__ import annotations

import io


def grab_foreground_jpeg(hwnd: int) -> bytes:
    """Photograph the monitor that holds hwnd. The caller hides its own windows first."""
    from PIL import ImageGrab

    from . import winapi

    left, top, right, bottom = winapi.monitor_rect(hwnd)
    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    image = _fit(image, 1920)
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _fit(image, longest: int):
    from PIL import Image

    width, height = image.size
    if max(width, height) <= longest:
        return image
    if width >= height:
        size = (longest, max(1, int(height * longest / width)))
    else:
        size = (max(1, int(width * longest / height)), longest)
    return image.resize(size, Image.Resampling.LANCZOS)
