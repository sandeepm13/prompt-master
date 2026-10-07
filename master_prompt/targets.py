"""Every window is eligible. The focused control is checked separately.

This does not look at a list of apps, and it does not read the control's text.
"""
from __future__ import annotations


def classify_window(process_name: str, window_title: str) -> str:
    """Return "text" for every process and title.

    ChatGPT, Cursor, Notepad, a search box, and any other window are the same
    here. "ignore" is not used.
    """
    del process_name, window_title
    return "text"
