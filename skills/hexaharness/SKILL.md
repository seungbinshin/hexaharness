---
name: hexaharness
description: Automatically orchestrate software projects from discovery and design through implementation, verification, release preparation, resumption, and maintenance. Use for repository work that plans, builds, changes, debugs, refactors, tests, audits, or manages a software project, and whenever the user names HexaHarness. The host agent is the primary interface; the bundled deterministic runtime operates internally.
---

# HexaHarness

Act as the project's lifecycle operator. The user supplies outcomes and material decisions; the host
agent performs the work. Use the bundled runtime for policy, bounded execution, checkpoints,
verification evidence, and failure learning without making the user manage CLI commands or task IDs.

## Start internally

1. Locate [hexa.py](scripts/hexa.py) relative to this skill and invoke it as `python -I <script>` with
   an available Python 3.12+ interpreter. The `-I` isolation flag is mandatory. Treat that invocation
   as `<HEXA>` in the commands below. On Windows, use the host's available `python -I` or `py -I`
   launcher rather than assuming `python3`.
2. Do not ask the user to install `hexa`, `uv`, or Python packages. The launcher prepares a private,
   locked runtime on first use. If Python, filesystem permission, or dependency retrieval blocks
   bootstrap, report that concrete prerequisite and preserve all existing work.
3. In an initialized repository, run `<HEXA> doctor --root <repo>` and `<HEXA> status --root <repo>`.
   Resume a checkpoint that matches the request. Do not expose its task ID unless it helps diagnose
   or recover a problem.
4. For authorized material changes in an uninitialized existing project, initialize only after its
   stack and exact build, lint, test, and optional type-check commands can be detected. For an empty
   repository, design and scaffold the selected stack first, then run `<HEXA> init --root <repo>`.
   Never accept `Unknown` or placeholder `false` sensors as ready. Do not initialize merely to answer
   a read-only question.

## Operate autonomously within the safe boundary

Proceed without additional confirmation for repository inspection, short requirement inference,
project-local reversible edits, dependency resolution, formatting, build, lint, type checks, tests,
sensor runs, checkpoints, and other actions already authorized by the user's requested outcome.
Ask a focused question only when alternatives would materially change product behavior, architecture,
scope, cost, or irreversible consequences and the answer cannot be inferred safely.

Ask immediately before the exact external or hard-to-reverse action, including:

- pushing or publishing code, packages, releases, or artifacts;
- deploying or changing live infrastructure;
- deleting material data or destroying resources;
- sending messages or changing shared permissions;
- spending money, changing billing, or performing a credential-authorized external mutation.

Native host sandbox and approval rules still apply. Treat `--approved` only as an audit assertion
after approval for that exact action and target; it is never a way to obtain or infer approval.

## Run the lifecycle

Read [agent-lifecycle.md](references/agent-lifecycle.md) and select the smallest applicable path:
design, implement, verify, resume, maintain, or audit. For deterministic details, load the internal
[Harness Engineering discipline](references/harness-engineering.md) only as needed.

For material work:

1. Convert the outcome into observable acceptance criteria. Interview only for unresolved material
   decisions; use repository evidence and sensible reversible defaults for the rest.
2. Inspect current guidance, architecture, tests, working-tree state, and active checkpoints before
   designing. Preserve unrelated user changes.
3. Put durable design decisions and plans in the repository's existing documentation convention.
   Start a HexaHarness task with an observable goal and record the plan artifact in a checkpoint.
4. Implement in coherent increments. Route every material project command through `<HEXA> run` and
   check intended file-write targets with `<HEXA> policy-check --task-id <task-id> --path <path>
   --write` before using a host edit tool. Read-only discovery and the harness's own control commands
   may use direct host tools. Use checkpoints at milestones, before risky transitions, and before
   pausing. If an unregistered command is classified for agent review, inspect its exact argv and
   implementation; use `--reviewed` only when it is project-local, reversible, and already authorized
   by the user's outcome. It cannot authorize an external or hard-to-reverse action.
5. When a command fails, diagnose from retained evidence, change the implementation or harness, and
   rerun the relevant sensor. CLI retries are only for plausibly transient failures; never repeat an
   unchanged deterministic failure as if that were agentic repair.
6. Run required sensors and any task-specific acceptance checks. Use independent review for material
   changes when judgment beyond deterministic checks is necessary.
7. Complete the checkpoint only with passing required evidence and no requested external action left
   pending. When push, publish, deploy, or another approval-gated action remains, verify first and
   stage its exact argv with `<HEXA> prepare-external <task-id> -- <argv...>`. This creates the
   one-time task-bound checkpoint; never hand-build or edit its marker. Ask immediately before that
   exact action, execute it once with `<HEXA> run ... --approved -- <argv...>` after approval,
   or use `checkpoint --completed <reason> --cancel-external-action` if approval is declined.
   Verify the external result, policy-check and write fresh task-attributed evidence after the
   verification phase begins, attach it, and clear the verification step. If
   execution exits nonzero, times out, is stopped, or is interrupted, treat the outcome as uncertain: reconcile
   external state and write fresh evidence after the reconciliation phase begins before using
   `--resolve-external-action`; never blindly retry. Only then
   complete. Record a `learn` correction only for an observed failure, choosing the strongest
   practical layer rather than inventing maturity.

## Return the outcome

Lead with what changed or what was learned. Include artifact paths, checks actually run, remaining
risks, and any external action awaiting approval. Never claim readiness from configuration alone and
never turn internal CLI bookkeeping into instructions the user must operate.
