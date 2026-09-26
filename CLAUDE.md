# CLAUDE.md — Master Prompt

Windows tray app. Global hotkey → grab text from the focused text box (clipboard + synthetic Ctrl+C/A) →
OpenAI-compatible LLM (Groq, then Gemini as fallback) → tkinter preview popup → paste back (Ctrl+V) → restore clipboard.

## Owner
Sandeep is a beginner. Explain changes in simple English and explain every technical term you use.

## Run / test
- Setup: `setup.bat` (creates `.venv`, installs `pystray` + `Pillow`, copies `config.example.toml` → `config.toml`)
- Run: `run.bat` (pythonw, no console) · `run-debug.bat` (with console)
- Check API keys without hotkeys: `.venv\Scripts\python -m master_prompt --check` / `--test "text" --style coding`
- Tests: `.venv\Scripts\python -m pytest -q` (only pure-Python parts; they also pass on Linux)
- Log: `data/master-prompt.log` · history: `data/history.jsonl`

## Architecture rules
- **Only the main thread touches tkinter.** Worker/hotkey/tray threads call `App._post(kind, *args)`;
  `App._pump` dispatches to `App._on_<kind>` every 40 ms.
- Every request bumps `App.job`; results carrying an old job id are ignored (cancel/retry safety).
- `winapi.py` is the ONLY file that uses ctypes. Every Win32 function must declare `argtypes`/`restype`
  (64-bit handle truncation otherwise).
- Before sending any keys after the hotkey, call `winapi.wait_for_modifiers_released()`.
  Otherwise Win+Shift stays held, and Ctrl+C becomes Win+Shift+Ctrl+C.
- Don't tap Alt to get foreground rights (it opens menus in some apps). Use the 0xE8 mask key and the AttachThreadInput method (see `focus_window`).
- `llm.py` uses stdlib `urllib` only, and every provider is OpenAI-compatible (`/chat/completions`).
- Prompt quality lives in `prompts.py` (`BASE_SYSTEM_PROMPT`, `BUILTIN_STYLES`, `clean_output`). Add a test case in
  `tests/test_core.py::test_clean_output` for any new output-cleaning rule.
- Python 3.11+ (uses `tomllib`).

## Known limitations / ideas (roadmap)
- Clipboard save/restore handles text only (images on the clipboard are lost during a run).
- Can't type into apps running as Administrator unless Master Prompt also runs elevated (Windows UIPI).
- Terminals are blocked for the main hotkey (Ctrl+C = SIGINT); use the clipboard hotkey there.
- Ideas: streaming tokens into the popup; UI Automation (`IUIAutomation` ValuePattern) to read/write text boxes
  without the clipboard; per-app default style (e.g. VS Code → coding); PyInstaller single .exe; settings window.
