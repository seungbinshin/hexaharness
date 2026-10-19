"""Read-only runtime discovery; presence is not evidence of successful provider execution."""

from __future__ import annotations

from typing import Any

from hexaharness import __version__
from hexaharness.config import CURRENT_SCHEMA_VERSION


def runtime_capabilities() -> dict[str, Any]:
    """Return public protocol support without inspecting the host or a project."""
    return {
        "schema_version": 1,
        "runtime_version": __version__,
        "python_requires": ">=3.12",
        "contracts": {
            "config_read": [1, CURRENT_SCHEMA_VERSION],
            "config_write": CURRENT_SCHEMA_VERSION,
            "improvement": 1,
            "task_budget": 1,
            "host_handoff": 1,
        },
        "capabilities": {
            "bounded_commands": True,
            "task_continuation": True,
            "text_improvement": True,
            "host_handoff": True,
            "provider_dispatch": False,
            "native_os_sandbox": False,
            "code_optimization": False,
            "semantic_judge": False,
        },
        "host_authority_required": True,
        "provider_evidence": "not-assessed",
        "stability": {
            "core": "stable",
            "text_improvement": "experimental",
            "host_handoff": "experimental",
        },
        "defaults": {"text_improvement_enabled": False, "provider_dispatch_enabled": False},
        "supported_evaluators": ["literal-text-v1"],
        "candidate_extensions": [".txt", ".md"],
        "candidate_max_bytes": 65536,
    }
