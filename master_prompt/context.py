"""
Where is the user typing? Worked out fresh on every click of the dot or Ctrl+Alt+P.

Coding tools and AI chats already have the project and the conversation, so only
what makes this one prompt exact is sent, in layers:

  base          always: where the user is typing, the project name, the user's note
  conversation  only when the draft is short or points back ("this", "it", "again"):
                the session title and the newest message, plus older messages about
                the same thing (coding assistants); the chat title and the visible
                messages (AI chats)
  project       only for a feature or docs request in a coding tool outside an
                assistant chat: the README's first line and the stack

Source code, git and the assistants' replies are never read.

- Coding tools (VS Code, Cursor, Windsurf, JetBrains...): the project, and the
  session of Claude Code, Codex or Copilot Chat when the user is in one.
- Browsers: the website, from the address bar. Only the domain is kept.
- Other apps: the app itself (WhatsApp, Outlook, Notepad...).

The user's own note ("My context") is saved per place: per project, per website,
per app. A note written for one project is never used in another.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

KIND_LABELS = {"ide": "coding tool", "ai_chat": "AI chat", "messaging": "messaging app", "email": "email"}

IDES = {
    "code.exe": "VS Code", "code - insiders.exe": "VS Code Insiders", "cursor.exe": "Cursor",
    "windsurf.exe": "Windsurf", "devenv.exe": "Visual Studio", "zed.exe": "Zed",
    "sublime_text.exe": "Sublime Text", "idea64.exe": "IntelliJ IDEA", "pycharm64.exe": "PyCharm",
    "webstorm64.exe": "WebStorm", "phpstorm64.exe": "PhpStorm", "clion64.exe": "CLion", "rider64.exe": "Rider",
    "goland64.exe": "GoLand", "rubymine64.exe": "RubyMine", "rustrover64.exe": "RustRover",
    "studio64.exe": "Android Studio", "eclipse.exe": "Eclipse", "fleet.exe": "Fleet",
}
_JETBRAINS = {"idea64.exe", "pycharm64.exe", "webstorm64.exe", "phpstorm64.exe", "clion64.exe", "rider64.exe",
              "goland64.exe", "rubymine64.exe", "rustrover64.exe", "studio64.exe", "fleet.exe"}
# VS Code family -> its folder in %APPDATA%, which lists the folders open in each window.
_VSCODE_DATA = {"code.exe": "Code", "code - insiders.exe": "Code - Insiders", "cursor.exe": "Cursor",
                "windsurf.exe": "Windsurf"}

BROWSERS = {
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "brave.exe": "Brave", "firefox.exe": "Firefox",
    "opera.exe": "Opera", "vivaldi.exe": "Vivaldi", "arc.exe": "Arc",
}

APPS = {
    "whatsapp.exe": ("messaging", "WhatsApp"), "whatsapp.root.exe": ("messaging", "WhatsApp"),
    "telegram.exe": ("messaging", "Telegram"), "slack.exe": ("messaging", "Slack"),
    "discord.exe": ("messaging", "Discord"), "ms-teams.exe": ("messaging", "Microsoft Teams"),
    "teams.exe": ("messaging", "Microsoft Teams"), "signal.exe": ("messaging", "Signal"),
    "outlook.exe": ("email", "Outlook"), "olk.exe": ("email", "Outlook"),
    "thunderbird.exe": ("email", "Thunderbird"), "hxoutlook.exe": ("email", "Mail"),
    "chatgpt.exe": ("ai_chat", "ChatGPT"), "claude.exe": ("ai_chat", "Claude"),
    "perplexity.exe": ("ai_chat", "Perplexity"),
}

SITES = {
    "chatgpt.com": ("ai_chat", "ChatGPT"), "chat.openai.com": ("ai_chat", "ChatGPT"),
    "claude.ai": ("ai_chat", "Claude"), "gemini.google.com": ("ai_chat", "Gemini"),
    "aistudio.google.com": ("ai_chat", "Google AI Studio"), "perplexity.ai": ("ai_chat", "Perplexity"),
    "copilot.microsoft.com": ("ai_chat", "Copilot"), "chat.deepseek.com": ("ai_chat", "DeepSeek"),
    "grok.com": ("ai_chat", "Grok"), "poe.com": ("ai_chat", "Poe"), "chat.mistral.ai": ("ai_chat", "Le Chat"),
    "v0.dev": ("ai_chat", "v0"), "v0.app": ("ai_chat", "v0"), "lovable.dev": ("ai_chat", "Lovable"),
    "bolt.new": ("ai_chat", "Bolt"),
    "mail.google.com": ("email", "Gmail"), "outlook.live.com": ("email", "Outlook"),
    "outlook.office.com": ("email", "Outlook"), "outlook.office365.com": ("email", "Outlook"),
    "mail.yahoo.com": ("email", "Yahoo Mail"), "mail.proton.me": ("email", "Proton Mail"),
    "web.whatsapp.com": ("messaging", "WhatsApp"), "web.telegram.org": ("messaging", "Telegram"),
    "app.slack.com": ("messaging", "Slack"), "discord.com": ("messaging", "Discord"),
    "teams.microsoft.com": ("messaging", "Microsoft Teams"), "teams.live.com": ("messaging", "Microsoft Teams"),
    "messenger.com": ("messaging", "Messenger"),
}


@dataclass
class Context:
    kind: str = "general"       # ide | ai_chat | messaging | email | general
    app: str = ""               # "VS Code", "ChatGPT", "WhatsApp", "Notepad"
    scope_key: str = ""         # where the user's note is saved: project:..., site:..., app:...
    scope_name: str = ""        # shown in the popup: "prompt-master", "claude.ai", "WhatsApp"
    project: str = ""
    project_path: str = ""
    stack: tuple[str, ...] = ()
    about: str = ""
    site: str = ""
    chat_title: str = ""        # AI chats: the conversation's title, from the window title
    recent: str = ""            # AI chats: the conversation visible above the input box
    session_tool: str = ""      # coding tools: "Claude Code", "Codex" or "Copilot Chat"
    session_title: str = ""     # coding tools: that session's title
    session_messages: tuple[str, ...] = ()  # coding tools: the user's last typed messages, oldest first
    note: str = ""

    @property
    def in_session(self) -> bool:
        """The user is typing their next message in a coding-assistant conversation."""
        return bool(self.session_title or self.session_messages)

    def summary(self) -> str:
        """One line for the popup, so the user sees what can be used."""
        if self.kind == "ide":
            bits = [self.project or self.app]
            if self.in_session:
                tool = self.session_tool or "Claude Code"
                bits.append(f"{tool} “{_short(self.session_title, 35)}”" if self.session_title else f"{tool} chat")
            else:
                bits.extend(self.stack[:2])
        elif self.chat_title:
            bits = [self.app, f"“{_short(self.chat_title, 45)}”"]
        else:
            bits = [self.app, KIND_LABELS.get(self.kind, "")]
        if self.recent:
            bits.append("+ visible messages")
        return " · ".join(b for b in bits if b)

    def layers(self, draft: str = "") -> list[str]:
        """The layers of context this draft needs (see the module docstring). "base" is always sent."""
        chosen = ["base"]
        if self.kind == "ide" and not self.in_session and (self.stack or self.about) and wants_project(draft):
            chosen.append("project")
        if (self.in_session or self.chat_title or self.recent) and needs_conversation(draft):
            chosen.append("conversation")
        return chosen

    def describe(self, draft: str = "") -> str:
        """The <context> block sent to the AI with the draft: only the layers it needs.

        Stable parts come first and the conversation last, so a provider that caches
        the start of a repeated prompt can reuse it, and the conversation is the
        freshest thing the model reads before the draft.
        """
        layers = self.layers(draft)
        tool = self.session_tool or "Claude Code"
        if self.in_session:
            where = f"the {tool} chat in {self.app or 'their editor'}"
        else:
            where = self.app or "an app"
            if self.kind in KIND_LABELS:
                where += f" ({KIND_LABELS[self.kind]})"
            if self.site:
                where += " in a web browser"
        lines = [f"Where the user is typing: {where}"]
        if self.project:
            lines.append(f"Project: {self.project}")
        if self.note:
            lines.append(f"The user's own note about their current work: {self.note}")
        if self.in_session:
            lines.append(f"The draft is the user's next message in this {tool} conversation. "
                         "The assistant already has the project, its files and the conversation.")
        if "project" in layers:
            if self.about:
                lines.append(f"About the project: {self.about}")
            if self.stack:
                lines.append("Tech stack: " + ", ".join(self.stack))
        if "conversation" in layers:
            messages = pick_messages(self.session_messages, draft)
            if self.session_title and not messages:
                # The title is set once at the start of a session, so it goes stale. The messages say what is
                # going on now; the title is only used when there are none.
                lines.append(f"{tool} session: {self.session_title}")
            if messages:
                lines.append(f"The user's latest messages in their {tool} session (oldest first):\n"
                             + "\n".join(f"- {m}" for m in messages))
            if self.chat_title:
                lines.append(f"Conversation title: {self.chat_title}")
            if self.recent:
                lines.append("Recent messages visible on screen (latest last; they include the AI's replies):\n"
                             + self.recent)
        return "\n".join(lines)


# ------------------------------------------------------------------ layers
MESSAGES_SENT = 3           # the newest message, plus older ones that share words with it or the draft
# Not "that", "there", "here": too common in self-contained sentences ("a script that renames...").
_REFERS_BACK = set("this it its it's these those same again above previous earlier still".split())
_FOLLOW_UP_START = ("and ", "also ", "now ", "but ", "instead ", "why ", "then ", "what about ", "how about ")
_PROJECT_WORDS = set("""
add implement build create feature support setup install document documentation docs readme architecture structure
""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", (text or "").lower())


def needs_conversation(draft: str) -> bool:
    """Only a draft that follows on from the chat needs it: one that points back ("this", "it",
    "again") or starts like a follow-up ("and", "why", "what about"). A new idea, even a short
    one like "i want to build something", is sent without the chat, or the chat's old topic
    gets pulled into it."""
    words = _words(draft)
    return any(w in _REFERS_BACK for w in words) or " ".join(words + [""]).startswith(_FOLLOW_UP_START)


def wants_project(draft: str) -> bool:
    """A new feature or a docs request is helped by knowing what the project is."""
    return any(w in _PROJECT_WORDS for w in _words(draft))


def pick_messages(messages: tuple[str, ...], draft: str, keep: int = MESSAGES_SENT) -> tuple[str, ...]:
    """The newest message always; older ones only when they are about the same thing. Oldest first."""
    if not messages:
        return ()
    *older, newest = messages
    want = keywords(draft) | keywords(newest)
    related = [m for m in older if keywords(m) & want][-(keep - 1):] if keep > 1 else []
    return (*(_short(m, OLDER_MESSAGE_CHARS) for m in related), newest)


# ------------------------------------------------------------------ detect
def detect(hwnd: int, notes: dict[str, str], *, read_chat: bool = True) -> Context:
    """Work out the context of the window the user is typing in.

    read_chat: in AI chats, also read the conversation visible above the input box.
    """
    from . import focus, winapi

    process = winapi.process_name(hwnd).lower()
    title = winapi.window_title(hwnd)
    ctx = Context()
    if process in IDES:
        ctx.kind, ctx.app = "ide", IDES[process]
        ctx.project, _ = parse_ide_title(title, process)
        path = find_project_path(process, ctx.project) if process in _VSCODE_DATA else None
        if path:
            ctx.project_path = str(path)
            ctx.stack, ctx.about = project_brief(path)
            if read_chat:
                add_session_details(ctx, focus.focused_labels(), process)
        ctx.scope_key = f"project:{(ctx.project_path or ctx.project).casefold()}" if ctx.project else f"app:{process}"
        ctx.scope_name = ctx.project or ctx.app
    elif process in BROWSERS:
        domain = domain_of(focus.read_address_bar(hwnd))
        kind, name = site_kind(domain)
        ctx.kind, ctx.site = kind, domain
        ctx.app = name or domain or BROWSERS[process]
        ctx.scope_key = f"site:{domain}" if domain else f"app:{process}"
        ctx.scope_name = domain or BROWSERS[process]
    else:
        ctx.kind, ctx.app = APPS.get(process, ("general", app_label(process)))
        ctx.scope_key, ctx.scope_name = f"app:{process}", ctx.app
    if ctx.kind == "ai_chat":
        # Chat and email titles hold names and subjects, so only AI chats use the title.
        ctx.chat_title = redact(chat_title(title, ctx.app))
        if read_chat:
            ctx.recent = visible_chat_text(*focus.read_visible_chat(hwnd))
    ctx.note = notes.get(ctx.scope_key, "")
    log.info("Context: %s (%s)", ctx.summary(), ctx.scope_key)
    return ctx


def app_label(process: str) -> str:
    stem = Path(process).stem
    return stem[:1].upper() + stem[1:] if stem else "this app"


# ------------------------------------------------------------- IDE window
_FILE_NAME = re.compile(r"^[^\\/:*?\"<>|]+\.[A-Za-z0-9]{1,10}$")


def parse_ide_title(title: str, process: str = "code.exe") -> tuple[str, str]:
    """(project, file) from a window title.

    VS Code family: "● box.py - prompt-master - Visual Studio Code"
    JetBrains:      "prompt-master – box.py"
    """
    t = re.sub(r"\s*\[(?:Administrator|Unsupported|Extension Development Host)\]", "", title or "")
    t = t.strip().lstrip("●*• ").strip()
    parts = [p.strip() for p in re.split(r"\s[-–—]\s", t) if p.strip()]
    if not parts:
        return "", ""
    if process in _JETBRAINS:
        project = parts[0]
        file = parts[-1] if len(parts) > 1 and _FILE_NAME.match(parts[-1]) else ""
        return _clean_project(project), file
    if len(parts) > 1:
        parts = parts[:-1]  # the app name
    if len(parts) == 1:
        only = parts[0]
        return ("", only) if _FILE_NAME.match(only) else (_clean_project(only), "")
    file = parts[0] if _FILE_NAME.match(parts[0]) else ""
    return _clean_project(parts[1]), file


def _clean_project(name: str) -> str:
    return re.sub(r"\s*\(Workspace\)$", "", name).strip()


_storage_cache: dict[str, tuple[float, list[str]]] = {}


def open_folders(process: str) -> list[str]:
    """Folders VS Code (or Cursor, Windsurf) has open or opened recently, newest first."""
    data_dir = _VSCODE_DATA.get(process)
    if not data_dir:
        return []
    cached = _storage_cache.get(data_dir)
    if cached and time.monotonic() - cached[0] < 10:
        return cached[1]
    path = Path(os.environ.get("APPDATA", "")) / data_dir / "User" / "globalStorage" / "storage.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    folders = folders_from_storage(state)
    _storage_cache[data_dir] = (time.monotonic(), folders)
    return folders


def folders_from_storage(state: dict) -> list[str]:
    uris: list[str] = []
    backups = state.get("backupWorkspaces") or {}
    uris += [f.get("folderUri", "") for f in backups.get("folders", []) if isinstance(f, dict)]
    windows = state.get("windowsState") or {}
    uris += [w.get("folder", "") for w in [windows.get("lastActiveWindow") or {}, *windows.get("openedWindows", [])]
             if isinstance(w, dict)]
    uris += list(((state.get("profileAssociations") or {}).get("workspaces") or {}).keys())
    paths: list[str] = []
    for uri in uris:
        path = _file_uri_to_path(uri)
        if path and path not in paths:
            paths.append(path)
    return paths


def find_project_path(process: str, project: str) -> Path | None:
    if not project:
        return None
    for folder in open_folders(process):
        path = Path(folder)
        if path.name.casefold() == project.casefold() and path.is_dir():
            return path
    return None


# ----------------------------------------------------------- project brief
_brief_cache: dict[str, tuple[float, tuple[tuple[str, ...], str]]] = {}

_JS = {
    "next": "Next.js", "react": "React", "vue": "Vue", "svelte": "Svelte", "@angular/core": "Angular",
    "react-native": "React Native", "electron": "Electron", "express": "Express", "@nestjs/core": "NestJS",
    "prisma": "Prisma", "@prisma/client": "Prisma", "drizzle-orm": "Drizzle", "mongoose": "MongoDB",
    "tailwindcss": "Tailwind CSS", "vite": "Vite", "remotion": "Remotion", "three": "Three.js",
}
_PY = {
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI", "streamlit": "Streamlit", "torch": "PyTorch",
    "tensorflow": "TensorFlow", "pandas": "pandas", "scikit-learn": "scikit-learn", "langchain": "LangChain",
    "sqlalchemy": "SQLAlchemy", "celery": "Celery", "playwright": "Playwright", "selenium": "Selenium",
    "pyqt5": "PyQt", "pyqt6": "PyQt", "pyside6": "PySide",
}


def project_brief(path: Path) -> tuple[tuple[str, ...], str]:
    """(stack, about) for a project folder. Cached for 5 minutes."""
    key = str(path)
    cached = _brief_cache.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    brief = (tuple(detect_stack(path)), readme_line(path))
    _brief_cache[key] = (time.monotonic(), brief)
    return brief


def _read(path: Path, limit: int = 20000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def detect_stack(path: Path) -> list[str]:
    """Language and main frameworks, read from the project's manifest files only."""
    stack: list[str] = []
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(_read(pkg, 200000))
        except ValueError:
            data = {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        stack.append("TypeScript" if "typescript" in deps or (path / "tsconfig.json").is_file() else "JavaScript")
        stack += [name for dep, name in _JS.items() if dep in deps]
    py_names = _python_requirements(path)
    if py_names is not None:
        stack.append("Python")
        stack += [name for dep, name in _PY.items() if dep in py_names]
    for marker, name in (("go.mod", "Go"), ("Cargo.toml", "Rust"), ("pom.xml", "Java (Maven)"),
                         ("build.gradle", "Java/Kotlin (Gradle)"), ("build.gradle.kts", "Kotlin (Gradle)"),
                         ("composer.json", "PHP"), ("Gemfile", "Ruby"), ("pubspec.yaml", "Flutter/Dart")):
        if (path / marker).is_file():
            stack.append(name)
    try:
        if any(path.glob("*.csproj")) or any(path.glob("*.sln")):
            stack.append("C#/.NET")
    except OSError:
        pass
    unique = list(dict.fromkeys(stack))
    return unique[:6]


def _python_requirements(path: Path) -> set[str] | None:
    """Package names from requirements.txt / pyproject.toml, or None when it isn't a Python project."""
    names: set[str] = set()
    found = False
    req = path / "requirements.txt"
    if req.is_file():
        found = True
        for line in _read(req).splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower())
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        found = True
        try:
            data = tomllib.loads(_read(pyproject, 200000))
        except tomllib.TOMLDecodeError:
            data = {}
        for dep in (data.get("project") or {}).get("dependencies") or []:
            names.add(re.split(r"[<>=!~\[; ]", str(dep), maxsplit=1)[0].lower())
        poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        names.update(str(k).lower() for k in poetry)
    if (path / "setup.py").is_file() or (path / "Pipfile").is_file():
        found = True
    return names if found else None


def readme_line(path: Path) -> str:
    """The first real sentence of the README: what the project is."""
    for name in ("README.md", "readme.md", "Readme.md", "README.rst", "README.txt", "README"):
        text = _read(path / name, 6000)
        if text:
            break
    else:
        return ""
    in_code = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or len(line) < 20 or line.startswith(("#", "<", "!", "[!", "[![", ">", "|", "---", "===", "*", "-")):
            continue
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links -> their text
        line = re.sub(r"[*_`]", "", line).strip()
        if len(line) < 20:
            continue
        first = re.match(r"(.{30,}?[.!?])\s", line + " ")
        if first:
            line = first.group(1)  # the first sentence is enough
        if len(line) > 200:
            line = line[:200].rsplit(" ", 1)[0] + "…"
        return line
    return ""


# ------------------------------------------------------------ project rules
# Files people write for AI coding assistants: short, hand-written, the best context there is.
RULES_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md",
               ".cursorrules", ".windsurfrules")
RULES_GLOBS = (".cursor/rules/*.mdc", ".cursor/rules/*.md", ".windsurf/rules/*.md")
RULES_BUDGET = 1500        # characters of rules sent with one draft
_RULES_READ_LIMIT = 20000  # characters read from all rules files together
_rules_cache: dict[str, tuple[float, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]]] = {}


def _rules_paths(path: Path) -> list[str]:
    found = [name for name in RULES_FILES if (path / name).is_file()]
    for pattern in RULES_GLOBS:
        try:
            found += [p.relative_to(path).as_posix() for p in sorted(path.glob(pattern))[:8] if p.is_file()]
        except OSError:
            pass
    return found


def clean_rules(text: str) -> str:
    """The prose of a rules file: no front matter, no HTML comments, no code blocks, no secrets."""
    t = (text or "").lstrip("﻿")
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            t = t[end + 4:].split("\n", 1)[-1]
    t = re.sub(r"<!--.*?-->", "", t, flags=re.DOTALL)
    kept, in_code = [], False
    for line in t.splitlines():
        if line.strip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if not in_code:
            kept.append(line.rstrip())
    return redact("\n".join(kept)).strip()


def rules_blocks(text: str, max_block: int = 400) -> list[tuple[str, str]]:
    """(heading, text) blocks: paragraphs and bullet groups under their heading."""
    blocks: list[tuple[str, str]] = []
    heading, current = "", []

    def flush() -> None:
        if current:
            blocks.append((heading, _short("\n".join(current), max_block + 100)))
            current.clear()

    for raw in text.splitlines():
        line = raw.strip()
        title = re.match(r"#{1,6}\s+(.*)", line)
        if title:
            flush()
            heading = title.group(1).strip()
        elif not line:
            flush()
        else:
            current.append(line)
            if sum(len(c) for c in current) >= max_block:
                flush()
    flush()
    return blocks


def project_rules(path: Path) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """(file names, blocks) from the project's rules files. Cached for 5 minutes.

    CLAUDE.md and AGENTS.md are often the same file (or a symlink), so a copy is read once.
    """
    key = str(path)
    cached = _rules_cache.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    names: list[str] = []
    blocks: list[tuple[str, str]] = []
    seen: set[str] = set()
    used = 0
    for name in _rules_paths(path):
        text = clean_rules(_read(path / name, _RULES_READ_LIMIT))
        if not text or text in seen:
            continue
        seen.add(text)
        names.append(name)
        blocks += rules_blocks(text)
        used += len(text)
        if used >= _RULES_READ_LIMIT:
            break
    result = (tuple(names), tuple(blocks))
    _rules_cache[key] = (time.monotonic(), result)
    return result


def render_rules(blocks: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    last = None
    for heading, text in blocks:
        if heading and heading != last:
            lines.append(f"{heading}:")
        last = heading
        lines.append(text)
    return "\n".join(lines)


# ---------------------------------------------------- picking what to send
_STOP_WORDS = set("""
the and for with this that these those from have has had are was were not you your our use using all any can
could will would should when then than into but its also just only must dont does did get got make made like
need want some more most very each other such them they their there here what which who how why where about
over under after before because been being out off may might let new add fix file files code src please
""".split())


def keywords(text: str) -> set[str]:
    """Words that say what a text is about. Splits camelCase, snake_case and paths; drops filler."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    words: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 3 or word.isdigit() or word in _STOP_WORDS:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # "buttons" matches "button"
        words.add(word)
    return words


def pick_relevant(items: list, draft: str, budget: int, *, text_of=str, size=len) -> list:
    """The items worth sending with this draft, in their original order.

    Everything fits: all of it, unchanged, so the prompt start stays the same between clicks.
    Too much: the items sharing the most words with the draft, then the earliest ones, until the budget is used.
    """
    if sum(size(item) for item in items) <= budget:
        return list(items)
    want = keywords(draft)
    ranked = sorted(range(len(items)), key=lambda i: (-len(keywords(text_of(items[i])) & want), i))
    chosen: list[int] = []
    used = 0
    for i in ranked:
        if used + size(items[i]) <= budget:
            chosen.append(i)
            used += size(items[i])
    return [items[i] for i in sorted(chosen)]


_SECRET_FILE = re.compile(r"(^|/)(\.env[^/]*|.*\.(pem|key|p12|pfx)|id_rsa[^/]*|.*secret[^/]*|credentials[^/]*)$",
                          re.IGNORECASE)


def parse_git_status(porcelain: str, limit: int = 8) -> tuple[str, ...]:
    """Changed file names from `git status --porcelain`. Secrets files are left out."""
    files: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4 or "D" in line[:2]:
            continue  # deleted files are not what the user is working on
        name = line[3:].split(" -> ")[-1].strip().strip('"')
        if name and not _SECRET_FILE.search(name) and name not in files:
            files.append(name)
    return tuple(files[:limit])


CHANGED_FILES_READ = 40    # read this many from git...
CHANGED_FILES_SHOWN = 8    # ...and send the ones that fit the draft best


def _git(path: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=2,
                             encoding="utf-8", errors="replace", creationflags=0x08000000)  # no console window
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_status(path: Path) -> tuple[str, tuple[str, ...]]:
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if not branch:
        return "", ()
    return branch, parse_git_status(_git(path, "status", "--porcelain=v1", "--untracked-files=normal"),
                                    limit=CHANGED_FILES_READ)


def parse_commits(log_text: str, limit: int = 5) -> tuple[str, ...]:
    """Commit subjects, one per line, newest first. They say what the user is working on."""
    subjects: list[str] = []
    for line in log_text.splitlines():
        line = _short(redact(" ".join(line.split())), 100)
        if line and line not in subjects:
            subjects.append(line)
    return tuple(subjects[:limit])


def recent_commits(path: Path, limit: int = 5) -> tuple[str, ...]:
    return parse_commits(_git(path, "log", f"-n{limit}", "--no-merges", "--format=%s"), limit)


# ------------------------------------------------- Claude Code sessions
SESSION_MAX_AGE = 6 * 3600   # a session untouched for longer is not the one being used
_SESSION_TAIL_BYTES = 600_000


def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def claude_session_dir(project_path: str, root: Path | None = None) -> Path | None:
    """Claude Code keeps one folder per project: D:\\master-promot\\prompt-master -> d--master-promot-prompt-master."""
    root = root or claude_projects_dir()
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project_path)).casefold()
    try:
        return next((d for d in root.iterdir() if d.is_dir() and d.name.casefold() == slug), None)
    except OSError:
        return None


def latest_claude_session(project_path: str, *, root: Path | None = None, now: float | None = None) -> Path | None:
    """The session file written most recently: the one the user is in."""
    folder = claude_session_dir(project_path, root)
    if folder is None:
        return None
    try:
        newest = max(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)
        if newest is None or (now or time.time()) - newest.stat().st_mtime > SESSION_MAX_AGE:
            return None
    except OSError:
        return None
    return newest


_PASTE_TAG = re.compile(r"</?pasted_content\b[^>]*>")


def _typed_text(content) -> str:
    """Only what the user typed or pasted: no tool results, no IDE notes, no system text."""
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return ""
    # Claude Code wraps pasted text in <pasted_content>. It is the user's message, so unwrap it.
    parts = [_PASTE_TAG.sub("", p) for p in parts]
    keep = [p.strip() for p in parts if p.strip() and not p.lstrip().startswith(("<", "[Request interrupted"))]
    return "\n".join(keep)


_SESSION_MAX_BYTES = 8_000_000


def _read_tail(path: Path, parse, keep: int) -> tuple[str, tuple[str, ...]]:
    """Run parse(lines, keep) on the end of a JSONL file.

    Long tool output can push the user's messages further back, so the window
    grows until enough are found (up to 8 MB). The rest of the file is never read.
    """
    tail = _SESSION_TAIL_BYTES
    while True:
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - tail))
                data = f.read().decode("utf-8", errors="ignore")
        except OSError:
            return "", ()
        lines = data.splitlines()
        if size > tail:
            lines = lines[1:]  # the first line was cut in half
        title, messages = parse(lines, keep)
        if len(messages) >= keep or size <= tail or tail >= _SESSION_MAX_BYTES:
            return title, messages
        tail *= 2


MESSAGES_READ = 6           # pick_messages() chooses which of these are sent
MESSAGE_CHARS = 500         # the newest message
OLDER_MESSAGE_CHARS = 220   # older ones only say what the conversation is about


def _clean_message(text: str) -> str:
    return _short(redact(" ".join(text.split())), MESSAGE_CHARS)


def read_claude_session(path: Path, keep: int = MESSAGES_READ) -> tuple[str, tuple[str, ...]]:
    """(session title, the user's last few typed messages). Claude's replies are never read."""
    return _read_tail(path, _parse_session, keep)


def _parse_session(lines: list[str], keep: int) -> tuple[str, tuple[str, ...]]:
    title, custom, typed = "", "", []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind == "custom-title":
            custom = str(entry.get("customTitle") or custom)
        elif kind == "ai-title":
            title = str(entry.get("aiTitle") or title)
        elif kind == "user" and not entry.get("isMeta") and not entry.get("isSidechain"):
            text = _typed_text((entry.get("message") or {}).get("content"))
            if text:
                typed.append(text)
    return redact(custom or title), tuple(_clean_message(t) for t in typed[-keep:])


def _norm_path(path) -> str:
    text = str(path or "").strip()
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text)) if text else ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ------------------------------------------------------- Codex sessions
# Format from the openai/codex source: codex-rs/rollout (file layout, session_index.jsonl),
# codex-rs/history (line envelope) and codex-rs/protocol (SessionMeta, EventMsg, UserInput).
CODEX_REQUEST_PREFIX = "## My request for Codex:"   # protocol.rs USER_MESSAGE_BEGIN
_NOISE = ("<", "# AGENTS.md instructions", "[Request interrupted")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_session_meta(path: Path) -> dict | None:
    """The FIRST session_meta line. Later ones can be copied from a fork's parent."""
    try:
        with open(path, "rb") as f:
            for _ in range(50):
                raw = f.readline()
                if not raw:
                    break
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        pass
    return None


def is_interactive_codex(meta: dict) -> bool:
    """A session the user chats in (VS Code extension or CLI), not a sub-agent or a background run."""
    if meta.get("parent_thread_id") or str(meta.get("thread_source") or "").casefold() == "subagent":
        return False
    source = meta.get("source", "vscode")  # SessionSource defaults to VSCode
    return isinstance(source, str) and source.casefold() in ("vscode", "cli")


def latest_codex_session(project_path: str, *, home: Path | None = None,
                         now: float | None = None) -> tuple[Path, dict] | None:
    """The newest interactive Codex session whose folder is this project.

    Active sessions are plain .jsonl; idle ones may be compressed to .jsonl.zst and are skipped.
    Resumed sessions keep writing to the day folder they started in, so every day is checked.
    """
    root = (home or codex_home()) / "sessions"
    want, now = _norm_path(project_path), now or time.time()
    recent: list[tuple[float, Path]] = []
    for folder, _, names in os.walk(root):
        for name in names:
            if name.startswith("rollout-") and name.endswith(".jsonl"):
                path = Path(folder) / name
                modified = _mtime(path)
                if now - modified <= SESSION_MAX_AGE:
                    recent.append((modified, path))
    for _, path in sorted(recent, reverse=True):
        meta = codex_session_meta(path)
        if meta and _norm_path(meta.get("cwd")) == want and is_interactive_codex(meta):
            return path, meta
    return None


def _codex_user_text(payload: dict) -> str:
    """What the user typed, from a legacy user_message or an item_completed UserMessage event."""
    kind = payload.get("type")
    if kind == "user_message":
        text = str(payload.get("message") or "")
    elif kind == "item_completed":
        item = payload.get("item") or {}
        if not isinstance(item, dict) or item.get("type") != "UserMessage":
            return ""
        text = "\n".join(str(c.get("text", "")) for c in item.get("content") or []
                         if isinstance(c, dict) and c.get("type") == "text")
    else:
        return ""
    if CODEX_REQUEST_PREFIX in text:
        text = text.split(CODEX_REQUEST_PREFIX, 1)[1]
    text = text.strip()
    return "" if text.startswith(_NOISE) else text


def _parse_codex(lines: list[str], keep: int) -> tuple[str, tuple[str, ...]]:
    typed: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "event_msg" and isinstance(entry.get("payload"), dict):
            text = _codex_user_text(entry["payload"])
            if text and (not typed or typed[-1] != text):  # some versions write both event kinds
                typed.append(text)
    return "", tuple(_clean_message(t) for t in typed[-keep:])


def codex_thread_name(thread_id, home: Path | None = None) -> str:
    """The session's name from session_index.jsonl. Renames are appended, so the last entry wins."""
    if not thread_id:
        return ""
    path = (home or codex_home()) / "session_index.jsonl"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2_000_000))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id") == thread_id:
            return str(entry.get("thread_name") or "")
    return ""


def read_codex_session(path: Path, meta: dict, keep: int = MESSAGES_READ,
                       home: Path | None = None) -> tuple[str, tuple[str, ...]]:
    _, messages = _read_tail(path, _parse_codex, keep)
    return redact(codex_thread_name(meta.get("id"), home)), messages


# ------------------------------------------------ Copilot Chat sessions
# Format from the microsoft/vscode source: chatSessionStore.ts (where sessions live),
# objectMutationLog.ts (the .jsonl change log), chatModel.ts and chatSessionOperationLog.ts
# (session fields), storageMain.ts (workspace.json).
_COPILOT_MAX_BYTES = 30_000_000
_workspace_folders: dict[str, str] = {}


def _file_uri_to_path(uri: str) -> str:
    parsed = urlparse(uri or "")
    if parsed.scheme != "file":
        return ""  # remote folders (SSH, WSL, containers) are not on this disk
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def vscode_user_dir(process: str) -> Path | None:
    data_dir = _VSCODE_DATA.get(process)
    return Path(os.environ.get("APPDATA", "")) / data_dir / "User" if data_dir else None


def copilot_session_folders(project_path: str, user_dir: Path) -> list[Path]:
    """chatSessions folders of every workspaceStorage entry whose workspace.json points at this project."""
    want = _norm_path(project_path)
    found: list[Path] = []
    try:
        entries = list((user_dir / "workspaceStorage").iterdir())
    except OSError:
        return found
    for entry in entries:
        key = str(entry)
        if key not in _workspace_folders:
            try:
                meta = json.loads((entry / "workspace.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            _workspace_folders[key] = _file_uri_to_path(meta.get("folder", "")) if isinstance(meta, dict) else ""
        folder = _workspace_folders[key]
        if folder and _norm_path(folder) == want and (entry / "chatSessions").is_dir():
            found.append(entry / "chatSessions")
    return found


def latest_copilot_session(project_path: str, process: str = "code.exe", *, user_dir: Path | None = None,
                           now: float | None = None) -> Path | None:
    user_dir = user_dir or vscode_user_dir(process)
    if user_dir is None:
        return None
    now = now or time.time()
    files = [p for folder in copilot_session_folders(project_path, user_dir)
             for p in folder.iterdir() if p.suffix in (".json", ".jsonl") and p.is_file()]
    newest = max(files, key=_mtime, default=None)
    if newest is None or now - _mtime(newest) > SESSION_MAX_AGE:
        return None
    return newest


def _walk(state, path: list):
    """The container holding path[-1], or None when the path does not exist."""
    current = state
    for key in path[:-1]:
        try:
            current = current[key]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def _assign(container, key, value) -> None:
    try:
        container[key] = value
    except (IndexError, TypeError):
        pass


def replay_chat_log(lines: list[str]) -> dict | None:
    """Rebuild a session from VS Code's change log (ObjectMutationLog).

    kind 0: {"v": full state}    kind 1: {"k": path, "v": value} set
    kind 2: {"k": path, "v": [items], "i": cut} push, after cutting the list at i
    kind 3: {"k": path} delete
    """
    state = None
    for line in lines:
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind, path = entry.get("kind"), entry.get("k") or []
        if kind == 0:
            state = entry.get("v")
            continue
        if state is None or not path:
            continue
        container = _walk(state, path)
        if container is None:
            continue
        if kind in (1, 3):
            _assign(container, path[-1], entry.get("v") if kind == 1 else None)
        elif kind == 2:
            try:
                items = container[path[-1]]
            except (KeyError, IndexError, TypeError):
                items = None
            items = list(items) if isinstance(items, list) else []
            if isinstance(entry.get("i"), int):
                del items[entry["i"]:]
            items.extend(entry.get("v") or [])
            _assign(container, path[-1], items)
    return state if isinstance(state, dict) else None


def _request_text(request) -> str:
    message = request.get("message") if isinstance(request, dict) else None
    if isinstance(message, str):  # the old format
        return message
    return str(message.get("text") or "") if isinstance(message, dict) else ""


def read_copilot_session(path: Path, keep: int = MESSAGES_READ) -> tuple[str, tuple[str, ...]]:
    """(title, the user's last few messages). Copilot's responses are never used."""
    try:
        if path.stat().st_size > _COPILOT_MAX_BYTES:
            return "", ()
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return "", ()
    if path.suffix == ".jsonl":
        data = replay_chat_log(text.splitlines())
    else:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return "", ()
    requests = [r for r in data.get("requests") or [] if isinstance(r, dict)]
    typed = [_request_text(r) for r in requests
             if not (r.get("isHidden") or r.get("hiddenFromTranscript") or r.get("requestHiddenFromTranscript"))]
    typed = [t for t in typed if t.strip()]
    # VS Code's own fallback title: the first line of the first request.
    title = str(data.get("customTitle") or "") or (_request_text(requests[0]).split("\n")[0][:200] if requests else "")
    return redact(title.strip()), tuple(_clean_message(t) for t in typed[-keep:])


# ---------------------------------------------- picking the session in use
def add_session_details(ctx: Context, focus_labels=None, process: str = "code.exe") -> None:
    """In a coding tool, attach the assistant session the user is working in.

    When the focused box clearly belongs to one assistant, only its session is used.
    Otherwise the session written most recently wins: it is the one just chatted in.
    """
    from .focus import agent_for_focus

    if ctx.kind != "ide" or not ctx.project_path:
        return
    owner = agent_for_focus(focus_labels)
    if owner == "other":
        return  # Cursor's own chat, Windsurf Cascade, a commit box: no readable session
    candidates = []
    if owner in (None, "claude"):
        path = latest_claude_session(ctx.project_path)
        if path:
            candidates.append((_mtime(path), "Claude Code", lambda p=path: read_claude_session(p)))
    if owner is None:
        found = latest_codex_session(ctx.project_path)
        if found:
            candidates.append((_mtime(found[0]), "Codex", lambda f=found: read_codex_session(*f)))
    if owner in (None, "copilot"):
        path = latest_copilot_session(ctx.project_path, process)
        if path:
            candidates.append((_mtime(path), "Copilot Chat", lambda p=path: read_copilot_session(p)))
    if not candidates:
        return
    _, tool, read = max(candidates, key=lambda c: c[0])
    title, messages = read()
    if title or messages:
        ctx.session_tool, ctx.session_title, ctx.session_messages = tool, title, messages


# --------------------------------------------------------------- AI chats
_BROWSER_SUFFIX = re.compile(
    r"\s+[-—–]\s+(?:Google Chrome|Brave|Microsoft​?\s?Edge|Mozilla Firefox|Firefox|Opera|Vivaldi|Arc)\s*$",
    re.IGNORECASE)
_NOT_A_TITLE = {"new chat", "new conversation", "new thread", "home", "chat", "recents", "projects"}


def chat_title(window_title: str, site_name: str) -> str:
    """The conversation's title from the window title.

    "Student laptop recommendations under ₹80,000 - Claude - Brave" -> "Student laptop recommendations under ₹80,000"
    A new or untitled chat ("ChatGPT", "New chat") has no title.
    """
    text = _BROWSER_SUFFIX.sub("", window_title or "").strip()
    parts = [re.sub(r"\s+and \d+ more pages?$", "", p.strip()) for p in re.split(r"\s[-—–|]\s", text)]
    skip = _NOT_A_TITLE | {(site_name or "").casefold()} | {name.casefold() for _, name in SITES.values()}
    parts = [p for p in parts if p and p.casefold() not in skip]
    return parts[0] if parts else ""


Rect = tuple[int, int, int, int]


def visible_chat_text(pieces: list[tuple[str, Rect]], box: Rect | None, max_chars: int = 900) -> str:
    """The conversation visible above the input box, latest last.

    Only text that sits above the box and inside its column is kept: the chat list in a
    sidebar, the page header's menus beside it, and the footer below the box are left out.
    """
    if not box:
        return ""
    left, top, right, _ = box
    lines: list[str] = []
    last_top = None
    for text, (l, t, r, b) in pieces:
        text = " ".join((text or "").split())
        if not text or r <= l or b <= t:
            continue
        if b > top + 2 or r <= left or l >= right:
            continue  # below or inside the input box, or outside its column
        if last_top is not None and abs(t - last_top) <= 4 and lines:
            # Same line: bold words and links arrive as separate pieces.
            lines[-1] += text if text[0] in ".,:;!?)%" else " " + text
        else:
            lines.append(text)
        last_top = t
    joined = redact("\n".join(lines))
    if len(joined) <= max_chars:
        return joined
    tail = joined[-max_chars:]
    cut = min((i for i in (tail.find("\n"), tail.find(" ")) if i >= 0), default=0)
    return "…" + tail[cut:].lstrip()


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_ -]?key)\b(\s*[:=]\s*)\S+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}"
               r"|github_pat_[A-Za-z0-9_]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}"
               r"\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
)


def redact(text: str) -> str:
    """Hide API keys, tokens and passwords before anything is sent."""
    text = _SECRET_PATTERNS[0].sub(r"\1\2[REDACTED]", text or "")
    for pattern in _SECRET_PATTERNS[1:]:
        text = pattern.sub("[REDACTED]", text)
    return text


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# ----------------------------------------------------------------- browser
def domain_of(address: str) -> str:
    """"https://www.chatgpt.com/c/123" -> "chatgpt.com". Anything that isn't a web address -> ""."""
    text = (address or "").strip()
    if not text or " " in text:
        return ""
    if "://" not in text:
        text = "https://" + text
    host = (urlparse(text).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def site_kind(domain: str) -> tuple[str, str]:
    """(kind, name) for a website. Unknown sites are "general" with no name."""
    for site, info in SITES.items():
        if domain == site or domain.endswith("." + site):
            return info
    return "general", ""


# ------------------------------------------------------------------- notes
def load_notes(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_note(path: Path, key: str, note: str) -> dict[str, str]:
    """Save (or, when empty, remove) the note for one place. Returns all notes."""
    notes = load_notes(path)
    note = note.strip()
    if note:
        notes[key] = note
    else:
        notes.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
    return notes
