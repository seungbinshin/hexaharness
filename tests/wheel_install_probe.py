"""Run with an isolated, freshly wheel-installed interpreter outside the checkout."""

from __future__ import annotations

import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import hexaharness
from hexaharness.capabilities import runtime_capabilities
from hexaharness.config import load_config, save_config
from hexaharness.host_adapter import prepare_host_request, receive_host_result
from hexaharness.improvement import begin_improvement, evaluate_candidate, promote_candidate
from hexaharness.improvement_models import HostAdapterSpec, HostResult, ImprovementConfig, TextCheck
from hexaharness.models import CommandSpec
from hexaharness.scaffold import initialize_project
from hexaharness.state import start_task
from hexaharness.workflow import complete_task


def main() -> None:
    package_path = Path(hexaharness.__file__).resolve()
    assert package_path.is_relative_to(Path(sys.prefix).resolve()), package_path
    assert "site-packages" in package_path.parts, package_path
    assert version("hexaharness") == hexaharness.__version__ == sys.argv[1]
    assert runtime_capabilities()["capabilities"]["provider_dispatch"] is False
    root = Path.cwd() / "fixture 한글 project"
    root.mkdir()
    source = root / "src"
    source.mkdir()
    target = source / "prompt.txt"
    target.write_text("Answer carefully.", encoding="utf-8")
    sensor = source / "verify.py"
    sensor.write_text(
        "from pathlib import Path\n"
        "text = Path('src/prompt.txt').read_text(encoding='utf-8')\n"
        "assert 'carefully' in text\n"
        "assert len(text.encode('utf-8')) <= 65536\n"
        "print('installed wheel sensor passed')\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-I", "src/verify.py"]
    initialize_project(
        root,
        project_name="wheel fixture",
        project_language="Python",
        build=command,
        test=command,
        lint=command,
    )
    config = load_config(root)
    config.sensors = [CommandSpec(name="test", argv=command, timeout_seconds=30, required=True)]
    config.improvement = ImprovementConfig(
        target="src/prompt.txt",
        checks=[
            TextCheck(name="baseline", text="carefully", required=True),
            TextCheck(name="citations", text="cite sources", weight=2),
        ],
    )
    save_config(root, config)
    task = start_task(root, "Verify installed wheel with a deterministic fixture")
    begin_improvement(root, config, task.task_id)
    transfer = prepare_host_request(
        root,
        config,
        task.task_id,
        key="fixture",
        adapter=HostAdapterSpec(
            adapter_id="local-fixture",
            model_id="deterministic-fixture",
            token_limit_enforced=True,
            cost_limit_enforced=True,
            cancellation_supported=True,
            reconciliation_supported=True,
        ),
        goal="Add a source citation instruction",
        max_tokens=100,
        max_cost_usd=0,
    )
    envelope = transfer.request
    assert transfer.created
    candidate = receive_host_result(
        root,
        config,
        task.task_id,
        key="fixture",
        result=HostResult(
            call_id=envelope.call_id,
            request_sha256=envelope.request_sha256,
            adapter_id=envelope.adapter_id,
            model_id=envelope.model_id,
            status="completed",
            provenance="fixture",
            provider_request_id="fixture-1",
            text="Answer carefully; cite sources.",
            tokens=10,
            cost_usd=0,
        ),
    )
    assert candidate is not None
    evaluate_candidate(root, config, task.task_id, candidate_id="baseline", key="baseline")
    evaluate_candidate(root, config, task.task_id, candidate_id=candidate.candidate_id, key="trial")
    promote_candidate(root, config, task.task_id, candidate.candidate_id)
    completed = complete_task(root, config, task.task_id, artifacts=["src/prompt.txt"])
    assert completed.status.value == "completed"
    assert completed.sensor_results and all(item.passed for item in completed.sensor_results)
    assert target.read_text(encoding="utf-8") == "Answer carefully; cite sources."
    print(
        json.dumps(
            {
                "version": hexaharness.__version__,
                "installed_from_wheel": True,
                "candidate_source": "deterministic-fixture",
                "provider_called": False,
                "status": completed.status.value,
                "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
