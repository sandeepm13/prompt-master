"""
The whole app:

  1. A dot floats on screen.
  2. The user types a prompt in any input box, then clicks the dot.
  3. "Regenerate the prompt?"  Yes -> the box text is replaced with a better prompt.
  4. An Undo label appears next to the dot. Clicking it puts the user's own text back.

Ctrl+Alt+P (configurable) does steps 2-3 in one go, without the popup.

Only the main thread touches tkinter. Workers post events to self.events,
and the main thread handles them every 40 ms.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import tkinter as tk

from . import box, context, focus, llm, winapi
from .config import CONFIG_PATH, DATA_DIR, ConfigError, Settings, load_settings, save_provider
from .hotkey import HotkeyListener, parse_hotkey
from .tray import Tray
from .ui import (
    ConfirmPopup, FloatDot, KeySetupWindow, NoteEditor, Toast,
    default_dot_origin, load_dot_position, save_dot_position,
)

log = logging.getLogger(__name__)
LOG_PATH = DATA_DIR / "master-prompt.log"
DOT_PATH = DATA_DIR / "float-dot.json"
NOTES_PATH = DATA_DIR / "context-notes.json"   # "My context", saved per project / website / app

UNDO_CHECK_MS = 1000  # while Undo is shown: has the generated prompt been sent?
TERMINAL_MESSAGE ="This doesn't work in a terminal: Ctrl+C there stops the running command."


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.events: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()  # no main window; only the dot and small popups
        self.toast = Toast(self.root)
        self.dot = FloatDot(self.root, on_click=self._on_dot_click, on_place=self._save_dot,
                            on_undo=self._on_undo)
        self.confirm = ConfirmPopup(self.root, on_yes=self._on_yes, on_no=self._on_no,
                                    on_edit_note=self._on_edit_note, on_clear_note=self._on_clear_note)
        self.key_window: KeySetupWindow | None = None
        self.note_editor: NoteEditor | None = None
        self.tray: Tray | None = None
        self.hotkey: HotkeyListener | None = None        # None: shortcut off, or another app owns it

        self.busy = False
        self.target = 0                                  # window the dot was clicked for
        self.context: context.Context | None = None      # where that window is, worked out on each click
        self.last: tuple[int, str, str] | None = None    # (window, original, generated) for Undo
        self.undo_box: tuple[str, str] | None = None     # (class, role) of the box the generated prompt was seen in
        self.undo_checking = False

    # ------------------------------------------------------------------ setup
    def run(self) -> None:
        self.tray = Tray({
            "api_keys": lambda: self._post("open_keys"),
            "open_config": lambda: self._open(CONFIG_PATH),
            "open_log": lambda: self._open(LOG_PATH),
            "reload": lambda: self._post("reload"),
            "quit": lambda: self._post("quit"),
        })
        self.tray.start()
        self._show_dot()
        hotkey_problem = self._start_hotkey()
        if not self.settings.usable_providers:
            self._on_open_keys()
        elif hotkey_problem:
            self._error(hotkey_problem)
        else:
            self.toast.show(f"dotix is running.\n{self._how_to()}", duration_ms=4000)
        self.root.after(40, self._pump)
        self.root.after(UNDO_CHECK_MS, self._undo_check_tick)
        try:
            self.root.mainloop()
        finally:
            self._stop_hotkey()
            if self.tray:
                self.tray.stop()

    def _how_to(self) -> str:
        if self.hotkey:
            return f"Type in any box, then click the dot or press {self.hotkey.hotkey.label}."
        return "Type in any box, then click the dot."

    def _start_hotkey(self) -> str:
        """Register the regenerate shortcut. Returns a message for the user if it can't be used."""
        text = self.settings.regenerate_hotkey
        if not text:
            return ""
        hk = parse_hotkey(text)  # already checked when config.toml was loaded
        listener = HotkeyListener(hk, on_press=lambda: self._post("hotkey"))
        listener.start()
        if not listener.registered:
            return (f"{hk.label} is already used by another app.\n"
                    "Pick another shortcut in config.toml, then reload it from the tray.")
        self.hotkey = listener
        return ""

    def _stop_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None

    def _show_dot(self) -> None:
        saved = load_dot_position(DOT_PATH)
        if saved is None:
            saved = default_dot_origin(winapi.work_area_at(0, 0), FloatDot.SIZE, FloatDot.SIZE)
        self.dot.show(*saved)

    def _save_dot(self, x: int, y: int) -> None:
        try:
            save_dot_position(DOT_PATH, x, y)
        except OSError:
            log.exception("Couldn't save the dot position")

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
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(40, self._pump)

    def _set_busy(self, busy: bool) -> None:
        """Working on a prompt (or an undo): the dot shows a spinner meanwhile."""
        self.busy = busy
        self.dot.set_busy(busy)

    def _thread(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _on_error(self, message: str) -> None:
        self._error(message)

    def _error(self, message: str) -> None:
        log.warning("Shown to user: %s", message)
        self.toast.show(message, kind="error", duration_ms=5000)

    # ------------------------------------------------------- dot -> Yes -> rewrite
    def _on_dot_click(self) -> None:
        hwnd = self._typing_window("Click in the input box with your prompt, then click the dot.")
        if not hwnd:
            return
        self._close_note_editor()  # an open editor belongs to the previous place
        self.target = hwnd
        self.context = self._detect_context(hwnd)
        self._show_context()
        self.confirm.show_beside(*self.dot.bounds())

    def _on_hotkey(self) -> None:
        """The shortcut: clicking the dot and Yes in one step, without the popup."""
        label = self.hotkey.hotkey.label if self.hotkey else "the shortcut"
        hwnd = self._typing_window(f"Click in the input box with your prompt, then press {label}.")
        if not hwnd:
            return
        self._on_no()  # an open popup or note editor belongs to the previous click
        self._start_rewrite(hwnd, self._detect_context(hwnd))

    def _typing_window(self, not_found: str) -> int:
        """The window the user is typing in, or 0 after telling them why it can't be used."""
        if self.busy:
            self.toast.show("Still working on it…", duration_ms=1800)
            return 0
        # The dot never takes focus, and the shortcut doesn't move it,
        # so the window in front is the one being typed in.
        hwnd = winapi.foreground_window()
        if not hwnd or winapi.window_pid(hwnd) == os.getpid():
            self._error(not_found)
            return 0
        if winapi.process_name(hwnd).lower() in self.settings.blocked_apps:
            self._error(TERMINAL_MESSAGE)
            return 0
        if focus.cursor_in_code(hwnd):
            self._error(focus.CODE_MESSAGE)
            return 0
        return hwnd

    def _detect_context(self, hwnd: int) -> context.Context:
        # Fresh every time: switching window, project or tab changes the context.
        return context.detect(hwnd, context.load_notes(NOTES_PATH), read_chat=self.settings.read_chat_messages)

    def _show_context(self) -> None:
        ctx = self.context
        if ctx:
            self.confirm.set_context(ctx.summary(), ctx.note, ctx.scope_name)

    def _on_no(self) -> None:
        self._close_note_editor()
        self.confirm.hide()
        self.target = 0
        self.context = None

    def _on_yes(self) -> None:
        if self.note_editor:
            self.note_editor.save()  # Yes while typing a note: keep what was typed
        self.confirm.hide()
        hwnd, self.target = self.target, 0
        ctx, self.context = self.context or context.Context(), None
        if self.busy or not hwnd:
            return
        self._start_rewrite(hwnd, ctx)

    def _start_rewrite(self, hwnd: int, ctx: context.Context) -> None:
        self._set_busy(True)
        self.dot.set_undo(False)
        self.toast.show("✨ Regenerating your prompt…", duration_ms=0)
        self._thread(self._rewrite_worker, hwnd, self.last, ctx)

    # "My context": one note per project / website / app
    def _on_edit_note(self) -> None:
        ctx = self.context
        if not ctx:
            return
        if self.note_editor:
            self.note_editor.win.lift()
            return
        self.note_editor = NoteEditor(self.root, ctx.scope_name, ctx.note, self.confirm.bounds(),
                                      on_save=self._on_note_saved, on_close=self._on_note_editor_closed)

    def _on_note_editor_closed(self) -> None:
        self.note_editor = None

    def _close_note_editor(self) -> None:
        if self.note_editor:
            self.note_editor.close()

    def _on_note_saved(self, note: str) -> None:
        ctx = self.context
        if not ctx:
            return
        try:
            context.save_note(NOTES_PATH, ctx.scope_key, note)
        except OSError as e:
            self._error(f"Couldn't save your context: {e}")
            return
        ctx.note = note.strip()
        log.info("Saved context note for %s (%s characters)", ctx.scope_key, len(ctx.note))
        self._show_context()

    def _on_clear_note(self) -> None:
        self._close_note_editor()
        self._on_note_saved("")

    def _rewrite_worker(self, hwnd: int, last, ctx: context.Context) -> None:
        try:
            draft = box.read_box(hwnd)
            original = box.original_for_undo(last, hwnd, draft)
            block = ctx.describe(draft) if ctx.app else ""
            log.info("Context layers: %s (%s characters)", ", ".join(ctx.layers(draft)), len(block))
            res = llm.enhance(draft, self.settings.providers, style=self.settings.default_style,
                              custom_styles=self.settings.custom_styles,
                              timeout=self.settings.timeout_seconds,
                              kind=ctx.kind, context=block, session=ctx.in_session)
            box.write_box(hwnd, res.text, self.settings.paste_restore_delay_ms)
            self._post("rewritten", hwnd, original, res.text)
        except (box.BoxError, llm.LLMError, winapi.ClipboardBusy) as e:
            self._post("rewrite_failed", str(e))
        except Exception as e:
            log.exception("Rewrite failed")
            self._post("rewrite_failed", f"Couldn't regenerate the prompt: {e}")

    def _on_rewritten(self, hwnd: int, original: str, generated: str) -> None:
        self._set_busy(False)
        self.last = (hwnd, original, generated)
        self.undo_box = None
        self.dot.set_undo(True)
        self.toast.show("✓ Prompt regenerated. Click Undo to get yours back.", kind="ok", duration_ms=2500)

    def _on_rewrite_failed(self, message: str) -> None:
        self._set_busy(False)
        self.dot.set_undo(self.last is not None)
        self._error(message)

    # ------------------------------------------------------------------- undo
    def _on_undo(self) -> None:
        if self.busy or not self.last:
            return
        hwnd, original, _ = self.last
        self._set_busy(True)
        self.dot.set_undo(False)
        self.toast.show("Putting your prompt back…", duration_ms=0)
        self._thread(self._undo_worker, hwnd, original)

    def _undo_worker(self, hwnd: int, original: str) -> None:
        try:
            box.write_box(hwnd, original, self.settings.paste_restore_delay_ms)
            self._post("undone")
        except (box.BoxError, winapi.ClipboardBusy) as e:
            self._post("undo_failed", str(e))
        except Exception as e:
            log.exception("Undo failed")
            self._post("undo_failed", f"Couldn't put your prompt back: {e}")

    def _on_undone(self) -> None:
        self._set_busy(False)
        self.last = None
        self.undo_box = None
        self.toast.show("✓ Your prompt is back", kind="ok", duration_ms=1800)

    def _on_undo_failed(self, message: str) -> None:
        self._set_busy(False)
        self.dot.set_undo(True)  # keep Undo so the user can try again
        self._error(message)

    # Undo goes away once the generated prompt was sent (or replaced): there is nothing left to undo,
    # and pasting the old text back would overwrite what the user typed next.
    def _undo_check_tick(self) -> None:
        last = self.last
        if last and not self.busy and not self.undo_checking:
            hwnd = last[0]
            if not winapi.is_window(hwnd):
                self._on_prompt_used()  # the window is gone
            elif winapi.foreground_window() == hwnd:
                self.undo_checking = True
                self._thread(self._undo_check_worker, last)
        self.root.after(UNDO_CHECK_MS, self._undo_check_tick)

    def _undo_check_worker(self, last) -> None:
        seen = None
        try:
            seen = focus.focused_text()
        finally:
            self._post("undo_checked", last, seen)

    def _on_undo_checked(self, last, seen) -> None:
        self.undo_checking = False
        if last is not self.last or self.busy or seen is None:
            return  # a newer rewrite, or a box whose text can't be read: leave Undo as it is
        class_name, role, text = seen
        generated = last[2]
        if self.undo_box is None:
            # Judge only after seeing the generated prompt in a box, and only that box afterwards,
            # so clicking somewhere else doesn't hide Undo.
            if not box.prompt_was_used(text, generated):
                self.undo_box = (class_name, role)
            return
        if (class_name, role) == self.undo_box and box.prompt_was_used(text, generated):
            self._on_prompt_used()

    def _on_prompt_used(self) -> None:
        log.info("The generated prompt was sent or replaced: Undo hidden")
        self.last = None
        self.undo_box = None
        self.dot.set_undo(False)

    # ------------------------------------------------------------ tray / keys
    def _on_reload(self) -> None:
        old_hotkey = self.settings.regenerate_hotkey
        try:
            self.settings = load_settings()
        except ConfigError as e:
            self._error(str(e))
            return
        # Also retry when it failed before: the app that owned the shortcut may be closed now.
        if self.settings.regenerate_hotkey != old_hotkey or not self.hotkey:
            self._stop_hotkey()
            problem = self._start_hotkey()
            if problem:
                self._error(problem)
                return
        n = len(self.settings.usable_providers)
        self.toast.show(f"Config reloaded · {n} provider(s) ready", kind="ok" if n else "error")

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
        """Try the key with one tiny request before saving it."""
        probe = llm.Provider(name=spec["name"], base_url=spec["base_url"], model=spec["model"],
                             api_key=spec.get("api_key", ""))
        error = ""
        try:
            llm.enhance("say hi", [probe], timeout=20)
        except llm.LLMError as e:
            error = str(e).replace("All providers failed:\n- ", "").replace(" Check the key in config.toml.", "")
        self._post("keys_tested", spec, error)

    def _on_keys_tested(self, spec: dict[str, str], error: str) -> None:
        if not self.key_window:
            return
        if error:
            self.key_window.show_status("That key didn't work:\n" + error, error=True)
            return
        try:
            save_provider(spec)
        except (ConfigError, OSError) as e:
            self.key_window.show_status(f"Couldn't save: {e}", error=True)
            return
        log.info("Saved API provider: %s", spec.get("name", ""))
        self.key_window.close()
        self._on_reload()
        self.toast.show(f"All set. {self._how_to()}", kind="ok", duration_ms=5000)

    def _on_quit(self) -> None:
        self.root.quit()

    def _open(self, path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
            subprocess.Popen(["notepad.exe", str(path)])
        except Exception as e:
            log.exception("open failed")
            self._post("error", f"Couldn't open {path}: {e}")
