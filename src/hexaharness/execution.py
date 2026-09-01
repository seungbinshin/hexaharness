from __future__ import annotations

import hashlib
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hexaharness.io import (
    atomic_write_text,
    compact_tail,
    redact_argv,
    redact_text_using_argv,
    redact_text_using_secret_values,
    sensitive_environment_values,
    slugify,
)
from hexaharness.paths import HarnessPaths


@dataclass(frozen=True)
class CapturedExecution:
    exit_code: int | None
    duration_seconds: float
    output_path: str
    summary: str
    timed_out: bool
    stopped: bool


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate the isolated command group, then force it down after a short grace period."""
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif os.name == "nt":  # pragma: no cover - exercised on Windows
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        else:  # pragma: no cover - platform-specific fallback
            process.terminate()
        process.wait(timeout=2)
        return
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercised on non-POSIX platforms
            process.kill()
    except (OSError, ProcessLookupError):
        return


def execute_capture(
    project_root: Path,
    *,
    task_id: str,
    label: str,
    argv: list[str],
    timeout_seconds: int,
) -> CapturedExecution:
    redacted = redact_argv(argv)
    # The stable suffix groups equivalent command shapes without making the filename
    # an offline oracle for credential values.
    digest = hashlib.sha256("\0".join(redacted).encode()).hexdigest()[:10]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{timestamp}-{slugify(label, limit=24)}-{digest}.log"
    output_file = HarnessPaths(project_root).task_artifacts(task_id) / filename
    started = time.monotonic()
    exit_code: int | None
    timed_out = False
    stopped = False
    stdout = ""
    stderr = ""
    try:
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            argv,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            text=True,
            start_new_session=os.name == "posix",
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_process_group(process)
                stdout, stderr = process.communicate()
                stderr = f"{stderr}\ncommand timed out after {timeout_seconds} seconds".strip()
                break
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                if HarnessPaths(project_root).stop_file.exists():
                    stopped = True
                    _stop_process_group(process)
                    stdout, stderr = process.communicate()
                    stderr = f"{stderr}\nemergency stop interrupted the command".strip()
                    break
        exit_code = process.returncode
    except FileNotFoundError as error:
        exit_code = 127
        stderr = f"command not found: {error.filename}"
    duration = time.monotonic() - started
    stdout = redact_text_using_argv(stdout, argv)
    stderr = redact_text_using_argv(stderr, argv)
    environment_secrets = sensitive_environment_values(os.environ)
    stdout = redact_text_using_secret_values(stdout, environment_secrets)
    stderr = redact_text_using_secret_values(stderr, environment_secrets)
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    summary = compact_tail(combined) or "command produced no output"
    log = (
        f"command: {shlex.join(redacted)}\n"
        f"exit_code: {exit_code}\n"
        f"timed_out: {str(timed_out).lower()}\n"
        f"stopped: {str(stopped).lower()}\n"
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
        stopped=stopped,
    )
