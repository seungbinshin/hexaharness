from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from hexaharness.execution import CapturedExecution, execute_capture
from hexaharness.io import redact_argv
from hexaharness.workflow import stop_task_or_harness


@pytest.mark.parametrize("parent_exits", [False, True])
def test_timeout_reaps_descendants_even_when_parent_exits(
    harness_project: Path, parent_exits: bool
) -> None:
    # The child deliberately survives graceful cancellation and retains the output pipe.
    # A sentinel makes this a process-lifetime assertion, not just an exit-code check.
    child = harness_project / "child.py"
    child.write_text(
        "import signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
        "Path('child-ready').write_text('ready')\n"
        "for i in range(300):\n"
        " Path('child-heartbeat').write_text(str(i)); time.sleep(0.02)\n"
        "Path('child-survived').write_text('alive')\n",
        encoding="utf-8",
    )
    parent = (
        "import subprocess, sys, time; from pathlib import Path; "
        "subprocess.Popen([sys.executable, 'child.py']); "
        "\nwhile not Path('child-ready').exists(): time.sleep(0.01)\n"
        + ("sys.exit(0)\n" if parent_exits else "time.sleep(15)\n")
    )
    captured = execute_capture(
        harness_project,
        task_id="tree-timeout",
        label="descendant-timeout",
        argv=[sys.executable, "-c", parent],
        timeout_seconds=1,
    )
    assert captured.duration_seconds < 5, captured
    assert not (harness_project / "child-survived").exists()
    heartbeat = harness_project / "child-heartbeat"
    before = heartbeat.read_bytes() if heartbeat.exists() else b""
    time.sleep(0.3)
    assert (heartbeat.read_bytes() if heartbeat.exists() else b"") == before
    if not parent_exits or os.name != "nt":
        assert captured.timed_out


def test_emergency_stop_interrupts_an_in_flight_command(harness_project: Path) -> None:
    started = harness_project / "command-started.txt"
    results: list[CapturedExecution] = []

    def execute() -> None:
        results.append(
            execute_capture(
                harness_project,
                task_id="verification",
                label="stoppable-command",
                argv=[
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import time; "
                        "Path('command-started.txt').write_text('started'); time.sleep(30)"
                    ),
                ],
                timeout_seconds=30,
            )
        )

    worker = threading.Thread(target=execute)
    worker.start()
    deadline = time.monotonic() + 5
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert started.is_file()

    stop_task_or_harness(harness_project, task_id=None, reason="test emergency stop")
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(results) == 1
    captured = results[0]
    assert captured.stopped
    assert not captured.timed_out
    assert "emergency stop interrupted" in captured.summary
    log = (harness_project / captured.output_path).read_text(encoding="utf-8")
    assert "stopped: true" in log


def test_execution_scrubs_echoed_secret_values_and_uses_redacted_fingerprint(
    harness_project: Path,
) -> None:
    script = (
        "import sys; "
        "print(sys.argv[2]); "
        "print(sys.argv[3].split('=', 1)[1]); "
        "print(sys.argv[5].rsplit(' ', 1)[-1]); "
        "print(sys.argv[6].split('=', 1)[1])"
    )
    first_argv = [
        sys.executable,
        "-c",
        script,
        "--access-token",
        "access-secret",
        "--password=password-secret",
        "-H",
        "Authorization: Bearer header-secret",
        "API_TOKEN=env-secret",
    ]
    second_argv = [
        *first_argv[:4],
        "different-access-secret",
        "--password=different-password-secret",
        *first_argv[6:7],
        "Authorization: Bearer different-header-secret",
        "API_TOKEN=different-env-secret",
    ]

    first = execute_capture(
        harness_project,
        task_id="redaction",
        label="credential-output",
        argv=first_argv,
        timeout_seconds=10,
    )
    second = execute_capture(
        harness_project,
        task_id="redaction",
        label="credential-output",
        argv=second_argv,
        timeout_seconds=10,
    )

    first_log = (harness_project / first.output_path).read_text(encoding="utf-8")
    for secret in ("access-secret", "password-secret", "header-secret", "env-secret"):
        assert secret not in first.summary
        assert secret not in first_log
    assert "<redacted>" in first_log

    first_suffix = Path(first.output_path).stem.rsplit("-", 1)[-1]
    second_suffix = Path(second.output_path).stem.rsplit("-", 1)[-1]
    expected = hashlib.sha256("\0".join(redact_argv(first_argv)).encode()).hexdigest()[:10]
    raw_secret_digest = hashlib.sha256("\0".join(first_argv).encode()).hexdigest()[:10]
    assert first_suffix == second_suffix == expected
    assert first_suffix != raw_secret_digest


def test_execution_scrubs_sensitive_ambient_environment_values(
    harness_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEXAHARNESS_TEST_API_TOKEN", "ambient-secret-sentinel")

    captured = execute_capture(
        harness_project,
        task_id="redaction",
        label="ambient-secret",
        argv=[
            sys.executable,
            "-c",
            "import os; print(os.environ['HEXAHARNESS_TEST_API_TOKEN'])",
        ],
        timeout_seconds=10,
    )

    log = (harness_project / captured.output_path).read_text(encoding="utf-8")
    assert "ambient-secret-sentinel" not in captured.summary
    assert "ambient-secret-sentinel" not in log
    assert "<redacted>" in log


def test_execution_scrubs_nested_json_secret_values(harness_project: Path) -> None:
    payload = '{"name":"demo","auth":{"client_secret":"json-secret-sentinel"}}'

    captured = execute_capture(
        harness_project,
        task_id="redaction",
        label="structured-secret",
        argv=[sys.executable, "-c", "import sys; print(sys.argv[-1])", "--data", payload],
        timeout_seconds=10,
    )

    log = (harness_project / captured.output_path).read_text(encoding="utf-8")
    assert "json-secret-sentinel" not in captured.summary
    assert "json-secret-sentinel" not in log
    assert '"client_secret":"<redacted>"' in log
