"""
Thin wrapper around the Windows API using ctypes (no extra packages needed).

Everything that "talks to Windows" lives here:
  * keyboard: pressing keys for the user (Ctrl+C, Ctrl+A, Ctrl+V)
  * clipboard: reading / writing text on the clipboard
  * windows: which window is in front, bringing a window back to the front
  * process name of the window in front (to skip terminals etc.)

IMPORTANT for 64-bit Python: every function that returns or takes a HANDLE
must have argtypes/restype set, otherwise pointers get cut to 32 bits and the
app crashes randomly. That is why every function below is declared explicitly.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:  # pragma: no cover - we only import this on Windows
    raise ImportError("master_prompt.winapi only works on Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_BACK, VK_TAB, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x09, 0x0D, 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_SPACE, VK_ESCAPE = 0x20, 0x1B
VK_INSERT = 0x2D
VK_MASK_KEY = 0xE8  # "unassigned" key. Tapping it stops a released Win key from opening Start.

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9

# --------------------------------------------------------------------------
# Function signatures
# --------------------------------------------------------------------------
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


# --------------------------------------------------------------------------
# Process setup
# --------------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    """Make the popup sharp on high-DPI screens and mouse coordinates correct."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def acquire_single_instance(name: str = "Local\\MasterPromptSingleInstance"):
    """Return a mutex handle, or None if another copy of the app is running."""
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle  # keep a reference for the lifetime of the app


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN}


def _key(vk: int, up: bool) -> None:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    user32.keybd_event(vk, scan, flags, 0)


def is_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


_MODIFIERS = (VK_LWIN, VK_RWIN, VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU)


def wait_for_modifiers_released(timeout: float = 1.5) -> None:
    """
    When the hotkey fires the user is still holding Win+Shift. If we pressed
    Ctrl+C now, Windows would see Win+Shift+Ctrl+C (a totally different
    shortcut). So we wait until the user lets go. If they keep holding the
    keys, we release them ourselves (with a "mask" key so Start doesn't open).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(is_key_down(vk) for vk in _MODIFIERS):
            return
        time.sleep(0.015)
    held = [vk for vk in _MODIFIERS if is_key_down(vk)]
    if held:
        _key(VK_MASK_KEY, False)
        _key(VK_MASK_KEY, True)
        for vk in held:
            _key(vk, True)
        time.sleep(0.03)


def send_combo(*vks: int, delay: float = 0.02) -> None:
    """Press keys in order, then release them in reverse. e.g. send_combo(VK_CONTROL, ord('C'))"""
    for vk in vks:
        _key(vk, False)
        time.sleep(delay)
    for vk in reversed(vks):
        _key(vk, True)
        time.sleep(delay)


def ctrl(letter: str) -> None:
    send_combo(VK_CONTROL, ord(letter.upper()))


def shift_insert() -> None:
    send_combo(VK_SHIFT, VK_INSERT)


# --------------------------------------------------------------------------
# Clipboard
# --------------------------------------------------------------------------
_EXCLUDE_FMT = None


def _exclude_format() -> int:
    """Clipboard format that tells Win+V clipboard history to ignore our temporary text."""
    global _EXCLUDE_FMT
    if _EXCLUDE_FMT is None:
        _EXCLUDE_FMT = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    return _EXCLUDE_FMT


class ClipboardBusy(RuntimeError):
    pass


def _open_clipboard(retries: int = 25, wait: float = 0.02) -> None:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(wait)
    raise ClipboardBusy("Another program is using the clipboard. Try again.")


def clipboard_sequence() -> int:
    """A number that goes up every time anything changes the clipboard."""
    return int(user32.GetClipboardSequenceNumber())


def get_clipboard_text() -> str | None:
    _open_clipboard()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _alloc_global(data: bytes) -> int:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("GlobalAlloc failed")
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def set_clipboard_text(text: str | None, *, hide_from_history: bool = False) -> None:
    """Put text on the clipboard. None = empty clipboard."""
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        if text is None:
            return
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = _alloc_global(data)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ClipboardBusy("Could not write to the clipboard.")
        if hide_from_history:
            ex = _alloc_global(b"\x00\x00\x00\x00")
            if not user32.SetClipboardData(_exclude_format(), ex):
                kernel32.GlobalFree(ex)
    finally:
        user32.CloseClipboard()


def wait_clipboard_change(since: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clipboard_sequence() != since:
            time.sleep(0.03)  # let the app finish writing all formats
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------
# Windows / focus
# --------------------------------------------------------------------------
def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def process_name(hwnd: int) -> str:
    """e.g. 'chrome.exe', 'ChatGPT.exe', 'claude.exe'. Empty string if unknown."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def focus_window(hwnd: int, timeout: float = 0.6) -> bool:
    """
    Bring a window back to the front. Windows blocks programs from stealing
    focus, so we use the well-known tricks: tap Alt first, and if that is not
    enough, temporarily "attach" our input to the target window's thread.
    """
    if not is_window(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # Usually this just works, because our popup is in front (the user just
    # pressed Enter in it), and the program in front is allowed to hand focus over.
    user32.SetForegroundWindow(hwnd)

    if user32.GetForegroundWindow() != hwnd:
        # Trick 1: tap a harmless unassigned key so we count as "last input".
        # (We avoid the classic Alt tap: it can open menus in apps like Notepad.)
        _key(VK_MASK_KEY, False)
        _key(VK_MASK_KEY, True)
        time.sleep(0.02)
        user32.SetForegroundWindow(hwnd)

    if user32.GetForegroundWindow() != hwnd:
        # Trick 2: attach to the thread that currently owns the foreground.
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for t in {fg_thread, target_thread}:
            if t and t != me and user32.AttachThreadInput(me, t, True):
                attached.append(t)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            for t in attached:
                user32.AttachThreadInput(me, t, False)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
    return False


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL


user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE


def monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the full monitor that contains this window."""
    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        r = info.rcMonitor
        return r.left, r.top, r.right, r.bottom
    return 0, 0, 1920, 1080


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the usable screen area (no taskbar) of the monitor at x,y."""
    mon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, 1920, 1080


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80, 0x8, 0x08000000
_GetWindowLong = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_SetWindowLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = ctypes.c_ssize_t
_SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetWindowLong.restype = ctypes.c_ssize_t


def make_no_activate(hwnd: int) -> None:
    """A window that shows on top but never takes keyboard focus (for toasts)."""
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    _SetWindowLong(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


# Owned by the hidden main window, an overlay disappears when another app is focused.
GWL_HWNDPARENT = -8
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SWP_SHOWWINDOW, SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x0040, 0x0020, 0x0200
user32.SetWindowPos.argtypes = [
    wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL


def pin_overlay(hwnd: int) -> None:
    """Keep a small overlay above other apps, and do not take keyboard focus."""
    make_no_activate(hwnd)
    _SetWindowLong(hwnd, GWL_HWNDPARENT, 0)
    user32.SetWindowPos(
        hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED | SWP_NOOWNERZORDER,
    )


# --------------------------------------------------------------------------
# Global hotkeys
# --------------------------------------------------------------------------
def register_hotkey(hotkey_id: int, modifiers: int, vk: int) -> bool:
    return bool(user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk))


def unregister_hotkey(hotkey_id: int) -> None:
    user32.UnregisterHotKey(None, hotkey_id)


def current_thread_id() -> int:
    return int(kernel32.GetCurrentThreadId())


def message_loop(on_hotkey) -> None:
    """Blocks the current thread and calls on_hotkey(id) on every WM_HOTKEY."""
    msg = wintypes.MSG()
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:  # WM_QUIT or error
            return
        if msg.message == WM_HOTKEY:
            on_hotkey(int(msg.wParam))


def post_quit(thread_id: int) -> None:
    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
