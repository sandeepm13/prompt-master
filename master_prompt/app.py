"""
The main app: connects hotkey -> grab text -> AI -> preview -> paste back.

Threads (so the app never freezes):
  * main thread ........ tkinter windows (popup, toasts). Only this thread touches UI.
  * "hotkeys" thread ... waits for Windows to tell us a shortcut was pressed.
  * worker threads ..... grabbing text, calling the AI, pasting (they sleep/wait a lot).
  * tray thread ........ the tray icon menu.
  * auto prompt .......... a floating dot. A click asks before any text is read.
Other threads never touch the UI directly; they put a message in self.events,
and the main thread reads it every 40 ms.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import subprocess
import threading
import time
import tkinter as tk

from . import capture, history, llm, winapi
from .config import (
    CONFIG_PATH, DATA_DIR, ConfigError, Settings, ensure_config_file, load_settings,
    save_provider_keys, set_auto_prompt,
)
from .hotkeys import HotkeyError, HotkeyListener, parse_hotkey
from .prompts import all_styles
from .tray import Tray
from .ui import (
    ConfirmPopup, FloatDot, KeySetupWindow, PreviewPopup, Toast,
    default_dot_origin, load_dot_position, save_dot_position,
)

log = logging.getLogger(__name__)
HISTORY_PATH = DATA_DIR / "history.jsonl"
LOG_PATH = DATA_DIR / "master-prompt.log"
DOT_PATH = DATA_DIR / "float-dot.json"
# Tried in order when the main hotkey is already taken by another app on this PC.
FALLBACK_HOTKEYS = ["win+alt+p", "ctrl+alt+p"]


def _pretty(hotkey: str) -> str:
    return "+".join(p.strip().capitalize() for p in hotkey.split("+"))


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.events: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()  # the main window stays hidden; we only use popups
        self.toast = Toast(self.root)
        self.popup: PreviewPopup | None = None
        self.confirm: ConfirmPopup | None = None
        self.dot: FloatDot | None = None
        self._pending = None  # focused text box held for Yes; text is not read here
        self._dot_hwnd = 0
        self._capture_uia = None
        self.key_window: KeySetupWindow | None = None
        self.hotkey_text = settings.hotkey_enhance  # the one really registered (may be a fallback)
        self.listener: HotkeyListener | None = None
        self.tray: Tray | None = None
        self.watcher = None

        self.job = 0                       # increases each request; old answers are ignored
        self.busy = False
        self.busy_since = 0.0
        self.cap: capture.Capture | None = None
        self.last_result: llm.Result | None = None
        self.style = settings.default_style

    # ------------------------------------------------------------------ setup
    def run(self) -> None:
        self._build_popup()
        self._build_confirm()
        self._build_dot()
        self.tray = Tray({
            "toggle_auto_prompt": lambda: self._post("toggle_auto_prompt"),
            "api_keys": lambda: self._post("open_keys"),
            "open_config": lambda: self._open(CONFIG_PATH),
            "open_history": lambda: self._open(HISTORY_PATH),
            "open_log": lambda: self._open(LOG_PATH),
            "reload": lambda: self._post("reload"),
            "quit": lambda: self._post("quit"),
        }, is_auto_prompt_on=lambda: self.settings.auto_prompt)
        self.tray.start()
        self._sync_watcher()

        if not self.settings.usable_providers:
            self._on_open_keys()
        else:
            self.toast.show("Master Prompt is running.\nClick the dot, then Yes, to improve the text in the box.",
                            duration_ms=5000)

        self.root.after(40, self._pump)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _build_popup(self) -> None:
        if self.popup:
            self.popup.win.destroy()
        self.styles = all_styles(self.settings.custom_styles)
        if self.style not in self.styles:
            self.style = "enhance"
        self.popup = PreviewPopup(self.root, self.styles, on_accept=self._accept, on_cancel=self._cancel,
                                  on_regenerate=self._regenerate, on_copy=self._copy)

    def _build_confirm(self) -> None:
        self.confirm = ConfirmPopup(self.root, on_yes=self._on_auto_yes, on_no=self._on_auto_no)

    def _build_dot(self) -> None:
        self.dot = FloatDot(self.root, on_click=self._on_dot_click, on_place=self._save_dot)

    def _start_hotkeys(self) -> None:
        if self.listener:
            self.listener.stop()
        main = self.settings.hotkey_enhance
        wanted = {"enhance": [main] + [h for h in FALLBACK_HOTKEYS if h != main.replace(" ", "").lower()]}
        if self.settings.hotkey_enhance_clipboard:
            wanted["enhance_clipboard"] = [self.settings.hotkey_enhance_clipboard]
        parsed = {}
        for name, texts in wanted.items():
            try:
                parsed[name] = [parse_hotkey(t) for t in texts]
            except HotkeyError as e:
                self.toast.show(f"Bad hotkey in config.toml: {e}", kind="error", duration_ms=8000)
        self.listener = HotkeyListener(parsed, lambda name: self._post("hotkey", name))
        self.listener.start()
        self.hotkey_text = self.listener.active.get("enhance", main)
        if self.tray and self.tray.icon:
            self.tray.icon.title = f"Master Prompt ({_pretty(self.hotkey_text)})"
        if self.listener.failed:
            keys = ", ".join(_pretty(t) for t in self.listener.failed.values())
            self.toast.show(f"{keys} is already used by another app.\nPick a different one in config.toml.",
                            kind="error", duration_ms=9000)

    def _hotkey_note(self) -> str:
        if self.hotkey_text != self.settings.hotkey_enhance:
            return f"\n({_pretty(self.settings.hotkey_enhance)} is used by another app on this PC.)"
        return ""

    # ------------------------------------------------------------- event pump
    def _post(self, kind: str, *payload) -> None:
        self.events.put((kind, payload))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    getattr(self, f"_on_{kind}")(*payload)
                except Exception:
                    log.exception("Error handling event %s", kind)
                    self.busy = False
        except queue.Empty:
            pass
        self.root.after(40, self._pump)

    def _thread(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    # --------------------------------------------------------------- handlers
    def _on_hotkey(self, name: str) -> None:
        if self.busy:
            if self.popup and self.popup.visible:
                self.popup._present()  # already open: just bring it to the front
                return
            if time.monotonic() - self.busy_since < 90:
                return  # still working on the previous request
            log.warning("Previous request looked stuck - starting a new one")
        self.busy = True
        self.busy_since = time.monotonic()
        self.job += 1
        self._thread(self._grab_worker, self.job, name)

    def _grab_worker(self, job: int, name: str) -> None:
        try:
            if name == "enhance_clipboard":
                cap = capture.grab_clipboard()
            else:
                cap = capture.grab_text(self.settings.auto_select_all, self.settings.blocked_apps,
                                        self.settings.restore_clipboard)
            self._post("captured", job, cap)
        except (capture.CaptureError, winapi.ClipboardBusy) as e:
            self._post("fail", job, str(e))
        except Exception as e:
            log.exception("grab failed")
            self._post("fail", job, f"Could not read the text: {e}")

    def _on_captured(self, job: int, cap: capture.Capture) -> None:
        if job != self.job:
            return
        self.cap = cap
        if self.settings.preview:
            self.popup.show_loading(cap.text, self.style)
        else:
            self.toast.show("✨ Enhancing your prompt…", duration_ms=0)
        self._thread(self._llm_worker, job, cap.text, self.style)

    def _llm_worker(self, job: int, text: str, style: str) -> None:
        try:
            res = llm.enhance(text, self.settings.providers, style=style,
                              custom_styles=self.settings.custom_styles,
                              timeout=self.settings.timeout_seconds)
            self._post("result", job, res)
        except llm.LLMError as e:
            self._post("llm_error", job, str(e))
        except Exception as e:
            log.exception("LLM call crashed")
            self._post("llm_error", job, f"Unexpected error: {e}")

    def _on_result(self, job: int, res: llm.Result) -> None:
        if job != self.job:
            return
        self.last_result = res
        if self.settings.preview:
            self.popup.show_result(res.text, f"{res.provider} · {res.seconds:.1f}s")
        else:
            self.toast.hide()
            self._accept(res.text)

    def _on_llm_error(self, job: int, message: str) -> None:
        if job != self.job:
            return
        if self.settings.preview and self.popup.visible:
            self.popup.show_error(message)  # stays busy until Esc / retry
        else:
            self._on_fail(job, message)

    def _on_fail(self, job: int, message: str) -> None:
        if job != self.job:
            return
        self.busy = False
        self._close_capture_uia()
        log.warning("Shown to user: %s", message)  # so the log explains failures, not just the toast
        self.toast.show(message, kind="error", duration_ms=6000)

    # popup buttons -----------------------------------------------------------
    def _accept(self, text: str) -> None:
        cap = self.cap
        if cap is None:
            return
        self.popup.hide()
        self._save_history(text, "accepted")
        self._thread(self._paste_worker, self.job, cap, text)

    def _paste_worker(self, job: int, cap: capture.Capture, text: str) -> None:
        try:
            how = capture.put_text(cap, text, self.settings.restore_clipboard,
                                   self.settings.paste_restore_delay_ms)
            self._post("pasted", job, how)
        except Exception as e:
            log.exception("paste failed")
            try:
                winapi.set_clipboard_text(text)
                self._post("fail", job, f"Couldn't paste ({e}). The prompt is on your clipboard - press Ctrl+V.")
            except Exception:
                self._post("fail", job, f"Couldn't paste: {e}")

    def _on_pasted(self, job: int, how: str) -> None:
        self.busy = False
        if how == "copied":
            self.toast.show("✓ Improved prompt copied — press Ctrl+V to paste it.", kind="ok", duration_ms=4000)
        else:
            self.toast.show("✓ Prompt improved  (Ctrl+Z to undo)", kind="ok", duration_ms=1800)

    def _cancel(self) -> None:
        self.job += 1  # ignore any answer still on its way
        self.busy = False
        self.popup.hide()
        if self.cap and self.cap.hwnd:
            winapi.focus_window(self.cap.hwnd, timeout=0.3)

    def _regenerate(self, style: str) -> None:
        if not self.cap:
            return
        self.style = style
        self.job += 1
        self.popup.show_loading(self.cap.text, style, reposition=False)
        self._thread(self._llm_worker, self.job, self.cap.text, style)

    def _copy(self, text: str) -> None:
        winapi.set_clipboard_text(text)
        self._save_history(text, "copied")
        self._cancel()
        self.toast.show("✓ Copied to clipboard", kind="ok", duration_ms=1800)

    # tray / misc ---------------------------------------------------------------
    def _on_toggle_preview(self) -> None:
        self.settings.preview = not self.settings.preview
        self.toast.show(f"Preview {'ON' if self.settings.preview else 'OFF (pastes directly)'}", duration_ms=1800)

    def _on_toggle_auto_prompt(self) -> None:
        enabled = not self.settings.auto_prompt
        try:
            path = ensure_config_file()
            text = path.read_text(encoding="utf-8")
            path.write_text(set_auto_prompt(text, enabled), encoding="utf-8")
        except (ConfigError, OSError) as e:
            self.toast.show(f"Couldn't save Auto prompt: {e}", kind="error", duration_ms=6000)
            return
        self.settings.auto_prompt = enabled
        self._sync_watcher()
        if enabled:
            self.toast.show("Auto prompt ON. Click the dot when your prompt is ready.", duration_ms=2600)
        else:
            self.toast.show("Auto prompt OFF", duration_ms=1800)

    def _sync_watcher(self) -> None:
        self._stop_watcher()
        if not self.settings.auto_prompt:
            self._hide_dot()
            return
        self._show_dot()

    def _show_dot(self) -> None:
        if self.dot is None:
            return
        saved = load_dot_position(DOT_PATH)
        if saved is None:
            area = winapi.work_area_at(0, 0)
            saved = default_dot_origin(area, FloatDot.SIZE, FloatDot.SIZE)
        self.dot.show(*saved)

    def _hide_dot(self) -> None:
        if self.confirm:
            self.confirm.hide()
        self._drop_pending()
        if self.dot:
            self.dot.hide()

    def _save_dot(self, x: int, y: int) -> None:
        try:
            save_dot_position(DOT_PATH, x, y)
        except OSError:
            log.exception("Couldn't save the dot position")

    def _on_dot_click(self) -> None:
        from .watch import capture_focused_agent

        if self.busy:
            self.toast.show("Still enhancing the previous prompt.", duration_ms=2000)
            return
        self._drop_pending()
        self._dot_hwnd = winapi.foreground_window()
        element, labels, kind, uia = capture_focused_agent()
        if element:
            self._pending = (element, labels, kind, uia)
        elif uia is not None:
            uia.close()
        if self.confirm and self.dot:
            self.confirm.show_beside(*self.dot.bounds())

    def _stop_watcher(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    def _drop_pending(self) -> None:
        pending = self._pending
        self._pending = None
        if not pending or not pending[0]:
            return
        from .watch import _release

        _release(pending[0])
        if pending[3] is not None:
            pending[3].close()

    def _take_pending(self):
        pending = self._pending
        self._pending = None
        if not pending:
            return None, None, ""
        self._capture_uia = pending[3]
        return pending[0], pending[1], pending[2]

    def _close_capture_uia(self) -> None:
        uia = self._capture_uia
        self._capture_uia = None
        if uia is not None:
            uia.close()

    def _on_auto_yes(self) -> None:
        if self.confirm:
            self.confirm.hide()
        if self.busy:
            self._drop_pending()
            return
        element, labels, kind = self._take_pending()
        image = self._grab_screen_hiding_dot()
        self.busy = True
        self.busy_since = time.monotonic()
        self.job += 1
        self.toast.show("✨ Enhancing your prompt…", duration_ms=0)
        if not element or labels is None or not kind:
            from .watch import _release

            _release(element)
            self._thread(self._auto_keyboard_worker, self.job, self._dot_hwnd, image)
            return
        self._thread(self._auto_worker, self.job, element, labels, kind, image)

    def _grab_screen_hiding_dot(self) -> bytes | None:
        """Hide the dot, photograph the monitor, then put the dot back."""
        show_again = bool(self.settings.auto_prompt and self.dot)
        if self.confirm:
            self.confirm.hide()
        if self.dot:
            self.dot.hide()
        try:
            self.root.update()
            time.sleep(0.12)
            from .screen import grab_foreground_jpeg

            hwnd = self._dot_hwnd or winapi.foreground_window()
            return grab_foreground_jpeg(hwnd)
        except Exception:
            log.exception("Couldn't capture the screen")
            return None
        finally:
            if show_again:
                self._show_dot()

    def _auto_keyboard_worker(self, job: int, hwnd: int, image: bytes | None) -> None:
        """Read and replace a box the usual way when it is not a plain text control.

        Chat apps such as WhatsApp draw their own message box. Selecting that
        box and copying it still reaches the text.
        """
        try:
            if hwnd:
                winapi.focus_window(hwnd)
                time.sleep(0.15)
            try:
                cap = capture.grab_text(
                    self.settings.auto_select_all, self.settings.blocked_apps, self.settings.restore_clipboard,
                )
                draft = cap.text
            except capture.CaptureError:
                if not image:
                    raise
                hwnd, proc, title = capture.target_info()
                cap = capture.Capture(hwnd, proc, title, "", "all")
                draft = ""
            res = llm.enhance_screen(
                draft, image, self.settings.providers, window_title=cap.title,
                timeout=max(self.settings.timeout_seconds, 45),
            )
            how = self._paste_back(cap.hwnd or hwnd, res.text)
            self.cap = cap
            self._post("pasted", job, how)
        except (capture.CaptureError, winapi.ClipboardBusy) as e:
            self._post("fail", job, str(e))
        except llm.LLMError as e:
            self._post("fail", job, str(e))
        except Exception as e:
            log.exception("auto prompt keyboard path failed")
            self._post("fail", job, f"Couldn't rewrite the prompt: {e}")

    def _auto_worker(self, job: int, element, labels, kind: str, image: bytes | None) -> None:
        from .agent_text import AgentTextError, read_agent_text, write_agent_text
        from .watch import COINIT_MULTITHREADED, _release, ole32

        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        try:
            try:
                text = read_agent_text(element, labels, kind)
            except AgentTextError as e:
                if not image or "Nothing to rewrite" not in str(e):
                    raise
                text = ""
            title = winapi.window_title(self._dot_hwnd) if self._dot_hwnd else ""
            res = llm.enhance_screen(
                text, image, self.settings.providers, window_title=title,
                timeout=max(self.settings.timeout_seconds, 45),
            )
            how = write_agent_text(element, labels, kind, res.text)
            log.info("Auto prompt %s", how)
            if how != "written":
                how = self._paste_back(self._dot_hwnd, res.text)
            self._post("auto_done", job, how, res)
        except AgentTextError as e:
            self._post("fail", job, str(e))
        except llm.LLMError as e:
            self._post("fail", job, str(e))
        except Exception as e:
            log.exception("auto prompt failed")
            self._post("fail", job, f"Couldn't rewrite the prompt: {e}")
        finally:
            _release(element)
            ole32.CoUninitialize()

    def _paste_back(self, hwnd: int, text: str) -> str:
        """Replace the box the draft was copied from with the generated prompt."""
        if not hwnd:
            hwnd = winapi.foreground_window()
        cap = capture.Capture(hwnd=hwnd, process="", title="", text="", mode="all")
        how = capture.put_text(
            cap, text, self.settings.restore_clipboard, max(self.settings.paste_restore_delay_ms, 1200),
        )
        if how == "copied":
            time.sleep(0.3)
            how = capture.put_text(
                cap, text, self.settings.restore_clipboard, max(self.settings.paste_restore_delay_ms, 1200),
            )
        return "written" if how == "pasted" else how

    def _on_auto_done(self, job: int, how: str, res: llm.Result) -> None:
        if job != self.job:
            return
        self.busy = False
        self._close_capture_uia()
        self.last_result = res
        self.toast.hide()
        if how == "copied":
            self.toast.show("✓ Improved prompt copied — press Ctrl+V to paste it.", kind="ok", duration_ms=4000)
        else:
            self.toast.show("✓ Prompt improved", kind="ok", duration_ms=1800)

    def _on_auto_no(self) -> None:
        if self.confirm:
            self.confirm.hide()
        self._drop_pending()

    def _on_reload(self) -> None:
        try:
            new = load_settings()
        except ConfigError as e:
            self.toast.show(str(e), kind="error", duration_ms=8000)
            return
        self.settings = new
        self.style = new.default_style
        self._build_popup()
        self._sync_watcher()
        n = len(new.usable_providers)
        self.toast.show(f"Config reloaded · {n} provider(s) ready", kind="ok" if n else "error")

    # API key window ------------------------------------------------------------
    def _on_open_keys(self) -> None:
        if self.key_window:
            self.key_window.win.lift()
            self.key_window.win.focus_force()
            return
        self.key_window = KeySetupWindow(self.root,
                                         on_submit=lambda keys: self._thread(self._keys_worker, keys),
                                         on_close=self._on_key_window_closed)

    def _on_key_window_closed(self) -> None:
        self.key_window = None

    def _keys_worker(self, keys: dict[str, str]) -> None:
        """Try each key with one tiny request before saving it."""
        errors = []
        by_name = {p.name: p for p in self.settings.providers}
        for name, key in keys.items():
            if not key:
                continue
            p = by_name.get(name)
            if p is None:
                errors.append(f"{name}: missing from config.toml")
                continue
            try:
                llm.enhance("say hi", [dataclasses.replace(p, api_key=key)], timeout=20)
            except llm.LLMError as e:
                msg = str(e).replace("All providers failed:\n- ", "").replace(" Check the key in config.toml.", "")
                errors.append(msg)
        self._post("keys_tested", keys, errors)

    def _on_keys_tested(self, keys: dict[str, str], errors: list[str]) -> None:
        if not self.key_window:
            return
        if errors:
            self.key_window.show_status("That key didn't work:\n" + "\n".join(errors), error=True)
            return
        try:
            save_provider_keys(keys)
        except (ConfigError, OSError) as e:
            self.key_window.show_status(f"Couldn't save: {e}", error=True)
            return
        log.info("Saved API keys for: %s", ", ".join(n for n, k in keys.items() if k))
        self.key_window.close()
        self._on_reload()
        self.toast.show("All set. Click the dot when your draft is ready.",
                        kind="ok", duration_ms=6000)

    def _on_quit(self) -> None:
        self.root.quit()

    def _save_history(self, text: str, action: str) -> None:
        if not self.settings.save_history or not self.cap:
            return
        r = self.last_result
        history.append(HISTORY_PATH, original=self.cap.text, enhanced=text, style=self.style,
                       provider=r.provider if r else "", model=r.model if r else "",
                       app=self.cap.process, seconds=r.seconds if r else 0, action=action)

    def _open(self, path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
            if str(path).endswith((".toml", ".log", ".jsonl")):
                subprocess.Popen(["notepad.exe", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as e:
            log.exception("open failed")
            self._post("fail", self.job, f"Couldn't open {path}: {e}")

    def _shutdown(self) -> None:
        self._hide_dot()
        self._stop_watcher()
        if self.listener:
            self.listener.stop()
        if self.tray:
            self.tray.stop()
