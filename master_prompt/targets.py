"""Decide whether a window is an LLM app, a coding IDE, or something to leave alone.

This looks only at the process name and window title. It does not read the
control's text and it does not call Windows APIs.
"""
from __future__ import annotations


_LLM_APPS = frozenset({
    "chatgpt.exe",
    "claude.exe",
    "copilot.exe",
    "perplexity.exe",
    "gemini.exe",
    "grok.exe",
    "deepseek.exe",
    "lm studio.exe",
    "ollama.exe",
    "jan.exe",
})

_BROWSERS = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
    "comet.exe",
})

_IDE_APPS = frozenset({
    "cursor.exe",
    "code.exe",
    "antigravity.exe",
    "kiro.exe",
    "codex.exe",
    "windsurf.exe",
    "devin.exe",
    "trae.exe",
    "zed.exe",
    "void.exe",
    "pearai.exe",
    "qoder.exe",
    "codebuddy.exe",
    "code - insiders.exe",
    "vscodium.exe",
    "positron.exe",
    "idea64.exe",
    "pycharm64.exe",
    "webstorm64.exe",
    "goland64.exe",
    "clion64.exe",
    "rider64.exe",
    "phpstorm64.exe",
    "rubymine64.exe",
    "datagrip64.exe",
    "studio64.exe",
    "fleet.exe",
    "devenv.exe",
    "sublime_text.exe",
})

# Substrings, already lowercased. A browser tab matches when its title contains one.
_LLM_TITLE_MARKERS = (
    "chatgpt",
    "openai",
    "claude",
    "gemini",
    "ai studio",
    "notebooklm",
    "perplexity",
    "grok",
    "deepseek",
    "copilot",
    "meta ai",
    "mistral",
    "le chat",
    "huggingchat",
    "you.com",
    "duck.ai",
    "qwen",
    "kimi",
    "doubao",
    "ernie",
    "z.ai",
    "minimax",
    "character.ai",
    "cohere",
    "phind",
    "openrouter",
    "lmarena",
    "t3 chat",
    "lovable",
    "bolt.new",
    "v0.app",
    "replit",
    "base44",
    "codespaces",
    "firebase studio",
    "stackblitz",
    "codesandbox",
    "openhands",
    "windsurf",
    "kiro",
    "trae",
    "antigravity",
    "codex",
)


def _process_exe(process_name: str) -> str:
    return process_name.replace("/", "\\").rsplit("\\", 1)[-1].strip().casefold()


def classify_window(process_name: str, window_title: str) -> str:
    """Return "llm", "ide", or "ignore" from a process name and window title."""
    exe = _process_exe(process_name)
    if exe in _IDE_APPS:
        return "ide"
    if exe in _LLM_APPS:
        return "llm"
    if exe in _BROWSERS and _title_is_llm(window_title):
        return "llm"
    return "ignore"


def _title_is_llm(window_title: str) -> bool:
    title = window_title.casefold()
    return any(marker in title for marker in _LLM_TITLE_MARKERS)
