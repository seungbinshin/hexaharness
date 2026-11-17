from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "hexaharness"
    / "scripts"
    / "process_profile.py"
)
ALL_MODULES = ["plan", "repair", "verify", "review"]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hexaharness_process_profile", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROFILE = _load()


@pytest.mark.parametrize(
    "environment", [{}, {"HEXAHARNESS_PROFILE": ""}, {"HEXAHARNESS_PROFILE": "  "}]
)
def test_unset_or_blank_profile_applies_every_module(environment: dict[str, str]) -> None:
    result = PROFILE.resolve(environment)
    assert result == {
        "schema_version": 1,
        "profile": "guided",
        "modules": ALL_MODULES,
        "direct_engagement": False,
        "source": "default",
        "warning": None,
    }


def test_explicit_guided_matches_the_default_modules() -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": " Guided "})
    assert result["profile"] == "guided"
    assert result["modules"] == ALL_MODULES
    assert result["direct_engagement"] is False
    assert result["source"] == "environment"


def test_lean_applies_no_module_and_allows_direct_engagement() -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": "LEAN"})
    assert result["profile"] == "lean"
    assert result["modules"] == []
    assert result["direct_engagement"] is True
    assert result["warning"] is None


def test_module_list_is_canonically_ordered_and_keeps_the_runtime_engaged() -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": "review, verify"})
    assert result["profile"] == "custom"
    assert result["modules"] == ["verify", "review"]
    assert result["direct_engagement"] is False


def test_complete_module_list_is_reported_as_guided() -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": "review,verify,repair,plan"})
    assert result["profile"] == "guided"
    assert result["modules"] == ALL_MODULES


@pytest.mark.parametrize(
    "value",
    ["frontier", "lean,verify", "verify,verify", "verify,", ",", "none", "off", "0", "plan;verify"],
)
def test_unrecognized_value_fails_toward_every_module(value: str) -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": value})
    assert result["profile"] == "guided"
    assert result["modules"] == ALL_MODULES
    assert result["direct_engagement"] is False
    assert result["source"] == "invalid-environment"
    assert isinstance(result["warning"], str)


def test_unrecognized_value_is_not_echoed() -> None:
    result = PROFILE.resolve({"HEXAHARNESS_PROFILE": "Zx9-operator-input"})
    assert "zx9" not in str(result["warning"]).lower()


def test_isolated_cli_prints_one_json_object_and_no_diagnostics() -> None:
    environment = {key: value for key, value in os.environ.items() if key != "HEXAHARNESS_PROFILE"}
    environment["HEXAHARNESS_PROFILE"] = "lean"
    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["profile"] == "lean"


def test_cli_rejects_arguments_without_writing_to_stdout() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "lean"], capture_output=True, check=False, text=True
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "usage" in completed.stderr
