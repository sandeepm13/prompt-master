"""
Grabbing text out of the app you are typing in, and putting the new text back.

There is no single Windows API that reads "the text box in any app"
(Chrome, Electron apps like ChatGPT/Claude desktop, VS Code... all draw their
own text boxes). The one thing EVERY app supports is the clipboard, so we do
exactly what you would do by hand:

    grab:   Ctrl+C   (if nothing was selected -> Ctrl+A, Ctrl+C)
    put:    Ctrl+A (if we selected all before) -> Ctrl+V

and we save + restore whatever was on your clipboard before, so you don't lose it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import winapi

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    pass


@dataclass
class Capture:
    hwnd: int            # the window you were typing in
    process: str         # e.g. "chrome.exe"
    title: str
    text: str
    mode: str            # "selection" | "all" | "clipboard"


def target_info() -> tuple[int, str, str]:
    hwnd = winapi.foreground_window()
    return hwnd, winapi.process_name(hwnd), winapi.window_title(hwnd)


def grab_text(auto_select_all: bool, blocked_apps: list[str], restore_clipboard: bool = True) -> Capture:
    hwnd, proc, title = target_info()
    log.info("Grab from %s (%s)", proc or "?", title[:60])
    if proc.lower() in blocked_apps:
        raise CaptureError(
            f"{proc} is in the blocked list (Ctrl+C would stop a running command there).\n"
            "Tip: copy the text yourself, then use the 'enhance clipboard' shortcut."
        )

    winapi.wait_for_modifiers_released()

    saved = None
    if restore_clipboard:
        try:
            saved = winapi.get_clipboard_text()
        except winapi.ClipboardBusy:
            saved = None

    try:
        # Empty the clipboard first, so we can tell whether Ctrl+C really copied something.
        winapi.set_clipboard_text(None)
        seq = winapi.clipboard_sequence()
        winapi.ctrl("C")
        text = ""
        mode = "selection"
        if winapi.wait_clipboard_change(seq, 0.35):
            text = winapi.get_clipboard_text() or ""

        if not text.strip() and auto_select_all:
            mode = "all"
            winapi.ctrl("A")
            time.sleep(0.05)
            seq = winapi.clipboard_sequence()
            winapi.ctrl("C")
            if winapi.wait_clipboard_change(seq, 0.6):
                text = winapi.get_clipboard_text() or ""
    finally:
        if restore_clipboard:
            try:
                winapi.set_clipboard_text(saved)
            except winapi.ClipboardBusy:
                log.warning("Could not restore clipboard after grab")

    if not text.strip():
        raise CaptureError("No text found. Click inside a text box that has some text, then press the shortcut.")
    return Capture(hwnd=hwnd, process=proc, title=title, text=text, mode=mode)


def grab_clipboard() -> Capture:
    """For apps where Ctrl+C is risky (terminals): enhance what's already on the clipboard."""
    hwnd, proc, title = target_info()
    text = winapi.get_clipboard_text() or ""
    if not text.strip():
        raise CaptureError("The clipboard has no text. Copy your prompt first.")
    return Capture(hwnd=hwnd, process=proc, title=title, text=text, mode="clipboard")


def put_text(cap: Capture, new_text: str, restore_clipboard: bool = True, restore_delay_ms: int = 600) -> str:
    """
    Put new_text back where it came from.
    Returns "pasted" or "copied" (copied = we couldn't get back to the window,
    so the text is left on the clipboard for you to paste with Ctrl+V).
    """
    if cap.mode == "clipboard":
        winapi.set_clipboard_text(new_text)
        return "copied"

    if not winapi.focus_window(cap.hwnd):
        log.warning("Could not re-focus the original window; leaving text on the clipboard")
        winapi.set_clipboard_text(new_text)
        return "copied"

    time.sleep(0.2)  # give the app a moment to put the cursor back in the text box (Win11 Notepad needs >0.08s)
    saved = None
    if restore_clipboard:
        try:
            saved = winapi.get_clipboard_text()
        except winapi.ClipboardBusy:
            saved = None

    winapi.set_clipboard_text(new_text, hide_from_history=restore_clipboard)
    if cap.mode == "all":
        winapi.ctrl("A")
        time.sleep(0.04)
    winapi.ctrl("V")

    if restore_clipboard:
        # Apps read the clipboard a little after Ctrl+V - wait before restoring.
        time.sleep(max(restore_delay_ms, 150) / 1000)
        try:
            winapi.set_clipboard_text(saved)
        except winapi.ClipboardBusy:
            log.warning("Could not restore clipboard after paste")
    return "pasted"
