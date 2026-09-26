"""
Global hotkeys.

A "global hotkey" is a shortcut that Windows itself listens for, no matter
which app is in front (that's how Win+H works). We ask Windows to tell us
when e.g. Win+Shift+P is pressed, using RegisterHotKey. This is the most
reliable method: no keyboard hooks, no admin rights, low CPU.

parse_hotkey() is pure Python so it can be unit-tested on any OS.
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
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}


@dataclass(frozen=True)
class Hotkey:
    text: str
    modifiers: int
    vk: int


class HotkeyError(ValueError):
    pass


def parse_hotkey(text: str) -> Hotkey:
    """'win+shift+p' -> Hotkey(modifiers=MOD_WIN|MOD_SHIFT, vk=ord('P'))"""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("Empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            raise HotkeyError(f"Unknown modifier '{p}' in '{text}'. Use ctrl, alt, shift, win.")
        mods |= _MODS[p]
    key = parts[-1]
    if key in _MODS:
        raise HotkeyError(f"'{text}' has no main key (only modifiers).")
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise HotkeyError(f"Unknown key '{key}' in '{text}'.")
    if mods == 0 and not (0x70 <= vk <= 0x87):
        raise HotkeyError(f"'{text}' needs at least one modifier (ctrl/alt/shift/win).")
    return Hotkey(text=text, modifiers=mods, vk=vk)


class HotkeyListener:
    """
    Runs a background thread that owns the hotkeys and calls
    callback(name) whenever one is pressed.

    Each name gets a list of hotkeys: the first one Windows lets us have is used
    (another app may already own e.g. Win+Shift+P on some PCs).
    """

    def __init__(self, hotkeys: dict[str, list[Hotkey]], callback: Callable[[str], None]):
        self._hotkeys = hotkeys
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.active: dict[str, str] = {}  # name -> hotkey text that is really registered
        self.failed: dict[str, str] = {}  # name -> first-choice hotkey text when none could be registered

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def _run(self) -> None:
        from . import winapi

        self._thread_id = winapi.current_thread_id()
        ids: dict[int, str] = {}
        for i, (name, choices) in enumerate(self._hotkeys.items(), start=1):
            for hk in choices:
                if winapi.register_hotkey(i, hk.modifiers, hk.vk):
                    ids[i] = name
                    self.active[name] = hk.text
                    log.info("Registered hotkey %s -> %s", hk.text, name)
                    break
                log.warning("Could not register hotkey %s (another app is probably using it)", hk.text)
            else:
                if choices:
                    self.failed[name] = choices[0].text
        self._ready.set()
        try:
            winapi.message_loop(lambda hid: ids.get(hid) and self._safe_call(ids[hid]))
        finally:
            for i in ids:
                winapi.unregister_hotkey(i)

    def _safe_call(self, name: str) -> None:
        try:
            self._callback(name)
        except Exception:  # never let the hotkey thread die
            log.exception("Hotkey callback failed")

    def stop(self) -> None:
        if self._thread_id:
            from . import winapi

            winapi.post_quit(self._thread_id)
        if self._thread and self._thread.is_alive():
            self._thread.join(2)  # wait until the old hotkeys are released before re-registering
