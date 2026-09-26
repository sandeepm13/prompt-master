"""Tests that run on any OS (no Windows APIs needed).  Run:  python -m pytest -q"""
import json
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from master_prompt.config import ConfigError, Provider, parse_settings, set_provider_key
from master_prompt.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotkeyError, parse_hotkey
from master_prompt.llm import LLMError, enhance
from master_prompt.prompts import all_styles, build_messages, clean_output

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
    assert s.hotkey_enhance == "win+shift+p"
    assert s.preview is True
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


def test_disabled_provider_skipped():
    s = parse_settings({"providers": [
        {"name": "a", "base_url": "u", "model": "m", "enabled": False},
        {"name": "b", "base_url": "u/", "model": "m"},
    ]})
    assert [p.name for p in s.providers] == ["b"] and s.providers[0].base_url == "u"


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
