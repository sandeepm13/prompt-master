"""
The icon in the Windows system tray (bottom-right, near the clock).
Right-click it for the menu. Uses the `pystray` + `Pillow` packages.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def make_icon_image(size: int = 64):
    """Render the dot itself as the system tray icon."""
    from . import glass

    pad = max(2, size // 8)
    dot_size = size - 2 * pad
    frame = glass.Frame(size, size, pad, pad, dot_size)
    return glass.render(frame)


class Tray:
    def __init__(self, actions: dict[str, Callable[[], None]]):
        self.icon = None
        try:
            import pystray
        except ImportError:
            log.warning("pystray not installed - running without a tray icon")
            return

        def act(name):
            return lambda icon, item: actions[name]()

        menu = pystray.Menu(
            pystray.MenuItem("API keys…", act("api_keys")),
            pystray.MenuItem("Open config", act("open_config")),
            pystray.MenuItem("Reload config", act("reload")),
            pystray.MenuItem("Open log", act("open_log")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", act("quit")),
        )
        self.icon = pystray.Icon("dotix", make_icon_image(), "dotix", menu)

    def start(self) -> None:
        if self.icon:
            self.icon.run_detached()

    def notify(self, message: str, title: str = "dotix") -> None:
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception:
                log.debug("tray notify failed", exc_info=True)

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
