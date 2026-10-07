"""
The little windows you see: the preview popup and the small "toast" messages.

Built with tkinter, which comes with Python - nothing to install.
All functions here must be called from the main (tkinter) thread.
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk
from typing import Callable

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    from . import winapi

# Colours (dark theme)
BG = "#1e1f24"
BG_2 = "#2a2c33"
FG = "#e8e8ea"
FG_DIM = "#9a9ca5"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#6a59f5"
ERROR = "#ff6b6b"
FONT = ("Segoe UI", 10)
FONT_TEXT = ("Segoe UI", 11)
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 11)


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


class _Button(tk.Label):
    """Flat button that looks the same on every Windows theme."""

    def __init__(self, master, text: str, command: Callable[[], None], primary: bool = False):
        self._bg = ACCENT if primary else BG_2
        self._hover = ACCENT_HOVER if primary else "#353843"
        super().__init__(master, text=text, bg=self._bg, fg="white" if primary else FG,
                         font=FONT, padx=14, pady=6, cursor="hand2")
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", lambda e: self._enabled and self._command())
        self.bind("<Enter>", lambda e: self._enabled and self.configure(bg=self._hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self._bg))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(fg=(("white" if self._bg == ACCENT else FG) if enabled else FG_DIM))


class PreviewPopup:
    """
    Shows the improved prompt so you can check / edit it.
      Enter        -> use it (paste into your app)
      Shift+Enter  -> new line
      Ctrl+R       -> try again
      Esc          -> cancel
    """

    WIDTH, HEIGHT = 620, 400

    def __init__(self, root: tk.Tk, styles: dict[str, dict], *,
                 on_accept: Callable[[str], None], on_cancel: Callable[[], None],
                 on_regenerate: Callable[[str], None], on_copy: Callable[[str], None]):
        self.root = root
        self.styles = styles
        self.on_accept, self.on_cancel = on_accept, on_cancel
        self.on_regenerate, self.on_copy = on_regenerate, on_copy
        self.loading = False
        self._dots = 0
        self._build()

    # ----- building the window -------------------------------------------
    def _build(self) -> None:
        w = self.win = tk.Toplevel(self.root)
        w.withdraw()
        w.title("Master Prompt")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        if IS_WINDOWS:
            w.attributes("-toolwindow", True)
        w.protocol("WM_DELETE_WINDOW", self._cancel)

        top = tk.Frame(w, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="✨ Master Prompt", bg=BG, fg=FG, font=FONT_TITLE).pack(side="left")

        style_names = [v.get("label", k) for k, v in self.styles.items()]
        self._label_to_key = {v.get("label", k): k for k, v in self.styles.items()}
        self.style_var = tk.StringVar(value=style_names[0])
        s = ttk.Style(w)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("MP.TCombobox", fieldbackground=BG_2, background=BG_2, foreground=FG,
                    arrowcolor=FG, bordercolor=BG_2, lightcolor=BG_2, darkcolor=BG_2)
        s.map("MP.TCombobox", fieldbackground=[("readonly", BG_2)], foreground=[("readonly", FG)])
        self.style_box = ttk.Combobox(top, textvariable=self.style_var, values=style_names,
                                      state="readonly", width=11, style="MP.TCombobox", font=FONT)
        self.style_box.pack(side="left", padx=(12, 0))
        self.style_box.bind("<<ComboboxSelected>>", lambda e: self._regenerate())

        self.status = tk.Label(top, text="", bg=BG, fg=FG_DIM, font=FONT_SMALL)
        self.status.pack(side="right")

        body = tk.Frame(w, bg=BG_2)
        body.pack(fill="both", expand=True, padx=14)
        self.text = tk.Text(body, wrap="word", bg=BG_2, fg=FG, insertbackground=FG,
                            selectbackground=ACCENT, relief="flat", font=FONT_TEXT,
                            padx=10, pady=8, undo=True, height=10)
        scroll = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("dim", foreground=FG_DIM)
        self.text.tag_configure("error", foreground=ERROR)

        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=14, pady=10)
        self.btn_use = _Button(bottom, "Use prompt", self._accept, primary=True)
        self.btn_use.pack(side="right")
        self.btn_copy = _Button(bottom, "Copy", self._copy)
        self.btn_copy.pack(side="right", padx=6)
        self.btn_retry = _Button(bottom, "Retry", self._regenerate)
        self.btn_retry.pack(side="right")
        self.btn_orig = _Button(bottom, "Original", self._toggle_original)
        self.btn_orig.pack(side="right", padx=6)
        # packed last so the buttons always keep their space on narrow screens
        tk.Label(bottom, text="Enter = use · Shift+Enter = new line · Esc = cancel",
                 bg=BG, fg=FG_DIM, font=FONT_SMALL, anchor="w").pack(side="left", fill="x", expand=True)

        w.bind("<Escape>", lambda e: self._cancel())
        w.bind("<Control-r>", lambda e: (self._regenerate(), "break")[1])
        w.bind("<Control-R>", lambda e: (self._regenerate(), "break")[1])
        self.text.bind("<Return>", lambda e: (self._accept(), "break")[1])
        self.text.bind("<Control-Return>", lambda e: (self._accept(), "break")[1])
        self.text.bind("<Shift-Return>", lambda e: (self.text.insert("insert", "\n"), "break")[1])

        self._original = ""
        self._result = ""
        self._showing_original = False

    # ----- public API ------------------------------------------------------
    def show_loading(self, original: str, style_key: str, reposition: bool = True) -> None:
        self._original = original
        self._showing_original = False
        self.btn_orig.configure(text="Original")
        label = self.styles.get(style_key, {}).get("label", style_key)
        self.style_var.set(label)
        self._set_text(original, "dim")
        self.text.configure(state="disabled")
        self._set_buttons(False)
        self.loading = True
        self._animate()
        if reposition or not self.win.winfo_viewable():
            scale = max(1.0, self.root.winfo_fpixels("1i") / 96)  # bigger on 125%/150% screens
            _place_near_cursor(self.win, int(self.WIDTH * scale), int(self.HEIGHT * scale))
        self._present()

    def show_result(self, text: str, info: str) -> None:
        self.loading = False
        self._result = text
        self.text.configure(state="normal")
        self._set_text(text)
        self.text.edit_reset()
        self.text.mark_set("insert", "end-1c")
        self.status.configure(text=info, fg=FG_DIM)
        self._set_buttons(True)
        self._present()

    def show_error(self, message: str) -> None:
        self.loading = False
        self.text.configure(state="normal")
        self._set_text(message + "\n\nPress Ctrl+R to retry or Esc to close.", "error")
        self.text.configure(state="disabled")
        self.status.configure(text="Failed", fg=ERROR)
        self._set_buttons(False)
        self.btn_retry.set_enabled(True)
        self._present()

    def hide(self) -> None:
        self.loading = False
        self.win.withdraw()

    @property
    def visible(self) -> bool:
        return bool(self.win.winfo_viewable())

    @property
    def current_style(self) -> str:
        return self._label_to_key.get(self.style_var.get(), "enhance")

    # ----- internals -------------------------------------------------------
    def _present(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.update_idletasks()
        if IS_WINDOWS:
            try:
                winapi.focus_window(int(self.win.wm_frame(), 16), timeout=0.3)
            except Exception:
                pass
        self.win.focus_force()
        self.text.focus_set()

    def _set_text(self, text: str, tag: str | None = None) -> None:
        prev = self.text.cget("state")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text, (tag,) if tag else ())
        self.text.configure(state=prev)

    def _set_buttons(self, enabled: bool) -> None:
        for b in (self.btn_use, self.btn_copy, self.btn_retry, self.btn_orig):
            b.set_enabled(enabled)

    def _animate(self) -> None:
        if not self.loading:
            return
        self._dots = (self._dots + 1) % 4
        self.status.configure(text="Enhancing" + "." * self._dots + " " * (3 - self._dots), fg=ACCENT)
        self.win.after(350, self._animate)

    def _current_text(self) -> str:
        return self.text.get("1.0", "end-1c").strip()

    def _accept(self) -> None:
        if self.loading or self._showing_original:
            if self._showing_original:
                self._toggle_original()
            return
        text = self._current_text()
        if text:
            self.on_accept(text)

    def _copy(self) -> None:
        if not self.loading and not self._showing_original:
            self.on_copy(self._current_text())

    def _cancel(self) -> None:
        self.on_cancel()

    def _regenerate(self) -> None:
        if not self.loading:
            self.on_regenerate(self.current_style)

    def _toggle_original(self) -> None:
        if self.loading:
            return
        if not self._showing_original:
            self._result = self._current_text()  # keep your edits
            self.text.configure(state="normal")
            self._set_text(self._original, "dim")
            self.text.configure(state="disabled")
            self.btn_orig.configure(text="Improved")
            self._showing_original = True
        else:
            self.text.configure(state="normal")
            self._set_text(self._result)
            self.btn_orig.configure(text="Original")
            self._showing_original = False


class ConfirmPopup:
    """
    Asks "Generate the prompt?" near the cursor.
    Yes calls on_yes. No and Escape call on_no.
    This window never reads the clipboard, the focused control, or any file.
    """

    def __init__(self, root: tk.Tk, *, on_yes: Callable[[], None], on_no: Callable[[], None]):
        self.on_yes = on_yes
        self.on_no = on_no
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.title("Master Prompt")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        if IS_WINDOWS:
            w.attributes("-toolwindow", True)
        w.protocol("WM_DELETE_WINDOW", self._no)

        tk.Label(w, text="Generate the prompt?", bg=BG, fg=FG, font=FONT_TITLE).pack(
            anchor="w", padx=16, pady=(14, 10))
        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        _Button(bottom, "Yes", self._yes, primary=True).pack(side="right")
        _Button(bottom, "No", self._no).pack(side="right", padx=(0, 8))
        w.bind("<Escape>", lambda e: self._no())

    def show(self) -> None:
        self.win.update_idletasks()
        width, height = self._size()
        _place_near_cursor(self.win, width, height)
        self._present()

    def show_beside(self, x: int, y: int, anchor_w: int, anchor_h: int) -> None:
        """Open beside the floating dot. This window does not read any text."""
        self.win.update_idletasks()
        width, height = self._size()
        left, top, right, bottom = _work_area(self.win, x, y)
        px = x - width - 10
        if px < left + 8:
            px = x + anchor_w + 10
        py = y + (anchor_h - height) // 2
        px = min(max(px, left + 8), max(left + 8, right - width - 8))
        py = min(max(py, top + 8), max(top + 8, bottom - height - 8))
        self.win.geometry(f"{width}x{height}+{px}+{py}")
        self._present()

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
        self.win.withdraw()

    def _yes(self) -> None:
        self.on_yes()

    def _no(self) -> None:
        self.on_no()


DRAG_THRESHOLD = 4
_DOT_MARGIN = 18


def pointer_action(dx: int, dy: int) -> str:
    """A short press is a click. A longer move is a drag."""
    if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
        return "click"
    return "drag"


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
    """A small always-on-top dot. Drag moves it. A click calls on_click. It never reads text."""

    SIZE = 32
    _CHROMA = "#010101"

    def __init__(self, root: tk.Tk, *, on_click: Callable[[], None], on_place: Callable[[int, int], None]):
        self.on_click = on_click
        self.on_place = on_place
        self._press: tuple[int, int] | None = None
        self._origin: tuple[int, int] = (0, 0)
        self._visible = False
        self._pin_job: str | None = None
        w = self.win = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self._CHROMA)
        if IS_WINDOWS:
            w.attributes("-transparentcolor", self._CHROMA)
        canvas = tk.Canvas(w, width=self.SIZE, height=self.SIZE, bg=self._CHROMA,
                           highlightthickness=0, bd=0, cursor="hand2")
        canvas.pack()
        canvas.create_oval(2, 2, self.SIZE - 2, self.SIZE - 2, fill=ACCENT, outline="")
        canvas.create_oval(11, 11, self.SIZE - 11, self.SIZE - 11, fill="#ffffff", outline="")
        self._canvas = canvas
        for target in (w, canvas):
            target.bind("<ButtonPress-1>", self._down)
            target.bind("<B1-Motion>", self._motion)
            target.bind("<ButtonRelease-1>", self._up)

    def show(self, x: int, y: int) -> None:
        area = _work_area(self.win, x, y)
        x, y = clamp_point(x, y, self.SIZE, self.SIZE, area)
        self.win.geometry(f"{self.SIZE}x{self.SIZE}+{x}+{y}")
        self._visible = True
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self._pin()
        self._schedule_pin()

    def hide(self) -> None:
        self._visible = False
        self._press = None
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
        self.win.attributes("-topmost", True)
        self._pin()
        self._schedule_pin()

    def _pin(self) -> None:
        if not IS_WINDOWS:
            return
        self.win.update_idletasks()
        try:
            winapi.pin_overlay(int(self.win.wm_frame(), 16))
        except Exception:
            pass

    def bounds(self) -> tuple[int, int, int, int]:
        return self.win.winfo_x(), self.win.winfo_y(), self.SIZE, self.SIZE

    def _down(self, event) -> None:
        self._press = (event.x_root, event.y_root)
        self._origin = (self.win.winfo_x(), self.win.winfo_y())

    def _motion(self, event) -> None:
        if self._press is None:
            return
        dx = event.x_root - self._press[0]
        dy = event.y_root - self._press[1]
        if pointer_action(dx, dy) != "drag":
            return
        nx = self._origin[0] + dx
        ny = self._origin[1] + dy
        area = _work_area(self.win, nx, ny)
        x, y = clamp_point(nx, ny, self.SIZE, self.SIZE, area)
        self.win.geometry(f"+{x}+{y}")
        self._canvas.configure(cursor="fleur")

    def _up(self, event) -> None:
        if self._press is None:
            return
        dx = event.x_root - self._press[0]
        dy = event.y_root - self._press[1]
        self._press = None
        self._canvas.configure(cursor="hand2")
        if pointer_action(dx, dy) == "click":
            self.on_click()
            return
        x, y = self.win.winfo_x(), self.win.winfo_y()
        self.on_place(x, y)


class KeySetupWindow:
    """
    First-run window: asks for the API keys, tests them, then saves them.
    Shown automatically when no key is set, and from the tray menu ("API keys...").
    """

    FIELDS = [  # provider name in config.toml, label, where to get a free key
        ("groq", "Groq API key (required)", "https://console.groq.com/keys"),
        ("gemini", "Gemini API key (optional backup)", "https://aistudio.google.com/apikey"),
    ]

    def __init__(self, root: tk.Tk, on_submit: Callable[[dict[str, str]], None],
                 on_close: Callable[[], None]):
        self.root = root
        self.on_submit, self.on_close = on_submit, on_close
        self.entries: dict[str, tk.Entry] = {}
        w = self.win = tk.Toplevel(root)
        w.title("Master Prompt - setup")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)

        tk.Label(w, text="✨ Welcome to Master Prompt", bg=BG, fg=FG, font=("Segoe UI Semibold", 13)
                 ).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(w, text="Click the dot, then Yes. The draft in the focused box becomes a better prompt.\n"
                         "It uses a free AI service. Paste your own free key below (takes 1 minute to get).",
                 bg=BG, fg=FG_DIM, font=FONT, justify="left").pack(anchor="w", padx=20)

        for name, label, url in self.FIELDS:
            row = tk.Frame(w, bg=BG)
            row.pack(fill="x", padx=20, pady=(14, 0))
            tk.Label(row, text=label, bg=BG, fg=FG, font=FONT).pack(side="left")
            link = tk.Label(row, text="Get a free key ↗", bg=BG, fg=ACCENT, font=FONT_SMALL, cursor="hand2")
            link.pack(side="right")
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            entry = tk.Entry(w, show="•", bg=BG_2, fg=FG, insertbackground=FG, relief="flat", font=FONT_TEXT,
                             width=52)
            entry.pack(fill="x", padx=20, pady=(4, 0), ipady=5)
            entry.bind("<Return>", lambda e: self._submit())
            self.entries[name] = entry

        self.status = tk.Label(w, text="Your keys are saved only on this PC.", bg=BG, fg=FG_DIM,
                               font=FONT_SMALL, justify="left", wraplength=460, anchor="w")
        self.status.pack(fill="x", padx=20, pady=(12, 0))
        bottom = tk.Frame(w, bg=BG)
        bottom.pack(fill="x", padx=20, pady=16)
        self.btn_save = _Button(bottom, "Test & save", self._submit, primary=True)
        self.btn_save.pack(side="right")
        _Button(bottom, "Later", self.close).pack(side="right", padx=8)
        w.bind("<Escape>", lambda e: self.close())

        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 3}")
        w.focus_force()
        self.entries["groq"].focus_set()

    def _submit(self) -> None:
        keys = {name: e.get().strip() for name, e in self.entries.items()}
        if not keys["groq"]:
            self.show_status("Paste your Groq key first.", error=True)
            return
        self.btn_save.set_enabled(False)
        self.show_status("Testing your key…")
        self.on_submit(keys)

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
        color = {"info": ACCENT, "error": ERROR, "ok": "#3ecf8e"}.get(kind, ACCENT)
        frame = tk.Frame(w, bg=BG, highlightthickness=1, highlightbackground=color)
        frame.pack()
        tk.Label(frame, text=message, bg=BG, fg=FG, font=FONT, padx=14, pady=8,
                 justify="left", wraplength=420).pack()
        w.update_idletasks()
        if IS_WINDOWS:
            try:
                winapi.make_no_activate(int(w.wm_frame(), 16))
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
