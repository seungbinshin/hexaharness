from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "skills" / "hexaharness" / "scripts" / "hexa.py"


def _load_launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hexaharness_bootstrap", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_root_resolves_from_skill_script() -> None:
    launcher = _load_launcher()

    assert launcher.plugin_root(LAUNCHER) == ROOT


def test_dependency_fingerprint_changes_with_lock(tmp_path: Path) -> None:
    launcher = _load_launcher()
    requirements = tmp_path / "runtime-requirements.txt"
    requirements.write_text("example==1\n", encoding="utf-8")
    first = launcher.dependency_fingerprint(tmp_path)

    requirements.write_text("example==2\n", encoding="utf-8")

    assert launcher.dependency_fingerprint(tmp_path) != first


def test_cache_and_runtime_environment_are_explicit(tmp_path: Path) -> None:
    launcher = _load_launcher()
    existing = os.pathsep.join(["first", "second"])
    environment = {
        "HEXAHARNESS_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": existing,
    }

    assert launcher.cache_root(environment) == (tmp_path / "cache").resolve()
    runtime = launcher.runtime_environment(environment)
    assert "PYTHONPATH" not in runtime
    assert runtime["PYTHONNOUSERSITE"] == "1"


def test_cache_falls_back_to_system_temp_when_user_cache_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    preferred = tmp_path / "blocked"
    temporary = tmp_path.parent / "system-temp" / "hexaharness-user"
    attempted: list[Path] = []

    def fake_ensure(path: Path) -> None:
        attempted.append(path)
        if path == preferred:
            raise PermissionError("blocked")

    monkeypatch.setattr(launcher, "cache_root", lambda environment=None: preferred)
    monkeypatch.setattr(launcher, "temporary_cache_root", lambda: temporary)
    monkeypatch.setattr(launcher, "_ensure_private_cache", fake_ensure)

    selected = launcher.select_cache_root({}, tmp_path / "project")

    assert selected == temporary
    assert attempted == [preferred, selected]


def test_cache_uses_hidden_project_fallback_when_other_locations_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    preferred = tmp_path / "blocked-user"
    temporary = tmp_path.parent / "blocked-temp"
    project = tmp_path / "project"
    attempted: list[Path] = []

    def fake_ensure(path: Path) -> None:
        attempted.append(path)
        if path in {preferred, temporary}:
            raise PermissionError("blocked")

    monkeypatch.setattr(launcher, "cache_root", lambda environment=None: preferred)
    monkeypatch.setattr(launcher, "temporary_cache_root", lambda: temporary)
    monkeypatch.setattr(launcher, "_ensure_private_cache", fake_ensure)

    selected = launcher.select_cache_root({}, project)

    assert selected == project.resolve() / ".hexaharness" / "runtime"
    assert attempted == [preferred, temporary, selected]


def test_runtime_ready_rejects_invalid_marker_and_cache_symlink(tmp_path: Path) -> None:
    launcher = _load_launcher()
    runtime = tmp_path / "runtime-example"
    python = launcher.environment_python(runtime)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    marker = runtime / ".ready.json"
    marker.write_text("{}\n", encoding="utf-8")

    assert not launcher._runtime_ready(runtime, "expected")

    marker.write_text(
        json.dumps(
            {
                "fingerprint": "expected",
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert launcher._runtime_ready(runtime, "expected")

    marker.unlink()
    python.unlink()
    python.parent.rmdir()
    runtime.rmdir()
    runtime.symlink_to(tmp_path, target_is_directory=True)
    assert not launcher._runtime_ready(runtime, "expected")


def test_install_lock_removes_the_owned_lock(tmp_path: Path) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"

    with launcher._install_lock(lock_path):
        assert lock_path.is_file()

    assert not lock_path.exists()


def test_install_lock_does_not_remove_a_replacement_lock(tmp_path: Path) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"

    with launcher._install_lock(lock_path):
        lock_path.unlink()
        lock_path.write_text("replacement owner\n", encoding="utf-8")

    assert lock_path.read_text(encoding="utf-8") == "replacement owner\n"


def test_stale_lock_with_live_owner_is_not_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(launcher, "STALE_LOCK_SECONDS", -1.0)

    assert not launcher._recover_stale_lock(lock_path)
    assert lock_path.is_file()


def test_dead_stale_lock_is_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(json.dumps({"pid": 999_999}), encoding="utf-8")
    monkeypatch.setattr(launcher, "STALE_LOCK_SECONDS", -1.0)
    monkeypatch.setattr(launcher, "_process_is_alive", lambda pid: False)

    assert launcher._recover_stale_lock(lock_path)
    assert not lock_path.exists()


def test_stale_recovery_does_not_remove_replacement_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"
    lock_path.write_text(json.dumps({"pid": 999_999}), encoding="utf-8")
    monkeypatch.setattr(launcher, "STALE_LOCK_SECONDS", -1.0)

    def replace_lock(pid: int) -> bool:
        lock_path.unlink()
        lock_path.write_text("replacement owner\n", encoding="utf-8")
        return False

    monkeypatch.setattr(launcher, "_process_is_alive", replace_lock)

    assert not launcher._recover_stale_lock(lock_path)
    assert lock_path.read_text(encoding="utf-8") == "replacement owner\n"


def test_live_stale_lock_serializes_competing_installers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    lock_path = tmp_path / "runtime.lock"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []
    monkeypatch.setattr(launcher, "STALE_LOCK_SECONDS", -1.0)
    monkeypatch.setattr(launcher, "LOCK_TIMEOUT_SECONDS", 2.0)

    def first_installer() -> None:
        try:
            with launcher._install_lock(lock_path):
                first_entered.set()
                release_first.wait(timeout=2.0)
        except BaseException as error:
            failures.append(error)

    def second_installer() -> None:
        try:
            with launcher._install_lock(lock_path):
                second_entered.set()
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=first_installer)
    second = threading.Thread(target=second_installer)
    first.start()
    assert first_entered.wait(timeout=1.0)
    second.start()
    assert not second_entered.wait(timeout=0.2)
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not failures
    assert second_entered.is_set()


def test_configured_runtime_must_exist(tmp_path: Path) -> None:
    launcher = _load_launcher()

    with pytest.raises(launcher.BootstrapError, match="does not exist"):
        launcher.resolve_runtime(
            ROOT, {"HEXAHARNESS_RUNTIME_PYTHON": str(tmp_path / "missing-python")}
        )


def test_launcher_executes_bundled_source_with_configured_runtime() -> None:
    environment = os.environ.copy()
    environment["HEXAHARNESS_RUNTIME_PYTHON"] = sys.executable

    result = subprocess.run(
        [sys.executable, "-I", str(LAUNCHER), "version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=ROOT,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0.2.0"


def test_launcher_ignores_shadow_package_in_target_repository(tmp_path: Path) -> None:
    (tmp_path / "hexaharness.py").write_text("raise SystemExit('shadowed')\n", encoding="utf-8")
    (tmp_path / "platform.py").write_text("raise SystemExit('shadowed')\n", encoding="utf-8")
    startup_marker = tmp_path / "sitecustomize-ran"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(startup_marker)!r}).touch()\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment["HEXAHARNESS_RUNTIME_PYTHON"] = sys.executable
    environment["PYTHONPATH"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-I", str(LAUNCHER), "version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0.2.0"
    assert "shadowed" not in result.stderr
    assert not startup_marker.exists()


def test_launcher_supports_plugin_paths_with_spaces(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin with spaces"
    launcher = plugin / "skills" / "hexaharness" / "scripts" / "hexa.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, launcher)
    shutil.copytree(ROOT / "src" / "hexaharness", plugin / "src" / "hexaharness")
    environment = os.environ.copy()
    environment["HEXAHARNESS_RUNTIME_PYTHON"] = sys.executable

    result = subprocess.run(
        [sys.executable, "-I", str(launcher), "version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0.2.0"
