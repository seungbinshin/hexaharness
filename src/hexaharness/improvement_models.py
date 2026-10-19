"""Versioned data contracts for the built-in text improvement strategy."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TextCheck(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    present: bool = True
    required: bool = False
    weight: float = Field(default=1, gt=0, le=1000)


class ImprovementConfig(StrictModel):
    """Literal checks evaluate data only; they never execute candidate text."""

    evaluator: Literal["literal-text-v1"] = "literal-text-v1"
    target: str
    checks: list[TextCheck] = Field(min_length=1, max_length=100)
    min_gain: float = Field(default=0, ge=0)
    max_candidates: int = Field(default=5, ge=1, le=20)

    @field_validator("checks")
    @classmethod
    def unique_names(cls, value: list[TextCheck]) -> list[TextCheck]:
        if len({item.name for item in value}) != len(value):
            raise ValueError("check names must be unique")
        return value


class GenerationRequest(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    parents: list[str]
    reserved_tokens: int = Field(ge=0)
    reserved_cost_usd: float = Field(ge=0)
    status: Literal["pending", "unknown", "settled", "over-budget"] = "pending"
    candidate_id: str | None = None
    actual_tokens: int | None = Field(default=None, ge=0)
    actual_cost_usd: float | None = Field(default=None, ge=0)


class Candidate(StrictModel):
    candidate_id: str
    text: str = Field(max_length=65536)
    sha256: str
    parents: list[str] = Field(default_factory=list)
    latest_attempt: str | None = None


class GenerationResponse(StrictModel):
    schema_version: Literal[1] = 1
    created: bool
    request: GenerationRequest


class EvaluationAttempt(StrictModel):
    attempt_id: str
    key: str = Field(min_length=1, max_length=100)
    candidate_id: str
    candidate_sha256: str
    evaluator_sha256: str
    status: Literal["running", "passed", "failed", "interrupted"] = "running"
    score: float | None = None
    output_path: str | None = None
    output_sha256: str | None = None


class ImprovementState(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["active", "promoting", "promoted", "cancelled", "conflict"] = "active"
    target: str
    config_sha256: str
    guidance_sha256: str
    evaluator_sha256: str
    specification: ImprovementConfig
    candidates: dict[str, Candidate]
    requests: dict[str, GenerationRequest] = Field(default_factory=dict)
    attempts: dict[str, EvaluationAttempt] = Field(default_factory=dict)
    selected_candidate: str | None = None
