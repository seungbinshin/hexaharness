from __future__ import annotations

import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hexaharness.io import atomic_write_text, compact_tail, redact_argv, slugify
from hexaharness.paths import HarnessPaths


@dataclass(frozen=True)
class CapturedExecution:
    exit_code: int | None
    duration_seconds: float
    output_path: str
    summary: str
    timed_out: bool


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def execute_capture(
    project_root: Path,
    *,
    task_id: str,
    label: str,
    argv: list[str],
    timeout_seconds: int,
) -> CapturedExecution:
    redacted = redact_argv(argv)
    digest = hashlib.sha256("\0".join(argv).encode()).hexdigest()[:10]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{timestamp}-{slugify(label, limit=24)}-{digest}.log"
    output_file = HarnessPaths(project_root).task_artifacts(task_id) / filename
    started = time.monotonic()
    exit_code: int | None
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            argv,
            cwd=project_root,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except FileNotFoundError as error:
        exit_code = 127
        stderr = f"command not found: {error.filename}"
    except subprocess.TimeoutExpired as error:
        exit_code = None
        timed_out = True
        stdout = _as_text(error.stdout)
        stderr = _as_text(error.stderr)
        stderr = f"{stderr}\ncommand timed out after {timeout_seconds} seconds".strip()
    duration = time.monotonic() - started
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    summary = compact_tail(combined) or "command produced no output"
    log = (
        f"command: {shlex.join(redacted)}\n"
        f"exit_code: {exit_code}\n"
        f"timed_out: {str(timed_out).lower()}\n"
        f"duration_seconds: {duration:.6f}\n"
        "\n[stdout]\n"
        f"{stdout}\n"
        "\n[stderr]\n"
        f"{stderr}\n"
    )
    atomic_write_text(output_file, log)
    relative = output_file.relative_to(project_root).as_posix()
    return CapturedExecution(
        exit_code=exit_code,
        duration_seconds=duration,
        output_path=relative,
        summary=summary,
        timed_out=timed_out,
    )
