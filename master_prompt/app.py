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
    save_provider, set_auto_prompt,
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
        self._restore: tuple[int, str, int] | None = None
        self._undo_element = None
        self._enter = None
        self._capture_uia = None
        self.key_window: KeySetupWindow | None = None
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
        from .watch import COINIT_MULTITHREADED, ole32

        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
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
        self._start_hotkeys()

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
        self.dot = FloatDot(self.root, on_click=self._on_dot_click, on_place=self._save_dot,
                            on_undo=self._on_undo)

    def _start_hotkeys(self) -> None:
        if self.listener:
            self.listener.stop()
            self.listener = None
        text = self.settings.hotkey_auto.strip()
        if not text:
            return
        try:
            parsed = {"auto": [parse_hotkey(text)]}
            if self.settings.hotkey_undo.strip():
                parsed["undo"] = [parse_hotkey(self.settings.hotkey_undo.strip())]
        except HotkeyError as e:
            self.toast.show(f"Bad hotkey in config.toml: {e}", kind="error", duration_ms=8000)
            return
        self.listener = HotkeyListener(parsed, lambda name: self._post("hotkey", name))
        self.listener.start()
        if self.listener.failed:
            self.toast.show(f"{_pretty(text)} is already used by another app.\nPick a different one in config.toml.",
                            kind="error", duration_ms=9000)

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
        """Ctrl+Alt+P: the same as clicking the dot and pressing Yes. Ctrl+Alt+Z: undo."""
        if name == "undo":
            if self._restore and not self.busy:
                self._on_undo()
            elif not self.busy:
                self.toast.show("Nothing to undo.", duration_ms=1800)
            return
        if self.busy:
            self.toast.show("Still enhancing the previous prompt.", duration_ms=2000)
            return
        self._begin_capture()
        self._on_auto_yes()

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
        self._show_undo_arrow()

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
            self.toast.show("✓ Prompt improved", kind="ok", duration_ms=1800)

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
            self._disarm_enter()
            return
        self._show_dot()
        self._show_undo_arrow()

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
        if self.busy:
            self.toast.show("Still enhancing the previous prompt.", duration_ms=2000)
            return
        self._begin_capture()
        if self.confirm and self.dot:
            self.confirm.show_beside(*self.dot.bounds())

    def _begin_capture(self) -> None:
        from .watch import capture_focused_agent

        self._drop_pending()
        self._dot_hwnd = winapi.foreground_window()
        element, labels, kind, uia = capture_focused_agent()
        if element:
            self._pending = (element, labels, kind, uia)
        elif uia is not None:
            uia.close()

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
        self._input_hwnd = winapi.focused_child(self._dot_hwnd or winapi.foreground_window())
        if self.dot:
            self.dot.set_undo(False)
        self.busy = True
        self.busy_since = time.monotonic()
        self.job += 1
        self.toast.show("✨ Enhancing your prompt…", duration_ms=0)
        if not element or labels is None or not kind:
            from .watch import _release

            _release(element)
            self._thread(self._auto_keyboard_worker, self.job, self._dot_hwnd)
            return
        self._thread(self._auto_worker, self.job, element, labels, kind)

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

    def _image_for_draft(self, draft: str) -> bytes | None:
        """Photograph only when the typed text is not enough on its own."""
        from .prompts import draft_needs_screen

        if not draft.strip():
            return None  # an empty box is reported to the user; never photograph the screen for it
        if not draft_needs_screen(draft):
            log.info("Skipping the screenshot; the draft is enough")
            return None
        log.info("Photographing the screen because the draft needs it")
        return self._grab_screen_on_ui_thread()

    def _grab_screen_on_ui_thread(self) -> bytes | None:
        if threading.current_thread() is threading.main_thread():
            return self._grab_screen_hiding_dot()
        done = threading.Event()
        holder: list[bytes | None] = []
        self._post("capture_screen", holder, done)
        if not done.wait(8):
            log.warning("The screenshot did not finish")
            return None
        return holder[0] if holder else None

    def _on_capture_screen(self, holder: list, done: threading.Event) -> None:
        try:
            holder.append(self._grab_screen_hiding_dot())
        finally:
            done.set()

    def _auto_keyboard_worker(self, job: int, hwnd: int) -> None:
        """Read and replace a box the usual way when it is not a plain text control.

        Chat apps such as WhatsApp draw their own message box. Selecting that
        box and copying it still reaches the text.
        """
        try:
            if hwnd:
                winapi.focus_window(hwnd)
                time.sleep(0.15)
            missed = None
            try:
                cap = capture.grab_text(
                    self.settings.auto_select_all, self.settings.blocked_apps, self.settings.restore_clipboard,
                )
                draft = cap.text
            except capture.CaptureError as e:
                missed = e
                hwnd, proc, title = capture.target_info()
                cap = capture.Capture(hwnd, proc, title, "", "all")
                draft = ""
            log.info("Copied %s characters from the box", len(draft))
            if capture.is_placeholder(draft):
                if missed and str(missed) != capture.EMPTY_BOX_MESSAGE:
                    raise missed
                self._post("fail", job, capture.EMPTY_BOX_MESSAGE)
                return
            image = self._image_for_draft(draft)
            res = llm.enhance_screen(
                draft, image, self.settings.providers, window_title=cap.title,
                timeout=max(self.settings.timeout_seconds, 45),
            )
            how = self._paste_back(cap.hwnd or hwnd, res.text)
            self.cap = cap
            self._post("auto_done", job, how, res, draft, cap.hwnd or hwnd, None)
        except (capture.CaptureError, winapi.ClipboardBusy) as e:
            self._post("fail", job, str(e))
        except llm.LLMError as e:
            self._post("fail", job, str(e))
        except Exception as e:
            log.exception("auto prompt keyboard path failed")
            self._post("fail", job, f"Couldn't rewrite the prompt: {e}")

    def _auto_worker(self, job: int, element, labels, kind: str) -> None:
        from .agent_text import AgentTextError, read_agent_text, write_agent_text
        from .watch import COINIT_MULTITHREADED, _release, ole32

        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        try:
            try:
                text = read_agent_text(element, labels, kind)
            except AgentTextError as e:
                if "Nothing to rewrite" not in str(e):
                    raise
                text = ""
            log.info("Read %s characters from the box (%s)", len(text), kind)
            # Many apps expose the grey hint ("Ask anything") as the box text; that is not a draft.
            if capture.is_placeholder(text, getattr(labels, "name", "")):
                self._post("fail", job, capture.EMPTY_BOX_MESSAGE)
                return
            image = self._image_for_draft(text)
            title = winapi.window_title(self._dot_hwnd) if self._dot_hwnd else ""
            res = llm.enhance_screen(
                text, image, self.settings.providers, window_title=title,
                timeout=max(self.settings.timeout_seconds, 45),
            )
            how = write_agent_text(element, labels, kind, res.text)
            log.info("Auto prompt %s", how)
            if how != "written":
                how = self._paste_back(self._dot_hwnd, res.text)
            self._post("auto_done", job, how, res, text, self._dot_hwnd, element)
            element = None
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

    def _on_auto_done(self, job: int, how: str, res: llm.Result, original: str, hwnd: int,
                      element=None) -> None:
        if job != self.job:
            self._release_undo_element(element)
            return
        self.busy = False
        self._close_capture_uia()
        self.last_result = res
        self.toast.hide()
        self._restore = (int(hwnd or 0), original, int(getattr(self, "_input_hwnd", 0) or 0))
        self._release_undo_element(self._undo_element)
        self._undo_element = element
        log.info("Undo armed with %s characters of the original draft", len(original or ""))
        self._show_undo_arrow()
        undo_key = _pretty(self.settings.hotkey_undo) if self.settings.hotkey_undo.strip() else ""
        hint = f"Press {undo_key} to undo." if undo_key else "Click Undo to put your text back."
        self.toast.show(f"Prompt improved. {hint}", kind="ok", duration_ms=3500)

    def _release_undo_element(self, element) -> None:
        if not element:
            return
        from .watch import _release

        _release(element)

    def _show_undo_arrow(self) -> None:
        show = self._restore is not None and self.settings.auto_prompt
        if self.dot:
            self.dot.set_undo(show)
        if show:
            self._arm_enter()
        else:
            self._disarm_enter()

    def _arm_enter(self) -> None:
        if self._enter is not None:
            return
        from .watch import EnterSignal

        self._enter = EnterSignal(lambda: self._post("prompt_used"))
        self._enter.start()

    def _disarm_enter(self) -> None:
        watcher = self._enter
        self._enter = None
        if watcher is not None:
            watcher.stop()

    def _on_prompt_used(self) -> None:
        """Enter puts the user's own text back into the same box."""
        self._on_undo()

    def _on_undo(self) -> None:
        if self.busy or not self._restore:
            return
        hwnd, text, focus = self._restore
        element = self._undo_element
        self._restore = None
        self._undo_element = None
        self._disarm_enter()
        if self.dot:
            self.dot.set_undo(False)
        self.busy = True
        self.busy_since = time.monotonic()
        self.job += 1
        self.toast.show("Restoring your text…", duration_ms=0)
        self._thread(self._undo_worker, self.job, hwnd, text, focus, element)

    def _undo_worker(self, job: int, hwnd: int, text: str, focus: int, element) -> None:
        from .agent_text import restore_element_text
        from .watch import COINIT_MULTITHREADED, _release, ole32

        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        try:
            how = restore_element_text(element, text) if element is not None else "copied"
            if how != "written":
                winapi.focus_control(hwnd, focus)
                time.sleep(0.2)
                if winapi.replace_edit_text(focus, text):
                    how = "written"
                else:
                    how = self._paste_back(hwnd, text)
            log.info("Undo %s (%s characters)", how, len(text or ""))
            self._post("undo_done", job, how, hwnd, text, focus)
        except Exception:
            log.exception("undo failed")
            self._post("undo_done", job, "copied", hwnd, text, focus)
        finally:
            _release(element)
            ole32.CoUninitialize()

    def _on_undo_done(self, job: int, how: str, hwnd: int, text: str, focus: int) -> None:
        if job != self.job:
            return
        self.busy = False
        self.toast.hide()
        if how == "copied":
            self._restore = (hwnd, text, focus)
            self._show_undo_arrow()
            self.toast.show("Your text is on the clipboard. Click the box and press Ctrl+V.",
                            kind="error", duration_ms=4000)
            return
        self.toast.show("Your text is back", kind="ok", duration_ms=1800)

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
        self._start_hotkeys()
        n = len(new.usable_providers)
        self.toast.show(f"Config reloaded · {n} provider(s) ready", kind="ok" if n else "error")

    # API key window ------------------------------------------------------------
    def _on_open_keys(self) -> None:
        if self.key_window:
            self.key_window.win.lift()
            self.key_window.win.focus_force()
            return
        self.key_window = KeySetupWindow(self.root,
                                         on_submit=lambda spec: self._thread(self._keys_worker, spec),
                                         on_close=self._on_key_window_closed)

    def _on_key_window_closed(self) -> None:
        self.key_window = None

    def _keys_worker(self, spec: dict[str, str]) -> None:
        """Try this provider with one tiny request before saving it."""
        errors = []
        probe = llm.Provider(
            name=spec["name"],
            base_url=spec["base_url"],
            model=spec["model"],
            api_key=spec.get("api_key", ""),
        )
        try:
            llm.enhance("say hi", [probe], timeout=20)
        except llm.LLMError as e:
            msg = str(e).replace("All providers failed:\n- ", "").replace(" Check the key in config.toml.", "")
            errors.append(msg)
        self._post("keys_tested", spec, errors)

    def _on_keys_tested(self, spec: dict[str, str], errors: list[str]) -> None:
        if not self.key_window:
            return
        if errors:
            self.key_window.show_status("That key didn't work:\n" + "\n".join(errors), error=True)
            return
        try:
            save_provider(spec)
        except (ConfigError, OSError) as e:
            self.key_window.show_status(f"Couldn't save: {e}", error=True)
            return
        log.info("Saved API provider: %s", spec.get("name", ""))
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
        self._release_undo_element(self._undo_element)
        self._undo_element = None
        self._hide_dot()
        self._disarm_enter()
        self._stop_watcher()
        if self.listener:
            self.listener.stop()
        if self.tray:
            self.tray.stop()
