"""Tell an agent input box from a source editor using labels only.

The control's text is never accepted here. "editor" and "unknown" stay unreadable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusLabels:
    control_type: str
    name: str
    ancestors: tuple[str, ...]


_EDITOR_MARKERS = (
    "editor",
    "monaco",
    "editor group",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".json",
    ".md",
    ".toml",
    ".cpp",
    ".go",
    ".rs",
    ".java",
)

_AGENT_MARKERS = (
    "agent",
    "composer",
    "chat input",
    "aichat",
    "ask follow-up",
    "prompt",
)


def _marker_in(text: str, marker: str) -> bool:
    """True when marker appears as its own label, not inside prompt-master or read_agent_text."""
    start = 0
    while True:
        found = text.find(marker, start)
        if found < 0:
            return False
        before = text[found - 1] if found else ""
        after_at = found + len(marker)
        after = text[after_at] if after_at < len(text) else ""
        if before in ("-", "_") or after in ("-", "_"):
            start = found + 1
            continue
        # ".md" must not match the longer extension ".mdc", and ".js" must not match ".json".
        if marker.startswith(".") and after.isalnum():
            start = found + 1
            continue
        return True


_EDITOR_WORDS = ("editor", "monaco", "editor group")
_EDITOR_EXTENSIONS = tuple(marker for marker in _EDITOR_MARKERS if marker.startswith("."))


def _has_editor_word(text: str) -> bool:
    return any(_marker_in(text, marker) for marker in _EDITOR_WORDS)


def _has_editor_extension(text: str) -> bool:
    # A chat transcript mentions filenames. A real file tab name is short.
    if len(text) > 200:
        return False
    return any(_marker_in(text, marker) for marker in _EDITOR_EXTENSIONS)


def classify_focus(labels: FocusLabels, window_kind: str) -> str:
    """Return "agent", "editor", or "unknown" from control labels and window kind."""
    if window_kind == "ignore":
        return "unknown"
    own = labels.name.casefold()
    ancestors = tuple(part.casefold() for part in labels.ancestors)
    folded = (labels.control_type.casefold(), own, *ancestors)
    # The window title is an ancestor of every control, so an open file such as
    # llm.py must not hide the agent box. Extensions count only on this control.
    if _has_editor_word(own) or _has_editor_extension(own) or any(_has_editor_word(part) for part in ancestors):
        return "editor"
    if window_kind == "ide" and any(_marker_in(part, marker) for part in folded for marker in _AGENT_MARKERS):
        return "agent"
    # Cursor's agent box is an Edit with no name. The source editor's Edit is named as an editor.
    if window_kind == "ide" and labels.control_type.casefold() == "edit" and not labels.name.strip():
        return "agent"
    if window_kind == "llm" and labels.control_type.casefold() in {"edit", "document"}:
        return "agent"
    return "unknown"
