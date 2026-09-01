#!/usr/bin/env -S python3 -I
"""Bootstrap and execute the plugin-bundled HexaHarness runtime.

This launcher intentionally uses only the Python standard library. On first use it creates a
versioned virtual environment in the user cache and installs the exact runtime dependencies from
``runtime-requirements.txt``. HexaHarness itself is loaded directly from the plugin's ``src`` tree,
so a plugin update does not require a separate CLI installation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import venv
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

MINIMUM_PYTHON = (3, 12)
LOCK_TIMEOUT_SECONDS = 180.0
STALE_LOCK_SECONDS = 600.0
MODULE_LOADER = (
    "import runpy,sys;"
    "source=sys.argv.pop(1);"
    "sys.path.insert(0,source);"
    "sys.argv[0]='hexa';"
    "runpy.run_module('hexaharness',run_name='__main__')"
)


class BootstrapError(RuntimeError):
    """Raised when the private runtime cannot be prepared safely."""


def plugin_root(script_path: Path | None = None) -> Path:
    """Return the plugin root from this skill-bundled script path."""
    path = (script_path or Path(__file__)).resolve()
    return path.parents[3]


def dependency_fingerprint(root: Path) -> str:
    """Identify the dependency environment without tying it to mutable source files."""
    requirements = root / "runtime-requirements.txt"
    if not requirements.is_file():
        raise BootstrapError(f"missing locked runtime requirements: {requirements}")
    digest = hashlib.sha256()
    digest.update(requirements.read_bytes())
    runtime_identity = (
        f"{sys.implementation.name}-{sys.implementation.cache_tag}-"
        f"{platform.system()}-{platform.machine()}"
    )
    digest.update(runtime_identity.encode())
    return digest.hexdigest()[:16]


def cache_root(environment: Mapping[str, str] | None = None) -> Path:
    """Choose a user-writable, overridable cache location."""
    env = os.environ if environment is None else environment
    if configured := env.get("HEXAHARNESS_CACHE_DIR"):
        return Path(configured).expanduser().absolute()
    if configured := env.get("XDG_CACHE_HOME"):
        return (Path(configured).expanduser() / "hexaharness").absolute()
    if sys.platform == "win32" and (configured := env.get("LOCALAPPDATA")):
        return (Path(configured).expanduser() / "HexaHarness" / "Cache").absolute()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Caches" / "hexaharness").absolute()
    return (Path.home() / ".cache" / "hexaharness").absolute()


def temporary_cache_root() -> Path:
    """Return an owner-scoped system-temporary cache without exposing the username."""
    if hasattr(os, "getuid"):
        scope = str(os.getuid())
    else:
        scope = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:12]
    return (Path(tempfile.gettempdir()) / f"hexaharness-{scope}").absolute()


def _ensure_private_cache(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise BootstrapError(f"runtime cache is not a real directory: {path}")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise BootstrapError(f"runtime cache is not owned by the current user: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        if os.name != "nt":
            raise


def select_cache_root(
    environment: Mapping[str, str] | None = None, project_dir: Path | None = None
) -> Path:
    """Prefer private user/temp caches, then fall back to the writable project sandbox."""
    env = os.environ if environment is None else environment
    preferred = cache_root(env)
    failures: list[str] = []
    try:
        _ensure_private_cache(preferred)
        return preferred
    except (BootstrapError, OSError) as preferred_error:
        if env.get("HEXAHARNESS_CACHE_DIR"):
            raise BootstrapError(
                f"configured runtime cache is unavailable: {preferred_error}"
            ) from (preferred_error)
        failures.append(str(preferred_error))

    project = (project_dir or Path.cwd()).resolve()
    temporary = temporary_cache_root()
    try:
        temporary.relative_to(project)
        temporary_is_project_local = True
    except ValueError:
        temporary_is_project_local = False
    if not temporary_is_project_local:
        try:
            _ensure_private_cache(temporary)
            return temporary
        except (BootstrapError, OSError) as temporary_error:
            failures.append(str(temporary_error))

    harness_dir = project / ".hexaharness"
    if harness_dir.is_symlink():
        raise BootstrapError(f"project cache parent cannot be a symlink: {harness_dir}")
    fallback = harness_dir / "runtime"
    try:
        _ensure_private_cache(fallback)
        return fallback
    except (BootstrapError, OSError) as fallback_error:
        failures.append(str(fallback_error))
        raise BootstrapError(
            "no private runtime cache location is writable: " + "; ".join(failures)
        ) from fallback_error


def environment_python(runtime_dir: Path) -> Path:
    """Return the platform-specific interpreter inside a virtual environment."""
    if sys.platform == "win32":
        return runtime_dir / "Scripts" / "python.exe"
    return runtime_dir / "bin" / "python"


def runtime_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Remove Python path injection before starting the isolated module loader."""
    result = dict(os.environ if environment is None else environment)
    result.pop("PYTHONPATH", None)
    result.pop("PYTHONHOME", None)
    result["PYTHONNOUSERSITE"] = "1"
    return result


def _runtime_ready(runtime_dir: Path, fingerprint: str) -> bool:
    marker_path = runtime_dir / ".ready.json"
    if runtime_dir.is_symlink() or marker_path.is_symlink():
        return False
    if not marker_path.is_file() or not environment_python(runtime_dir).is_file():
        return False
    if hasattr(os, "getuid") and runtime_dir.stat().st_uid != os.getuid():
        return False
    try:
        marker: object = json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(marker, dict):
        return False
    return marker == {
        "fingerprint": fingerprint,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


type FileIdentity = tuple[int, int, int, int, int]


def _file_identity(stat_result: os.stat_result) -> FileIdentity:
    """Return the stable filesystem identity used to recognize an owned lock."""
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_ctime_ns,
        stat_result.st_mtime_ns,
        stat_result.st_size,
    )


def _unlink_owned_lock(lock_path: Path, identity: FileIdentity) -> bool:
    """Remove a lock only while its path still identifies the caller's file."""
    try:
        current_identity = _file_identity(lock_path.stat(follow_symlinks=False))
    except FileNotFoundError:
        return False
    if current_identity != identity:
        return False
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return False
    return True


def _process_is_alive(pid: int) -> bool:
    """Conservatively report whether a lock owner's process still exists."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name != "posix":
        # Signal 0 is not a portable liveness probe; unknown platforms stay conservative.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError):
        return False
    except OSError:
        return True
    return True


def _stale_lock_snapshot(lock_path: Path) -> tuple[FileIdentity, int | None] | None:
    """Return the stable identity and owner PID of an expired lock, if available."""
    try:
        path_stat = lock_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if time.time() - path_stat.st_mtime <= STALE_LOCK_SECONDS:
        return None

    identity = _file_identity(path_stat)
    if stat.S_ISLNK(path_stat.st_mode):
        return identity, None
    if not stat.S_ISREG(path_stat.st_mode):
        return None

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags)
    except (FileNotFoundError, OSError):
        return None
    try:
        if _file_identity(os.fstat(descriptor)) != identity:
            return None
        payload = os.read(descriptor, 8193)
    finally:
        os.close(descriptor)
    if len(payload) > 8192:
        owner: object = None
    else:
        try:
            owner = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            owner = None

    try:
        current_identity = _file_identity(lock_path.stat(follow_symlinks=False))
    except FileNotFoundError:
        return None
    if current_identity != identity:
        return None

    pid: int | None = None
    if isinstance(owner, dict):
        candidate = owner.get("pid")
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            pid = candidate
    return identity, pid


def _recover_stale_lock(lock_path: Path) -> bool:
    """Recover an expired lock only when its recorded owner is no longer alive."""
    snapshot = _stale_lock_snapshot(lock_path)
    if snapshot is None:
        return False
    identity, pid = snapshot
    if pid is not None and _process_is_alive(pid):
        return False
    return _unlink_owned_lock(lock_path, identity)


@contextmanager
def _install_lock(lock_path: Path) -> Iterator[None]:
    """Serialize first-use installation with stale-lock recovery."""
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    identity: FileIdentity | None = None
    while descriptor is None:
        try:
            candidate = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            candidate_identity: FileIdentity | None = None
            try:
                remaining = memoryview(
                    json.dumps({"pid": os.getpid(), "created": time.time()}).encode()
                )
                while remaining:
                    written = os.write(candidate, remaining)
                    if written == 0:
                        raise OSError("could not write runtime lock ownership")
                    remaining = remaining[written:]
                candidate_identity = _file_identity(os.fstat(candidate))
            except BaseException:
                candidate_identity = _file_identity(os.fstat(candidate))
                os.close(candidate)
                _unlink_owned_lock(lock_path, candidate_identity)
                raise
            assert candidate_identity is not None
            descriptor = candidate
            identity = candidate_identity
        except FileExistsError:
            if _recover_stale_lock(lock_path):
                continue
            if time.monotonic() >= deadline:
                raise BootstrapError(f"timed out waiting for runtime lock: {lock_path}") from None
            time.sleep(0.1)
    try:
        yield
    finally:
        if descriptor is not None and identity is not None:
            try:
                _unlink_owned_lock(lock_path, identity)
            finally:
                os.close(descriptor)


def _create_runtime(root: Path, runtime_dir: Path) -> None:
    requirements = root / "runtime-requirements.txt"
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{runtime_dir.name}-", dir=runtime_dir.parent))
    pip_temporary: Path | None = None
    try:
        pip_temporary = Path(tempfile.mkdtemp(prefix=".pip-", dir=runtime_dir.parent))
        venv.EnvBuilder(with_pip=True, clear=True).create(temporary)
        python = environment_python(temporary)
        pip_environment = runtime_environment()
        for name in ("TMPDIR", "TEMP", "TMP"):
            pip_environment[name] = str(pip_temporary)
        subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-deps",
                "--requirement",
                str(requirements),
            ],
            check=True,
            cwd=root,
            env=pip_environment,
        )
        marker = {
            "fingerprint": runtime_dir.name.removeprefix("runtime-"),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }
        (temporary / ".ready.json").write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if runtime_dir.is_symlink():
            runtime_dir.unlink()
        elif runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        temporary.replace(runtime_dir)
    except (OSError, subprocess.CalledProcessError) as error:
        raise BootstrapError(f"could not install locked runtime dependencies: {error}") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if pip_temporary is not None and pip_temporary.exists():
            shutil.rmtree(pip_temporary)


def resolve_runtime(
    root: Path, environment: Mapping[str, str] | None = None
) -> tuple[Path, dict[str, str]]:
    """Return the private interpreter and environment, preparing them if necessary."""
    env = os.environ if environment is None else environment
    if configured := env.get("HEXAHARNESS_RUNTIME_PYTHON"):
        python = Path(configured).expanduser().absolute()
        if not python.is_file():
            raise BootstrapError(f"configured runtime interpreter does not exist: {python}")
        return python, runtime_environment(env)

    fingerprint = dependency_fingerprint(root)
    base = select_cache_root(env)
    runtime_dir = base / f"runtime-{fingerprint}"
    if not _runtime_ready(runtime_dir, fingerprint):
        lock_path = base / f".{fingerprint}.lock"
        with _install_lock(lock_path):
            if not _runtime_ready(runtime_dir, fingerprint):
                print(
                    "HexaHarness: preparing the private runtime (first use only)...",
                    file=sys.stderr,
                )
                _create_runtime(root, runtime_dir)
    return environment_python(runtime_dir), runtime_environment(env)


def run(argv: Sequence[str] | None = None) -> NoReturn:
    """Replace this process with the bundled CLI."""
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        raise BootstrapError(f"Python {required}+ is required; found {sys.version.split()[0]}")
    root = plugin_root()
    if not (root / "src" / "hexaharness" / "__main__.py").is_file():
        raise BootstrapError(f"bundled HexaHarness source is missing under: {root}")
    python, env = resolve_runtime(root)
    arguments = [
        str(python),
        "-I",
        "-c",
        MODULE_LOADER,
        str(root / "src"),
        *(argv if argv is not None else sys.argv[1:]),
    ]
    os.execve(str(python), arguments, env)


def main() -> NoReturn:
    try:
        run()
    except BootstrapError as error:
        print(f"HexaHarness bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(78) from error


if __name__ == "__main__":
    main()
