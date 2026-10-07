"""
Idle watch for Auto prompt.

The keyboard hook stores the time of the last key and nothing else.
When the idle gap passes, this looks at the foreground process and the
focused control's type and names. It never reads the control's text.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from .focus_kind import FocusLabels, classify_focus
from .idle import IdleTimer
from .targets import classify_window

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_TIMER = 0x0113
COINIT_MULTITHREADED = 0x0
CLSCTX_INPROC_SERVER = 0x1
VT_I4 = 3
VT_BSTR = 8
UIA_CONTROL_TYPE = 30003
UIA_NAME = 30005

_CONTROL_TYPE_NAMES = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}

_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class _VarValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("bstrVal", ctypes.c_void_p)]


class VARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("r1", wintypes.USHORT),
        ("r2", wintypes.USHORT),
        ("r3", wintypes.USHORT),
        ("u", _VarValue),
    ]


def _guid(text: str) -> GUID:
    g = GUID()
    ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


def _vtable(unk: ctypes.c_void_p):
    return ctypes.cast(unk, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]


def _release(unk) -> None:
    if not unk:
        return
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vtable(unk)[2])
    release(unk)


def _call(unk, slot: int, restype, argtypes):
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(_vtable(unk)[slot])
    return fn


ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
ole32.CLSIDFromString.restype = ctypes.HRESULT
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.HRESULT
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = ctypes.HRESULT
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.restype = None

_CLSID_UIA = _guid("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
_IID_UIA = _guid("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")


class _Uia:
    """Focused-control labels only. Never asks for ValuePattern or TextPattern."""

    def __init__(self) -> None:
        self.auto = ctypes.c_void_p()
        self.walker = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_UIA), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_UIA), ctypes.byref(self.auto),
        )
        if hr < 0 or not self.auto:
            raise OSError(f"UI Automation is unavailable ({hr})")
        get_walker = _call(self.auto, 14, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])
        hr = get_walker(self.auto, ctypes.byref(self.walker))
        if hr < 0 or not self.walker:
            raise OSError(f"UI Automation walker is unavailable ({hr})")

    def close(self) -> None:
        _release(self.walker)
        _release(self.auto)
        self.walker = ctypes.c_void_p()
        self.auto = ctypes.c_void_p()

    def focused(self) -> tuple[FocusLabels, ctypes.c_void_p] | None:
        element = ctypes.c_void_p()
        get_focused = _call(self.auto, 8, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])
        if get_focused(self.auto, ctypes.byref(element)) < 0 or not element:
            return None
        try:
            control_type = _CONTROL_TYPE_NAMES.get(_property_int(element, UIA_CONTROL_TYPE), "")
            name = _property_name(element, UIA_NAME)
            ancestors = _ancestor_names(self.walker, element)
        except Exception:
            _release(element)
            raise
        return FocusLabels(control_type, name, ancestors), element


def _property(element, prop_id: int) -> VARIANT:
    var = VARIANT()
    get_value = _call(element, 10, ctypes.HRESULT, [ctypes.c_int, ctypes.POINTER(VARIANT)])
    hr = get_value(element, prop_id, ctypes.byref(var))
    if hr < 0:
        var.vt = 0
    return var


def _property_int(element, prop_id: int) -> int:
    var = _property(element, prop_id)
    return int(var.u.lVal) if var.vt == VT_I4 else 0


def _property_name(element, prop_id: int) -> str:
    var = _property(element, prop_id)
    if var.vt != VT_BSTR or not var.u.bstrVal:
        return ""
    text = ctypes.wstring_at(var.u.bstrVal)
    oleaut32.SysFreeString(var.u.bstrVal)
    return text


def _ancestor_names(walker, element) -> tuple[str, ...]:
    names: list[str] = []
    current = element
    get_parent = _call(walker, 3, ctypes.HRESULT, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])
    for _ in range(24):
        parent = ctypes.c_void_p()
        if get_parent(walker, current, ctypes.byref(parent)) < 0 or not parent:
            break
        if current is not element:
            _release(current)
        current = parent
        name = _property_name(current, UIA_NAME)
        if name:
            names.append(name)
    if current is not element:
        _release(current)
    return tuple(names)


def _bind_hook_api(user32) -> None:
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.KillTimer.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL


class AutoWatcher:
    """While running, a pause in an agent box asks the main thread to show ConfirmPopup."""

    def __init__(self, idle_seconds: float, on_agent) -> None:
        self.timer = IdleTimer(idle_seconds)
        self.timer.enabled = True
        self._on_agent = on_agent
        self.element = None  # focused agent element; text is not read here
        self.labels: FocusLabels | None = None
        self.window_kind = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._hook = None
        self._proc = None
        self._user32 = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="auto-prompt", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            log.warning("Auto prompt watcher did not start")

    def stop(self) -> None:
        if self._thread_id:
            from . import winapi

            winapi.post_quit(self._thread_id)
        if self._thread and self._thread.is_alive():
            self._thread.join(2)
        self._thread = None
        self._thread_id = 0

    def _run(self) -> None:
        from . import winapi

        user32 = self._user32 = winapi.user32
        _bind_hook_api(user32)
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = winapi.current_thread_id()
        self._proc = _HOOKPROC(self._on_key)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        timer_id = user32.SetTimer(None, 0, 50, None)
        if not self._hook:
            log.warning("Auto prompt keyboard hook was not installed")
        self._ready.set()
        uia: _Uia | None = None
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0 or result == -1:
                    return
                if msg.message == WM_TIMER and self.timer.poll(time.monotonic()):
                    try:
                        if uia is None:
                            uia = _Uia()
                        self._on_idle(uia)
                    except Exception:
                        log.exception("Auto prompt idle check failed")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if timer_id:
                user32.KillTimer(None, timer_id)
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._release_element()
            if uia is not None:
                uia.close()
            ole32.CoUninitialize()

    def _on_key(self, nCode, wParam, lParam):
        # lParam holds the key. It is never read and never stored.
        try:
            if nCode >= 0 and int(wParam) in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.timer.note_activity(time.monotonic())
            if self._user32 is not None:
                return self._user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
        except Exception:
            log.exception("Auto prompt hook failed")
        return 0

    def _on_idle(self, uia: _Uia) -> None:
        from . import winapi

        hwnd = winapi.foreground_window()
        kind = classify_window(winapi.process_name(hwnd), winapi.window_title(hwnd))
        if kind == "ignore":
            return
        stamp = self.timer._last
        found = uia.focused()
        if found is None or self.timer._last != stamp:
            if found is not None:
                _release(found[1])
            return
        labels, element = found
        focus = classify_focus(labels, kind)
        if focus != "agent":
            _release(element)
            return
        self._hold(element, labels, kind)
        try:
            self._on_agent()
        except Exception:
            log.exception("Auto prompt could not ask to generate")

    def take_agent(self):
        """Hand the approved box to the caller. Text is not read here."""
        with self._lock:
            element, self.element = self.element, None
            labels, self.labels = self.labels, None
            kind, self.window_kind = self.window_kind, ""
            return element, labels, kind

    def _hold(self, element, labels: FocusLabels, kind: str) -> None:
        with self._lock:
            old = self.element
            self.element = element
            self.labels = labels
            self.window_kind = kind
        if old is not None and old is not element:
            _release(old)

    def _release_element(self) -> None:
        with self._lock:
            element, self.element = self.element, None
            self.labels = None
            self.window_kind = ""
        _release(element)


UIA_VALUE_PATTERN = 10002
UIA_TEXT_PATTERN = 10014
_PATTERN_CONTROLS = frozenset({"edit", "document", "combobox", "group", "custom", "pane", "text"})


def _supports_text(element) -> bool:
    """True when this control can hold typed text. The text itself is not read."""
    for pattern_id in (UIA_VALUE_PATTERN, UIA_TEXT_PATTERN):
        pattern = ctypes.c_void_p()
        get_pattern = _call(element, 16, ctypes.HRESULT, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
        hr = get_pattern(element, pattern_id, ctypes.byref(pattern))
        if pattern:
            _release(pattern)
        if hr >= 0 and pattern:
            return True
    return False


def _parent_element(walker, element):
    parent = ctypes.c_void_p()
    get_parent = _call(walker, 3, ctypes.HRESULT, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])
    if get_parent(walker, element, ctypes.byref(parent)) < 0 or not parent:
        return None
    return parent


def _as_input(walker, element, kind: str):
    """Return this element when it is a text box, including a chat composer."""
    control_type = _CONTROL_TYPE_NAMES.get(_property_int(element, UIA_CONTROL_TYPE), "")
    name = _property_name(element, UIA_NAME)
    ancestors = _ancestor_names(walker, element)
    writable = control_type.casefold() in _PATTERN_CONTROLS and _supports_text(element)
    labels = FocusLabels(control_type, name, ancestors, writable)
    if classify_focus(labels, kind) != "agent":
        return None
    return labels


def capture_focused_agent():
    """Return the focused text box, or nothing.

    Labels only. The control's text is not read. The caller releases the element.
    A chat composer that is not a plain Edit is accepted when it can hold text.
    """
    from . import winapi

    ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    uia = None
    element = None
    try:
        hwnd = winapi.foreground_window()
        kind = classify_window(winapi.process_name(hwnd), winapi.window_title(hwnd))
        if kind == "ignore":
            return None, None, "", None
        uia = _Uia()
        found = uia.focused()
        if found is None:
            uia.close()
            return None, None, "", None
        _labels, element = found
        current = element
        for _ in range(8):
            labels = _as_input(uia.walker, current, kind)
            if labels is not None:
                if current is not element:
                    _release(element)
                return current, labels, kind, uia
            parent = _parent_element(uia.walker, current)
            if current is not element:
                _release(current)
            if parent is None:
                break
            current = parent
        if current is not None and current is not element:
            _release(current)
        _release(element)
        uia.close()
        return None, None, "", None
    except Exception:
        log.exception("Couldn't look at the focused control")
        _release(element)
        if uia is not None:
            uia.close()
        return None, None, "", None
