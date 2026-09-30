# ✨ Master Prompt

Type a rough prompt in any app, press **Ctrl + Alt + P**, and it turns into a clear, well-structured prompt.
It works like Grammarly or Win+H: one shortcut, and it works in ChatGPT, Claude, Gemini, your browser, VS Code, Notion, WhatsApp Desktop, and other apps.

```
 you type / speak (Win+H)       press Ctrl+Alt+P            popup shows the better prompt       Enter
 "make me react todo app  ──►   text is copied and sent  ──►  (you can edit it, pick a style)  ──► it replaces
  with login how to start"      to Groq (Gemini as backup)                                          your text
```

---

## 1. Setup (one time, about 3 minutes)

1. **Install Python 3.11+** from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"** during install.
2. **Get a free API key** (an API key is like a password that lets this app use the AI):
   - Groq: <https://console.groq.com/keys> (main, very fast)
   - Gemini: <https://aistudio.google.com/apikey> (backup, used when Groq fails)
3. Double-click **`setup.bat`**. It installs what's needed and opens `config.toml`.
4. In `config.toml`, paste your keys:
   ```toml
   api_key = "gsk_your_groq_key_here"      # under the groq provider
   api_key = "AIza_your_gemini_key_here"   # under the gemini provider
   ```
   Save the file.
5. Double-click **`test-key.bat`**. You should see an improved prompt printed. If you see `FAILED`, the message tells you why.
6. Double-click **`run.bat`**. A purple ✨ icon appears near the clock (it may be under the `^` arrow).
7. Optional: double-click **`add-to-startup.bat`** so it starts every time you log in.

## 2. How to use

| What you want | What you do |
|---|---|
| Improve the whole prompt in a text box | Click in the box, press **Ctrl+Alt+P** |
| Improve only part of the text | Select that part, press **Ctrl+Alt+P** |
| In the popup: use the prompt | **Enter** (or the "Use prompt" button) |
| In the popup: add a new line | **Shift+Enter** |
| Try again / pick another style | **Ctrl+R**, or change the dropdown (Enhance, Coding, Concise, Detailed) |
| See what you originally wrote | "Original" button |
| Cancel | **Esc** |
| Undo after it replaced your text | **Ctrl+Z** in your app |
| Terminal / Claude Code / apps where it can't grab text | Copy your text, press **Ctrl+Alt+O**, then paste with Ctrl+V |

Tray icon (right-click): turn preview on/off, open config, reload config, open history, open log, quit.

## 3. How it works

```
master_prompt/
├── __main__.py   start here: `python -m master_prompt` (also --test and --check)
├── app.py        connects all the steps below; keeps the app responsive using threads
├── hotkeys.py    asks Windows to tell us when Ctrl+Alt+P is pressed (RegisterHotKey)
├── capture.py    copies the text out of your app and pastes the new text back
├── winapi.py     low-level Windows functions (keyboard, clipboard, windows) using ctypes
├── llm.py        sends the text to Groq/Gemini and tries the next one if a provider fails
├── prompts.py    the instructions that tell the AI HOW to rewrite your prompt  ← tweak this!
├── ui.py         the preview popup + small notification messages (tkinter)
├── tray.py       the tray icon and its menu
└── history.py    saves before/after pairs to data/history.jsonl
```

**Why it works in every app:** there's no single Windows feature that reads text from any text box,
because Chrome, Electron apps (ChatGPT/Claude desktop), and VS Code each draw their own boxes.
But every app supports the **clipboard**, so the app does what you'd do by hand:
`Ctrl+C` (or `Ctrl+A` + `Ctrl+C`) → AI → `Ctrl+V`. It saves your clipboard first and puts it back afterwards.

**Why it's reliable:**
- Global hotkey through Windows itself (`RegisterHotKey`), not a keyboard hook, so there's no lag and no admin rights needed.
- Waits for you to release Win/Shift before pressing Ctrl+C. Otherwise Windows would see Win+Shift+Ctrl+C.
- Uses the "clipboard sequence number" to know for sure if Ctrl+C copied something.
- Brings the same window back into focus before pasting. If that fails, the prompt stays on the clipboard and you just press Ctrl+V.
- If Groq is down or rate-limited, it automatically uses Gemini.
- Only one copy can run at a time.
- Temporary clipboard content is hidden from Win+V clipboard history.

## 4. Settings (`config.toml`)

- `hotkeys.enhance`: change the shortcut, e.g. `"ctrl+alt+p"`.
- `behavior.preview = false`: skip the popup and replace text directly, like Win+H.
- `[[providers]]`: the AI services, tried from top to bottom. You can add OpenRouter or Ollama (offline). Examples are in the file.
- `[styles.xxx]`: add your own style to the dropdown.
- `apps.blocked`: apps where Ctrl+C means "stop" (terminals). The main shortcut is skipped there.

After changing settings: tray icon → **Reload config**.

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| "Ctrl+Alt+P is already used by another app" | Change `enhance` in config.toml, then Reload config |
| Nothing happens in one specific app | That app may be running as Administrator. Windows blocks normal apps from typing into admin apps. Run `run.bat` as admin, or use Ctrl+Alt+O |
| It pasted into the wrong place | You switched windows while it was thinking. Press Ctrl+Z there; the prompt is on your clipboard |
| Old text isn't replaced, the new text is added next to it | That app keeps the selection in a strange way. Select all yourself (Ctrl+A) before pressing the shortcut |
| Errors | Run `run-debug.bat` to see messages, or open `data/master-prompt.log` |

## 6. Developing (with Claude Code)

```bat
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest -q          :: tests (they work without Windows APIs)
run-debug.bat                              :: run with a console window
```
See `CLAUDE.md` for notes for Claude Code.
