"""
Start the app:            python -m master_prompt
Test your API key only:   python -m master_prompt --test "make me a todo app in react"
Check your config:        python -m master_prompt --check
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler


def setup_logging(console: bool) -> None:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(DATA_DIR / "master-prompt.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if console and sys.stderr is not None:  # pythonw.exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")


def cmd_check() -> int:
    from .config import CONFIG_PATH, load_settings
    from .hotkeys import parse_hotkey

    s = load_settings()
    print(f"Config file : {CONFIG_PATH}")
    print(f"Hotkey      : {s.hotkey_enhance}  -> OK ({parse_hotkey(s.hotkey_enhance)})")
    if s.hotkey_enhance_clipboard:
        print(f"Clip hotkey : {s.hotkey_enhance_clipboard}  -> OK")
    print(f"Preview     : {s.preview}")
    print("Providers (tried in this order):")
    for p in s.providers:
        state = "ready" if p.usable else f"NO KEY (set api_key or env var {p.api_key_env})"
        print(f"  - {p.name:10s} {p.model:32s} {state}")
    return 0 if s.usable_providers else 1


def cmd_test(text: str, style: str) -> int:
    from .config import load_settings
    from .llm import LLMError, enhance

    s = load_settings()
    print(f"Original:\n{text}\n")
    try:
        r = enhance(text, s.providers, style=style, custom_styles=s.custom_styles, timeout=s.timeout_seconds)
    except LLMError as e:
        print(f"FAILED: {e}")
        return 1
    print(f"Improved ({r.provider} / {r.model}, {r.seconds:.1f}s):\n{r.text}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="master_prompt", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", metavar="TEXT", help="improve TEXT once and print it (no hotkeys)")
    parser.add_argument("--style", default="enhance", help="style for --test (enhance, coding, concise, detailed)")
    parser.add_argument("--check", action="store_true", help="check config.toml and API keys")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):  # Windows consoles: print any language/emoji safely
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    setup_logging(console=bool(args.test or args.check))
    from .config import ConfigError

    try:
        if args.check:
            return cmd_check()
        if args.test:
            return cmd_test(args.test, args.style)

        if sys.platform != "win32":
            print("The hotkey app only runs on Windows. You can still use --test / --check here.")
            return 2

        from . import winapi
        from .app import App
        from .config import load_settings

        winapi.enable_dpi_awareness()
        lock = winapi.acquire_single_instance()
        if lock is None:
            logging.getLogger(__name__).info("Already running - exiting")
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "Master Prompt is already running (see the tray icon).",
                                             "Master Prompt", 0x40)
            return 0
        App(load_settings()).run()
        return 0
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr or sys.stdout)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(e), "Master Prompt - config problem", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(main())
