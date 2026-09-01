from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SECRET_FLAG_NAMES = {
    "--api-key",
    "--password",
    "--secret",
    "--token",
    "-p",
}
SECRET_ASSIGNMENT = re.compile(r"(?i)(api[_-]?key|password|secret|token)=")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)


def slugify(value: str, *, fallback: str = "task", limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:limit].rstrip("-")


def redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for part in argv:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if part.lower() in SECRET_FLAG_NAMES:
            redacted.append(part)
            redact_next = True
            continue
        if SECRET_ASSIGNMENT.search(part):
            key = part.split("=", 1)[0]
            redacted.append(f"{key}=<redacted>")
            continue
        redacted.append(part)
    return redacted


def compact_tail(value: str, *, limit: int = 2000) -> str:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized
    return f"...<truncated>\n{normalized[-limit:]}"
