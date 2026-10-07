"""
Loads settings from config.toml (in the project folder, e.g. D:\\master-prompt\\config.toml).

If config.toml does not exist yet, it is created from config.example.toml.
API keys can be written in config.toml OR set as environment variables
(GROQ_API_KEY, GEMINI_API_KEY) - environment variables are safer.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def bundle_dir() -> Path:
    """Folder with the files shipped with the app (config.example.toml)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """
    Folder that holds config.toml and data/.
      * run from source (run.bat) ...... the project folder, e.g. D:\\master-prompt
      * installed .exe ................. %APPDATA%\\MasterPrompt - every Windows user gets their own
                                         settings + API key, and the install folder stays read-only.
    MASTER_PROMPT_HOME overrides both (handy for testing).
    """
    if os.environ.get("MASTER_PROMPT_HOME"):
        return Path(os.environ["MASTER_PROMPT_HOME"])
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("APPDATA") or Path.home()) / "MasterPrompt"
    return Path(__file__).resolve().parent.parent


APP_DIR = app_dir()
CONFIG_PATH = APP_DIR / "config.toml"
EXAMPLE_CONFIG_PATH = bundle_dir() / "config.example.toml"
DATA_DIR = APP_DIR / "data"


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    api_key_env: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048
    extra_params: dict = field(default_factory=dict)

    def resolved_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "").strip()
        return ""

    @property
    def usable(self) -> bool:
        # Local servers like Ollama don't need a key.
        local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        return bool(self.resolved_key()) or local


@dataclass
class Settings:
    hotkey_enhance: str = "win+shift+p"
    hotkey_enhance_clipboard: str = ""
    preview: bool = True
    auto_select_all: bool = True
    restore_clipboard: bool = True
    default_style: str = "enhance"
    timeout_seconds: float = 30.0
    paste_restore_delay_ms: int = 600
    save_history: bool = True
    auto_prompt: bool = True
    auto_prompt_idle_seconds: float = 3.0
    providers: list[Provider] = field(default_factory=list)
    blocked_apps: list[str] = field(default_factory=list)
    custom_styles: dict[str, dict] = field(default_factory=dict)

    @property
    def usable_providers(self) -> list[Provider]:
        return [p for p in self.providers if p.usable]


class ConfigError(ValueError):
    pass


def ensure_config_file() -> Path:
    if not CONFIG_PATH.exists() and EXAMPLE_CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
    return CONFIG_PATH


def set_provider_key(text: str, provider: str, key: str) -> str:
    """
    Return config.toml text with `api_key = "<key>"` set inside the [[providers]] block
    whose name is `provider`. Edits the text (instead of re-writing the TOML) so the
    user's comments survive.
    """
    key = key.strip()
    if any(c in key for c in '"\\\r\n'):
        raise ConfigError("That API key contains characters a key never has (quotes or line breaks).")
    lines = text.splitlines(keepends=True)
    blocks: list[list[int]] = []  # line numbers of each [[providers]] block
    current: list[int] | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[[providers]]"):
            current = []
            blocks.append(current)
        elif s.startswith("["):
            current = None
        elif current is not None:
            current.append(i)
    for block in blocks:
        names = [i for i in block if re.match(r'\s*name\s*=\s*"' + re.escape(provider) + '"', lines[i])]
        if not names:
            continue
        new_line = f'api_key = "{key}"\n'
        for i in block:
            if re.match(r"\s*api_key\s*=", lines[i]):
                lines[i] = new_line
                break
        else:
            lines.insert(names[0] + 1, new_line)
        return "".join(lines)
    raise ConfigError(f"config.toml has no [[providers]] entry named '{provider}'.")


def save_provider_keys(keys: dict[str, str], path: Path | None = None) -> None:
    """Write {provider name: key} into config.toml (empty keys are skipped)."""
    path = path or ensure_config_file()
    text = path.read_text(encoding="utf-8")
    for name, key in keys.items():
        if key.strip():
            text = set_provider_key(text, name, key)
    path.write_text(text, encoding="utf-8")


def set_auto_prompt(text: str, enabled: bool) -> str:
    """
    Return config.toml text with `auto_prompt = true|false` set inside [behavior].
    Edits the text (instead of re-writing the TOML) so the user's comments survive.
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    end: int | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"\[\s*behavior\s*\]\s*(?:#.*)?$", s):
            start = i
        elif start is not None and s.startswith("["):
            end = i
            break
    if start is None:
        raise ConfigError("config.toml has no [behavior] table.")
    if end is None:
        end = len(lines)
    value = "true" if enabled else "false"
    for i in range(start + 1, end):
        if re.match(r"\s*auto_prompt\s*=", lines[i]):
            comment = ""
            match = re.search(r"(\s+#.*)$", lines[i].rstrip("\r\n"))
            if match:
                comment = match.group(1)
            ending = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f"auto_prompt = {value}{comment}{ending}"
            break
    else:
        lines.insert(end, f"auto_prompt = {value}\n")
    return "".join(lines)


def parse_settings(data: dict) -> Settings:
    s = Settings()
    hk = data.get("hotkeys", {})
    s.hotkey_enhance = str(hk.get("enhance", s.hotkey_enhance))
    s.hotkey_enhance_clipboard = str(hk.get("enhance_clipboard", s.hotkey_enhance_clipboard))

    b = data.get("behavior", {})
    s.preview = bool(b.get("preview", s.preview))
    s.auto_select_all = bool(b.get("auto_select_all", s.auto_select_all))
    s.restore_clipboard = bool(b.get("restore_clipboard", s.restore_clipboard))
    s.default_style = str(b.get("default_style", s.default_style))
    s.timeout_seconds = float(b.get("timeout_seconds", s.timeout_seconds))
    s.paste_restore_delay_ms = int(b.get("paste_restore_delay_ms", s.paste_restore_delay_ms))
    s.save_history = bool(b.get("save_history", s.save_history))
    s.auto_prompt = bool(b.get("auto_prompt", s.auto_prompt))
    idle = float(b.get("auto_prompt_idle_seconds", s.auto_prompt_idle_seconds))
    s.auto_prompt_idle_seconds = idle if idle >= 1 else 1.0

    providers = data.get("providers", [])
    if not isinstance(providers, list):
        raise ConfigError("[[providers]] must be a list of tables")
    for i, p in enumerate(providers):
        for req in ("name", "base_url", "model"):
            if not p.get(req):
                raise ConfigError(f"Provider #{i + 1} is missing '{req}'")
        if p.get("enabled", True) is False:
            continue
        s.providers.append(Provider(
            name=str(p["name"]),
            base_url=str(p["base_url"]).rstrip("/"),
            model=str(p["model"]),
            api_key=str(p.get("api_key", "")),
            api_key_env=str(p.get("api_key_env", "")),
            temperature=float(p.get("temperature", 0.3)),
            max_tokens=int(p.get("max_tokens", 2048)),
            extra_params=dict(p.get("extra_params", {})),
        ))

    apps = data.get("apps", {})
    s.blocked_apps = [str(a).lower() for a in apps.get("blocked", [])]

    styles = data.get("styles", {})
    if isinstance(styles, dict):
        s.custom_styles = {str(k): dict(v) for k, v in styles.items() if isinstance(v, dict)}
    return s


def load_settings(path: Path | None = None) -> Settings:
    path = path or ensure_config_file()
    if not path.exists():
        raise ConfigError(f"No config file found at {path}. Copy config.example.toml to config.toml.")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config.toml has a typo: {e}") from e
    return parse_settings(data)
