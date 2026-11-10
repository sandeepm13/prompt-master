"""
Draws the floating dot (and its Undo pill) as frosted glass, one animation frame at a time.

Pillow draws each frame at 3x size and scales it down, so the edges are smooth.
On Windows the frame is shown with per-pixel transparency (see winapi.update_layered):
the glass body is see-through and the shadow and glow fade softly into the desktop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SS = 3  # supersampling

# Glass colours (RGBA)
DOT_TOP = (168, 156, 255, 205)
DOT_BOTTOM = (98, 78, 240, 235)
PILL_TOP = (46, 48, 60, 180)
PILL_BOTTOM = (24, 25, 31, 205)
GLOW = (124, 108, 255)
TEXT = (232, 232, 234)


@dataclass
class Frame:
    """Everything needed to draw one frame. Positions are in window pixels, before scaling."""

    width: int
    height: int
    dot_x: float               # top-left of the dot at scale 1
    dot_y: float
    size: int
    pill: tuple[float, float, float, float] | None = None  # full Undo pill: x, y, w, h
    label_x: float = 0.0
    font_px: int = 13
    scale: float = 1.0         # dot size: pops in, lifts on hover, squashes on press
    hover: float = 0.0         # 0..1 glow and brightness
    open: float = 1.0          # 0..1 how far the pill has grown out of the dot
    undo_hover: float = 0.0    # 0..1
    ripple: float = 1.0        # 0..1 click ripple; 1 = finished (invisible)
    busy: float = 0.0          # 0..1 the white core turns into a spinner
    spin: float = 0.0          # spinner angle in degrees


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@lru_cache(maxsize=16)
def _gradient(w: int, h: int) -> Image.Image:
    """Vertical L gradient: 0 at the top, 255 at the bottom."""
    return Image.linear_gradient("L").resize((max(1, w), max(1, h)))


@lru_cache(maxsize=16)
def _fade_down(w: int, h: int) -> Image.Image:
    """Vertical L gradient: 255 at the top, 0 at the bottom."""
    return ImageChops.invert(_gradient(w, h))


@lru_cache(maxsize=8)
def _font(px: int, symbol: bool = False) -> ImageFont.ImageFont:
    names = ("seguisym.ttf", "segoeui.ttf", "DejaVuSans.ttf") if symbol else ("segoeui.ttf", "DejaVuSans.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _paint(dst: Image.Image, mask: Image.Image, top: tuple, bottom: tuple | None = None,
           alpha: float = 1.0) -> None:
    """Fill mask on dst with a colour, or a vertical gradient from top to bottom (RGBA)."""
    if alpha <= 0.003:
        return
    w, h = dst.size
    if bottom is None or bottom == top:
        fill = Image.new("RGBA", (w, h), top)
    else:
        fill = Image.composite(Image.new("RGBA", (w, h), bottom), Image.new("RGBA", (w, h), top), _gradient(w, h))
    a = ImageChops.multiply(fill.getchannel("A"), mask)
    if alpha < 1:
        a = a.point(lambda v: int(v * alpha))
    fill.putalpha(a)
    dst.alpha_composite(fill)


def _mask(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    m = Image.new("L", size, 0)
    return m, ImageDraw.Draw(m)


def _ring(size, box, width: float) -> Image.Image:
    m, d = _mask(size)
    d.ellipse(box, outline=255, width=max(1, int(round(width))))
    return m


def pill_box(f: Frame) -> tuple[float, float, float, float] | None:
    """The pill as it is now: it grows out from behind the dot as `open` goes 0 -> 1."""
    if f.pill is None or f.open <= 0.001:
        return None
    px, py, pw, ph = f.pill
    t = max(0.0, min(1.0, f.open))
    return (_lerp(f.dot_x, px, t), _lerp(f.dot_y, py, t), _lerp(f.size, pw, t), _lerp(f.size, ph, t))


def render(f: Frame) -> Image.Image:
    """One frame as a straight (not premultiplied) RGBA image of f.width x f.height."""
    W, H = f.width, f.height
    lo = Image.new("RGBA", (W, H), (0, 0, 0, 0))          # soft layers: shadows and glow
    hi = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))  # crisp layers, drawn big
    big = hi.size

    r = f.size / 2 * max(0.0, f.scale)
    cx, cy = f.dot_x + f.size / 2, f.dot_y + f.size / 2
    pill = pill_box(f)
    pill_a = max(0.0, min(1.0, f.open)) if pill else 0.0

    # --- soft layers (drawn at 1x, then blurred)
    shadow, d = _mask((W, H))
    if pill:
        x, y, w, h = pill
        d.rounded_rectangle((x, y + 2, x + w, y + h + 2), radius=h / 2, fill=int(110 * pill_a))
    if r > 0.5:
        d.ellipse((cx - r, cy - r + 2 + f.hover, cx + r, cy + r + 2 + f.hover), fill=120)
    shadow = shadow.filter(ImageFilter.GaussianBlur(3 + 1.5 * f.hover))
    _paint(lo, shadow, (0, 0, 0, 255), alpha=0.75)

    glow_a = max(f.hover, 0.55 * f.busy)
    if glow_a > 0.01 and r > 0.5:
        glow, d = _mask((W, H))
        g = r + 3
        d.ellipse((cx - g, cy - g, cx + g, cy + g), fill=255)
        glow = glow.filter(ImageFilter.GaussianBlur(4))
        _paint(lo, glow, GLOW + (255,), alpha=0.65 * glow_a)

    # --- crisp layers (3x)
    def S(*vals: float) -> tuple[float, ...]:
        return tuple(v * SS for v in vals)

    if f.ripple < 1 and r > 0.5:  # a ring that grows and fades after a click
        t = f.ripple
        rr = (f.size / 2) * (1 + 0.55 * t)
        _paint(hi, _ring(big, S(cx - rr, cy - rr, cx + rr, cy + rr), SS * (2.2 - 1.6 * t)),
               (190, 180, 255, 255), alpha=(1 - t) ** 2 * 0.9)

    if pill:
        x, y, w, h = pill
        box = S(x, y, x + w, y + h)
        body, d = _mask(big)
        d.rounded_rectangle(box, radius=h / 2 * SS, fill=255)
        top = tuple(int(_lerp(a, b, f.undo_hover)) for a, b in zip(PILL_TOP, (62, 64, 80, 215)))
        _paint(hi, body, top, PILL_BOTTOM, alpha=pill_a)
        sheen, d = _mask(big)  # light catching the top edge of the glass
        d.rounded_rectangle(S(x + 1, y + 1, x + w - 1, y + h * 0.55), radius=h / 2 * SS, fill=255)
        _paint(hi, ImageChops.multiply(sheen, _fade_down(*big)),
               (255, 255, 255, 34), alpha=pill_a)
        rim, d = _mask(big)
        d.rounded_rectangle(box, radius=h / 2 * SS, outline=255, width=SS)
        _paint(hi, rim, (255, 255, 255, 70 + int(50 * f.undo_hover)), (255, 255, 255, 22), alpha=pill_a)

        label_a = max(0.0, min(1.0, (f.open - 0.45) / 0.55))
        if label_a > 0.01:
            shift = 6 * (1 - label_a)
            colour = tuple(int(_lerp(a, 255, f.undo_hover)) for a in TEXT)
            text, d = _mask(big)
            font, icon = _font(f.font_px * SS), _font(f.font_px * SS, symbol=True)
            tx, ty = (f.label_x + shift) * SS, (y + h / 2) * SS
            d.text((tx, ty), "↩", font=icon, fill=255, anchor="lm")
            d.text((tx + icon.getlength("↩") + 6 * SS, ty), "Undo", font=font, fill=255, anchor="lm")
            _paint(hi, text, colour + (255,), alpha=label_a)

    if r > 0.5:
        box = S(cx - r, cy - r, cx + r, cy + r)
        body, d = _mask(big)
        d.ellipse(box, fill=255)
        top = tuple(int(_lerp(a, b, f.hover)) for a, b in zip(DOT_TOP, (186, 176, 255, 225)))
        _paint(hi, body, top, DOT_BOTTOM)

        sheen, d = _mask(big)  # curved highlight over the top half
        d.ellipse(S(cx - r * 0.78, cy - r * 0.94, cx + r * 0.78, cy + r * 0.12), fill=255)
        sheen = ImageChops.multiply(ImageChops.multiply(sheen, body), _fade_down(*big))
        _paint(hi, sheen, (255, 255, 255, 130 + int(60 * f.hover)))

        _paint(hi, _ring(big, box, SS * 1.0), (255, 255, 255, 170), (255, 255, 255, 50))

        core = r * 0.36 * (1 + 0.12 * f.hover) * (1 - f.busy)
        if core > 0.3:
            c, d = _mask(big)
            d.ellipse(S(cx - core, cy - core, cx + core, cy + core), fill=255)
            _paint(hi, c, (255, 255, 255, 255))
        if f.busy > 0.01:
            a, d = _mask(big)
            sr = r * 0.48
            sweep = 100 + 160 * (0.5 + 0.5 * math.sin(math.radians(f.spin * 2)))
            d.arc(S(cx - sr, cy - sr, cx + sr, cy + sr), f.spin, f.spin + sweep, fill=255, width=int(SS * 2.2))
            _paint(hi, a, (255, 255, 255, 255), alpha=f.busy)

    lo.alpha_composite(hi.resize((W, H), Image.LANCZOS))
    # Almost-clear pixels would still catch clicks on Windows. Make them fully clear.
    lo.putalpha(lo.getchannel("A").point(lambda v: 0 if v < 6 else v))
    return lo


def premultiplied_bgra(img: Image.Image) -> bytes:
    """Pixels in the order UpdateLayeredWindow wants: B, G, R, A with colour premultiplied by alpha."""
    r, g, b, a = img.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def flatten(img: Image.Image, matte: tuple[int, int, int], key: tuple[int, int, int]) -> Image.Image:
    """For windows without per-pixel transparency: soft edges go onto matte, clear pixels become key."""
    base = Image.new("RGBA", img.size, matte + (255,))
    base.alpha_composite(img)
    out = Image.new("RGB", img.size, key)
    out.paste(base.convert("RGB"), mask=img.getchannel("A").point(lambda v: 255 if v >= 110 else 0))
    return out
