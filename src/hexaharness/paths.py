from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _validate_existing_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path


def _validate_existing_directory(
    path: Path,
    *,
    label: str,
    reject_child_symlinks: bool = False,
) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    if path.is_dir() and reject_child_symlinks:
        for child in path.iterdir():
            if child.is_symlink():
                raise ValueError(f"managed runtime entries cannot be symlinks: {child}")
    return path


def _validate_component(value: str, *, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must be a single safe path component")
    return value


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if HarnessPaths(candidate).config.is_file():
            return candidate
    return current


@dataclass(frozen=True)
class HarnessPaths:
    project_root: Path

    @property
    def harness_dir(self) -> Path:
        return _validate_existing_directory(
            self.project_root / ".hexaharness",
            label="HexaHarness directory",
        )

    @property
    def config(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "harness.yaml",
            label="HexaHarness configuration",
        )

    @property
    def guide_rules(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "guide-rules.yaml",
            label="HexaHarness guide rules",
        )

    @property
    def guides_markdown(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "GUIDES.md",
            label="HexaHarness rendered guides",
        )

    @property
    def decisions(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "decisions.jsonl",
            label="HexaHarness decision log",
        )

    @property
    def states(self) -> Path:
        return _validate_existing_directory(
            self.harness_dir / "state",
            label="HexaHarness state directory",
            reject_child_symlinks=True,
        )

    @property
    def events(self) -> Path:
        return _validate_existing_directory(
            self.harness_dir / "events",
            label="HexaHarness event directory",
            reject_child_symlinks=True,
        )

    @property
    def artifacts(self) -> Path:
        return _validate_existing_directory(
            self.harness_dir / "artifacts",
            label="HexaHarness artifact directory",
            reject_child_symlinks=True,
        )

    @property
    def proposals(self) -> Path:
        return _validate_existing_directory(
            self.harness_dir / "proposals",
            label="HexaHarness proposal directory",
            reject_child_symlinks=True,
        )

    @property
    def runtime(self) -> Path:
        return _validate_existing_directory(
            self.harness_dir / "runtime",
            label="HexaHarness project runtime directory",
        )

    @property
    def stop_file(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "STOP",
            label="HexaHarness emergency-stop file",
        )

    @property
    def external_action_key(self) -> Path:
        return _validate_existing_file(
            self.harness_dir / "external-action.key",
            label="HexaHarness external-action binding key",
        )

    def state_file(self, task_id: str) -> Path:
        task_id = _validate_component(task_id, label="task ID")
        return _validate_existing_file(
            self.states / f"{task_id}.json",
            label="HexaHarness task checkpoint",
        )

    def task_artifacts(self, task_id: str) -> Path:
        task_id = _validate_component(task_id, label="task ID")
        return _validate_existing_directory(
            self.artifacts / task_id,
            label="HexaHarness task artifact directory",
            reject_child_symlinks=True,
        )

    def validate_layout(self) -> None:
        """Reject managed symlinks and wrong path types before a harness operation mutates data."""
        _ = (
            self.harness_dir,
            self.config,
            self.guide_rules,
            self.guides_markdown,
            self.decisions,
            self.states,
            self.events,
            self.artifacts,
            self.proposals,
            self.runtime,
            self.stop_file,
            self.external_action_key,
        )

    def ensure_runtime_dirs(self) -> None:
        for directory in (self.states, self.events, self.artifacts, self.proposals):
            directory.mkdir(parents=True, exist_ok=True)
