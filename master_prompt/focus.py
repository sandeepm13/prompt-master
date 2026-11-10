"""
Is the cursor in a code editor? Checked before anything is copied or pasted,
so a code file is never read, sent to the AI, or overwritten.

Windows UI Automation gives the focused control's class name and type, and
the names and class names of the controls around it. The control's text is
never read.
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading

log = logging.getLogger(__name__)

CODE_MESSAGE = ("Your cursor is in a code file, so nothing was changed.\n"
                "Click in the chat box (Copilot, Cursor, Claude), then click the dot.")

# Class names are matched as whole words, so "chatInputArea" is not "inputarea".
# Desktop code editors: Visual Studio, Notepad++.
_NATIVE_CODE_CLASSES = frozenset({"wpftextview", "scintilla"})
# Code editors drawn in a web page or Electron app: VS Code, Cursor, Windsurf, CodeMirror, Ace.
_WEB_CODE_CLASSES = frozenset({
    "native-edit-context", "inputarea", "monaco-editor", "cm-content", "cm-editor",
    "codemirror", "ace_text-input", "ace_editor",
})
# VS Code's code files sit in an editor group: "app.py, Editor Group 1".
_CODE_FILE_ANCESTOR_NAMES = ("editor group",)
_CODE_FILE_ANCESTOR_CLASSES = frozenset({"editor-instance"})
# These boxes use the same editor widget, but hold a prompt or a message, not code.
_INPUT_ANCESTOR_CLASSES = ("interactive-input", "chat-input", "inline-chat", "scm-editor", "composer")
_INPUT_ANCESTOR_NAMES = ("source control input", "chat input")


def _words(*class_names: str) -> frozenset[str]:
    return frozenset(word for name in class_names for word in name.casefold().split())


def is_code_editor(class_name: str, role: str = "", ancestor_names: tuple[str, ...] = (),
                   ancestor_classes: tuple[str, ...] = ()) -> bool:
    """True when the focused control is a code editor, not a chat or prompt box."""
    own = _words(class_name)
    if own & _NATIVE_CODE_CLASSES:
        return True
    above = _words(*ancestor_classes)
    code_widget = role.casefold() == "editor" or bool((own | above) & _WEB_CODE_CLASSES)
    if not code_widget:
        return False
    names = tuple(n.casefold() for n in ancestor_names)
    if any(part in n for n in names for part in _CODE_FILE_ANCESTOR_NAMES) or above & _CODE_FILE_ANCESTOR_CLASSES:
        return True
    is_input = (any(part in word for word in above for part in _INPUT_ANCESTOR_CLASSES)
                or any(n.startswith(part) for n in names for part in _INPUT_ANCESTOR_NAMES))
    return not is_input


# Apps that edit code. If the focused control can't be checked in these, nothing is changed.
_IDE_APPS = frozenset({
    "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "notepad++.exe",
})
# Editors that don't tell UI Automation where the cursor is. A code file in the title is enough to stop.
_TITLE_ONLY_EDITORS = frozenset({
    "notepad.exe", "sublime_text.exe", "eclipse.exe", "zed.exe", "atom.exe", "studio64.exe",
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "phpstorm64.exe", "clion64.exe", "rider64.exe",
    "goland64.exe", "rubymine64.exe", "datagrip64.exe", "rustrover64.exe", "fleet.exe",
})
_IDE_APPS = _IDE_APPS | _TITLE_ONLY_EDITORS

_CODE_EXTENSIONS = (
    "py", "pyw", "ipynb", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte", "java", "kt", "kts",
    "c", "h", "cc", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "swift", "dart", "scala", "lua", "r",
    "m", "pl", "sh", "bash", "ps1", "bat", "cmd", "sql", "html", "htm", "css", "scss", "sass", "less",
    "json", "yaml", "yml", "toml", "xml", "ini", "cfg", "conf", "env", "gradle", "tf", "dockerfile",
)
_CODE_FILE = re.compile(r"\.(?:" + "|".join(_CODE_EXTENSIONS) + r")\b", re.IGNORECASE)


def title_shows_code_file(title: str) -> bool:
    """True when a window title names a code file, e.g. "app.py - Notepad"."""
    return bool(_CODE_FILE.search(title or ""))


def must_not_touch(process: str, title: str, focus_is_code: bool | None) -> bool:
    """Decide whether the box may be read and replaced.

    focus_is_code is the UI Automation verdict, or None when it couldn't be checked.
    """
    process = (process or "").lower()
    if focus_is_code:
        return True
    if process in _TITLE_ONLY_EDITORS and title_shows_code_file(title):
        return True
    if focus_is_code is None and process in _IDE_APPS:
        return True  # couldn't check inside an IDE: stay safe
    return False


def cursor_in_code(hwnd: int) -> bool:
    """True when the cursor is in code (or might be), so nothing may be copied or pasted."""
    from . import winapi

    process, title = winapi.process_name(hwnd), winapi.window_title(hwnd)
    verdict = _focus_verdict()
    blocked = must_not_touch(process, title, verdict)
    if blocked:
        log.info("Not touching %s: the cursor is in code (%s)", process, "checked" if verdict else "by title")
    return blocked


def _focus_verdict(timeout: float = 1.5) -> bool | None:
    """Look at the focused control. Runs in its own thread so COM never touches the UI thread."""
    result: list[bool | None] = [None]

    def run() -> None:
        try:
            result[0] = _check_focus()
        except Exception:
            log.exception("Couldn't look at the focused control")

    thread = threading.Thread(target=run, name="focus-check", daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0]


def read_address_bar(hwnd: int, timeout: float = 1.5) -> str:
    """The text in a browser window's address bar, e.g. "chatgpt.com/c/...". Empty if not found.

    The caller keeps only the domain.
    """
    result = [""]

    def run() -> None:
        try:
            result[0] = _address_bar(hwnd) or ""
        except Exception:
            log.exception("Couldn't read the address bar")

    thread = threading.Thread(target=run, name="address-bar", daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0]


def read_visible_chat(hwnd: int, timeout: float = 1.5) -> tuple[list[tuple[str, tuple[int, int, int, int]]],
                                                               tuple[int, int, int, int] | None]:
    """Text pieces visible in the window, with their screen rectangles, plus the input box's rectangle.

    context.visible_chat_text() keeps only what is above the input box, in its column.
    """
    result: list = [([], None)]

    def run() -> None:
        try:
            result[0] = _visible_text(hwnd)
        except Exception:
            log.exception("Couldn't read the visible chat")

    thread = threading.Thread(target=run, name="visible-chat", daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0]


# ------------------------------------------------------------ UI Automation
UIA_IS_OFFSCREEN = 30022
UIA_TEXT = 50020
VT_BOOL = 11
_MAX_PIECES = 3000
UIA_CONTROL_TYPE = 30003
UIA_LOCALIZED_CONTROL_TYPE = 30004
UIA_NAME = 30005
UIA_CLASS_NAME = 30012
UIA_VALUE = 30045
UIA_EDIT = 50004
TREE_SCOPE_DESCENDANTS = 4
VT_I4 = 3
VT_BSTR = 8


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


def _call(obj, slot: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[slot])


def _release(obj) -> None:
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)


def _text_property(element, prop_id: int) -> str:
    var = _VARIANT()
    get = _call(element, 10, ctypes.c_int, ctypes.POINTER(_VARIANT))  # GetCurrentPropertyValue
    try:
        get(element, prop_id, ctypes.byref(var))
    except OSError:
        return ""
    if var.vt != VT_BSTR or not var.value:
        return ""
    try:
        return ctypes.wstring_at(var.value)
    finally:
        ctypes.windll.oleaut32.SysFreeString(ctypes.c_void_p(var.value))


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _automation(auto: ctypes.c_void_p) -> bool:
    clsid = _guid("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")  # CUIAutomation
    iid = _guid("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")    # IUIAutomation
    return ctypes.windll.ole32.CoCreateInstance(
        ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(auto)) >= 0


def _address_bar(hwnd: int) -> str:
    """The first text field in the browser window is its address bar (it comes before the page)."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # multithreaded
    auto, root, condition, found = (ctypes.c_void_p() for _ in range(4))
    try:
        if not _automation(auto):
            return ""
        _call(auto, 6, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(  # ElementFromHandle
            auto, ctypes.c_void_p(hwnd), ctypes.byref(root))
        edit = _VARIANT()
        edit.vt, edit.value = VT_I4, UIA_EDIT
        _call(auto, 23, ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p))(  # CreatePropertyCondition
            auto, UIA_CONTROL_TYPE, edit, ctypes.byref(condition))
        if not root or not condition:
            return ""
        _call(root, 5, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(  # FindFirst
            root, TREE_SCOPE_DESCENDANTS, condition, ctypes.byref(found))
        return _text_property(found, UIA_VALUE) if found else ""
    finally:
        _release(found)
        _release(condition)
        _release(root)
        _release(auto)
        ole32.CoUninitialize()


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _rect(element) -> tuple[int, int, int, int] | None:
    r = _RECT()
    try:
        _call(element, 43, ctypes.POINTER(_RECT))(element, ctypes.byref(r))  # get_CurrentBoundingRectangle
    except OSError:
        return None
    return r.left, r.top, r.right, r.bottom


def _condition(auto, prop: int, vt: int, value: int) -> ctypes.c_void_p:
    var = _VARIANT()
    var.vt, var.value = vt, value
    cond = ctypes.c_void_p()
    _call(auto, 23, ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p))(  # CreatePropertyCondition
        auto, prop, var, ctypes.byref(cond))
    return cond


def _visible_text(hwnd: int):
    """On-screen text pieces of the window, and the rectangle of the focused input box."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # multithreaded
    auto, root, focused, both, found = (ctypes.c_void_p() for _ in range(5))
    is_text = on_screen = ctypes.c_void_p()
    pieces: list[tuple[str, tuple[int, int, int, int]]] = []
    try:
        if not _automation(auto):
            return pieces, None
        _call(auto, 8, ctypes.POINTER(ctypes.c_void_p))(auto, ctypes.byref(focused))  # GetFocusedElement
        box = _rect(focused) if focused else None
        if not box:
            return pieces, None
        _call(auto, 6, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(  # ElementFromHandle
            auto, ctypes.c_void_p(hwnd), ctypes.byref(root))
        is_text = _condition(auto, UIA_CONTROL_TYPE, VT_I4, UIA_TEXT)
        on_screen = _condition(auto, UIA_IS_OFFSCREEN, VT_BOOL, 0)
        _call(auto, 25, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(  # CreateAndCondition
            auto, is_text, on_screen, ctypes.byref(both))
        _call(root, 6, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(  # FindAll
            root, TREE_SCOPE_DESCENDANTS, both, ctypes.byref(found))
        if not found:
            return pieces, box
        count = ctypes.c_int()
        _call(found, 3, ctypes.POINTER(ctypes.c_int))(found, ctypes.byref(count))  # get_Length
        get = _call(found, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))         # GetElement
        for i in range(min(count.value, _MAX_PIECES)):
            element = ctypes.c_void_p()
            if get(found, i, ctypes.byref(element)) < 0 or not element:
                continue
            try:
                text = _text_property(element, UIA_NAME)
                rect = _rect(element) if text.strip() else None
                if rect:
                    pieces.append((text, rect))
            finally:
                _release(element)
        return pieces, box
    finally:
        for obj in (found, both, on_screen, is_text, root, focused, auto):
            _release(obj)
        ole32.CoUninitialize()


Labels = tuple[str, str, tuple[str, ...], tuple[str, ...]]  # class name, role, ancestor names, ancestor classes

# Which assistant a focused IDE chat box belongs to, from the class names around it.
_COPILOT_CLASSES = ("interactive-input", "inline-chat")          # VS Code's chat widget (Copilot Chat)
_UNREADABLE_CLASSES = ("composer", "aichat", "cascade")           # Cursor's own chat, Windsurf Cascade
_UNREADABLE_NAMES = ("source control input",)                     # the commit message box


def agent_for_focus(labels: Labels | None) -> str | None:
    """"copilot", "claude", "other" (an assistant whose sessions can't be read, or a commit box),
    or None when the box can't be told apart (for example Codex's panel)."""
    if not labels:
        return None
    class_name, _, names, classes = labels
    words = " ".join((class_name, *classes)).casefold()
    if any(part in words for part in _COPILOT_CLASSES):
        return "copilot"
    if any(part in words for part in _UNREADABLE_CLASSES) or any(
            n.casefold().startswith(part) for n in names for part in _UNREADABLE_NAMES):
        return "other"
    if class_name.startswith("messageInput"):  # Claude Code extension's input box
        return "claude"
    return None


def focused_labels(timeout: float = 1.5) -> Labels | None:
    """Type and names of the focused control and the controls around it. Never its text."""
    result: list[Labels | None] = [None]

    def run() -> None:
        try:
            result[0] = _focus_labels()
        except Exception:
            log.exception("Couldn't look at the focused control")

    thread = threading.Thread(target=run, name="focus-labels", daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0]


def focused_text(timeout: float = 1.0) -> tuple[str, str, str] | None:
    """(class name, role, text) of the focused control, read with UI Automation: no clipboard, no keys.

    Only used to tell whether the box still holds the generated prompt (so Undo can go away once
    it was sent). The text is never stored, logged or sent. None when the control has no readable text.
    """
    result: list[tuple[str, str, str] | None] = [None]

    def run() -> None:
        try:
            result[0] = _focused_text()
        except Exception:
            log.exception("Couldn't read the focused control")

    thread = threading.Thread(target=run, name="focus-text", daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0]


def _focused_text() -> tuple[str, str, str] | None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # multithreaded
    auto, element = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if not _automation(auto):
            return None
        _call(auto, 8, ctypes.POINTER(ctypes.c_void_p))(auto, ctypes.byref(element))  # GetFocusedElement
        if not element:
            return None
        var = _VARIANT()
        try:
            _call(element, 10, ctypes.c_int, ctypes.POINTER(_VARIANT))(element, UIA_VALUE, ctypes.byref(var))
        except OSError:
            return None
        if var.vt != VT_BSTR:
            return None  # no Value: this control's text can't be read this way
        try:
            text = ctypes.wstring_at(var.value) if var.value else ""
        finally:
            if var.value:
                ctypes.windll.oleaut32.SysFreeString(ctypes.c_void_p(var.value))
        return _text_property(element, UIA_CLASS_NAME), _text_property(element, UIA_LOCALIZED_CONTROL_TYPE), text
    finally:
        _release(element)
        _release(auto)
        ole32.CoUninitialize()


def _check_focus() -> bool | None:
    labels = _focus_labels()
    if labels is None:
        return None
    verdict = is_code_editor(*labels)
    log.info("Focused control: class=%r role=%r -> %s", labels[0], labels[1],
             "code editor" if verdict else "text box")
    return verdict


def _focus_labels() -> Labels | None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # multithreaded
    auto = ctypes.c_void_p()
    walker = ctypes.c_void_p()
    element = ctypes.c_void_p()
    try:
        if not _automation(auto):
            return None
        _call(auto, 8, ctypes.POINTER(ctypes.c_void_p))(auto, ctypes.byref(element))   # GetFocusedElement
        _call(auto, 14, ctypes.POINTER(ctypes.c_void_p))(auto, ctypes.byref(walker))   # ControlViewWalker
        if not element or not walker:
            return None
        class_name = _text_property(element, UIA_CLASS_NAME)
        role = _text_property(element, UIA_LOCALIZED_CONTROL_TYPE)
        names: list[str] = []
        classes: list[str] = []
        get_parent = _call(walker, 3, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        current = element
        for _ in range(24):
            parent = ctypes.c_void_p()
            try:
                get_parent(walker, current, ctypes.byref(parent))
            except OSError:
                break
            if current is not element:
                _release(current)
            if not parent:
                current = element
                break
            current = parent
            if name := _text_property(current, UIA_NAME):
                names.append(name)
            if cls := _text_property(current, UIA_CLASS_NAME):
                classes.append(cls)
        if current is not element:
            _release(current)
        return class_name, role, tuple(names), tuple(classes)
    finally:
        _release(element)
        _release(walker)
        _release(auto)
        ole32.CoUninitialize()
