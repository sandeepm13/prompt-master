# Master Prompt

Master Prompt is a Windows tray app that rewrites a rough draft into a clear prompt and puts the result back in the same text box. It works in any app that has a text field, including chat composers and search boxes.

A small draggable dot stays on screen. Click it, confirm, and the draft is rewritten using the screen as private context. The result is an improved prompt, not a description of the screen.

The screenshot goes to a vision-capable model when one is available, and the rewrite falls back to the draft alone if the provider cannot accept an image.

## Requirements

- Windows 10 or later
- Python 3.11 or newer, with tkinter (included in the python.org installer)
- An API key for at least one provider. [Groq](https://console.groq.com/keys) is the default. [Gemini](https://aistudio.google.com/apikey) is the backup.

`--check` and `--test` can run on other operating systems. The tray app and the dot require Windows.

## Installation

1. Install Python from [python.org](https://www.python.org/downloads/) and select **Add python.exe to PATH**.
2. Double-click `setup.bat`. It creates `.venv`, installs dependencies, and opens `config.toml`.
3. Add a key in `config.toml`, or set an environment variable and restart the app:

   ```toml
   api_key = ""
   api_key_env = "GROQ_API_KEY"
   ```

   ```bat
   setx GROQ_API_KEY "your-key"
   setx GEMINI_API_KEY "your-key"
   ```

4. Double-click `test-key.bat`. A rewritten sample means the key works.
5. Double-click `run.bat`. A purple icon appears in the system tray, sometimes under the overflow arrow.

From a terminal, after setup:

```bat
.venv\Scripts\python.exe -m master_prompt --check
.venv\Scripts\python.exe -m master_prompt
```

Only one instance runs at a time. Starting it again shows a message that it is already running.

## Usage

Turn Auto prompt on from the tray menu. A round dot appears. Drag it anywhere. Click in the text box, then click the dot when the draft is ready.

**Yes** hides the dot, photographs the monitor, shows the dot again, and replaces the focused text. **No** leaves the draft unchanged.

The focused control must be a text box. Buttons, file tabs, and trees are left alone. If the paste cannot focus the original window, the improved prompt stays on the clipboard for Ctrl+V.

### Tray menu

Right-click the icon to turn Auto prompt on or off, edit API keys, open or reload `config.toml`, open history or the log, and quit.

History is stored in `data/history.jsonl`. The log is `data/master-prompt.log`. For a visible console, use `run-debug.bat`.

## Configuration

Settings live in `config.toml`. Reload them from the tray after editing. `config.toml` is not committed; start from `config.example.toml`.

| Setting | Purpose |
|---|---|
| `behavior.auto_prompt` | Show the draggable dot. This is the only way to rewrite a box. |
| `behavior.restore_clipboard` | Put the previous clipboard contents back after a paste. |
| `behavior.timeout_seconds` | Time limit per provider. |
| `behavior.save_history` | Keep before/after pairs in `data/history.jsonl`. |
| `[[providers]]` | Models tried from top to bottom. A later provider is used when an earlier one fails. |

Groq and Gemini are configured by default. OpenRouter and a local Ollama server are included as comments in `config.example.toml`. A local `base_url` does not need an API key.

Add a style with a section such as:

```toml
[styles.linkedin]
label = "LinkedIn"
instructions = "Ask for a hook, short lines, and a call to action."
```

Auto prompt follows `SCREEN_SYSTEM_PROMPT` in `master_prompt/prompts.py`.

## Contributing

1. Run `setup.bat`, or create a virtual environment and install `requirements.txt`.
2. Install pytest: `.venv\Scripts\python.exe -m pip install pytest`
3. Run tests: `.venv\Scripts\python.exe -m pytest -q`

Tests do not call Windows APIs. Use `run-debug.bat` to exercise the dot on a machine.

Do not commit `config.toml`, `.venv/`, or `data/`. Those paths are listed in `.gitignore` and may contain API keys and prompt history.
