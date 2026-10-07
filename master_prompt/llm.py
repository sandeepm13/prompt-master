"""
Talks to the AI.

Groq, Gemini, OpenRouter, OpenAI, Ollama and LM Studio all accept the same
"OpenAI-compatible" request format, so one small function works for all of
them. We only use Python's built-in urllib - no extra packages.

Reliability: providers are tried in the order written in config.toml.
If the first one fails (no internet, rate limit, server error...), the next
one is tried automatically.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Provider
from .prompts import build_messages, build_screen_messages, clean_output

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class Result:
    text: str
    provider: str
    model: str
    seconds: float


def _friendly_http_error(p: Provider, code: int, body: str) -> str:
    snippet = body[:300].replace("\n", " ")
    if code in (401, 403):
        return f"{p.name}: API key rejected ({code}). Check the key in config.toml."
    if code == 404:
        return f"{p.name}: model '{p.model}' or URL not found (404). Check the model name. {snippet}"
    if code == 429:
        return f"{p.name}: rate limit / free quota reached (429)."
    if code >= 500:
        return f"{p.name}: server error ({code})."
    return f"{p.name}: HTTP {code}: {snippet}"


def call_provider(p: Provider, messages: list[dict], timeout: float) -> str:
    url = p.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": p.model,
        "messages": messages,
        "temperature": p.temperature,
        "max_tokens": p.max_tokens,
        "stream": False,
    }
    payload.update(p.extra_params)
    headers = {"Content-Type": "application/json", "User-Agent": "master-prompt/1.0"}
    key = p.resolved_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise LLMError(_friendly_http_error(p, e.code, body)) from e
    except (socket.timeout, TimeoutError) as e:
        raise LLMError(f"{p.name}: timed out after {timeout:.0f}s.") from e
    except urllib.error.URLError as e:
        raise LLMError(f"{p.name}: can't connect ({e.reason}). Are you online?") from e

    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"{p.name}: unexpected response: {body[:200]}") from e
    if isinstance(content, list):  # some APIs return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = clean_output(content or "")
    if not text:
        raise LLMError(f"{p.name}: returned an empty answer.")
    return text


# gpt-oss accepts only a text string. Groq's vision model accepts the screenshot.
GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
_VISION_WORDS = ("gemini", "gpt-4o", "gpt-4.1", "claude", "qwen", "scout", "vision")


def _sees_images(p: Provider) -> bool:
    label = f"{p.name} {p.model}".casefold()
    return any(word in label for word in _VISION_WORDS)


def provider_for_screen(p: Provider, *, has_image: bool) -> Provider:
    """Use a model that can read a screenshot. Leave text-only calls unchanged."""
    if not has_image or _sees_images(p):
        return p
    if p.name.casefold() == "groq":
        return dataclasses.replace(p, model=GROQ_VISION_MODEL, extra_params={})
    return p


def _vision_first(providers: list[Provider]) -> list[Provider]:
    """Models that can see a screenshot go first. The others stay as backups."""
    return sorted(providers, key=lambda p: 0 if _sees_images(p) else 1)


def enhance_screen(draft: str, image_jpeg: bytes | None, providers: list[Provider], *,
                   window_title: str = "", timeout: float = 45.0) -> Result:
    """Rewrite a draft using the screen-aware prompt and, when present, a screenshot."""
    if not draft.strip() and not image_jpeg:
        raise LLMError("There is no text to improve.")
    usable = [p for p in providers if p.usable]
    if not usable:
        raise LLMError("No API key found. Open config.toml (tray icon > Open config) and add your Groq or Gemini key.")
    if image_jpeg:
        usable = _vision_first(provider_for_screen(p, has_image=True) for p in usable)
    messages = build_screen_messages(draft, image_jpeg, window_title)
    text_messages = build_screen_messages(draft, None, window_title)
    errors: list[str] = []
    for p in usable:
        start = time.monotonic()
        try:
            try:
                text = call_provider(p, messages, timeout)
            except LLMError as e:
                if not image_jpeg or "must be a string" not in str(e):
                    raise
                log.warning("%s cannot see the screenshot; rewriting from the draft", p.name)
                text = call_provider(p, text_messages, timeout)
            secs = time.monotonic() - start
            log.info("Screen prompt with %s/%s in %.1fs", p.name, p.model, secs)
            return Result(text=text, provider=p.name, model=p.model, seconds=secs)
        except LLMError as e:
            log.warning("%s", e)
            errors.append(str(e))
    raise LLMError("All providers failed:\n- " + "\n- ".join(errors))


def enhance(draft: str, providers: list[Provider], *, style: str = "enhance",
            custom_styles: dict | None = None, timeout: float = 30.0) -> Result:
    if not draft.strip():
        raise LLMError("There is no text to improve.")
    usable = [p for p in providers if p.usable]
    if not usable:
        raise LLMError("No API key found. Open config.toml (tray icon > Open config) and add your Groq or Gemini key.")

    messages = build_messages(draft, style, custom_styles)
    errors: list[str] = []
    for p in usable:
        start = time.monotonic()
        try:
            text = call_provider(p, messages, timeout)
            secs = time.monotonic() - start
            log.info("Enhanced with %s/%s in %.1fs", p.name, p.model, secs)
            return Result(text=text, provider=p.name, model=p.model, seconds=secs)
        except LLMError as e:
            log.warning("%s", e)
            errors.append(str(e))
    raise LLMError("All providers failed:\n- " + "\n- ".join(errors))
