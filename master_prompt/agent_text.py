"""Read and replace one already-approved agent box.

classify_focus runs before any text API. An editor or an unknown control
never reaches ValuePattern, TextPattern, or the keyboard.
"""
from __future__ import annotations

import ctypes
import logging
import time

from .focus_kind import FocusLabels, classify_focus

log = logging.getLogger(__name__)

UIA_VALUE_PATTERN = 10002
UIA_TEXT_PATTERN = 10014


class AgentTextError(RuntimeError):
    pass


def read_agent_text(element, labels: FocusLabels, window_kind: str, *, reader=None) -> str:
    """Return the box text. The reader is not called unless the box is an agent input."""
    if classify_focus(labels, window_kind) != "agent":
        raise AgentTextError("That box can't be rewritten.")
    text = (reader or _read_element_text)(element)
    if text is None or not str(text).strip():
        raise AgentTextError("Nothing to rewrite.")
    return str(text)


def write_agent_text(
    element,
    labels: FocusLabels,
    window_kind: str,
    new_text: str,
    *,
    set_value=None,
    refresh=None,
    send_keys=None,
    select_text=None,
) -> str:
    """Write with ValuePattern.SetValue. Keys are a last resort and only for an agent box.

    Returns "written" when the box was changed, or "copied" when the caller
    should leave the new prompt on the clipboard.
    """
    if classify_focus(labels, window_kind) != "agent":
        return "copied"
    # Cursor's agent box accepts SetValue and still shows the old prompt.
    # An IDE write has to select that box's own text and paste. Ctrl+A is never sent.
    if window_kind != "ide":
        if (set_value or _try_set_value)(element, new_text):
            return "written"
    elif set_value is not None and set_value(element, new_text):
        return "written"
    # A test double for SetValue must not move the real focus.
    if set_value is None and not _set_focus(element):
        return "copied"
    fresh = (refresh or _labels_now)(element)
    if fresh is None or classify_focus(fresh, window_kind) != "agent":
        return "copied"
    if window_kind == "ide":
        return _paste_into_agent_box(
            element, window_kind, new_text, refresh=refresh, send_keys=send_keys, select_text=select_text,
        )
    previous, original = _clipboard_and_box(element) if send_keys is None else (None, None)
    if not (send_keys or _send_paste_keys)(new_text):
        return "copied"
    if send_keys is None:
        return _finish_paste(element, new_text, previous, original)
    return "written"


def _paste_into_agent_box(
    element,
    window_kind: str,
    new_text: str,
    *,
    refresh,
    send_keys,
    select_text,
) -> str:
    """Select this element's own text, then paste. Never sends Ctrl+A."""
    choose = select_text or _select_element_text
    if not choose(element):
        if select_text is None and _try_set_value(element, new_text):
            return "written"
        log.info("Agent box could not be selected; leaving the prompt on the clipboard")
        return "copied"
    if refresh is None and not _set_focus(element):
        return "copied"
    fresh = (refresh or _labels_now)(element)
    if fresh is None or classify_focus(fresh, window_kind) != "agent":
        return "copied"
    if send_keys is None and _foreground_kind() != window_kind:
        log.info("Agent box lost focus; leaving the prompt on the clipboard")
        return "copied"
    previous, original = _clipboard_and_box(element) if send_keys is None else (None, None)
    if not (send_keys or _paste_only)(new_text):
        return "copied"
    if send_keys is None:
        return _finish_paste(element, new_text, previous, original)
    return "written"


def _read_element_text(element) -> str:
    from .watch import _release

    pattern = _pattern(element, UIA_VALUE_PATTERN)
    if pattern is not None:
        try:
            text = _read_bstr(pattern, 4)
            if text is not None:
                return text
        finally:
            _release(pattern)
    pattern = _pattern(element, UIA_TEXT_PATTERN)
    if pattern is not None:
        try:
            text = _read_text_pattern(pattern)
            if text is not None:
                return text
        finally:
            _release(pattern)
    raise AgentTextError("Couldn't read that box.")


def _try_set_value(element, new_text: str) -> bool:
    from .watch import _call, _release, oleaut32

    pattern = _pattern(element, UIA_VALUE_PATTERN)
    if pattern is None:
        return False
    bstr = None
    try:
        oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
        oleaut32.SysAllocString.restype = ctypes.c_void_p
        bstr = oleaut32.SysAllocString(new_text)
        if not bstr:
            return False
        set_value = _call(pattern, 3, ctypes.HRESULT, [ctypes.c_void_p])
        if set_value(pattern, bstr) != 0:
            return False
        return _matches(element, new_text)
    except Exception:
        log.exception("SetValue failed")
        return False
    finally:
        if bstr:
            oleaut32.SysFreeString(bstr)
        _release(pattern)


def _pattern(element, pattern_id: int):
    from .watch import _call, _release

    pattern = ctypes.c_void_p()
    get_pattern = _call(element, 16, ctypes.HRESULT, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
    hr = get_pattern(element, pattern_id, ctypes.byref(pattern))
    if hr < 0 or not pattern:
        if pattern:
            _release(pattern)
        return None
    return pattern


def _read_bstr(target, slot: int) -> str | None:
    from .watch import _call, oleaut32

    bstr = ctypes.c_void_p()
    fn = _call(target, slot, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])
    if fn(target, ctypes.byref(bstr)) < 0:
        return None
    if not bstr:
        return ""
    try:
        return ctypes.wstring_at(bstr)
    finally:
        oleaut32.SysFreeString(bstr)


def _read_text_pattern(pattern) -> str | None:
    from .watch import _call, _release

    rng = ctypes.c_void_p()
    document = _call(pattern, 7, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])
    if document(pattern, ctypes.byref(rng)) < 0 or not rng:
        return None
    try:
        return _read_bstr_text(rng)
    finally:
        _release(rng)


def _read_bstr_text(text_range) -> str | None:
    from .watch import _call, oleaut32

    bstr = ctypes.c_void_p()
    get_text = _call(text_range, 12, ctypes.HRESULT, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
    if get_text(text_range, -1, ctypes.byref(bstr)) < 0:
        return None
    if not bstr:
        return ""
    try:
        return ctypes.wstring_at(bstr)
    finally:
        oleaut32.SysFreeString(bstr)


def _set_focus(element) -> bool:
    from .watch import _call

    try:
        focus = _call(element, 3, ctypes.HRESULT, [])
        return focus(element) >= 0
    except Exception:
        log.exception("SetFocus failed")
        return False


def _labels_now(element) -> FocusLabels | None:
    from .watch import (
        UIA_CONTROL_TYPE,
        UIA_NAME,
        _CONTROL_TYPE_NAMES,
        _Uia,
        _ancestor_names,
        _property_int,
        _property_name,
    )

    try:
        uia = _Uia()
    except Exception:
        log.exception("Couldn't read control labels")
        return None
    try:
        control_type = _CONTROL_TYPE_NAMES.get(_property_int(element, UIA_CONTROL_TYPE), "")
        name = _property_name(element, UIA_NAME)
        ancestors = _ancestor_names(uia.walker, element)
        return FocusLabels(control_type, name, ancestors)
    except Exception:
        log.exception("Couldn't read control labels")
        return None
    finally:
        uia.close()


def _select_element_text(element) -> bool:
    """Select this element's own text range. This is not a window-wide Ctrl+A."""
    from .watch import _call, _release

    pattern = _pattern(element, UIA_TEXT_PATTERN)
    if pattern is None:
        return False
    rng = ctypes.c_void_p()
    try:
        document = _call(pattern, 7, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])
        if document(pattern, ctypes.byref(rng)) < 0 or not rng:
            return False
        select = _call(rng, 16, ctypes.HRESULT, [])
        return select(rng) == 0
    except Exception:
        log.exception("Couldn't select the agent box")
        return False
    finally:
        _release(rng)
        _release(pattern)


def _matches(element, new_text: str) -> bool:
    want = new_text.replace("\r\n", "\n").strip()
    if not want:
        return False
    for got in _element_texts(element):
        if got.replace("\r\n", "\n").strip() == want:
            return True
    return False


def _element_texts(element) -> tuple[str, ...]:
    from .watch import _release

    found: list[str] = []
    pattern = _pattern(element, UIA_VALUE_PATTERN)
    if pattern is not None:
        try:
            text = _read_bstr(pattern, 4)
            if text:
                found.append(text)
        finally:
            _release(pattern)
    pattern = _pattern(element, UIA_TEXT_PATTERN)
    if pattern is not None:
        try:
            text = _read_text_pattern(pattern)
            if text:
                found.append(text)
        finally:
            _release(pattern)
    return tuple(found)


def _foreground_kind() -> str:
    from . import winapi
    from .targets import classify_window

    hwnd = winapi.foreground_window()
    return classify_window(winapi.process_name(hwnd), winapi.window_title(hwnd))


def clipboard_to_keep(previous: str | None, original: str | None, new_text: str) -> str | None:
    """Text to put back after a paste. None clears the clipboard.

    The prompt copied from the input box and the generated prompt are both dropped.
    """
    prev = (previous or "").strip()
    if not prev or prev == (original or "").strip() or prev == new_text.strip():
        return None
    return previous


def _clipboard_and_box(element) -> tuple[str | None, str | None]:
    from . import winapi

    try:
        previous = winapi.get_clipboard_text()
    except Exception:
        previous = None
    try:
        original = _read_element_text(element)
    except AgentTextError:
        original = None
    return previous, original


def _finish_paste(element, new_text: str, previous: str | None, original: str | None) -> str:
    """Wait until the box shows only the new prompt, then clear those clipboard texts."""
    time.sleep(0.35)
    if not _matches(element, new_text) and not _try_set_value(element, new_text):
        log.info("Paste did not change the agent box")
        return "copied"
    _settle_clipboard(previous, original, new_text)
    return "written"


def _settle_clipboard(previous: str | None, original: str | None, new_text: str) -> None:
    from . import winapi

    time.sleep(0.25)
    try:
        winapi.set_clipboard_text(clipboard_to_keep(previous, original, new_text))
    except Exception:
        log.exception("Couldn't clear the clipboard")


def _paste_only(new_text: str) -> bool:
    from . import winapi

    try:
        winapi.set_clipboard_text(new_text, hide_from_history=True)
        winapi.ctrl("V")
    except Exception:
        log.exception("Couldn't paste into the agent box")
        return False
    return True


def _send_paste_keys(new_text: str) -> bool:
    from . import winapi

    try:
        winapi.set_clipboard_text(new_text, hide_from_history=True)
        winapi.ctrl("A")
        winapi.ctrl("V")
    except Exception:
        log.exception("Couldn't paste into the agent box")
        return False
    return True
