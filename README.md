# HexaHarness

HexaHarness is a host-neutral production harness for agent-driven work. A thin pair of Agent Skills
guides Codex, ChatGPT Work, and Claude Code; the deterministic `hexa` CLI owns policy checks, bounded
command execution, checkpoints, sensor evidence, failure learning, and structured events.

It deliberately stops short of being another model runtime. Your host agent performs reasoning.
HexaHarness makes that work recoverable, inspectable, bounded, and progressively improvable.

## Six layers

| Layer | What HexaHarness provides |
|---|---|
| Guides | Exact project commands plus dated, failure-derived rules |
| Sensors | Shell-free build, lint, type, test, and schema command execution |
| Agentic loop | Finite retries, timeouts, stopping conditions, and escalation packets |
| Memory | Atomic task checkpoints, artifact pointers, and restart recovery |
| Permissions | Command and path allow/ask/deny decisions |
| Observability | JSONL events, retained command output, audits, and trip wires |

## Install for development

Requirements: Python 3.12+ and `uv`.

```bash
uv sync
uv run hexa --help
uv tool install .
```

The last command puts `hexa` on `PATH`, allowing the companion skills to use it from any repository.

## First project

```bash
cd your-project
hexa init
hexa audit
hexa start "Add the billing export with verified CSV output"
```

Use the returned task ID throughout the lifecycle:

```bash
hexa checkpoint TASK_ID --completed "Mapped API fields" --next "Implement exporter"
hexa policy-check -- git diff --check
hexa run TASK_ID -- git diff --check
hexa verify TASK_ID
hexa complete TASK_ID --artifact output/export.csv
```

Commands passed to `hexa run` are argument arrays and run without a shell. Unknown or externally
visible actions require approval; denied actions cannot be bypassed through the CLI.

## Agent Skills and plugins

- Codex and ChatGPT use `.codex-plugin/plugin.json` and the bundled `skills/` directory. Invoke the
  explicit entry skill as `$hexaharness` or select it in ChatGPT.
- Claude Code can load this checkout with `claude --plugin-dir .`. Invoke
  `/hexaharness:hexaharness`; the companion skill is `/hexaharness:harness-engineering`.
- The skill files use the open Agent Skills structure and keep product-specific OpenAI metadata in
  `agents/openai.yaml`.

## Command surface

| Command | Purpose |
|---|---|
| `hexa init` | Create minimum six-layer project configuration |
| `hexa doctor` | Validate configuration and list active sensors |
| `hexa start` | Create a recoverable task checkpoint |
| `hexa checkpoint` | Record steps, artifacts, and host-reported usage |
| `hexa policy-check` | Evaluate a command or path without acting |
| `hexa run` | Execute an approved policy-gated command without a shell |
| `hexa verify` | Run computational sensors and retain evidence |
| `hexa complete` | Require sensors before advancing to completed |
| `hexa resume` | Recover or explicitly reactivate a checkpoint |
| `hexa learn` | Convert an observed failure into a traceable correction |
| `hexa audit` | Evaluate the twelve-item production-readiness checklist |
| `hexa stop` | Stop a task or activate the project emergency stop |
| `hexa prune` | Preview or apply configured runtime-evidence retention |

## Security boundary

HexaHarness enforces commands executed through `hexa`; it does not replace the host operating-system
sandbox. Skills therefore require host agents to retain their native filesystem, network, and
approval controls. Runtime command output is stored under `.hexaharness/` and ignored by Git because
it may contain local paths or sensitive application output.

See [architecture](docs/architecture.md), [operations](docs/operations.md), and
[security policy](SECURITY.md).

## Development

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov
uv build
```

Licensed under the MIT License.
