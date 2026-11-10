"""
Read and replace the text in the input box the user is typing in.

Every app supports the clipboard, so this does what a person would do:
    read:    Ctrl+A, Ctrl+C
    replace: Ctrl+A, Ctrl+V

The clipboard is only borrowed. Whatever was on it before is put back right
after each copy or paste, so neither the draft nor the new prompt is left
behind for Ctrl+V.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

EMPTY_MESSAGE = "Nothing to regenerate.\nClick in your input box and type a prompt first."
WINDOW_LOST_MESSAGE = "Couldn't get back to the window with your text.\nClick in the box and try again."


class BoxError(RuntimeError):
    pass


def same_text(a: str, b: str) -> bool:
    """Boxes change line endings and spacing on paste; the words must match."""
    words = (b or "").split()
    return bool(words) and (a or "").split() == words


def original_for_undo(last: tuple[int, str, str] | None, hwnd: int, draft: str) -> str:
    """The text Undo should bring back.

    `last` is (window, original, generated) from the previous rewrite. When the
    user regenerates the generated prompt again, Undo still returns what the
    user typed, not the earlier generated version.
    """
    if last and last[0] == hwnd and same_text(draft, last[2]):
        return last[1]
    return draft


def prompt_was_used(text: str, generated: str) -> bool:
    """True once the box no longer holds the generated prompt: it was sent (the box is empty) or
    replaced. Small edits to the prompt don't count, so Undo stays for them."""
    words = set((text or "").split())
    if not words:
        return True
    prompt = set((generated or "").split())
    return len(words & prompt) < len(prompt) / 2


def read_box(hwnd: int) -> str:
    """Copy the whole text of the focused box, then give the clipboard back."""
    from . import winapi

    _bring_back(hwnd)
    saved = _clipboard()
    text = ""
    try:
        winapi.set_clipboard_text(None)
        seq = winapi.clipboard_sequence()
        winapi.ctrl("A")
        time.sleep(0.05)
        winapi.ctrl("C")
        if winapi.wait_clipboard_change(seq, 0.8):
            text = winapi.get_clipboard_text() or ""
    finally:
        _give_back(saved)
    if not text.strip():
        raise BoxError(EMPTY_MESSAGE)
    log.info("Read %s characters from the box", len(text))
    return text


def write_box(hwnd: int, text: str, paste_delay_ms: int = 600) -> None:
    """Replace the whole text of the focused box, then give the clipboard back."""
    from . import winapi

    _bring_back(hwnd)
    saved = _clipboard()
    try:
        winapi.set_clipboard_text(text, hide_from_history=True)
        winapi.ctrl("A")
        time.sleep(0.05)
        winapi.ctrl("V")
        # Apps read the clipboard a moment after Ctrl+V.
        time.sleep(max(paste_delay_ms, 200) / 1000)
    finally:
        _give_back(saved)
    log.info("Wrote %s characters into the box", len(text))


def _bring_back(hwnd: int) -> None:
    """Bring the box's window to the front. Never copy from or paste over a code file."""
    from . import focus, winapi

    if not winapi.focus_window(hwnd):
        raise BoxError(WINDOW_LOST_MESSAGE)
    winapi.wait_for_modifiers_released()
    time.sleep(0.05)
    if focus.cursor_in_code(hwnd):
        raise BoxError(focus.CODE_MESSAGE)


def _clipboard() -> str | None:
    from . import winapi

    try:
        return winapi.get_clipboard_text()
    except winapi.ClipboardBusy:
        return None


def _give_back(saved: str | None) -> None:
    """Put the user's own clipboard back. With nothing saved, leave it empty."""
    from . import winapi

    for _ in range(5):
        try:
            winapi.set_clipboard_text(saved)
            return
        except winapi.ClipboardBusy:
            time.sleep(0.1)
    log.warning("Couldn't restore the clipboard")
