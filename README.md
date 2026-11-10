# Master Prompt

Master Prompt rewrites the prompt you typed into a clearer one, in the same input box. It works in any app with a text box: ChatGPT, Claude, WhatsApp, a search box, and so on.

1. A small dot floats on your screen. Drag it anywhere.
2. Type your prompt in any input box.
3. Click the dot. It asks **Regenerate the prompt?**
4. Click **Yes**. Your text in the box is replaced with the improved prompt.
5. Don't like it? Click **Undo** next to the dot. Your original text comes back.

Or skip steps 3 and 4: press **Ctrl+Alt+P** while the caret is in the box.

The app uses the clipboard for a moment to read and replace the text (Ctrl+A, Ctrl+C / Ctrl+V). Whatever you had copied before is put back right away, so neither your draft nor the generated prompt is left on Ctrl+V.

## Requirements

- Windows 10 or later
- Python 3.11 or newer, with tkinter (included in the python.org installer)
- An API key for any service that speaks the OpenAI chat API, such as Groq, Gemini, OpenAI, or OpenRouter. A local server such as Ollama does not need a key.

`--check` and `--test` can run on other operating systems. The dot and the keyboard shortcut require Windows.

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

Click in the input box, type your prompt, then click the dot and press **Yes**. While the prompt is being generated, the text in the box stays selected. Don't type until it is replaced.

**Undo** stays next to the dot until you use it, regenerate again, or send the prompt. Once you send it, or replace it with something else, Undo goes away by itself, since there is nothing left to undo. Small edits to the prompt keep Undo. To tell, the app reads the box's text about once a second with Windows UI Automation (no clipboard, no key presses), only in the window you rewrote in, and only while Undo is shown. That text is never stored or sent. In boxes whose text can't be read this way, Undo stays as before. If you regenerate the generated prompt a second time, Undo still brings back what you originally typed.

If the box is empty, nothing happens and a message asks you to type a prompt first. The dot does nothing in terminals, because Ctrl+C there stops the running command.

### Keyboard shortcut

Press **Ctrl+Alt+P** in any app to regenerate the prompt in the box you are typing in. It does the same as clicking the dot and pressing **Yes**, without the popup. It uses the same context, including your **My context** note, and **Undo** appears next to the dot afterwards. In terminals and code files it does nothing, like the dot.

Change it or turn it off in `config.toml`, then reload the config from the tray:

```toml
[hotkeys]
regenerate = "ctrl+alt+p"   # set to "" to turn it off
```

Write a shortcut as modifiers and a key joined with `+`. The modifiers are `ctrl`, `alt`, `shift` and `win`. The key is a letter, a digit, `f1` to `f24`, `space`, or a key such as `/` or `;`. Examples: `ctrl+shift+space`, `win+alt+p`. A shortcut needs at least one modifier, except F1 to F24.

If another app already uses the shortcut, a message says so when Master Prompt starts. Pick a different one and reload. On some keyboard layouts, Ctrl+Alt is the same as AltGr, so Ctrl+Alt+P can block a character you type with AltGr+P. If that happens, pick a different shortcut.

### Context

Your IDE's assistant and your AI chat already have the project and the conversation. Master Prompt sends only what makes this one prompt exact, in layers, worked out fresh on every rewrite:

| Layer | What is sent | When |
| --- | --- | --- |
| **Base** | Where you are typing, the project name, your note | Always |
| **Conversation** | Your newest message, plus older messages about the same thing (at most three). The session title only when there are no messages, because it is set once and goes stale. In AI chats: the chat title and the messages on screen. | Only when the draft follows on from the chat: it points back ("this", "it", "again", "same", "above", "still") or starts like a follow-up ("and", "also", "now", "but", "why", "what about"…) |
| **Project** | The README's first sentence and the stack (from `package.json`, `pyproject.toml` and similar) | Only for a feature or docs request ("add", "build", "implement", "docs", "readme"…) in a coding tool, outside an assistant chat |

A new idea, even a short one like "i want to build something", is sent with the base layer only, so the chat's earlier topic isn't pulled into it. The rewriter's main job is clear English and the right technical terms. Source code, git and the assistants' replies are never read. The log (`data/master-prompt.log`) shows which layers each rewrite used.

Where the conversation comes from:

- **Coding assistant sessions** (in VS Code, Cursor or Windsurf): your last six messages in the project's current session, from which the ones above are picked. Pasted text counts as a message.
  - **Claude Code**: from `~\.claude\projects\<project>\*.jsonl`.
  - **Codex**: from `~\.codex\sessions\…\rollout-*.jsonl` (or `CODEX_HOME`), matched to the project by the session's working folder. Sub-agent and background sessions are skipped. The title comes from `session_index.jsonl`.
  - **Copilot Chat**: from VS Code's `workspaceStorage\<id>\chatSessions`, matched to the project through `workspace.json`.
  - When the focused box clearly belongs to one assistant, only its session is used. Otherwise the session written most recently wins. Sessions untouched for six hours, Cursor's own chat, Windsurf Cascade and the commit message box are skipped.
  - In a session, "this" and "it" are read from your newest message, and a short follow-up stays short.
- **AI chats** (ChatGPT, Claude, Gemini and others, in a browser or desktop app): the conversation's title from the tab, and the messages visible on screen above the input box, so a follow-up like "make it cheaper" is rewritten about the right topic. Only the chat column is read: the sidebar with your other chats, the footer, and messages scrolled out of view are left out, and API keys or passwords are replaced with `[REDACTED]`. Turn this off with `read_chat_messages = false` in `config.toml`.
- **Browsers**: the website, read from the address bar. Only the domain is kept.
- **Other apps**: the app itself, for example WhatsApp or Outlook.

In chat apps and email, the text is rewritten as a message to a person, not as an AI prompt.

Click **✎ Add context** in the popup to write a note about your current work, for example "fixing the login bug". The note is saved for that project, website or app only, and is never used anywhere else. Click **✕** to clear it. Notes are stored in `data/context-notes.json`.

### Tray menu

Right-click the tray icon to edit API keys, open or reload `config.toml`, open the log, and quit. The log is `data/master-prompt.log`. For a visible console, use `run-debug.bat`.

## Configuration

Settings live in `config.toml`. Reload them from the tray after editing. `config.toml` is not committed; start from `config.example.toml`.


| Setting                           | Purpose                                                                              |
| --------------------------------- | ------------------------------------------------------------------------------------ |
| `behavior.default_style`          | `enhance`, `coding`, `concise`, `detailed`, or your own style.                       |
| `behavior.timeout_seconds`        | Time limit per provider.                                                             |
| `behavior.paste_restore_delay_ms` | Wait after Ctrl+V before your clipboard is put back. Raise it if a slow app pastes the wrong text. |
| `hotkeys.regenerate`              | Keyboard shortcut that regenerates the prompt (default `ctrl+alt+p`). `""` turns it off. |
| `[[providers]]`                   | Models tried from top to bottom. A later provider is used when an earlier one fails. |
| `apps.blocked`                    | Apps where the dot does nothing (terminals).                                         |


`config.example.toml` includes Groq and Gemini as examples. OpenRouter and a local Ollama server are in the comments. A local `base_url` does not need an API key. The tray command **API keys…** asks for a key and detects Groq, Gemini, OpenAI, OpenRouter, or Anthropic from it.

Add your own style, then set `default_style` to its name:

```toml
[styles.linkedin]
label = "LinkedIn"
instructions = "Ask for a hook, short lines, and a call to action."
```

The rewriting instructions live in `BASE_SYSTEM_PROMPT` in `master_prompt/prompts.py`.

