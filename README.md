# Master Prompt

Master Prompt is a Windows tray app that rewrites a rough draft into a clear prompt and puts the result back in the same text box. It works in any app that has a text field, including chat composers and search boxes.

A small draggable dot stays on screen. Click it, confirm, and the draft is rewritten. A draft that already says what to do is sent as text. A screenshot is taken only when the draft is short and points at the screen, such as "fix this". An empty box is never photographed. The result is an improved prompt, not a description of the screen.

When a screenshot is sent, it goes to a vision-capable model. If that model cannot accept an image, the rewrite uses the draft alone.

## Requirements

- Windows 10 or later
- Python 3.11 or newer, with tkinter (included in the python.org installer)
- An API key for any service that speaks the OpenAI chat API, such as Groq, Gemini, OpenAI, or OpenRouter. A local server such as Ollama does not need a key.

`--check` and `--test` can run on other operating systems. The tray app and the dot require Windows.

## Installation

1. Install Python from [python.org](https://www.python.org/downloads/) and select **Add python.exe to PATH**.
2. Double-click `setup.bat`. It creates `.venv` and installs dependencies.
3. Double-click `run.bat`. A purple icon appears in the system tray, sometimes under the overflow arrow. If no key is saved yet, the app asks you to enter an API key. It detects the service from the key, tests it, and saves it in `config.toml` on this PC.
4. Double-click `test-key.bat` if you want to check the key from a terminal. A rewritten sample means it works.

From a terminal, after setup:

```bat
.venv\Scripts\python.exe -m master_prompt --check
.venv\Scripts\python.exe -m master_prompt
```

Only one instance runs at a time. Starting it again shows a message that it is already running.

## Usage

Turn Auto prompt on from the tray menu. A round dot appears. Drag it anywhere. Click in the text box, then click the dot when the draft is ready.

**Yes** replaces the focused text. A complete draft skips the screenshot. A short draft that points at the screen hides the dot, photographs the monitor, and shows the dot again. **No** leaves the draft unchanged.

If the box is empty, nothing is generated and no screenshot is taken. A message asks you to type your prompt first. Grey hint text such as "Ask anything" or "[ctrl esc to focus or unfocus Claude]" counts as empty.

If you do not want the new prompt, press Enter or click **Undo**. Your previous text goes back into the same box, and the undo box goes away. Shift+Enter still adds a line and does not restore.

### Keyboard shortcuts

Both shortcuts work in any app, with or without the dot. They also work when Auto prompt is off and the dot is hidden.

| Shortcut | Action |
| --- | --- |
| Ctrl+Alt+P | Rewrite the text in the focused box. This is the same as clicking the dot and pressing Yes, without the confirm step. |
| Ctrl+Alt+Z | Undo the last rewrite and put your previous text back. Only the last rewrite is remembered, and it is forgotten when you quit. |

Change or turn off a shortcut in `config.toml`:

```toml
[hotkeys]
auto = "ctrl+alt+p"   # set to "" to turn off
undo = "ctrl+alt+z"   # set to "" to turn off
```

If another app already uses a shortcut, a message says so. Pick a different one and reload the config from the tray.

The focused control must be a text box. Buttons, file tabs, and trees are left alone. If the paste cannot focus the original window, the improved prompt stays on the clipboard for Ctrl+V.

### Tray menu

Right-click the icon to turn Auto prompt on or off, edit API keys, open or reload `config.toml`, open history or the log, and quit.

History is stored in `data/history.jsonl`. The log is `data/master-prompt.log`. For a visible console, use `run-debug.bat`.

## Configuration

Settings live in `config.toml`. Reload them from the tray after editing. `config.toml` is not committed; start from `config.example.toml`.


| Setting                      | Purpose                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `behavior.auto_prompt`       | Show the draggable dot. This is the only way to rewrite a box.                       |
| `behavior.restore_clipboard` | Put the previous clipboard contents back after a paste.                              |
| `behavior.timeout_seconds`   | Time limit per provider.                                                             |
| `behavior.save_history`      | Keep before/after pairs in `data/history.jsonl`.                                     |
| `[[providers]]`              | Models tried from top to bottom. A later provider is used when an earlier one fails. |


`config.example.toml` includes Groq and Gemini as examples. OpenRouter and a local Ollama server are in the comments. A local `base_url` does not need an API key. The tray command **API keys…** asks for a key and detects Groq, Gemini, OpenAI, OpenRouter, or Anthropic from it.

Add a style with a section such as:

```toml
[styles.linkedin]
label = "LinkedIn"
instructions = "Ask for a hook, short lines, and a call to action."
```

Auto prompt follows `SCREEN_SYSTEM_PROMPT` in `master_prompt/prompts.py`.

