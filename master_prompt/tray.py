"""
The icon in the Windows system tray (bottom-right, near the clock).
Right-click it for the menu. Uses the `pystray` + `Pillow` packages.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def make_icon_image(size: int = 64):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 2, size - 2), radius=size // 4, fill=(124, 108, 255, 255))
    # a simple 4-point sparkle
    c, r, t = size / 2, size * 0.34, size * 0.09
    d.polygon([(c, c - r), (c + t, c - t), (c + r, c), (c + t, c + t),
               (c, c + r), (c - t, c + t), (c - r, c), (c - t, c - t)], fill="white")
    return img


class Tray:
    def __init__(self, actions: dict[str, Callable[[], None]],
                 is_auto_prompt_on: Callable[[], bool]):
        self.icon = None
        try:
            import pystray
        except ImportError:
            log.warning("pystray not installed - running without a tray icon")
            return

        def act(name):
            return lambda icon, item: actions[name]()

        menu = pystray.Menu(
            pystray.MenuItem("Auto prompt", act("toggle_auto_prompt"),
                             checked=lambda item: is_auto_prompt_on()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("API keys…", act("api_keys")),
            pystray.MenuItem("Open config", act("open_config")),
            pystray.MenuItem("Reload config", act("reload")),
            pystray.MenuItem("Open history", act("open_history")),
            pystray.MenuItem("Open log", act("open_log")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", act("quit")),
        )
        self.icon = pystray.Icon("master-prompt", make_icon_image(), "Master Prompt", menu)

    def start(self) -> None:
        if self.icon:
            self.icon.run_detached()

    def notify(self, message: str, title: str = "Master Prompt") -> None:
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
