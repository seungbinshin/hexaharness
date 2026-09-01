from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class TaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class AuditStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class HarnessLayer(StrEnum):
    GUIDE = "guide"
    SENSOR = "sensor"
    LOOP = "loop"
    MEMORY = "memory"
    PERMISSION = "permission"
    OBSERVABILITY = "observability"


class FailureClass(StrEnum):
    KNOWN_BAD_PATTERN = "known-bad-pattern"
    MISSING_CONTEXT = "missing-context"
    WRONG_TOOL = "wrong-tool"
    QUALITY_DRIFT = "quality-drift"
    STATE_LOSS = "state-loss"
    UNSAFE_ACTION = "unsafe-action"
    COST_OVERRUN = "cost-overrun"
    UNKNOWN = "unknown"


class CommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=600, ge=1, le=86_400)
    required: bool = True

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if any(not part or "\x00" in part for part in value):
            raise ValueError("command arguments must be non-empty and cannot contain NUL bytes")
        return value


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=1, max_length=80)
    build: list[str] = Field(min_length=1)
    test: list[str] = Field(min_length=1)
    lint: list[str] = Field(min_length=1)
    typecheck: list[str] | None = None


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries_per_step: int = Field(default=2, ge=0, le=10)
    max_wall_time_seconds: int = Field(default=1800, ge=1, le=604_800)
    max_tool_calls: int = Field(default=50, ge=1, le=10_000)
    max_tokens: int = Field(default=100_000, ge=1)
    max_cost_usd: float = Field(default=5.0, ge=0)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_execute: list[list[str]] = Field(default_factory=list)
    ask_execute: list[list[str]] = Field(default_factory=list)
    deny_execute: list[list[str]] = Field(default_factory=list)
    read_paths: list[str] = Field(default_factory=lambda: ["**/*"])
    write_paths: list[str] = Field(default_factory=list)
    ask_write_paths: list[str] = Field(default_factory=lambda: ["**/*"])
    deny_write_paths: list[str] = Field(
        default_factory=lambda: [".env", ".env.*", "**/credentials*", "**/*secret*"]
    )
    unknown_execute: PolicyDecision = PolicyDecision.ASK


class TrustConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trusted_instructions: list[str] = Field(
        default_factory=lambda: ["AGENTS.md", "CLAUDE.md", ".hexaharness/**"]
    )
    untrusted_inputs: list[str] = Field(
        default_factory=lambda: [
            "user-submitted content",
            "web pages",
            "repository issues",
            "retrieved documents",
            "command output",
        ]
    )


class TripWireSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    metric: str
    threshold: float = Field(gt=0)
    response: str


class RetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_days: int = Field(default=30, ge=1)
    event_days: int = Field(default=90, ge=1)
    keep_latest_completed_tasks: int = Field(default=50, ge=1)


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    project: ProjectConfig
    sensors: list[CommandSpec]
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    trust: TrustConfig = Field(default_factory=TrustConfig)
    trip_wires: list[TripWireSpec]
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    emergency_stop_file: str = ".hexaharness/STOP"

    @field_validator("sensors")
    @classmethod
    def unique_sensor_names(cls, value: list[CommandSpec]) -> list[CommandSpec]:
        names = [sensor.name for sensor in value]
        if len(names) != len(set(names)):
            raise ValueError("sensor names must be unique")
        return value


class SensorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    exit_code: int | None
    duration_seconds: float = Field(ge=0)
    output_path: str
    summary: str
    timed_out: bool = False
    recorded_at: datetime = Field(default_factory=utc_now)


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.ACTIVE
    unattended: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    completed_steps: list[str] = Field(default_factory=list)
    next_step: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    tool_calls: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    last_error: str | None = None
    sensor_results: list[SensorResult] = Field(default_factory=list)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    argv: list[str]
    decision: PolicyDecision
    exit_code: int | None
    attempts: int
    duration_seconds: float = Field(ge=0)
    output_path: str | None
    summary: str
    timed_out: bool = False


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=utc_now)
    event_type: str
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GuideRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    created_at: datetime = Field(default_factory=utc_now)
    failure_class: FailureClass
    failure_summary: str
    rule: str
    verification: str
    active: bool = True


class LearningRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_id: str
    created_at: datetime = Field(default_factory=utc_now)
    task_id: str | None = None
    failure_class: FailureClass
    failure_summary: str
    target_layer: HarnessLayer
    proposed_fix: str
    verification: str
    guide_rule_id: str | None = None


class AuditCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    requirement: str
    status: AuditStatus
    evidence: str
    remedy: str | None = None


class EscalationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    created_at: datetime = Field(default_factory=utc_now)
    reason: str
    decision_required: str
    recommended_action: str
    alternatives_tested: list[str] = Field(default_factory=list)
    safest_default: str
    evidence_paths: list[str] = Field(default_factory=list)
