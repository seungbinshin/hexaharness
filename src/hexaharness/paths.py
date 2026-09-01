from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONFIG_RELATIVE_PATH = Path(".hexaharness/harness.yaml")


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_RELATIVE_PATH).is_file():
            return candidate
    return current


@dataclass(frozen=True)
class HarnessPaths:
    project_root: Path

    @property
    def harness_dir(self) -> Path:
        return self.project_root / ".hexaharness"

    @property
    def config(self) -> Path:
        return self.harness_dir / "harness.yaml"

    @property
    def guide_rules(self) -> Path:
        return self.harness_dir / "guide-rules.yaml"

    @property
    def guides_markdown(self) -> Path:
        return self.harness_dir / "GUIDES.md"

    @property
    def decisions(self) -> Path:
        return self.harness_dir / "decisions.jsonl"

    @property
    def states(self) -> Path:
        return self.harness_dir / "state"

    @property
    def events(self) -> Path:
        return self.harness_dir / "events"

    @property
    def artifacts(self) -> Path:
        return self.harness_dir / "artifacts"

    @property
    def proposals(self) -> Path:
        return self.harness_dir / "proposals"

    @property
    def stop_file(self) -> Path:
        return self.harness_dir / "STOP"

    def state_file(self, task_id: str) -> Path:
        return self.states / f"{task_id}.json"

    def task_artifacts(self, task_id: str) -> Path:
        return self.artifacts / task_id

    def ensure_runtime_dirs(self) -> None:
        for directory in (self.states, self.events, self.artifacts, self.proposals):
            directory.mkdir(parents=True, exist_ok=True)
