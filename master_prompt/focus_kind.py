"""Tell a text box from everything else using the control type only.

The control's text is never accepted here. An Edit, Document, or ComboBox
may be rewritten. That includes prompt boxes, document boxes, and search boxes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusLabels:
    control_type: str
    name: str
    ancestors: tuple[str, ...]
    writable: bool = False


_TEXT_CONTROLS = frozenset({"edit", "document", "combobox"})
# Chat apps such as WhatsApp draw the message box as a group or a custom control.
_SOFT_CONTROLS = frozenset({"group", "custom", "pane", "text"})
_FIELD_NAMES = ("type a message", "write a message", "search", "compose", "chat input")


def classify_focus(labels: FocusLabels, window_kind: str) -> str:
    """Return "agent" for a text box, otherwise "unknown".

    "agent" means Yes may rewrite this control. A normal text box, a search box,
    and a chat composer such as "Type a message" are included. Buttons, trees,
    and file tabs are not.
    """
    if window_kind == "ignore":
        return "unknown"
    if labels.writable:
        return "agent"
    control = labels.control_type.casefold()
    if control in _TEXT_CONTROLS:
        return "agent"
    name = labels.name.casefold()
    if control in _SOFT_CONTROLS and any(part in name for part in _FIELD_NAMES):
        return "agent"
    return "unknown"
