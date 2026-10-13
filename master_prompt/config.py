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
from urllib.parse import urlparse


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
    hotkey_auto: str = "ctrl+alt+p"
    hotkey_undo: str = "ctrl+alt+z"
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


# Most specific prefix first. sk-or- and sk-ant- must be checked before sk-.
_KEY_PROVIDERS = (
    ("gsk_", "groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    ("AIza", "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash"),
    ("sk-or-", "openrouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    ("sk-ant-", "anthropic", "https://api.anthropic.com/v1", "claude-sonnet-4-5"),
    ("sk-", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
)


def detect_provider(api_key: str) -> dict[str, str] | None:
    """Tell which service a key belongs to from its prefix. None if it is unknown."""
    key = api_key.strip()
    for prefix, name, base_url, model in _KEY_PROVIDERS:
        if key.startswith(prefix):
            return {"name": name, "base_url": base_url, "model": model, "api_key": key}
    return None


def provider_name_for_url(base_url: str) -> str:
    """Pick a short config name from an OpenAI-compatible base URL."""
    host = (urlparse(base_url).hostname or "custom").lower()
    for needle, name in (
        ("groq.com", "groq"),
        ("googleapis.com", "gemini"),
        ("openai.com", "openai"),
        ("openrouter.ai", "openrouter"),
    ):
        if needle in host:
            return name
    if host in ("localhost", "127.0.0.1"):
        return "local"
    parts = [p for p in host.split(".") if p not in ("www", "api", "com", "ai", "io", "dev", "net")]
    raw = parts[0] if parts else "custom"
    name = re.sub(r"[^a-z0-9]+", "", raw)
    return name or "custom"


def _provider_blocks(lines: list[str]) -> list[list[int]]:
    blocks: list[list[int]] = []
    current: list[int] | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[[providers]]"):
            current = [i]
            blocks.append(current)
        elif s.startswith("["):
            current = None
        elif current is not None:
            current.append(i)
    return blocks


def _assignment_value(line: str, key: str) -> str | None:
    match = re.match(rf'\s*{key}\s*=\s*"([^"]*)"', line)
    return match.group(1) if match else None


def _set_field(block_lines: list[str], key: str, value: str) -> None:
    new = f'{key} = "{value}"\n'
    for i, line in enumerate(block_lines):
        if re.match(rf"\s*{key}\s*=", line):
            block_lines[i] = new
            return
    block_lines.append(new)


def upsert_provider(text: str, *, name: str, base_url: str, model: str, api_key: str) -> str:
    """
    Update the [[providers]] block named `name`, or append one.
    Comments outside that block stay. extra_params is dropped only when the model changes,
    because those options belong to the previous model.
    """
    for label, value in (("name", name), ("base_url", base_url), ("model", model), ("api_key", api_key)):
        if any(c in value for c in '"\\\r\n'):
            raise ConfigError(f"The {label} contains a quote or a line break.")
    if not name or not base_url or not model:
        raise ConfigError("A provider needs a name, a base URL, and a model.")
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for block in _provider_blocks(lines):
        names = [i for i in block if _assignment_value(lines[i], "name") == name]
        if not names:
            continue
        header = block[0]
        content = [lines[i] for i in block[1:]]
        old_model = next((_assignment_value(ln, "model") for ln in content if _assignment_value(ln, "model")), None)
        _set_field(content, "base_url", base_url.rstrip("/"))
        _set_field(content, "model", model)
        _set_field(content, "api_key", api_key)
        if old_model and old_model != model:
            content = [ln for ln in content if not re.match(r"\s*extra_params\s*=", ln)]
        return "".join(lines[: header + 1] + content + lines[block[-1] + 1 :])
    addition = (
        f'\n[[providers]]\nname = "{name}"\nbase_url = "{base_url.rstrip("/")}"\n'
        f'model = "{model}"\napi_key = "{api_key}"\ntemperature = 0.3\n'
    )
    body = "".join(lines).rstrip()
    return (body + "\n" if body else "") + addition


def save_provider(spec: dict[str, str], path: Path | None = None) -> None:
    """Write one provider (name, base URL, model, API key) into config.toml."""
    path = path or ensure_config_file()
    text = path.read_text(encoding="utf-8")
    path.write_text(upsert_provider(
        text,
        name=spec["name"].strip(),
        base_url=spec["base_url"].strip(),
        model=spec["model"].strip(),
        api_key=spec.get("api_key", "").strip(),
    ), encoding="utf-8")


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
    s.hotkey_auto = str(hk.get("auto", s.hotkey_auto))
    s.hotkey_undo = str(hk.get("undo", s.hotkey_undo))

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
