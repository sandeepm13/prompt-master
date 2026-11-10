"""
The keyboard shortcut (Ctrl+Alt+P by default) that regenerates the prompt.

It is a global shortcut: Windows listens for it in every app, with
RegisterHotKey. No keyboard hook, no admin rights. Pressing it does what
clicking the dot and then Yes does, without the popup.

parse_hotkey() is pure Python so it can be tested on any OS.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}

HOTKEY_ID = 1


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True)
class Hotkey:
    text: str       # as written in config.toml, e.g. "ctrl+alt+p"
    modifiers: int
    vk: int

    @property
    def label(self) -> str:
        """'ctrl+alt+p' -> 'Ctrl+Alt+P', for messages."""
        return "+".join(p.capitalize() for p in self.text.replace(" ", "").lower().split("+") if p)


def parse_hotkey(text: str) -> Hotkey:
    """'ctrl+alt+p' -> Hotkey(modifiers=MOD_CONTROL | MOD_ALT, vk=ord('P'))"""
    parts = [p for p in text.replace(" ", "").lower().split("+") if p]
    if not parts:
        raise HotkeyError("The shortcut is empty.")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            raise HotkeyError(f"Unknown key '{p}' in shortcut '{text}'. Use ctrl, alt, shift or win.")
        mods |= _MODS[p]
    key = parts[-1]
    if key in _MODS:
        raise HotkeyError(f"Shortcut '{text}' has no main key, only ctrl/alt/shift/win.")
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise HotkeyError(f"Unknown key '{key}' in shortcut '{text}'.")
    if mods == 0 and not 0x70 <= vk <= 0x87:
        raise HotkeyError(f"Shortcut '{text}' needs ctrl, alt, shift or win (only F1-F24 work alone).")
    return Hotkey(text=text, modifiers=mods, vk=vk)


class HotkeyListener:
    """
    A background thread that owns the shortcut and calls on_press() each time
    it is pressed. on_press runs on that thread, so it should only post an event.
    """

    def __init__(self, hotkey: Hotkey, on_press: Callable[[], None]):
        self.hotkey = hotkey
        self._on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False  # False: another app already uses this shortcut

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def _run(self) -> None:
        from . import winapi

        self._thread_id = winapi.current_thread_id()
        # RegisterHotKey belongs to the thread that calls it, so the message loop must run here too.
        self.registered = winapi.register_hotkey(HOTKEY_ID, self.hotkey.modifiers, self.hotkey.vk)
        if self.registered:
            log.info("Registered shortcut %s", self.hotkey.text)
        else:
            log.warning("Couldn't register shortcut %s (another app is probably using it)", self.hotkey.text)
        self._ready.set()
        if not self.registered:
            return
        try:
            winapi.message_loop(lambda hid: hid == HOTKEY_ID and self._safe_press())
        finally:
            winapi.unregister_hotkey(HOTKEY_ID)

    def _safe_press(self) -> None:
        try:
            self._on_press()
        except Exception:  # never let the shortcut thread die
            log.exception("Shortcut callback failed")

    def stop(self) -> None:
        if self._thread_id and self._thread and self._thread.is_alive():
            from . import winapi

            winapi.post_quit(self._thread_id)
            self._thread.join(2)  # the old shortcut must be released before it is registered again
