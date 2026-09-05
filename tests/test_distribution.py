from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

from hexaharness import __version__

ROOT = Path(__file__).resolve().parents[1]


def _read_json(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_distribution_versions_match_project() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    codex = _read_json(".codex-plugin/plugin.json")
    claude = _read_json(".claude-plugin/plugin.json")
    marketplace = _read_json(".claude-plugin/marketplace.json")
    marketplace_plugin = marketplace["plugins"][0]  # type: ignore[index]

    assert version == __version__
    assert codex["version"] == version
    assert claude["version"] == version
    assert marketplace_plugin["version"] == version  # type: ignore[index]


def test_marketplaces_point_to_the_public_plugin_root() -> None:
    codex = _read_json(".agents/plugins/marketplace.json")
    claude = _read_json(".claude-plugin/marketplace.json")
    codex_plugin = codex["plugins"][0]  # type: ignore[index]
    claude_plugin = claude["plugins"][0]  # type: ignore[index]

    assert codex_plugin["source"] == {  # type: ignore[index]
        "source": "url",
        "url": "https://github.com/seungbinshin/hexaharness.git",
    }
    assert claude_plugin["source"] == {  # type: ignore[index]
        "source": "github",
        "repo": "seungbinshin/hexaharness",
    }


def test_only_primary_skill_is_discoverable_across_hosts() -> None:
    skill_files = sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "skills").rglob("SKILL.md")
    )

    assert skill_files == ["skills/hexaharness/SKILL.md"]
    assert (ROOT / "skills/hexaharness/references/operating-loop.md").is_file()


def test_runtime_requirements_match_uv_lock(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        return
    generated = tmp_path / "runtime-requirements.txt"
    subprocess.run(
        [
            uv,
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            str(generated),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    def without_command_header(value: str) -> list[str]:
        return [
            line for line in value.splitlines() if not line.startswith(("# This file", "#    uv"))
        ]

    committed = (ROOT / "runtime-requirements.txt").read_text(encoding="utf-8")
    assert without_command_header(generated.read_text(encoding="utf-8")) == without_command_header(
        committed
    )


def test_sdist_contains_only_the_intended_release_surface(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        return
    output_dir = tmp_path / "dist"
    result = subprocess.run(
        [uv, "build", "--no-build-isolation", "--sdist", "--out-dir", str(output_dir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"sdist build failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    archives = list(output_dir.glob("hexaharness-*.tar.gz"))
    assert len(archives) == 1

    with tarfile.open(archives[0], mode="r:gz") as archive:
        members = archive.getnames()
    roots = {name.split("/", 1)[0] for name in members}
    assert len(roots) == 1
    root_prefix = f"{roots.pop()}/"
    paths = {
        name.removeprefix(root_prefix)
        for name in members
        if name.startswith(root_prefix) and name != root_prefix.rstrip("/")
    }

    required = {
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".github/workflows/ci.yml",
        ".hexaharness/GUIDES.md",
        ".hexaharness/harness.yaml",
        "LICENSE",
        "README.md",
        "README.ko.md",
        "docs/architecture.md",
        "docs/operations.md",
        "pyproject.toml",
        "runtime-requirements.txt",
        "skills/hexaharness/SKILL.md",
        "skills/hexaharness/agents/openai.yaml",
        "skills/hexaharness/scripts/hexa.py",
        "src/hexaharness/__init__.py",
        "tests/test_distribution.py",
        "uv.lock",
    }
    assert required <= paths

    forbidden_prefixes = (
        ".hexaharness/artifacts/",
        ".hexaharness/events/",
        ".hexaharness/proposals/",
        ".hexaharness/runtime/",
        ".hexaharness/state/",
        "build/",
        "dist/",
    )
    assert not any(path.startswith(forbidden_prefixes) for path in paths)
    assert ".hexaharness/STOP" not in paths
    assert ".hexaharness/external-action.key" not in paths
    assert not any(
        "/" not in path and path.startswith("tmp") and path.endswith(".tar.gz") for path in paths
    )
