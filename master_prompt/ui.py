"""
The little windows you see: the dot, the "Regenerate?" popup, the API key window and toasts.

Built with tkinter, which comes with Python - nothing to install.
All functions here must be called from the main (tkinter) thread.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from . import glass
from .motion import Motion

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    from . import winapi
    winapi.load_app_fonts()

# Colours: Deep Obsidian Glass Canvas (matching reference rgba(18, 14, 24) - NO purple spread across canvas)
BG = "#120e18"              # Deep obsidian glass canvas
BG_2 = "#1c1824"            # Elevated inner card canvas
BG_3 = "#262230"            # Raised / hover card surface
BORDER = "#2f283d"          # Soft lavender-tinted obsidian rim (rgba(216, 180, 254, 0.22))
BORDER_HIGHLIGHT = "#443b59" # Specular rim
BORDER_FOCUS = "#7c3aed"    # Active focus border
FG = "#f4f4f7"              # Crisp high-contrast text
FG_DIM = "#a2a1ad"          # Clean secondary text
FG_FAINT = "#686775"        # Subtle faint label text

# Accent colors (Purple is ONLY on buttons & interactive accents)
ACCENT = "#c084fc"          # Lavender link / context accent
ACCENT_HOVER = "#e9d5ff"    # Soft Lilac hover
ACCENT_PURPLE = "#a855f7"   # Ready status dot (●)
ERROR = "#f87171"

# Typography: Inter (Primary Font) + Plus Jakarta Sans (Buttons & Capsules)
FONT_FAMILY = "Inter"
FONT_FAMILY_BTN = "Plus Jakarta Sans"

FONT = (FONT_FAMILY, 10)
FONT_TEXT = (FONT_FAMILY, 11)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_TINY = (FONT_FAMILY, 8, "bold")
FONT_TITLE = (FONT_FAMILY, 11, "bold")
FONT_BUTTON = (FONT_FAMILY_BTN, 10, "bold")


def _px(widget: tk.Misc, n: float) -> int:
    """n pixels at 100% display scaling, in real pixels."""
    return round(n * max(1.0, widget.winfo_fpixels("1i") / 96))


def _rgb(colour: str) -> tuple[int, int, int]:
    if not colour:
        return (24, 23, 28)
    if isinstance(colour, str) and colour.startswith("#"):
        c = colour.lstrip("#")
        if len(c) == 3:
            return int(c[0] * 2, 16), int(c[1] * 2, 16), int(c[2] * 2, 16)
        if len(c) >= 6:
            return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    try:
        from PIL import ImageColor
        return ImageColor.getrgb(colour)[:3]
    except Exception:
        return (24, 23, 28)


def _mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def _rounded_gradient(width: int, height: int, radius: float, top_colour: str, bottom_colour: str,
                      bg: str, outline: str | None = None, specular: float = 0.5,
                      bottom_bevel: str | None = None, outline_width: float = 1) -> ImageTk.PhotoImage:
    """
    Renders a hardware-smooth rounded rectangle with a vertical linear gradient,
    a specular light bevel along the top edge, and an optional 3D cushion shadow at the bottom.
    Downsampled with Lanczos supersampling (3x) for pristine macOS-quality glass buttons.
    """
    ss = 3
    w_ss, h_ss = max(1, width * ss), max(1, height * ss)
    rad_ss = radius * ss

    img = Image.new("RGBA", (w_ss, h_ss), (*_rgb(bg), 255))
    mask = Image.new("L", (w_ss, h_ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w_ss - 1, h_ss - 1), radius=rad_ss, fill=255)

    top_rgb = _rgb(top_colour)
    bot_rgb = _rgb(bottom_colour)
    if top_rgb == bot_rgb:
        fill_img = Image.new("RGBA", (w_ss, h_ss), (*top_rgb, 255))
    else:
        grad = Image.linear_gradient("L").resize((w_ss, h_ss))
        top_img = Image.new("RGB", (w_ss, h_ss), top_rgb)
        bot_img = Image.new("RGB", (w_ss, h_ss), bot_rgb)
        fill_img = Image.composite(bot_img, top_img, grad).convert("RGBA")

    draw_fill = ImageDraw.Draw(fill_img)

    # Top specular reflection highlight (Cluely / macOS Sonoma glass highlight)
    if specular > 0.01 and h_ss > 6 * ss:
        spec_alpha = int(255 * min(1.0, specular))
        inset = max(rad_ss * 0.45, 2 * ss)
        draw_fill.line([(inset, ss), (w_ss - 1 - inset, ss)], fill=(255, 255, 255, spec_alpha), width=ss)

    # Bottom 3D cushion shadow bevel
    if bottom_bevel and h_ss > 6 * ss:
        inset = max(rad_ss * 0.45, 2 * ss)
        draw_fill.line([(inset, h_ss - 1 - ss), (w_ss - 1 - inset, h_ss - 1 - ss)],
                       fill=(*_rgb(bottom_bevel), 180), width=ss)

    img.paste(fill_img, (0, 0), mask)

    # Outline
    if outline:
        draw_out = ImageDraw.Draw(img)
        draw_out.rounded_rectangle((0, 0, w_ss - 1, h_ss - 1), radius=rad_ss,
                                   outline=(*_rgb(outline), 255), width=max(1, round(outline_width * ss)))

    return ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS).convert("RGB"))


def _rounded_card(width: int, height: int, radius: float, fill: str, bg: str,
                  outline: str | None = None, outline_width: float = 1,
                  specular: bool = True) -> ImageTk.PhotoImage:
    """Glass card canvas: deep obsidian with subtle specular reflection at the top rim."""
    ss = 3
    w_ss, h_ss = max(1, width * ss), max(1, height * ss)
    img = Image.new("RGBA", (w_ss, h_ss), (*_rgb(bg), 255))
    draw = ImageDraw.Draw(img)
    rad_ss = radius * ss
    box = (0, 0, w_ss - 1, h_ss - 1)

    # Base card
    draw.rounded_rectangle(box, radius=rad_ss, fill=(*_rgb(fill), 255))

    # Specular top highlight (macOS Sonoma / Cluely obsidian glass top edge)
    if specular and h_ss > 10 * ss:
        inset = max(rad_ss * 0.35, 2 * ss)
        draw.line([(inset, ss), (w_ss - 1 - inset, ss)], fill=(255, 255, 255, 45), width=ss)

    # Soft lavender outline
    if outline:
        draw.rounded_rectangle(box, radius=rad_ss, outline=(*_rgb(outline), 255),
                               width=max(1, round(outline_width * ss)))

    return ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS).convert("RGB"))


def _rounded(width: int, height: int, radius: float, fill: str, bg: str, outline: str | None = None,
             outline_width: float = 1) -> ImageTk.PhotoImage:
    """A smooth rounded rectangle on a solid background, for canvas-drawn panels and buttons."""
    return _rounded_card(width, height, radius, fill, bg, outline, outline_width, specular=False)


def _frameless(win: tk.Toplevel) -> None:
    """No title bar. Translucent obsidian glass with DWM Acrylic and rounded corners on Windows 11."""
    win.overrideredirect(True)
    win.configure(bg=BG)
    rounded = False
    if IS_WINDOWS:
        win.update_idletasks()
        try:
            hwnd = int(win.wm_frame(), 16)
            rounded = winapi.round_corners(hwnd, _rgb(BORDER))
            winapi.enable_acrylic(hwnd)
        except Exception:
            rounded = False
    if not rounded:
        win.configure(highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)


def _dot_icon(size: int) -> ImageTk.PhotoImage:
    """The glass dot, small, for window headers."""
    pad = max(2, size // 6)
    img = glass.render(glass.Frame(size + 2 * pad, size + 2 * pad, pad, pad - 1, size))
    base = Image.new("RGBA", img.size, _rgb(BG) + (255,))
    base.alpha_composite(img)
    return ImageTk.PhotoImage(base.convert("RGB"))


def _cursor_and_area(root: tk.Misc) -> tuple[int, int, tuple[int, int, int, int]]:
    if IS_WINDOWS:
        x, y = winapi.cursor_pos()
        return x, y, winapi.work_area_at(x, y)
    x, y = root.winfo_pointerxy()
    return x, y, (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())


def _work_area(win: tk.Misc, x: int, y: int) -> tuple[int, int, int, int]:
    if IS_WINDOWS:
        return winapi.work_area_at(x, y)
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def _place_near_cursor(win: tk.Toplevel, width: int, height: int, offset: int = 18) -> None:
    x, y, (left, top, right, bottom) = _cursor_and_area(win)
    px = min(max(x + offset, left + 8), right - width - 8)
    py = y + offset
    if py + height > bottom - 8:  # not enough room below the mouse -> show above it
        py = max(top + 8, y - height - offset)
    win.geometry(f"{width}x{height}+{px}+{py}")


class _Button(tk.Canvas):
    """
    Rounded glass button, drawn smooth with specular highlights and animated hover and press.
    kind: "primary" (deep royal amethyst to midnight indigo gradient with 3D cushion shadow),
          "secondary" (translucent obsidian glass pill with soft lavender rim),
          or "ghost" (no fill until hovered, e.g. the ✕).
    """

    _GRADIENTS = {
        # (top_idle, bottom_idle), (top_hover, bottom_hover), text_idle, text_hover, outline_idle, outline_hover, specular, bottom_bevel
        "primary": (
            ("#9b5ae4", "#682a9d"),  # Exact match to the luminous purple of the dot
            ("#a86ef0", "#793aaf"),  # Elevated Luminous Violet hover
            "#ffffff",               # Pure crisp white text
            "#ffffff",
            "#8149b1",               # Matching violet rim
            "#ad83d4",               # Soft luminous hover rim
            0.50,                    # Top specular highlight (glass reflection)
            "#3e1660",               # Cushion shadow bevel
        ),
        "secondary": (
            ("#24222a", "#1a1920"),  # Deep obsidian glass pill
            ("#302e38", "#24222c"),  # Elevated glass hover
            "#e9d5ff",               # Soft Lilac text
            "#ffffff",               # Pure white hover
            "#3a3745",               # Clean subtle rim
            "#6b488a",               # Lavender hover rim
            0.20,                    # Subtle specular reflection
            "#100f14",
        ),
        "ghost": (
            (None, None),
            ("#282730", "#201f26"),
            "#a2a1ad",
            "#ffffff",
            None,
            "#3d3c48",
            0.0,
            None,
        ),
    }

    def __init__(self, master, text: str, command: Callable[[], None], primary: bool = False, *,
                 kind: str | None = None, square: int | None = None, width: int | None = None,
                 height: int | None = None, font=FONT_BUTTON):
        self._kind = kind or ("primary" if primary else "secondary")
        self._bg = master.cget("bg")
        self._font = font
        h_default = 34
        if square:
            w_px = h_px = _px(master, square)
        else:
            text_w = tkfont.Font(master, font=font).measure(text)
            w_px = _px(master, width) if width is not None else (text_w + _px(master, 28))
            h_px = _px(master, height) if height is not None else _px(master, h_default)
        super().__init__(master, width=w_px, height=h_px, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self._size = (w_px, h_px)
        self._radius = h_px / 2 if square else _px(master, 9)
        self._command = command
        self._enabled = True
        self._inside = False
        self._photo = None
        self._image = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(w_px / 2, h_px / 2, text=text, font=font)
        self.motion = Motion(self, self._paint, hover=0.0, press=0.0)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._down)
        self.bind("<ButtonRelease-1>", self._up)
        self._paint()

    def _paint(self) -> None:
        (t_idle, b_idle), (t_hov, b_hov), txt_idle, txt_hov, out_idle, out_hov, spec, bevel = self._GRADIENTS[self._kind]
        h, p = self.motion["hover"], self.motion["press"]

        if t_idle is None:
            # Ghost button (transparent until hovered)
            if h > 0.02:
                top = _mix(self._bg, t_hov, h)
                bot = _mix(self._bg, b_hov, h)
                outline = _mix(self._bg, out_hov, h)
            else:
                top = bot = self._bg
                outline = None
        else:
            top = _mix(t_idle, t_hov, h)
            bot = _mix(b_idle, b_hov, h)
            outline = _mix(out_idle, out_hov, h) if (out_idle and out_hov) else out_idle

        # Pressed state: deepen gradient
        if p > 0.01:
            top = _mix(top, "#000000", 0.25 * p)
            bot = _mix(bot, "#000000", 0.35 * p)

        if not self._enabled:
            top = bot = _mix(self._bg, FG_FAINT, 0.15)
            outline = BORDER
            spec = 0.0
            bevel = None

        text = _mix(txt_idle, txt_hov, h) if self._enabled else FG_FAINT

        self._photo = _rounded_gradient(*self._size, self._radius, top, bot, self._bg,
                                        outline=outline, specular=spec, bottom_bevel=bevel)
        self.itemconfigure(self._image, image=self._photo)
        self.itemconfigure(self._label, fill=text)

    def _enter(self, _e) -> None:
        self._inside = True
        if self._enabled:
            self.motion.to("hover", 1, 0.18, "power2.out")

    def _leave(self, _e) -> None:
        self._inside = False
        self.motion.to("hover", 0, 0.25, "power2.out")
        self.motion.to("press", 0, 0.15, "power2.out")

    def _down(self, _e) -> None:
        if self._enabled:
            self.motion.to("press", 1, 0.08, "power2.out")

    def _up(self, _e) -> None:
        self.motion.to("press", 0, 0.3, "power2.out")
        if self._enabled and self._inside:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if not enabled:
            self.motion.set("hover", 0)
        self._paint()


class _Card(tk.Canvas):
    """A rounded obsidian glass card of a fixed width. Put widgets in .body; the panel grows to fit their height."""

    def __init__(self, master, width: int, *, fill: str = BG_2, outline: str | None = BORDER,
                 radius: int = 10, pad: tuple[int, int] = (12, 10), specular: bool = True):
        self._bg = master.cget("bg")
        super().__init__(master, width=width, height=1, bg=self._bg, highlightthickness=0, bd=0)
        self._fill, self._outline = fill, outline
        self._radius = _px(master, radius)
        self._pad = (_px(master, pad[0]), _px(master, pad[1]))
        self._width = width
        self._specular = specular
        self._photo = None
        self._image = self.create_image(0, 0, anchor="nw")
        self.body = tk.Frame(self, bg=fill)
        self.create_window(self._pad[0], self._pad[1], window=self.body, anchor="nw",
                           width=width - 2 * self._pad[0])
        self._height = 0

    @property
    def inner_width(self) -> int:
        return self._width - 2 * self._pad[0]

    def fit(self) -> None:
        """Grow or shrink to the content. Call after changing what is inside."""
        self.body.update_idletasks()
        height = self.body.winfo_reqheight() + 2 * self._pad[1]
        if height != self._height:
            self._height = height
            self.configure(height=height)
            self._draw()

    def _draw(self) -> None:
        if self._height:
            self._photo = _rounded_card(self._width, self._height, self._radius, self._fill, self._bg,
                                        self._outline, specular=self._specular)
            self.itemconfigure(self._image, image=self._photo)

    def set_outline(self, colour: str | None) -> None:
        self._outline = colour
        self._draw()


def _header(win: tk.Misc, title: str, on_close: Callable[[], None], icon: ImageTk.PhotoImage | None = None,
            ) -> tk.Frame:
    row = tk.Frame(win, bg=BG)
    if icon is not None:
        tk.Label(row, image=icon, bg=BG, bd=0).pack(side="left", padx=(0, _px(win, 8)))
    tk.Label(row, text=title, bg=BG, fg=FG, font=FONT_TITLE).pack(side="left")
    _Button(row, "✕", on_close, kind="ghost", square=26, font=FONT_SMALL).pack(side="right")
    return row


def short(text: str, limit: int = 60) -> str:
    """One line, cut at a word, for showing a note in the popup."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


class ConfirmPopup:
    """
    Asks "Regenerate the prompt?" beside the dot, and shows the context that will be used:
    what was detected, and the user's note for this place ("My context").
    It never takes keyboard focus, so the caret stays in the user's input box.
    """

    def __init__(self, root: tk.Tk, *, on_yes: Callable[[], None], on_no: Callable[[], None],
                 on_edit_note: Callable[[], None], on_clear_note: Callable[[], None]):
        self.on_yes = on_yes
        self.on_no = on_no
        self._anchor: tuple[int, int, int, int] | None = None
        self._pos = (0, 0)
        w = self.win = tk.Toplevel(root)
        self.motion = Motion(w, self._apply_motion, slide=0.0)
        w.withdraw()
        w.title("dotix")
        w.attributes("-topmost", True)
        _frameless(w)
        pad = _px(w, 16)
        width = _px(w, 340)

        self._icon = _dot_icon(_px(w, 16))
        _header(w, "Regenerate this prompt?", self._no, self._icon).pack(fill="x", padx=(pad, _px(w, 10)),
                                                                          pady=(_px(w, 12), 0))

        card = self.card = _Card(w, width - 2 * pad)
        card.pack(padx=pad, pady=(_px(w, 10), 0))
        body, wrap = card.body, card.inner_width
        self.context_box = tk.Frame(body, bg=BG_2)

        tag_row = tk.Frame(self.context_box, bg=BG_2)
        tag_row.pack(fill="x")
        tk.Label(tag_row, text="●", bg=BG_2, fg=ACCENT_PURPLE, font=(FONT_FAMILY, 7)).pack(side="left", padx=(0, _px(w, 5)))
        tk.Label(tag_row, text="WRITING IN", bg=BG_2, fg=FG_FAINT, font=FONT_TINY).pack(side="left")

        self.place = tk.Label(self.context_box, text="", bg=BG_2, fg=FG, font=FONT_BUTTON, anchor="w",
                              justify="left", wraplength=wrap)
        self.place.pack(fill="x", pady=(_px(w, 2), 0))
        self.detail = tk.Label(self.context_box, text="", bg=BG_2, fg=FG_DIM, font=FONT_SMALL, anchor="w",
                               justify="left", wraplength=wrap)
        self.divider = tk.Frame(self.context_box, bg="#2d2c36", height=1)

        row = tk.Frame(body, bg=BG_2)
        row.pack(fill="x", side="bottom")
        self.note = tk.Label(row, text="", bg=BG_2, fg=ACCENT, font=FONT_SMALL, anchor="w", justify="left",
                             wraplength=wrap - _px(w, 28), cursor="hand2")
        self.note.pack(side="left", fill="x", expand=True)
        self.note.bind("<Button-1>", lambda e: on_edit_note())
        self.note.bind("<Enter>", lambda e: self._note_hover(True))
        self.note.bind("<Leave>", lambda e: self._note_hover(False))
        self.clear = _Button(row, "✕", on_clear_note, kind="ghost", square=22, font=FONT_TINY)
        self._has_note = False

        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=pad, pady=(_px(w, 14), pad))
        _Button(bottom, "Dismiss", self._no, width=96, font=FONT_BUTTON).pack(side="left", padx=(0, _px(w, 8)))
        _Button(bottom, "✨ Regenerate", self._yes, primary=True, width=204, font=FONT_BUTTON).pack(side="left", fill="x", expand=True)
        if IS_WINDOWS:
            w.update_idletasks()
            try:
                winapi.make_no_activate(int(w.wm_frame(), 16))
            except Exception:
                pass

    def _note_hover(self, inside: bool) -> None:
        base = FG if self._has_note else ACCENT
        self.note.configure(fg=(ACCENT_HOVER if not self._has_note else "#ffffff") if inside else base)

    def set_context(self, detected: str, note: str, place: str) -> None:
        """Show what will be used. Called again after the note changes."""
        # "prompt-master · Claude Code “Chat title”": the first part is the place, the rest the details.
        head, _, rest = (detected or "").partition(" · ")
        if head:
            self.place.configure(text=head)
            self.detail.configure(text=rest)
            if rest:
                self.detail.pack(fill="x", pady=(_px(self.win, 1), 0))
            else:
                self.detail.pack_forget()
            self.divider.pack(fill="x", pady=(_px(self.win, 10), _px(self.win, 8)))
            self.context_box.pack(fill="x", side="top")
        else:
            self.context_box.pack_forget()
        self._has_note = bool(note)
        if note:
            self.note.configure(text=f"✎  {short(note)}", fg=FG)
            self.clear.pack(side="right")
        else:
            self.note.configure(text=f"+  Add context for {place}", fg=ACCENT)
            self.clear.pack_forget()
        self.card.fit()
        if self._anchor and self.win.winfo_viewable():
            self.show_beside(*self._anchor)

    def bounds(self) -> tuple[int, int, int, int]:
        return self.win.winfo_x(), self.win.winfo_y(), self.win.winfo_width(), self.win.winfo_height()

    def show_beside(self, x: int, y: int, anchor_w: int, anchor_h: int) -> None:
        """Open beside the floating dot."""
        self._anchor = (x, y, anchor_w, anchor_h)
        self.card.fit()
        self.win.geometry("")  # fit the content again
        self.win.update_idletasks()
        width, height = self._size()
        left, top, right, bottom = _work_area(self.win, x, y)
        px = x - width - 10
        if px < left + 8:
            px = x + anchor_w + 10
        py = y + (anchor_h - height) // 2
        px = min(max(px, left + 8), max(left + 8, right - width - 8))
        py = min(max(py, top + 8), max(top + 8, bottom - height - 8))
        self._pos = (px, py)
        if self.win.winfo_viewable():
            self.win.geometry(f"{width}x{height}+{px + round(self.motion['slide'])}+{py}")
            self._present()
            return
        # Opening: glide out smoothly beside the dot.
        towards_dot = 10 if px < x else -10
        m = self.motion
        m.set("slide", towards_dot)
        self.win.geometry(f"{width}x{height}+{px + towards_dot}+{py}")
        self._present()
        m.to("slide", 0, 0.35, "power3.out")

    def _apply_motion(self) -> None:
        if not self.win.winfo_viewable():
            return
        px, py = self._pos
        self.win.geometry(f"+{px + round(self.motion['slide'])}+{py}")

    def _size(self) -> tuple[int, int]:
        scale = max(1.0, self.win.winfo_fpixels("1i") / 96)
        width = max(self.win.winfo_reqwidth(), int(240 * scale))
        height = max(self.win.winfo_reqheight(), int(84 * scale))
        return width, height

    def _present(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        if IS_WINDOWS:
            self.win.update_idletasks()
            try:
                winapi.pin_overlay(int(self.win.wm_frame(), 16))
            except Exception:
                pass

    def hide(self) -> None:
        self._anchor = None
        self.motion.kill()
        self.win.withdraw()

    def _yes(self) -> None:
        self.on_yes()

    def _no(self) -> None:
        self.on_no()


class NoteEditor:
    """
    A small window for writing "My context" for one place (a project, a website, an app).
    Unlike the popup, it takes keyboard focus so the user can type.
    Enter saves, Shift+Enter adds a line, Esc cancels.
    """

    def __init__(self, root: tk.Tk, place: str, note: str, near: tuple[int, int, int, int], *,
                 on_save: Callable[[str], None], on_close: Callable[[], None]):
        self.on_save, self.on_close = on_save, on_close
        self._closed = False
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("dotix — My context")
        w.attributes("-topmost", True)
        _frameless(w)
        pad = _px(w, 18)
        width = _px(w, 380)

        header = _header(w, "My context", self.close)
        header.pack(fill="x", padx=(pad, _px(w, 10)), pady=(_px(w, 12), 0))
        tk.Label(w, text=f"Added to every prompt you write in {place}.", bg=BG, fg=FG_DIM, font=FONT_SMALL,
                 anchor="w", justify="left", wraplength=width - 2 * pad).pack(fill="x", padx=pad)

        self.field = _Card(w, width - 2 * pad, radius=10, pad=(12, 10))
        self.field.pack(padx=pad, pady=(_px(w, 12), 0))
        self.text = tk.Text(self.field.body, width=1, height=4, wrap="word", bg=BG_2, fg=FG,
                            insertbackground=ACCENT_PURPLE,
                            selectbackground="#6d28d9", selectforeground="#ffffff", relief="flat", bd=0,
                            highlightthickness=0, font=FONT_TEXT, padx=0, pady=0, insertwidth=2)
        self.text.pack(fill="both")
        self.text.insert("1.0", note)
        self.placeholder = tk.Label(self.field.body, text="e.g. Fixing the login bug. Explain in simple English.",
                                    bg=BG_2, fg=FG_FAINT, font=FONT_TEXT, anchor="w", justify="left",
                                    wraplength=self.field.inner_width - _px(w, 8))
        self.placeholder.bind("<Button-1>", lambda e: self.text.focus_set())
        self._sync_placeholder()
        self.field.fit()

        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=pad, pady=(_px(w, 14), pad))
        tk.Label(bottom, text="Enter  save   ·   Shift+Enter  new line", bg=BG, fg=FG_FAINT,
                 font=FONT_SMALL).pack(side="left")
        _Button(bottom, "Save", self.save, primary=True, width=80).pack(side="right")
        _Button(bottom, "Cancel", self.close, width=80).pack(side="right", padx=(0, _px(w, 8)))

        self.text.bind("<Return>", lambda e: (self.save(), "break")[1])
        self.text.bind("<Shift-Return>", lambda e: (self.text.insert("insert", "\n"), "break")[1])
        self.text.bind("<KeyRelease>", lambda e: self._sync_placeholder())
        self.text.bind("<FocusIn>", lambda e: self.field.set_outline(BORDER_FOCUS))
        self.text.bind("<FocusOut>", lambda e: self.field.set_outline(BORDER))
        w.bind("<Escape>", lambda e: self.close())
        # No title bar: drag the header to move the window.
        for part in (header, *header.winfo_children()):
            if not isinstance(part, _Button):
                part.bind("<ButtonPress-1>", self._drag_start)
                part.bind("<B1-Motion>", self._drag)

        w.update_idletasks()
        width, height = w.winfo_reqwidth(), w.winfo_reqheight()
        x, y, near_w, near_h = near
        left, top, right, bottom_edge = _work_area(w, x, y)
        px = min(max(x + near_w - width, left + 8), right - width - 8)
        py = y - height - 10 if y - height - 10 > top else y + near_h + 10
        self._pos = (px, min(py, bottom_edge - height - 8))
        # Open: rise smoothly into position.
        self.motion = Motion(w, self._apply_motion, rise=8.0)
        self._apply_motion(force=True)
        w.deiconify()
        w.lift()
        if IS_WINDOWS:
            try:
                winapi.focus_window(int(w.wm_frame(), 16), timeout=0.3)
            except Exception:
                pass
        w.focus_force()
        self.text.focus_set()
        self.text.mark_set("insert", "end-1c")
        # A window without a title bar gets activated a moment later: put the caret in the box then.
        w.after(60, lambda: self._closed or self.text.focus_force())
        self.motion.to("rise", 0, 0.35, "power3.out")

    def _apply_motion(self, force: bool = False) -> None:
        if self._closed or not (force or self.win.winfo_exists()):
            return
        px, py = self._pos
        self.win.geometry(f"+{px}+{py + round(self.motion['rise'])}")

    def _sync_placeholder(self) -> None:
        if self.text.get("1.0", "end-1c"):
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=_px(self.win, 4), y=0)  # just right of the caret

    def _drag_start(self, event) -> None:
        self._grab = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())
        self.motion.kill("rise")
        self.motion.set("rise", 0)

    def _drag(self, event) -> None:
        gx, gy = self._grab
        self._pos = (event.x_root - gx, event.y_root - gy)
        self.win.geometry(f"+{self._pos[0]}+{self._pos[1]}")

    def current(self) -> str:
        return self.text.get("1.0", "end-1c").strip()

    def save(self) -> None:
        text = self.current()
        self.close()
        self.on_save(text)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close()


DRAG_THRESHOLD = 4
_DOT_MARGIN = 18


def pointer_action(dx: int, dy: int) -> str:
    """A short press is a click. A longer move is a drag."""
    if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
        return "click"
    return "drag"


def dot_click_target(x: int, undo: bool, size: int = 32, gap: int = 8, pad: int = 4) -> str | None:
    """The dot regenerates. The Undo label beside it, when shown, undoes once."""
    if not undo:
        if 0 <= x < size:
            return "generate"
        return None
    if x < pad + size:
        return "generate"
    if x >= pad + size + gap:
        return "undo"
    return None


def clamp_point(x: int, y: int, width: int, height: int, area: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = area
    return (
        min(max(x, left), max(left, right - width)),
        min(max(y, top), max(top, bottom - height)),
    )


def default_dot_origin(area: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int]:
    left, top, right, bottom = area
    x = right - width - _DOT_MARGIN
    y = top + max(0, bottom - top - height) // 2
    return clamp_point(x, y, width, height, area)


def load_dot_position(path: Path) -> tuple[int, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data["x"]), int(data["y"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def save_dot_position(path: Path, x: int, y: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")


class FloatDot:
    """A small always-on-top glass dot. Drag moves it. A click calls on_click. It never reads text.

    After a prompt is written, an Undo pill grows out from behind the dot.
    One click on its label calls on_undo.

    Everything moves with GSAP-style tweens (see motion.py): the dot pops in when shown,
    lifts and glows on hover, squashes on press and springs back with a ripple on click,
    and spins while a prompt is being written (set_busy).
    """

    SIZE = 32
    GAP = 8
    PAD = 4
    BOX_H = 40
    LABEL_W = 78
    MARGIN = 12  # clear space around the dot for its shadow, glow and click ripple
    _CHROMA = "#010101"

    def __init__(self, root: tk.Tk, *, on_click: Callable[[], None], on_place: Callable[[int, int], None],
                 on_undo: Callable[[], None] | None = None):
        self.on_click = on_click
        self.on_place = on_place
        self.on_undo = on_undo or (lambda: None)
        self._press: tuple[int, int] | None = None
        self._origin: tuple[int, int] = (0, 0)
        self._visible = False
        self._undo = False    # the Undo label can be clicked
        self._boxed = False   # the window is laid out for the Undo pill (stays on while it closes)
        self._busy = False
        self._dragging = False
        self._hovered: str | None = None
        self._pin_job: str | None = None
        self._photo = None
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self._CHROMA)
        canvas = tk.Canvas(w, bg=self._CHROMA, highlightthickness=0, bd=0, cursor="arrow")
        canvas.pack()
        self._canvas = canvas
        self._image = canvas.create_image(0, 0, anchor="nw")
        self._font_px = max(11, round(w.winfo_fpixels("10p")))
        # Per-pixel transparency on Windows: real see-through glass and soft shadows.
        # Elsewhere (or if it fails), the frame is flattened onto a colour key.
        self._layered = IS_WINDOWS
        if not self._layered:
            self._use_color_key()
        self.motion = Motion(w, self._draw, alpha=1.0, scale=1.0, hover=0.0, pill=0.0, undo_hover=0.0,
                             ripple=1.0, busy=0.0, spin=0.0)
        canvas.bind("<ButtonPress-1>", self._down)
        canvas.bind("<B1-Motion>", self._motion)
        canvas.bind("<ButtonRelease-1>", self._up)
        canvas.bind("<Motion>", self._hover)
        canvas.bind("<Leave>", lambda e: self._dragging or self._press or self._set_hovered(None))

    # ----------------------------------------------------------------- layout
    def _full_span(self) -> int:
        return self.PAD + self.SIZE + self.GAP + self.LABEL_W + self.PAD

    def _span(self) -> int:
        return self._full_span() if self._boxed else self.SIZE

    def _height(self) -> int:
        return self.BOX_H if self._boxed else self.SIZE

    def _window_size(self) -> tuple[int, int]:
        return self._span() + 2 * self.MARGIN, self._height() + 2 * self.MARGIN

    def _dot_offset(self) -> tuple[int, int]:
        """Where the dot sits inside the visible box (the pill, or just the dot)."""
        if self._boxed:
            return self.PAD, (self.BOX_H - self.SIZE) // 2
        return 0, 0

    def _dot_origin(self) -> tuple[int, int]:
        """Top-left of the round dot on screen, even while it sits inside the Undo pill."""
        ox, oy = self._dot_offset()
        return self.win.winfo_x() + self.MARGIN + ox, self.win.winfo_y() + self.MARGIN + oy

    def _place(self, x: int, y: int) -> None:
        """Move so the dot's top-left is at x, y (kept on screen)."""
        ox, oy = self._dot_offset()
        x, y = x - ox, y - oy
        x, y = clamp_point(x, y, self._span(), self._height(), _work_area(self.win, x, y))
        width, height = self._window_size()
        self._canvas.configure(width=width, height=height)
        self.win.geometry(f"{width}x{height}+{x - self.MARGIN}+{y - self.MARGIN}")
        if self._visible:
            self.win.update_idletasks()
            self._draw()

    def _hit(self, x: int, y: int) -> str | None:
        """'generate', 'undo' or None for a point in window coordinates."""
        lx, ly = x - self.MARGIN, y - self.MARGIN
        if not (0 <= lx < self._span() and 0 <= ly < self._height()):
            return None
        target = dot_click_target(lx, self._boxed, self.SIZE, self.GAP, self.PAD)
        if target == "undo" and not self._undo:
            return None  # the pill is closing
        return target

    # ---------------------------------------------------------------- drawing
    def _hwnd(self) -> int:
        return int(self.win.wm_frame(), 16)

    def _use_color_key(self) -> None:
        self._layered = False
        if IS_WINDOWS:
            try:
                winapi.make_layered(self._hwnd(), False)  # Tk can't colour-key a window drawn by us
            except Exception:
                pass
            self.win.attributes("-transparentcolor", self._CHROMA)

    def _draw(self) -> None:
        if not self._visible:
            return
        m = self.motion
        width, height = self._window_size()
        ox, oy = self._dot_offset()
        dot_x, dot_y = self.MARGIN + ox, self.MARGIN + oy
        frame = glass.Frame(
            width, height, dot_x, dot_y, self.SIZE,
            pill=(self.MARGIN, self.MARGIN, self._full_span(), self.BOX_H) if self._boxed else None,
            label_x=dot_x + self.SIZE + self.GAP, font_px=self._font_px,
            scale=m["scale"], hover=m["hover"], open=m["pill"], undo_hover=m["undo_hover"],
            ripple=m["ripple"], busy=m["busy"], spin=m["spin"],
        )
        image = glass.render(frame)
        alpha = max(0.0, min(1.0, m["alpha"]))
        if self._layered:
            try:
                if winapi.update_layered(self._hwnd(), width, height, glass.premultiplied_bgra(image),
                                         round(255 * alpha)):
                    return
            except Exception:
                pass
            self._use_color_key()
        from PIL import ImageTk
        self._photo = ImageTk.PhotoImage(glass.flatten(image, (30, 31, 36), (1, 1, 1)))
        self._canvas.itemconfigure(self._image, image=self._photo)
        self.win.attributes("-alpha", alpha)

    # ------------------------------------------------------------ public API
    def set_undo(self, show: bool) -> None:
        show = bool(show)
        if self._undo == show:
            return
        self._undo = show
        if show:
            if not self._boxed:
                origin = self._dot_origin()
                self._boxed = True
                if self._visible:
                    self._place(*origin)
            if self._visible:
                self.motion.to("pill", 1, 0.55, "power3.out")
            else:
                self.motion.set("pill", 1)
        elif self._visible:
            self.motion.to("pill", 0, 0.3, "power2.inOut", on_complete=self._unbox)
        else:
            self.motion.set("pill", 0)
            self._unbox()

    def _unbox(self) -> None:
        """After the pill has closed: shrink the window back to just the dot."""
        if self._undo or not self._boxed:
            return  # it was opened again meanwhile
        origin = self._dot_origin()
        self._boxed = False
        if self._hovered == "undo":
            self._set_hovered(None)
        if self._visible:
            self._place(*origin)

    def set_busy(self, busy: bool) -> None:
        """While a prompt is being written, the white core turns into a spinner."""
        busy = bool(busy)
        if self._busy == busy:
            return
        self._busy = busy
        m = self.motion
        if busy:
            m.to("busy", 1, 0.3, "power2.out")
            m.from_to("spin", m["spin"] % 360, m["spin"] % 360 + 360, 0.9, "none", repeat=-1)
        else:
            m.to("busy", 0, 0.35, "power2.out", on_complete=lambda: self._busy or m.kill("spin"))

    def show(self, x: int, y: int) -> None:
        # x, y is the dot's top-left. The pill, when shown, wraps that same dot.
        appearing = not self._visible
        self._place(x, y)
        if not appearing:
            return
        self._visible = True
        m = self.motion
        m.set("alpha", 0)
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self._pin()
        self.win.update_idletasks()
        self._draw()  # first frame while still fully clear, so nothing flashes
        m.from_to("alpha", 0, 1, 0.35, "power2.out")
        m.from_to("scale", 0.3, 1, 0.75, "elastic.out")
        m.from_to("ripple", 0, 1, 0.7, "power2.out", delay=0.12)
        self._schedule_pin()

    def hide(self) -> None:
        self._visible = False
        self._press = None
        self._dragging = False
        self.motion.kill()
        if self._pin_job is not None:
            self.win.after_cancel(self._pin_job)
            self._pin_job = None
        self.win.withdraw()

    def _schedule_pin(self) -> None:
        if not self._visible:
            return
        self._pin_job = self.win.after(400, self._keep_visible)

    def _keep_visible(self) -> None:
        self._pin_job = None
        if not self._visible:
            return
        if not self.win.winfo_viewable():
            self.win.deiconify()
            self._draw()
        self.win.attributes("-topmost", True)
        self._pin()
        if self._layered:
            try:
                if not winapi.is_layered(self._hwnd()):
                    self._draw()  # Windows or Tk rebuilt the window: send the picture again
            except Exception:
                pass
        self._schedule_pin()

    def _pin(self) -> None:
        if not IS_WINDOWS:
            return
        self.win.update_idletasks()
        try:
            winapi.pin_overlay(self._hwnd())
        except Exception:
            pass

    def bounds(self) -> tuple[int, int, int, int]:
        """The visible dot (or pill) on screen, without the clear margin around it."""
        return self.win.winfo_x() + self.MARGIN, self.win.winfo_y() + self.MARGIN, self._span(), self._height()

    # ------------------------------------------------------------ pointer
    def _set_hovered(self, target: str | None) -> None:
        if target == self._hovered:
            return
        self._hovered = target
        m = self.motion
        on_dot = target == "generate"
        m.to("hover", 1.0 if on_dot else 0.0, 0.25, "power2.out")
        m.to("scale", 1.1 if on_dot else 1.0, 0.45 if on_dot else 0.3, "back.out" if on_dot else "power2.out")
        m.to("undo_hover", 1.0 if target == "undo" else 0.0, 0.2, "power2.out")
        self._canvas.configure(cursor="hand2" if target else "arrow")

    def _hover(self, event) -> None:
        if self._press is None:
            self._set_hovered(self._hit(event.x, event.y))

    def _down(self, event) -> None:
        self._press = (event.x_root, event.y_root)
        self._origin = (self.win.winfo_x(), self.win.winfo_y())
        if self._hit(event.x, event.y) == "generate":
            self.motion.to("scale", 0.86, 0.12, "power2.out")  # squash under the finger

    def _motion(self, event) -> None:
        if self._press is None:
            return
        dx = event.x_root - self._press[0]
        dy = event.y_root - self._press[1]
        if not self._dragging:
            if pointer_action(dx, dy) != "drag":
                return
            self._dragging = True
            self.motion.to("scale", 1.14, 0.35, "back.out")  # picked up
            self.motion.to("hover", 1.0, 0.25, "power2.out")
            self._canvas.configure(cursor="fleur")
        nx = self._origin[0] + dx + self.MARGIN
        ny = self._origin[1] + dy + self.MARGIN
        x, y = clamp_point(nx, ny, self._span(), self._height(), _work_area(self.win, nx, ny))
        self.win.geometry(f"+{x - self.MARGIN}+{y - self.MARGIN}")

    def _up(self, event) -> None:
        if self._press is None:
            return
        dx = event.x_root - self._press[0]
        dy = event.y_root - self._press[1]
        self._press = None
        dragged, self._dragging = self._dragging, False
        target = self._hit(event.x, event.y)
        self._hovered = None  # re-apply hover from scratch
        self._set_hovered(target)
        if not dragged and pointer_action(dx, dy) == "click":
            if target == "generate":
                m = self.motion
                m.from_to("scale", 0.86, 1.1, 0.7, "elastic.out")  # spring back
                m.from_to("ripple", 0, 1, 0.6, "power2.out")
                self.on_click()
            elif target == "undo":
                self.motion.from_to("undo_hover", 0.2, 1.0, 0.35, "power2.out")
                self.on_undo()
            return
        self.on_place(*self._dot_origin())


class KeySetupWindow:
    """
    First-run window: asks for an API key, detects the service, tests it, then saves it.
    Shown automatically when no key is set, and from the tray menu ("API keys...").
    """

    def __init__(self, root: tk.Tk, on_submit: Callable[[dict[str, str]], None],
                 on_close: Callable[[], None]):
        from .config import detect_provider

        self.root = root
        self.on_submit, self.on_close = on_submit, on_close
        self._detect = detect_provider
        w = self.win = tk.Toplevel(root)
        w.title("dotix — setup")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)

        tk.Label(w, text="Welcome to dotix", bg=BG, fg=FG, font=(FONT_FAMILY, 13, "bold")
                 ).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(w, text="Type a prompt in any box, click the dot, then Yes. It is rewritten in place.\n"
                         "Paste an API key. The app detects the service from the key.",
                 bg=BG, fg=FG_DIM, font=FONT, justify="left", wraplength=460).pack(anchor="w", padx=20)

        tk.Label(w, text="Enter API key", bg=BG, fg=FG, font=FONT).pack(anchor="w", padx=20, pady=(14, 0))
        self.entry = tk.Entry(w, show="•", bg=BG_2, fg=FG, insertbackground=ACCENT_PURPLE,
                              selectbackground="#6d28d9", selectforeground="#ffffff", relief="flat",
                              font=FONT_TEXT, width=52, highlightthickness=1, highlightbackground=BORDER,
                              highlightcolor=BORDER_FOCUS)
        self.entry.pack(fill="x", padx=20, pady=(4, 0), ipady=5)
        self.entry.bind("<Return>", lambda e: self._submit())

        self.status = tk.Label(w, text="The key is saved only on this PC, in config.toml.", bg=BG, fg=FG_DIM,
                               font=FONT_SMALL, justify="left", wraplength=460, anchor="w")
        self.status.pack(fill="x", padx=20, pady=(12, 0))
        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=20, pady=16)
        self.btn_save = _Button(bottom, "Test & save", self._submit, primary=True, width=120)
        self.btn_save.pack(side="right")
        _Button(bottom, "Later", self.close, width=80).pack(side="right", padx=8)
        w.bind("<Escape>", lambda e: self.close())

        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 3}")
        w.focus_force()
        self.entry.focus_set()

    def _submit(self) -> None:
        key = self.entry.get().strip()
        if not key:
            self.show_status("Enter an API key.", error=True)
            return
        spec = self._detect(key)
        if spec is None:
            self.show_status("This key isn't recognized. Paste a Groq, Gemini, OpenAI, OpenRouter, or Anthropic key.",
                             error=True)
            return
        self.btn_save.set_enabled(False)
        self.show_status(f"Detected {spec['name']}. Testing your key…")
        self.on_submit(spec)

    def show_status(self, message: str, error: bool = False) -> None:
        self.status.configure(text=message, fg=ERROR if error else FG_DIM)
        if error:
            self.btn_save.set_enabled(True)

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close()


class Toast:
    """A small message near the mouse that disappears by itself and never steals focus."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.win: tk.Toplevel | None = None
        self._after = None

    def show(self, message: str, *, kind: str = "info", duration_ms: int = 2600) -> None:
        self.hide()
        w = self.win = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        color = {"info": ACCENT_PURPLE, "error": ERROR, "ok": "#34d399"}.get(kind, ACCENT_PURPLE)
        frame = tk.Frame(w, bg=BG, highlightthickness=1, highlightbackground=color)
        frame.pack()
        tk.Label(frame, text=message, bg=BG, fg=FG, font=FONT, padx=14, pady=8,
                 justify="left", wraplength=420).pack()
        w.update_idletasks()
        if IS_WINDOWS:
            try:
                hwnd = int(w.wm_frame(), 16)
                winapi.make_no_activate(hwnd)
                winapi.round_corners(hwnd, _rgb(color))
                winapi.enable_acrylic(hwnd)
            except Exception:
                pass
        _place_near_cursor(w, w.winfo_reqwidth(), w.winfo_reqheight())
        w.deiconify()
        if duration_ms > 0:
            self._after = self.root.after(duration_ms, self.hide)

    def hide(self) -> None:
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
