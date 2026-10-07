"""Tests that run on any OS (no Windows APIs needed).  Run:  python -m pytest -q"""
import json
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from master_prompt.agent_text import AgentTextError, clipboard_to_keep, read_agent_text, write_agent_text
from master_prompt.config import ConfigError, Provider, parse_settings, set_auto_prompt, set_provider_key
from master_prompt.ui import clamp_point, default_dot_origin, load_dot_position, pointer_action, save_dot_position
from master_prompt.focus_kind import FocusLabels, classify_focus
from master_prompt.idle import IdleTimer
from master_prompt.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotkeyError, parse_hotkey
from master_prompt.llm import LLMError, enhance, provider_for_screen
from master_prompt.prompts import all_styles, build_messages, build_screen_messages, clean_output
from master_prompt.targets import classify_window

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- hotkeys
def test_parse_hotkey_basic():
    hk = parse_hotkey("win+shift+p")
    assert hk.modifiers == MOD_WIN | MOD_SHIFT and hk.vk == ord("P")
    assert parse_hotkey("Ctrl + Alt + Space").modifiers == MOD_CONTROL | MOD_ALT
    assert parse_hotkey("ctrl+alt+space").vk == 0x20
    assert parse_hotkey("f9").vk == 0x78
    assert parse_hotkey("ctrl+shift+1").vk == ord("1")


@pytest.mark.parametrize("bad", ["", "p", "ctrl+shift", "hyper+p", "ctrl+nosuchkey"])
def test_parse_hotkey_bad(bad):
    with pytest.raises(HotkeyError):
        parse_hotkey(bad)


# ----------------------------------------------------------------- config
def test_example_config_parses():
    with open(ROOT / "config.example.toml", "rb") as f:
        s = parse_settings(tomllib.load(f))
    assert s.hotkey_enhance == "ctrl+alt+p"
    assert s.preview is True
    assert s.auto_prompt is True
    assert s.auto_prompt_idle_seconds == 3
    assert [p.name for p in s.providers] == ["groq", "gemini"]
    assert "windowsterminal.exe" in s.blocked_apps
    parse_hotkey(s.hotkey_enhance)
    parse_hotkey(s.hotkey_enhance_clipboard)


def test_provider_key_from_env(monkeypatch):
    p = Provider(name="g", base_url="https://x", model="m", api_key_env="MP_TEST_KEY")
    assert not p.usable
    monkeypatch.setenv("MP_TEST_KEY", "abc")
    assert p.usable and p.resolved_key() == "abc"
    assert Provider(name="o", base_url="http://localhost:11434/v1", model="m").usable


def test_set_provider_key_keeps_comments_and_other_providers():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    new = set_provider_key(set_provider_key(text, "gemini", "AIza-1"), "groq", " gsk_abc ")
    s = parse_settings(tomllib.loads(new))
    assert {p.name: p.api_key for p in s.providers} == {"groq": "gsk_abc", "gemini": "AIza-1"}
    assert new.count("\n") == text.count("\n") and "#  Free keys:" in new
    again = set_provider_key(new, "groq", "gsk_new")  # replaced, not added
    assert "gsk_abc" not in again and again.count('api_key = "gsk_new"') == 1


def test_set_provider_key_adds_missing_line_and_rejects_bad_input():
    text = '[[providers]]\nname = "groq"\nbase_url = "u"\nmodel = "m"\n\n[apps]\nblocked = []\n'
    s = parse_settings(tomllib.loads(set_provider_key(text, "groq", "k1")))
    assert s.providers[0].api_key == "k1"
    with pytest.raises(ConfigError):
        set_provider_key(text, "nope", "k")
    with pytest.raises(ConfigError):
        set_provider_key(text, "groq", 'bad"key')


def test_auto_prompt_idle_seconds_below_one_becomes_one():
    s = parse_settings({"behavior": {"auto_prompt_idle_seconds": 0}})
    assert s.auto_prompt_idle_seconds == 1


def test_set_auto_prompt_toggles_without_dropping_comments():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    on = set_auto_prompt(text, True)
    assert parse_settings(tomllib.loads(on)).auto_prompt is True
    assert "#  Free keys:" in on and "ask \"Generate the prompt?\"" in on
    assert on.count("\n") == text.count("\n")
    off = set_auto_prompt(on, False)
    assert parse_settings(tomllib.loads(off)).auto_prompt is False
    assert "#  Free keys:" in off and "ask \"Generate the prompt?\"" in off
    assert off.count("\n") == on.count("\n")
    assert off.count("auto_prompt = false") == 1


def test_disabled_provider_skipped():
    s = parse_settings({"providers": [
        {"name": "a", "base_url": "u", "model": "m", "enabled": False},
        {"name": "b", "base_url": "u/", "model": "m"},
    ]})
    assert [p.name for p in s.providers] == ["b"] and s.providers[0].base_url == "u"


# --------------------------------------------------------------- windows
def test_classify_window_is_text_for_every_app():
    assert classify_window("Cursor.exe", "prompt-master - Cursor") == "text"
    assert classify_window("chrome.exe", "ChatGPT - Google Chrome") == "text"
    assert classify_window("chrome.exe", "Gmail - Google Chrome") == "text"
    assert classify_window("WindowsTerminal.exe", "Windows PowerShell") == "text"
    assert classify_window("notepad.exe", "Untitled - Notepad") == "text"
    assert classify_window("WINWORD.EXE", "Document1 - Word") == "text"
    assert classify_window("WhatsApp.exe", "WhatsApp") == "text"
    assert classify_window("Claude.exe", "Claude") == "text"


# ----------------------------------------------------------------- focus
def test_classify_focus_file_tab_is_not_a_text_box():
    labels = FocusLabels("TabItem", "focus_kind.py", ("Editor Group",))
    assert classify_focus(labels, "text") == "unknown"


def test_classify_focus_cursor_composer_is_agent():
    labels = FocusLabels("Edit", "Composer", ())
    assert classify_focus(labels, "text") == "agent"


def test_classify_focus_whatsapp_message_box_is_agent():
    assert classify_focus(FocusLabels("Group", "Type a message", ()), "text") == "agent"
    assert classify_focus(FocusLabels("Custom", "", (), writable=True), "text") == "agent"
    assert classify_focus(FocusLabels("Group", "Chat list", ()), "text") == "unknown"


def test_classify_focus_search_box_is_agent():
    assert classify_focus(FocusLabels("Edit", "Search", ("Editor Group",)), "text") == "agent"
    assert classify_focus(FocusLabels("ComboBox", "Search", ()), "text") == "agent"


def test_classify_focus_blank_control_is_unknown():
    assert classify_focus(FocusLabels("", "", ()), "text") == "unknown"


def test_classify_focus_chatgpt_text_box_is_agent():
    assert classify_focus(FocusLabels("Edit", "Message ChatGPT", ()), "text") == "agent"


def test_classify_focus_any_text_box_is_agent():
    assert classify_focus(FocusLabels("Edit", "compose", ()), "text") == "agent"
    assert classify_focus(FocusLabels("Edit", "Text Editor", ()), "text") == "agent"
    assert classify_focus(FocusLabels("Document", "Document", ()), "text") == "agent"
    assert classify_focus(FocusLabels("ComboBox", "To", ()), "text") == "agent"


def test_classify_focus_ignore_is_unknown():
    assert classify_focus(FocusLabels("Edit", "compose", ()), "ignore") == "unknown"


def test_classify_focus_open_file_title_does_not_hide_a_text_box():
    title = "llm.py - prompt-master - Cursor"
    assert classify_focus(FocusLabels("Edit", "", (title, title, "Desktop 1")), "text") == "agent"
    assert classify_focus(FocusLabels("TabItem", "llm.py", (title,)), "text") == "unknown"
    assert classify_focus(FocusLabels("Edit", "Search files", (title, "Editor Group")), "text") == "agent"


def test_classify_focus_project_title_is_not_a_text_box():
    title = ".gitignore - prompt-master - Cursor"
    assert classify_focus(FocusLabels("Tree", "Files Explorer", (title,)), "text") == "unknown"
    assert classify_focus(FocusLabels("Text", "read_agent_text", ()), "text") == "unknown"
    assert classify_focus(FocusLabels("Edit", "", (title,)), "text") == "agent"


def test_read_agent_text_does_not_call_reader_for_editor_or_unknown():
    calls = []

    def reader(element):
        calls.append(element)
        return "rewrite me"

    editor = FocusLabels("TabItem", "main.py", ("Editor Group",))
    unknown = FocusLabels("Button", "OK", ())
    with pytest.raises(AgentTextError):
        read_agent_text("box", editor, "ide", reader=reader)
    with pytest.raises(AgentTextError):
        read_agent_text("box", unknown, "ide", reader=reader)
    assert calls == []
    assert read_agent_text("box", FocusLabels("Edit", "Composer", ()), "ide", reader=reader) == "rewrite me"
    assert calls == ["box"]
    with pytest.raises(AgentTextError, match="Nothing to rewrite."):
        read_agent_text("box", FocusLabels("Edit", "Composer", ()), "ide", reader=lambda element: "  ")


def test_write_agent_text_sends_no_keys_unless_still_agent():
    keys = []

    def send_keys(text):
        keys.append(text)
        return True

    editor = FocusLabels("TabItem", "main.py", ("Editor Group",))
    assert write_agent_text("el", editor, "ide", "new prompt", send_keys=send_keys) == "copied"
    assert write_agent_text(
        "el",
        FocusLabels("Edit", "Composer", ()),
        "ide",
        "new prompt",
        set_value=lambda element, text: False,
        refresh=lambda element: FocusLabels("Button", "OK", ()),
        send_keys=send_keys,
    ) == "copied"
    assert keys == []


def test_write_ide_agent_box_pastes_its_own_text_and_never_uses_ctrl_a():
    keys = []
    selected = []

    def send_keys(text):
        keys.append(text)
        return True

    def select_text(element):
        selected.append(element)
        return True

    agent = FocusLabels("Edit", "", ("llm.py - prompt-master - Cursor",))
    assert write_agent_text(
        "el",
        agent,
        "ide",
        "new prompt",
        set_value=lambda element, text: False,
        refresh=lambda element: agent,
        select_text=select_text,
        send_keys=send_keys,
    ) == "written"
    assert selected == ["el"]
    assert keys == ["new prompt"]

    keys.clear()
    assert write_agent_text(
        "el",
        agent,
        "ide",
        "new prompt",
        set_value=lambda element, text: False,
        refresh=lambda element: agent,
        select_text=lambda element: False,
        send_keys=send_keys,
    ) == "copied"
    assert keys == []


def test_paste_clears_the_original_prompt_and_the_generated_prompt():
    assert clipboard_to_keep("fix the login", "fix the login", "Rewrite the login flow.") is None
    assert clipboard_to_keep("Rewrite the login flow.", "fix the login", "Rewrite the login flow.") is None
    assert clipboard_to_keep(None, "fix the login", "Rewrite the login flow.") is None
    assert clipboard_to_keep("https://example.com", "fix the login", "Rewrite the login flow.") == "https://example.com"


def test_dot_click_is_a_short_press_and_a_drag_moves_it():
    assert pointer_action(0, 0) == "click"
    assert pointer_action(4, -4) == "click"
    assert pointer_action(5, 0) == "drag"


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


# ------------------------------------------------------------------- idle
def test_idle_timer_fires_once_then_waits_for_another_key():
    timer = IdleTimer(3)
    timer.enabled = True
    timer.note_activity(0)
    assert timer.poll(2.9) is False
    assert timer.poll(3.0) is True
    assert timer.poll(6) is False
    timer.note_activity(6)
    assert timer.poll(8.9) is False
    assert timer.poll(9.0) is True


def test_idle_timer_disabled_never_fires():
    timer = IdleTimer(3)
    timer.note_activity(0)
    assert timer.enabled is False
    assert timer.poll(3.0) is False
    assert timer.poll(6) is False


def test_idle_timer_seconds_below_one_become_one():
    timer = IdleTimer(0)
    timer.enabled = True
    timer.note_activity(0)
    assert timer.poll(0.9) is False
    assert timer.poll(1.0) is True


# ---------------------------------------------------------------- prompts
def test_build_messages_wraps_draft_and_adds_style():
    msgs = build_messages("  fix my code  ", "coding")
    assert msgs[0]["role"] == "system" and "software developer" in msgs[0]["content"]
    assert "<draft>\nfix my code\n</draft>" in msgs[1]["content"]


def test_build_screen_messages_sends_the_draft_and_the_screenshot():
    msgs = build_screen_messages("fix the login", b"\xff\xd8jpeg")
    assert msgs[0]["role"] == "system"
    assert "context-aware prompt enhancer" in msgs[0]["content"]
    assert "<enhanced_prompt>" in msgs[0]["content"]
    assert "4 to 8" in msgs[0]["content"]
    parts = msgs[1]["content"]
    assert "<draft>\nfix the login\n</draft>" in parts[0]["text"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_groq_screen_call_uses_a_model_that_can_see_the_screenshot():
    groq = Provider("groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", extra_params={"reasoning_effort": "low"})
    ready = provider_for_screen(groq, has_image=True)
    assert ready.model == "qwen/qwen3.8-27b"
    assert ready.extra_params == {}
    assert provider_for_screen(groq, has_image=False).model == "openai/gpt-oss-120b"
    gemini = Provider("gemini", "https://example.com", "gemini-3.5-flash")
    assert provider_for_screen(gemini, has_image=True).model == "gemini-3.5-flash"


def test_build_screen_messages_allows_an_empty_draft():
    msgs = build_screen_messages("  ", None)
    assert "<draft>\n\n</draft>" in msgs[1]["content"]
    titled = build_screen_messages("  ", None, "WhatsApp")
    assert "<window_title>\nWhatsApp\n</window_title>" in titled[1]["content"]


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
    ("<enhanced_prompt>\nDo X.\n</enhanced_prompt>", "Do X."),
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
        out = {"choices": [{"message": {"content": "Here is the improved prompt:\nBETTER: " + body["messages"][1]["content"][-30:]}}]}
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


def test_all_fail_gives_friendly_error(server):
    providers = [Provider(name="only", base_url=server + "/limited", model="m", api_key="k")]
    with pytest.raises(LLMError, match="rate limit"):
        enhance("x", providers, timeout=5)


def test_no_keys_error():
    with pytest.raises(LLMError, match="No API key"):
        enhance("x", [Provider(name="g", base_url="https://api.example.com", model="m")])
