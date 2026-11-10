"""Tests that run on any OS (no Windows APIs needed).  Run:  python -m pytest -q"""
import json
import os
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from master_prompt.box import original_for_undo, prompt_was_used, same_text
from master_prompt.config import (
    ConfigError, Provider, detect_provider, parse_settings, provider_name_for_url, upsert_provider,
)
from master_prompt.context import (
    Context, chat_title, claude_session_dir, codex_session_meta, detect_stack, domain_of, folders_from_storage,
    is_interactive_codex, keywords, latest_claude_session, latest_codex_session, latest_copilot_session, load_notes,
    parse_commits, parse_git_status, parse_ide_title, pick_messages, pick_relevant, project_rules, read_claude_session,
    read_codex_session, read_copilot_session, readme_line, redact, render_rules, replay_chat_log, save_note,
    site_kind, visible_chat_text,
)
from master_prompt.focus import agent_for_focus, is_code_editor, must_not_touch, title_shows_code_file
from master_prompt.hotkey import MOD_ALT, MOD_CONTROL, MOD_WIN, HotkeyError, parse_hotkey
from master_prompt import glass
from master_prompt.llm import LLMError, enhance
from master_prompt.motion import EASES, Motion, get_ease
from master_prompt.prompts import all_styles, build_messages, clean_output
from master_prompt.ui import (
    clamp_point, default_dot_origin, dot_click_target, load_dot_position, pointer_action, save_dot_position, short,
)

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------- config
def test_example_config_parses():
    with open(ROOT / "config.example.toml", "rb") as f:
        s = parse_settings(tomllib.load(f))
    assert s.default_style == "enhance" and s.timeout_seconds == 30 and s.paste_restore_delay_ms == 600
    assert [p.name for p in s.providers] == ["groq", "gemini"]
    assert "windowsterminal.exe" in s.blocked_apps


def test_old_config_keys_are_ignored():
    s = parse_settings({"hotkeys": {"auto": "ctrl+shift+p"}, "behavior": {"auto_prompt": True, "preview": False}})
    assert s.default_style == "enhance" and s.regenerate_hotkey == "ctrl+alt+p"


def test_regenerate_hotkey_setting():
    assert parse_settings({}).regenerate_hotkey == "ctrl+alt+p"
    assert parse_settings({"hotkeys": {"regenerate": " win+alt+p "}}).regenerate_hotkey == "win+alt+p"
    assert parse_settings({"hotkeys": {"regenerate": ""}}).regenerate_hotkey == ""  # turned off
    with pytest.raises(ConfigError, match="regenerate"):
        parse_settings({"hotkeys": {"regenerate": "ctrl+alt+nope"}})


def test_parse_hotkey():
    hk = parse_hotkey("Ctrl + Alt + P")
    assert hk.modifiers == MOD_CONTROL | MOD_ALT and hk.vk == ord("P") and hk.label == "Ctrl+Alt+P"
    assert parse_hotkey("ctrl+shift+space").vk == 0x20
    assert parse_hotkey("win+alt+/").modifiers == MOD_WIN | MOD_ALT
    assert parse_hotkey("f9").vk == 0x78 and parse_hotkey("f9").modifiers == 0  # F-keys may stand alone
    for bad in ("", "p", "ctrl+alt", "hyper+p", "ctrl+é"):
        with pytest.raises(HotkeyError):
            parse_hotkey(bad)


def test_provider_key_from_env(monkeypatch):
    p = Provider(name="g", base_url="https://x", model="m", api_key_env="MP_TEST_KEY")
    assert not p.usable
    monkeypatch.setenv("MP_TEST_KEY", "abc")
    assert p.usable and p.resolved_key() == "abc"
    assert Provider(name="o", base_url="http://localhost:11434/v1", model="m").usable


def test_detect_provider_from_key_prefix():
    groq = detect_provider("  gsk_example  ")
    assert groq["name"] == "groq" and groq["api_key"] == "gsk_example"
    assert groq["base_url"] == "https://api.groq.com/openai/v1"
    assert detect_provider("AIza-example")["name"] == "gemini"
    assert detect_provider("sk-or-v1-example")["name"] == "openrouter"
    assert detect_provider("sk-ant-example")["name"] == "anthropic"
    assert detect_provider("sk-proj-example")["name"] == "openai"
    assert detect_provider("not-a-known-key") is None


def test_provider_name_for_url():
    assert provider_name_for_url("https://api.groq.com/openai/v1") == "groq"
    assert provider_name_for_url("https://api.openai.com/v1") == "openai"
    assert provider_name_for_url("https://api.together.xyz/v1") == "together"
    assert provider_name_for_url("http://localhost:11434/v1") == "local"


def test_upsert_provider_updates_known_block_and_appends_a_new_one():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    same_model = upsert_provider(
        text, name="groq", base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-120b", api_key="gsk_same",
    )
    assert 'api_key = "gsk_same"' in same_model and "reasoning_effort" in same_model
    changed = upsert_provider(
        same_model, name="groq", base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-20b", api_key="gsk_new",
    )
    groq = next(p for p in parse_settings(tomllib.loads(changed)).providers if p.name == "groq")
    assert groq.model == "openai/gpt-oss-20b" and groq.api_key == "gsk_new" and groq.extra_params == {}
    added = upsert_provider(
        changed, name="openai", base_url="https://api.openai.com/v1",
        model="gpt-4o-mini", api_key="sk-test",
    )
    names = [p.name for p in parse_settings(tomllib.loads(added)).providers]
    assert names == ["groq", "gemini", "openai"]
    assert "Any OpenAI-compatible service works" in added
    with pytest.raises(ConfigError):
        upsert_provider(text, name="x", base_url="https://x", model='bad"model', api_key="k")


def test_disabled_provider_skipped():
    s = parse_settings({"providers": [
        {"name": "a", "base_url": "u", "model": "m", "enabled": False},
        {"name": "b", "base_url": "u/", "model": "m"},
    ]})
    assert [p.name for p in s.providers] == ["b"] and s.providers[0].base_url == "u"


# -------------------------------------------------------------------- box
def test_same_text_ignores_line_endings_and_spacing():
    assert same_text("Fix the\r\nlogin  flow ", "Fix the\nlogin flow")
    assert not same_text("Fix the login", "Fix the login flow")
    assert not same_text("", "")


def test_undo_goes_away_once_the_generated_prompt_was_sent_or_replaced():
    generated = "I want to add a dark mode toggle to the settings window."
    assert not prompt_was_used(generated, generated)                        # still in the box
    assert not prompt_was_used(generated.replace("window", "page") + " Thanks", generated)  # small edits keep Undo
    assert prompt_was_used("", generated) and prompt_was_used("  \n", generated)            # sent: the box is empty
    assert prompt_was_used("now fix the login bug", generated)              # replaced by the next message


def test_undo_returns_what_the_user_typed_even_after_regenerating_twice():
    assert original_for_undo(None, 1, "fix login") == "fix login"
    last = (1, "fix login", "Help me fix the login flow.")
    # Regenerating the generated prompt again: Undo still goes back to the user's text.
    assert original_for_undo(last, 1, "Help me fix the login flow.\r\n") == "fix login"
    # The user typed something new, or is in another window: that becomes the new original.
    assert original_for_undo(last, 1, "write a poem") == "write a poem"
    assert original_for_undo(last, 2, "Help me fix the login flow.") == "Help me fix the login flow."


# ------------------------------------------------------------------ focus
# Labels below were read from real windows with UI Automation.
def test_code_files_are_never_rewritten():
    vscode_file = is_code_editor(
        "native-edit-context", "editor",
        ("prompts.py (m2j9hz), preview, Editor Group 1", "prompt-master - Visual Studio Code"),
        ("monaco-editor no-user-select vs-dark", "editor-instance", "part editor"),
    )
    assert vscode_file
    assert is_code_editor("inputarea monaco-mouse-cursor-text")                    # older Monaco
    assert is_code_editor("cm-content", ancestor_classes=("cm-editor ͼ1",))         # CodeMirror
    assert is_code_editor("ace_text-input", ancestor_classes=("ace_editor ace-tm",))
    assert is_code_editor("WpfTextView")                                            # Visual Studio
    assert is_code_editor("Scintilla")                                              # Notepad++
    assert is_code_editor("monaco-editor modified-in-monaco-diff-editor", "code",
                          ("focus_kind.py (Working Tree), preview, Editor Group 1",),
                          ("editor-instance", "part editor"))                       # diff view


def test_every_kind_of_code_file_is_protected_in_any_editor():
    for title in ("app.py - Notepad", "*index.js - Notepad", "Main.java - Notepad", "style.css - Notepad",
                  "config.json - Notepad", "deploy.yaml - Notepad", "query.sql - Notepad"):
        assert must_not_touch("Notepad.exe", title, False), title
    assert must_not_touch("pycharm64.exe", "prompt-master – box.py", False)
    assert must_not_touch("sublime_text.exe", "main.go - Sublime Text", False)
    # Plain notes and prompts in Notepad are still fine.
    assert not must_not_touch("notepad.exe", "Untitled - Notepad", False)
    assert not must_not_touch("notepad.exe", "notes.txt - Notepad", False)
    # In VS Code the check sees the chat box even while a .py file is the open tab.
    assert not must_not_touch("Code.exe", "box.py - prompt-master - Visual Studio Code", False)
    assert must_not_touch("Code.exe", "box.py - prompt-master - Visual Studio Code", True)
    # If the check fails inside an IDE, nothing is changed. Elsewhere it goes ahead.
    assert must_not_touch("Cursor.exe", "app.py - prompt-master - Cursor", None)
    assert not must_not_touch("chrome.exe", "ChatGPT - Google Chrome", None)
    # A chat about a file in the browser is not a code file.
    assert not must_not_touch("chrome.exe", "Fix app.py bug - ChatGPT - Google Chrome", False)
    assert title_shows_code_file("app.py - Notepad") and not title_shows_code_file("Untitled - Notepad")


def test_chat_and_prompt_boxes_inside_an_ide_are_allowed():
    copilot_chat = is_code_editor("native-edit-context", "editor", ("Chat",),
                                  ("monaco-editor", "interactive-input-editor", "interactive-input-part"))
    commit_box = is_code_editor("native-edit-context", "editor",
                                ("Source Control Input", "Source Control Management"),
                                ("monaco-editor", "monaco-list-row"))
    assert not copilot_chat and not commit_box
    assert not is_code_editor("messageInput_cKsPxg", "edit")                        # Claude Code extension
    assert not is_code_editor("chatInputArea sc-x1", "edit")                        # look-alike class name
    assert not is_code_editor("OmniboxViewViews", "edit")                           # Chrome address bar
    assert not is_code_editor("", "")


# ---------------------------------------------------------------- context
def test_ide_title_gives_project_and_file():
    assert parse_ide_title("● box.py - prompt-master - Visual Studio Code") == ("prompt-master", "box.py")
    assert parse_ide_title("app.tsx - shop (Workspace) - Cursor", "cursor.exe") == ("shop", "app.tsx")
    # The Claude panel is the active tab: the project is still found, there is no file.
    assert parse_ide_title("Dot prompt regenerate an… - prompt-master - Visual Studio Code") == ("prompt-master", "")
    assert parse_ide_title("prompt-master - Visual Studio Code") == ("prompt-master", "")
    assert parse_ide_title("prompt-master – box.py", "pycharm64.exe") == ("prompt-master", "box.py")
    assert parse_ide_title("") == ("", "")


def test_project_folders_come_from_vscode_storage():
    state = {
        "backupWorkspaces": {"folders": [{"folderUri": "file:///d%3A/master-promot/prompt-master"},
                                         {"folderUri": "vscode-remote://ssh-remote+box/home/x"}]},
        "windowsState": {"lastActiveWindow": {"folder": "file:///d%3A/ReelEditor"}, "openedWindows": []},
        "profileAssociations": {"workspaces": {"file:///c%3A/Users/Me/My%20App": "__default__profile__"}},
    }
    assert folders_from_storage(state) == ["d:/master-promot/prompt-master", "d:/ReelEditor", "c:/Users/Me/My App"]


def test_project_brief_reads_manifests_and_readme_not_code(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"dependencies": {"next": "15", "react": "19", "@prisma/client": "6"}, "devDependencies": {"typescript": "5"}}))
    (tmp_path / "README.md").write_text("# Shop\n\n[![ci](x)](y)\n\nA small **online shop** built for [Acme](https://a.b).\n")
    assert detect_stack(tmp_path) == ["TypeScript", "Next.js", "React", "Prisma"]
    assert readme_line(tmp_path) == "A small online shop built for Acme."
    py = tmp_path / "py"
    py.mkdir()
    (py / "requirements.txt").write_text("# deps\nfastapi>=0.1\nSQLAlchemy[asyncio]==2\n")
    assert detect_stack(py) == ["Python", "FastAPI", "SQLAlchemy"]
    assert detect_stack(tmp_path / "missing") == []


def test_git_status_lists_changed_files_but_never_secrets():
    porcelain = (" M master_prompt/app.py\n?? notes.txt\n M .env\nR  old.py -> new.py\n?? keys/server.pem\n"
                 "D  removed.py\n D gone.py\n")
    assert parse_git_status(porcelain) == ("master_prompt/app.py", "notes.txt", "new.py")


def test_recent_commit_subjects_are_short_and_hide_secrets():
    log_text = "fix(website): serve the installer\n\nadd key sk-abcdefghijklmnopqrstuv\n" + "x" * 300 + "\n"
    commits = parse_commits(log_text)
    assert commits[0] == "fix(website): serve the installer"
    assert "sk-" not in commits[1] and "[REDACTED]" in commits[1]
    assert len(commits[2]) <= 101 and parse_commits("a\nb\nc\nd\ne\nf\n") == ("a", "b", "c", "d", "e")


def test_project_rules_are_read_without_code_or_secrets(tmp_path):
    claude = ("---\ndescription: x\n---\n# Conventions\nUse Tailwind for all styling.\n\n"
              "```bash\nrm -rf build\n```\n<!-- private -->\n## Testing\n- Run pytest before committing.\n"
              "- token: abc123secret\n")
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(claude, encoding="utf-8")  # the usual copy of CLAUDE.md
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "api.mdc").write_text("---\nglobs: api/**\n---\nAPI routes live in api/.\n")
    names, blocks = project_rules(tmp_path)
    assert names == ("CLAUDE.md", ".cursor/rules/api.mdc")
    text = render_rules(list(blocks))
    assert "Conventions:\nUse Tailwind for all styling." in text and "Testing:\n- Run pytest" in text
    assert "API routes live in api/." in text
    for gone in ("rm -rf", "private", "description:", "globs:", "abc123secret"):
        assert gone not in text
    assert project_rules(tmp_path / "missing") == ((), ())


def test_only_the_context_that_fits_the_draft_is_kept_when_there_is_too_much():
    assert keywords("NavBar is broken on mobile_view, buttons") >= {"nav", "bar", "broken", "mobile", "view", "button"}
    items = ["intro", "a", "b"]
    assert pick_relevant(items, "anything", 100) == items  # all fits: unchanged, so the prompt start can be cached
    blocks = [("Overview", "A shop built with Next.js."), ("Styling", "Use Tailwind. " * 20),
              ("Payments", "Stripe checkout lives in lib/stripe.ts. " * 3)]
    picked = pick_relevant(blocks, "the stripe checkout fails", 200, text_of=lambda b: f"{b[0]} {b[1]}",
                           size=lambda b: len(b[1]))
    assert [h for h, _ in picked] == ["Overview", "Payments"]


LONG_DRAFT = "write a python script that renames every photo in a folder by the date the photo was taken"


@pytest.mark.parametrize("draft,expected", [
    ("what is this", ["base", "conversation"]),                       # points back: needs the chat
    ("why did it fail again after I restarted the app and pressed the shortcut", ["base", "conversation"]),
    ("what about mobile", ["base", "conversation"]),                  # starts like a follow-up
    ("and dark mode too", ["base", "conversation"]),
    # A new idea, even a short one, goes without the chat: with it, the session's old topic
    # (Ctrl+Alt+P) was pulled into "i want to build something".
    ("i want to build something", ["base"]),
    ("fix the login page", ["base"]),
    (LONG_DRAFT, ["base"]),                                           # says it all: nothing else
    ("add a dark mode toggle to the settings window with a saved preference per user", ["base"]),
])
def test_a_draft_gets_only_the_layers_it_needs_in_a_session(draft, expected):
    ctx = Context(kind="ide", app="VS Code", project="shop", stack=("Python",), about="A store.",
                  session_title="Checkout bug", session_messages=("restart the app",))
    assert ctx.layers(draft) == expected  # in a session the assistant has the project: never the project layer


def test_project_layer_only_for_feature_or_docs_requests_outside_a_chat():
    ide = Context(kind="ide", app="VS Code", project="shop", stack=("Python",), about="A store.")
    assert ide.layers("add a dark mode toggle to the settings window with a saved preference per user") == [
        "base", "project"]
    assert ide.layers("how do I write the docs for the payment flow") == ["base", "project"]
    assert ide.layers(LONG_DRAFT) == ["base"]
    text = ide.describe("add a dark mode toggle")
    assert "About the project: A store." in text and "Tech stack: Python" in text
    assert "A store." not in ide.describe(LONG_DRAFT) and "Python" not in ide.describe(LONG_DRAFT)


def test_in_a_coding_session_only_the_session_is_sent():
    # Sending the whole project made "what is this" come back as a question about the project.
    ctx = Context(kind="ide", app="VS Code", project="shop", stack=("Python",), about="A store.", note="payments",
                  session_title="Checkout bug", session_messages=("restart the app", "what is this"))
    assert ctx.in_session and not Context(kind="ide", project="shop").in_session
    text = ctx.describe("what is this")
    for part in ("the Claude Code chat in VS Code", "Project: shop", "own note about their current work: payments",
                 "next message in this Claude Code conversation", "(oldest first):\n- what is this"):
        assert part in text
    # Left out: the stack, the README line, an older message about something else, and the session
    # title, which Claude Code sets once at the start, so it goes stale while the messages are current.
    for left_out in ("Python", "A store.", "restart the app", "Checkout bug"):
        assert left_out not in text
    assert text.index("own note") < text.index("latest messages")  # stable parts first, for caching
    assert "latest messages" not in ctx.describe(LONG_DRAFT)       # a self-contained draft needs no chat
    title_only = Context(kind="ide", app="VS Code", project="shop", session_title="Checkout bug")
    assert "Claude Code session: Checkout bug" in title_only.describe("fix this")  # no messages: the title helps


def test_newest_message_always_goes_and_older_ones_only_when_related():
    messages = ("set up the stripe webhook", "change the logo colour", "the stripe webhook returns 400",
                "x" * 600, "why does it fail")
    assert pick_messages(messages, "fix it") == ("why does it fail",)
    assert pick_messages(messages, "the webhook still fails") == (
        "set up the stripe webhook", "the stripe webhook returns 400", "why does it fail")
    assert pick_messages((), "anything") == ()
    long_old = ("stripe " * 100, "stripe again")
    assert len(pick_messages(long_old, "")[0]) <= 221  # older messages are cut short; the newest is kept longer


def test_pasted_text_counts_as_a_typed_message(tmp_path):
    path = tmp_path / "s.jsonl"
    _jsonl(path, [{"type": "user", "message": {"content": [
        {"type": "text", "text": '\n\n<pasted_content id="ce98">\n**Task** add a shortcut\n</pasted_content id="ce98">'}]}}])
    assert read_claude_session(path)[1] == ("**Task** add a shortcut",)


def test_session_drafts_skip_the_coding_sections():
    in_session = build_messages("what is this", kind="ide", context="x", session=True)[0]["content"]
    outside = build_messages("what is this", kind="ide", context="x")[0]["content"]
    assert "software developer" in outside and "software developer" not in in_session
    assert "next message in a coding-assistant session" in in_session
    assert "software developer" in build_messages("x", "coding", kind="ide", session=True)[0]["content"]


def test_browser_site_comes_from_the_domain_only():
    assert domain_of("chatgpt.com/c/68ab12") == "chatgpt.com"
    assert domain_of("https://www.claude.ai/chat/1") == "claude.ai"
    assert domain_of("how to fix login") == ""
    assert site_kind("mail.google.com") == ("email", "Gmail")
    assert site_kind("web.whatsapp.com") == ("messaging", "WhatsApp")
    assert site_kind("claude.ai") == ("ai_chat", "Claude")
    assert site_kind("example.com") == ("general", "")


def test_context_is_described_without_inventing_anything():
    ide = Context(kind="ide", app="VS Code", project="prompt-master", stack=("Python",),
                  about="A prompt rewriter.", note="fixing the paste bug")
    assert ide.summary() == "prompt-master · Python"
    text = ide.describe("add a paste retry")
    for part in ("VS Code (coding tool)", "Project: prompt-master", "About the project: A prompt rewriter.",
                 "Tech stack: Python", "fixing the paste bug"):
        assert part in text
    chat = Context(kind="messaging", app="WhatsApp")
    assert chat.summary() == "WhatsApp · messaging app"
    assert chat.describe() == "Where the user is typing: WhatsApp (messaging app)"


def test_each_place_keeps_its_own_note(tmp_path):
    path = tmp_path / "notes.json"
    save_note(path, "project:d:/a", "fixing login")
    save_note(path, "project:d:/b", "adding payments")
    save_note(path, "site:chatgpt.com", "explain simply")
    notes = load_notes(path)
    assert notes["project:d:/a"] == "fixing login" and notes["project:d:/b"] == "adding payments"
    save_note(path, "project:d:/a", "  ")  # cleared with ✕
    assert "project:d:/a" not in load_notes(path) and load_notes(tmp_path / "none.json") == {}


def test_messages_and_emails_are_not_turned_into_ai_prompts():
    msg = build_messages("bro send file tmrw", kind="messaging")
    assert "message to a person, not a prompt" in msg[0]["content"]
    assert "message the user meant to send" in msg[1]["content"]
    mail = build_messages("need the report by friday", kind="email")
    assert "email body" in mail[1]["content"]
    ide = build_messages("fix this", kind="ide", context="Project: prompt-master")
    assert "software developer" in ide[0]["content"] and "<context>" in ide[0]["content"]
    assert "<context>\nProject: prompt-master\n</context>\n\n<draft>\nfix this\n</draft>" in ide[1]["content"]
    plain = build_messages("fix this")
    assert "<context>" not in plain[0]["content"] + plain[1]["content"]


def test_conversation_title_comes_from_the_tab():
    assert chat_title("Student laptop recommendations under ₹80,000 - Claude - Brave", "Claude") == \
        "Student laptop recommendations under ₹80,000"
    assert chat_title("Fix Python import error - Google Chrome", "ChatGPT") == "Fix Python import error"
    assert chat_title("Trip plan and 3 more pages - Personal - Microsoft​ Edge", "ChatGPT") == "Trip plan"
    # A new chat, or a desktop app, shows only the product name: no title.
    for title in ("ChatGPT - Google Chrome", "New chat - Claude - Brave", "Claude", "Gemini - Mozilla Firefox"):
        assert chat_title(title, "") == "", title


def test_only_the_chat_column_above_the_input_box_is_read():
    box = (400, 900, 1200, 980)                      # the input box
    pieces = [
        ("Recents: Tax filing help", (10, 120, 300, 140)),          # sidebar chat list: left out
        ("I want a laptop for coding", (700, 200, 1150, 220)),
        ("under 80k", (700, 222, 800, 240)),                        # next line of the same message
        ("Here are three", (420, 300, 600, 320)),
        ("options", (602, 300, 680, 320)),                          # same line, separate piece (bold)
        (":", (681, 300, 686, 320)),
        ("my draft in the box", (420, 920, 900, 940)),              # inside the box: left out
        ("Claude can make mistakes.", (500, 990, 900, 1005)),        # footer below: left out
        ("", (420, 400, 500, 420)),
    ]
    assert visible_chat_text(pieces, box) == "I want a laptop for coding\nunder 80k\nHere are three options:"
    assert visible_chat_text(pieces, None) == ""
    long = [(f"line {i} " + "word " * 10, (420, 10 + i * 20, 900, 28 + i * 20)) for i in range(40)]
    tail = visible_chat_text(long, box, max_chars=200)
    assert tail.startswith("…") and tail.endswith("line 39 " + "word " * 9 + "word") and len(tail) <= 202


def test_secrets_are_hidden_before_anything_is_sent():
    text = redact("use key sk-proj-abcdefghijklmnop1234 and password: hunter2, token=abc.def, "
                  "AKIAABCDEFGHIJKLMNOP, gsk_abcdefghijklmnopqrst and hash 0123456789abcdef0123456789abcdef")
    for secret in ("sk-proj-abcdefghijklmnop1234", "hunter2", "abc.def", "AKIAABCDEFGHIJKLMNOP",
                   "gsk_abcdefghijklmnopqrst", "0123456789abcdef0123456789abcdef"):
        assert secret not in text
    assert "password: [REDACTED]" in text and redact("plain words") == "plain words"


def test_ai_chat_context_shows_title_and_visible_messages():
    ctx = Context(kind="ai_chat", app="Claude", site="claude.ai", chat_title="Student laptops under 80k",
                  recent="I want a laptop for coding\nHere are three options")
    assert ctx.summary() == "Claude · “Student laptops under 80k” · + visible messages"
    text = ctx.describe("make it cheaper")
    assert "Conversation title: Student laptops under 80k" in text
    assert text.endswith("(latest last; they include the AI's replies):\nI want a laptop for coding\n"
                         "Here are three options")
    rules = build_messages("make it cheaper", kind="ai_chat", context=text)[0]["content"]
    assert "do not repeat the AI's replies" in rules


def _jsonl(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_claude_code_session_of_the_open_project_is_found(tmp_path):
    root = tmp_path / "projects"
    (root / "d--master-promot-prompt-master").mkdir(parents=True)
    (root / "d--other-project").mkdir()
    old = root / "d--master-promot-prompt-master" / "old.jsonl"
    new = root / "d--master-promot-prompt-master" / "new.jsonl"
    _jsonl(old, [])
    _jsonl(new, [])
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert claude_session_dir(r"D:\master-promot\prompt-master", root).name == "d--master-promot-prompt-master"
    assert latest_claude_session(r"D:\master-promot\prompt-master", root=root, now=2100) == new
    # A session nobody touched for hours is not the one in use.
    assert latest_claude_session(r"D:\master-promot\prompt-master", root=root, now=2000 + 7 * 3600) is None
    assert latest_claude_session(r"D:\no-such-project", root=root, now=2100) is None


def test_only_what_the_user_typed_is_read_from_a_session(tmp_path):
    path = tmp_path / "s.jsonl"
    _jsonl(path, [
        {"type": "ai-title", "aiTitle": "Dot prompt regenerate and undo"},
        {"type": "user", "message": {"role": "user", "content": "add undo next to the dot"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "CLAUDE REPLY"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "TOOL OUTPUT"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "META"}},
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "<ide_opened_file>box.py</ide_opened_file>"},
            {"type": "text", "text": "it should not change code, key sk-abcdefghijklmnopqrstu"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "user", "isSidechain": True, "message": {"content": "SUBAGENT"}},
        {"type": "user", "message": {"content": "build option 1 and option 2"}},
        {"type": "ai-title", "aiTitle": "Dot prompt regenerate and undo"},
    ])
    title, messages = read_claude_session(path)
    assert title == "Dot prompt regenerate and undo"
    assert messages == ("add undo next to the dot", "it should not change code, key [REDACTED]",
                        "build option 1 and option 2")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "custom-title", "customTitle": "My renamed session"}) + "\n")
    assert read_claude_session(path)[0] == "My renamed session"
    assert read_claude_session(tmp_path / "missing.jsonl") == ("", ())


def test_the_focused_box_tells_which_assistant_is_in_use():
    claude_code = ("messageInput_cKsPxg", "edit", (), ("inputContainer_x", "monaco-workbench"))
    copilot = ("native-edit-context", "editor", ("Chat",), ("monaco-editor", "interactive-input-part"))
    commit = ("native-edit-context", "editor", ("Source Control Input",), ("monaco-editor",))
    cursor_chat = ("aislash-editor-input", "edit", (), ("composer-bar",))
    unknown_webview = ("textarea", "edit", (), ("root",))
    assert agent_for_focus(claude_code) == "claude"
    assert agent_for_focus(copilot) == "copilot"
    assert agent_for_focus(commit) == "other" and agent_for_focus(cursor_chat) == "other"
    assert agent_for_focus(unknown_webview) is None and agent_for_focus(None) is None
    ctx = Context(kind="ide", app="VS Code", project="prompt-master", stack=("Python",),
                  session_title="Dot prompt regenerate and undo", session_messages=("add undo", "fix this"))
    assert ctx.summary() == "prompt-master · Claude Code “Dot prompt regenerate and undo”"
    assert "Claude Code session (oldest first):\n- add undo\n- fix this" in ctx.describe("undo is still broken")
    ctx.session_tool = "Codex"
    assert ctx.summary().endswith("Codex “Dot prompt regenerate and undo”")


# Codex fixtures follow the openai/codex source: RolloutItemWire (type + payload), SessionMeta,
# EventMsg::UserMessage / ItemCompleted(TurnItem::UserMessage), UserInput::Text, SessionIndexEntry.
def _codex_meta(cwd, source="vscode", **extra):
    return {"timestamp": "2026-11-10T10:00:00Z", "type": "session_meta",
            "payload": {"id": "thread-1", "session_id": "thread-1", "timestamp": "2026-11-10T10:00:00Z",
                        "cwd": cwd, "originator": "codex_vscode", "cli_version": "0.170.0", "source": source,
                        "base_instructions": {"text": "BASE INSTRUCTIONS"}, **extra}}


def _codex_event(payload):
    return {"timestamp": "2026-11-10T10:00:01Z", "type": "event_msg", "payload": payload}


def test_codex_session_for_the_project_is_found(tmp_path):
    home = tmp_path / ".codex"
    day = home / "sessions" / "2026" / "11" / "10"
    old_day = home / "sessions" / "2026" / "10" / "01"   # a resumed session keeps its first day's folder
    day.mkdir(parents=True)
    old_day.mkdir(parents=True)
    project = r"D:\master-promot\prompt-master"
    mine = old_day / "rollout-2026-10-01T09-00-00-aaa.jsonl"
    other = day / "rollout-2026-11-10T09-00-00-bbb.jsonl"
    sub = day / "rollout-2026-11-10T09-30-00-ccc.jsonl"
    idle = day / "rollout-2026-11-10T08-00-00-ddd.jsonl.zst"
    _jsonl(mine, [_codex_meta(project)])
    _jsonl(other, [_codex_meta(r"D:\another-project")])
    _jsonl(sub, [_codex_meta(project, parent_thread_id="thread-1")])          # a sub-agent
    idle.write_bytes(b"\x28\xb5\x2f\xfd")                                       # compressed: idle, skipped
    for i, path in enumerate((mine, other, sub, idle)):
        os.utime(path, (1000 + i, 1000 + i))
    found = latest_codex_session(project.lower().replace("\\", "/"), home=home, now=1100)
    assert found is not None and found[0] == mine and found[1]["cwd"] == project
    assert latest_codex_session(project, home=home, now=1000 + 7 * 3600) is None   # untouched for hours
    assert not is_interactive_codex({"source": "exec"}) and not is_interactive_codex({"source": {"subagent": "x"}})
    assert is_interactive_codex({"source": "cli"}) and is_interactive_codex({})


def test_only_what_the_user_typed_is_read_from_a_codex_session(tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    path = tmp_path / "rollout.jsonl"
    _jsonl(path, [
        _codex_meta(r"D:\p"),
        {"timestamp": "t", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        _codex_event({"type": "user_message", "message": "add a dark mode toggle"}),
        _codex_event({"type": "agent_message", "message": "CODEX REPLY"}),
        {"timestamp": "t", "type": "response_item", "payload": {"type": "function_call_output", "output": "TOOL"}},
        _codex_event({"type": "item_completed", "thread_id": "thread-1", "turn_id": "t2", "item": {
            "type": "UserMessage", "id": "u2", "content": [
                {"type": "text", "text": "## My request for Codex:\nuse my key sk-abcdefghijklmnopqrstu", "text_elements": []},
                {"type": "local_image", "path": "C:\\shot.png"}]}}),
        # Some versions write the same message as both event kinds.
        _codex_event({"type": "user_message", "message": "## My request for Codex:\nuse my key sk-abcdefghijklmnopqrstu"}),
        _codex_event({"type": "item_completed", "thread_id": "thread-1", "turn_id": "t3",
                      "item": {"type": "AgentMessage", "id": "a3", "content": [{"type": "text", "text": "REPLY"}]}}),
        _codex_event({"type": "user_message", "message": "now do the same for the settings page"}),
    ])
    (home / "session_index.jsonl").write_text(
        json.dumps({"id": "thread-1", "thread_name": "Dark mode", "updated_at": "1"}) + "\n" +
        json.dumps({"id": "thread-9", "thread_name": "Other", "updated_at": "2"}) + "\n" +
        json.dumps({"id": "thread-1", "thread_name": "Dark mode toggle", "updated_at": "3"}) + "\n",
        encoding="utf-8")
    title, messages = read_codex_session(path, codex_session_meta(path), home=home)
    assert title == "Dark mode toggle"                                       # the latest rename wins
    assert messages == ("add a dark mode toggle", "use my key [REDACTED]", "now do the same for the settings page")


# Copilot Chat fixtures follow the microsoft/vscode source: workspace.json (storageMain.ts),
# chatSessions/<id>.json|.jsonl (chatSessionStore.ts), the kind 0-3 log (objectMutationLog.ts),
# requests[].message as string or {text, parts} and customTitle (chatModel.ts).
def test_copilot_chat_change_log_is_replayed_like_vscode_does():
    lines = [json.dumps(e) for e in (
        {"kind": 0, "v": {"version": 3, "sessionId": "s1", "creationDate": 1, "customTitle": None,
                          "requests": [{"requestId": "r1", "message": {"text": "explain this regex", "parts": []},
                                        "response": [{"value": "COPILOT ANSWER"}]}]}},
        {"kind": 2, "k": ["requests"], "v": [{"requestId": "r2", "message": {"text": "make it faster", "parts": []}}]},
        {"kind": 1, "k": ["requests", 1, "message", "text"], "v": "make it faster please"},
        {"kind": 1, "k": ["customTitle"], "v": "Regex help"},
        {"kind": 2, "k": ["requests"], "v": [{"requestId": "r3", "message": {"text": "oops", "parts": []}}]},
        {"kind": 2, "k": ["requests"], "i": 2},                       # cut the list back: r3 removed
        {"kind": 3, "k": ["creationDate"]},
        {"kind": 1, "k": ["no", "such", "path"], "v": 1},             # ignored, not a crash
    )]
    state = replay_chat_log(["\ufeff" + lines[0], *lines[1:], "", "not json"])
    assert [r["requestId"] for r in state["requests"]] == ["r1", "r2"]
    assert state["requests"][1]["message"]["text"] == "make it faster please"
    assert state["customTitle"] == "Regex help" and state["creationDate"] is None
    assert replay_chat_log([json.dumps({"kind": 1, "k": ["a"], "v": 1})]) is None   # no initial state


def test_copilot_chat_session_for_the_project_is_found_and_read(tmp_path):
    user = tmp_path / "Code" / "User"
    mine = user / "workspaceStorage" / "abc123"
    other = user / "workspaceStorage" / "def456"
    (mine / "chatSessions").mkdir(parents=True)
    (other / "chatSessions").mkdir(parents=True)
    (mine / "workspace.json").write_text(json.dumps({"folder": "file:///d%3A/master-promot/prompt-master"}))
    (other / "workspace.json").write_text(json.dumps({"folder": "file:///d%3A/other"}))
    old_json = mine / "chatSessions" / "s-old.json"
    old_json.write_text(json.dumps({"version": 3, "sessionId": "s-old", "customTitle": None, "requests": [
        {"requestId": "r1", "message": "old format message"}]}), encoding="utf-8")
    log = mine / "chatSessions" / "s-new.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in (
        {"kind": 0, "v": {"version": 3, "sessionId": "s-new", "customTitle": None, "requests": [
            {"requestId": "r1", "message": {"text": "why is the build failing\nsecond line", "parts": []}},
            {"requestId": "r2", "message": {"text": "HIDDEN", "parts": []}, "hiddenFromTranscript": True}]}},
        {"kind": 2, "k": ["requests"], "v": [{"requestId": "r3", "message": {"text": "fix it", "parts": []}}]},
    )) + "\n", encoding="utf-8")
    (other / "chatSessions" / "x.jsonl").write_text(json.dumps({"kind": 0, "v": {"requests": []}}) + "\n")
    os.utime(old_json, (1000, 1000))
    os.utime(log, (2000, 2000))
    found = latest_copilot_session(r"D:\master-promot\prompt-master", user_dir=user, now=2100)
    assert found == log
    title, messages = read_copilot_session(found)
    assert title == "why is the build failing"             # VS Code's default: first line of the first request
    assert messages == ("why is the build failing second line", "fix it")
    assert read_copilot_session(old_json) == ("old format message", ("old format message",))
    assert latest_copilot_session(r"D:\master-promot\prompt-master", user_dir=user, now=2000 + 7 * 3600) is None


def test_short_note_for_the_popup():
    assert short("fixing   the\npaste bug") == "fixing the paste bug"
    assert short("word " * 30, 20).endswith("…") and len(short("word " * 30, 20)) <= 21


# -------------------------------------------------------------------- dot
def test_dot_click_is_a_short_press_and_a_drag_moves_it():
    assert pointer_action(0, 0) == "click"
    assert pointer_action(4, -4) == "click"
    assert pointer_action(5, 0) == "drag"
    assert dot_click_target(10, False) == "generate"
    assert dot_click_target(50, False) is None
    assert dot_click_target(10, True) == "generate"
    assert dot_click_target(50, True) == "undo"


def test_dot_starts_on_the_right_and_stays_on_screen():
    assert default_dot_origin((0, 0, 1000, 800), 32, 32) == (950, 384)
    assert clamp_point(-20, 900, 32, 32, (0, 0, 800, 600)) == (0, 568)


def test_dot_position_round_trip(tmp_path):
    path = tmp_path / "float-dot.json"
    assert load_dot_position(path) is None
    save_dot_position(path, 120, 340)
    assert load_dot_position(path) == (120, 340)
    path.write_text("nope", encoding="utf-8")
    assert load_dot_position(path) is None


# ----------------------------------------------------------------- motion
class _FakeWidget:
    """Collects after() calls so a test can run the ticker by hand."""

    def __init__(self):
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append(fn)
        return len(self.jobs)


def _motion(**values):
    clock = {"t": 0.0}
    frames = []
    widget = _FakeWidget()
    m = Motion(widget, lambda: frames.append(dict(m.values)), clock=lambda: clock["t"], **values)
    return m, clock, frames, widget


def test_eases_start_at_0_and_end_at_1():
    for name, ease in EASES.items():
        assert ease(0) == pytest.approx(0, abs=1e-9), name
        assert ease(1) == pytest.approx(1, abs=1e-9), name
    assert get_ease("back.out")(0.6) > 1  # overshoots, then settles
    with pytest.raises(ValueError):
        get_ease("bounce.sideways")


def test_tween_moves_a_value_and_calls_on_complete():
    m, clock, frames, widget = _motion(scale=1.0)
    done = []
    m.to("scale", 2.0, 1.0, "linear", on_complete=lambda: done.append(True))
    clock["t"] = 0.5
    m.step()
    assert m["scale"] == pytest.approx(1.5)
    assert m.active("scale") and not done
    clock["t"] = 1.2
    m.step()
    assert m["scale"] == 2.0 and done == [True] and not m.active("scale")
    assert frames[-1]["scale"] == 2.0


def test_new_tween_starts_from_the_current_value():
    m, clock, _, _ = _motion(hover=0.0)
    m.to("hover", 1.0, 1.0, "linear")
    clock["t"] = 0.4
    m.step()
    m.to("hover", 0.0, 1.0, "linear")  # mouse left half way: no jump back to 1
    clock["t"] = 0.9
    m.step()
    assert m["hover"] == pytest.approx(0.2)


def test_repeat_forever_and_yoyo():
    m, clock, _, _ = _motion(spin=0.0, pulse=0.0)
    m.from_to("spin", 0, 360, 1.0, "none", repeat=-1)
    m.from_to("pulse", 0, 1, 1.0, "none", repeat=1, yoyo=True)
    clock["t"] = 2.25
    m.step()
    assert m["spin"] == pytest.approx(90) and m.active("spin")
    assert m["pulse"] == 0.0 and not m.active("pulse")  # forward, then back, then done
    m.kill("spin")
    assert not m.active("spin")


def test_ticker_runs_only_while_something_moves():
    m, clock, frames, widget = _motion(alpha=0.0)
    m.to("alpha", 1.0, 0.1, "linear")
    assert len(widget.jobs) == 1
    clock["t"] = 1.0
    widget.jobs.pop()()
    assert m["alpha"] == 1.0 and widget.jobs == []  # finished: no more frames scheduled


# ------------------------------------------------------------------ glass
def test_glass_dot_frame_is_soft_edged_and_clear_around_it():
    f = glass.Frame(56, 56, 12, 12, 32)
    img = glass.render(f)
    assert img.size == (56, 56) and img.mode == "RGBA"
    alpha = img.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0           # margin is clear, so clicks pass through
    assert alpha.getpixel((28, 28)) == 255       # white core
    edge = [alpha.getpixel((x, 28)) for x in range(10, 16)]
    assert any(0 < a < 255 for a in edge)        # anti-aliased edge, not a hard step
    assert len(glass.premultiplied_bgra(img)) == 56 * 56 * 4


def test_glass_pill_grows_out_of_the_dot():
    f = glass.Frame(150, 64, 16, 16, 32, pill=(12, 12, 126, 40), label_x=56, open=0.0)
    assert glass.pill_box(f) is None
    f.open = 1.0
    assert glass.pill_box(f) == (12, 12, 126, 40)
    f.open = 0.5
    x, y, w, h = glass.pill_box(f)
    assert 32 < w < 126 and 32 < h < 40
    for state in (dict(open=1.0, undo_hover=1.0), dict(busy=1.0, spin=45), dict(ripple=0.3, hover=1, scale=1.1)):
        for k, v in state.items():
            setattr(f, k, v)
        assert glass.render(f).size == (150, 64)


def test_flatten_puts_soft_edges_on_a_colour_key():
    img = glass.render(glass.Frame(56, 56, 12, 12, 32))
    flat = glass.flatten(img, (30, 31, 36), (1, 1, 1))
    assert flat.mode == "RGB" and flat.getpixel((0, 0)) == (1, 1, 1)
    assert flat.getpixel((28, 28)) == (255, 255, 255)


# ---------------------------------------------------------------- prompts
def test_build_messages_wraps_draft_and_adds_style():
    msgs = build_messages("  fix my code  ", "coding")
    assert msgs[0]["role"] == "system" and "software developer" in msgs[0]["content"]
    assert "<draft>\nfix my code\n</draft>" in msgs[1]["content"]


def test_custom_style_merges():
    styles = all_styles({"linkedin": {"label": "LinkedIn", "instructions": "hook"}})
    assert styles["linkedin"]["label"] == "LinkedIn" and "enhance" in styles
    assert "hook" in build_messages("x", "linkedin", {"linkedin": {"instructions": "hook"}})[0]["content"]


@pytest.mark.parametrize("raw,expected", [
    ("Here is the improved prompt:\nDo X.", "Do X."),
    ("**Improved prompt:**\nDo X.", "Do X."),
    ("```markdown\nDo X.\n```", "Do X."),
    ('"Do X."', "Do X."),
    ("<think>hmm</think>\nDo X.", "Do X."),
    ("<draft>\nDo X.\n</draft>", "Do X."),
    ("Do X.\n```py\nprint(1)\n```", "Do X.\n```py\nprint(1)\n```"),  # inner code kept
])
def test_clean_output(raw, expected):
    assert clean_output(raw) == expected


# ------------------------------------------------- LLM client + fallback
class _Handler(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.path.startswith("/limited"):
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"error":"rate"}')
            return
        finish = "length" if self.path.startswith("/cut") else "stop"
        out = {"choices": [{"finish_reason": finish, "message": {
            "content": "Here is the improved prompt:\nBETTER: " + body["messages"][1]["content"][-30:]}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Handler.calls = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_fallback_to_second_provider(server):
    providers = [
        Provider(name="first", base_url=server + "/limited", model="m1", api_key="k1"),
        Provider(name="second", base_url=server + "/ok", model="m2", api_key="k2",
                 extra_params={"reasoning_effort": "low"}),
    ]
    r = enhance("make todo app", providers, timeout=5)
    assert r.provider == "second" and r.text.startswith("BETTER:")
    assert [c[0] for c in _Handler.calls] == ["/limited/chat/completions", "/ok/chat/completions"]
    assert _Handler.calls[1][1] == "Bearer k2"
    assert _Handler.calls[1][2]["model"] == "m2" and _Handler.calls[1][2]["reasoning_effort"] == "low"


def test_cut_off_answer_is_never_used(server):
    """Long code can make the answer stop halfway. That half answer must not reach the box."""
    with pytest.raises(LLMError, match="cut off"):
        enhance("fix this\n" + "x = 1\n" * 50, [Provider(name="one", base_url=server + "/cut", model="m", api_key="k")],
                timeout=5)
    # A backup provider that finishes is used instead.
    r = enhance("fix this", [Provider(name="one", base_url=server + "/cut", model="m", api_key="k"),
                             Provider(name="two", base_url=server + "/ok", model="m", api_key="k")], timeout=5)
    assert r.provider == "two"


def test_code_in_the_draft_is_kept_word_for_word():
    system = build_messages("fix this\n\ndef f(): return 1/0")[0]["content"]
    assert "Keep facts, names, numbers, code" in system and "Do NOT answer, solve or carry out" in system


def test_all_fail_gives_friendly_error(server):
    providers = [Provider(name="only", base_url=server + "/limited", model="m", api_key="k")]
    with pytest.raises(LLMError, match="rate limit"):
        enhance("x", providers, timeout=5)


def test_no_keys_error():
    with pytest.raises(LLMError, match="No API key"):
        enhance("x", [Provider(name="g", base_url="https://api.example.com", model="m")])
