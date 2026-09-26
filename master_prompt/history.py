"""Saves every before/after pair to data/history.jsonl (one JSON object per line)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def append(path: Path, *, original: str, enhanced: str, style: str, provider: str,
           model: str, app: str, seconds: float, action: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": app, "style": style, "provider": provider, "model": model,
            "seconds": round(seconds, 2), "action": action,
            "original": original, "enhanced": enhanced,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Could not write history")
