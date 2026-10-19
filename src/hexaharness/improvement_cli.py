"""The improvement protocol is a command of the existing hexa application."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from hexaharness.config import load_config
from hexaharness.improvement import (
    _read_text,
    _safe_file,
    begin_improvement,
    cancel_improvement,
    evaluate_candidate,
    promote_candidate,
    request_candidate,
    resume_improvement,
    submit_candidate,
)
from hexaharness.models import PolicyDecision
from hexaharness.paths import find_project_root
from hexaharness.policy import evaluate_path
from hexaharness.state import load_task


class ImprovementAction(StrEnum):
    BEGIN = "begin"
    REQUEST = "request"
    SUBMIT = "submit"
    EVALUATE = "evaluate"
    PROMOTE = "promote"
    CANCEL = "cancel"
    RESUME = "resume"
    STATUS = "status"


def improve(
    task_id: Annotated[str, typer.Argument()],
    action: Annotated[ImprovementAction, typer.Option()],
    root: Annotated[Path | None, typer.Option()] = None,
    key: Annotated[str | None, typer.Option()] = None,
    candidate_id: Annotated[str, typer.Option()] = "baseline",
    candidate_file: Annotated[Path | None, typer.Option()] = None,
    parent: Annotated[list[str] | None, typer.Option()] = None,
    max_tokens: Annotated[int, typer.Option(min=0)] = 0,
    max_cost_usd: Annotated[float, typer.Option(min=0)] = 0,
    tokens: Annotated[int | None, typer.Option(min=0)] = None,
    cost_usd: Annotated[float | None, typer.Option(min=0)] = None,
    abandon: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the versioned built-in text improvement protocol on an existing task."""
    project = (root or find_project_root()).resolve()
    config = load_config(project)
    result: BaseModel | None
    if (
        action in {ImprovementAction.REQUEST, ImprovementAction.SUBMIT, ImprovementAction.EVALUATE}
        and not key
    ):
        raise typer.BadParameter("--key is required for idempotent generation/evaluation")
    if action == ImprovementAction.BEGIN:
        result = begin_improvement(project, config, task_id)
    elif action == ImprovementAction.REQUEST:
        assert key is not None
        result = request_candidate(
            project,
            config,
            task_id,
            key=key,
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            parents=parent,
        )
    elif action == ImprovementAction.SUBMIT:
        assert key is not None
        if candidate_file is None:
            raise typer.BadParameter("--candidate-file is required")
        path = _safe_file(project, str(candidate_file))
        if evaluate_path(config, project, path, write=False).decision != PolicyDecision.ALLOW:
            raise typer.BadParameter("candidate input is not an allowed project file")
        result = submit_candidate(
            project,
            config,
            task_id,
            key=key,
            text=_read_text(path),
            tokens=tokens,
            cost_usd=cost_usd,
        )
    elif action == ImprovementAction.EVALUATE:
        assert key is not None
        result = evaluate_candidate(project, config, task_id, candidate_id=candidate_id, key=key)
    elif action == ImprovementAction.PROMOTE:
        result = promote_candidate(project, config, task_id, candidate_id)
    elif action == ImprovementAction.CANCEL:
        cancel_improvement(project, task_id)
        result = None
    elif action == ImprovementAction.RESUME:
        result = resume_improvement(project, config, task_id, abandon=abandon)
    else:
        result = load_task(project, task_id).improvement
    typer.echo(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": task_id,
                "action": action,
                "result": result.model_dump(mode="json") if result else None,
            },
            indent=2,
        )
    )
