---
name: hexaharness
description: Orchestrate software projects from discovery and design through implementation, verification, release, resumption, and maintenance. Use for repository work that plans, builds, changes, debugs, refactors, tests, audits, or manages a software project, and whenever the user names HexaHarness. The host agent is the primary interface; the bundled deterministic runtime operates internally.
---

# HexaHarness

Turn the user's project goal into verified work. You own design, implementation, causal repair,
and communication; the bundled runtime records policy decisions, execution bounds, checkpoints,
and evidence. Keep commands, task IDs, and routine bookkeeping internal.

## Start

- Resolve [hexa.py](scripts/hexa.py) relative to this skill. Invoke it with an available Python 3.12+
  interpreter as `python -I <script>`; call that invocation `<HEXA>`. Isolation with `-I` is required.
  The launcher prepares its private, locked dependencies. If bootstrap is blocked, report the
  concrete missing prerequisite; do not ask the user to operate a separate CLI installation.
- Inspect project instructions, existing code and checks, working-tree changes, and active tasks.
  In an initialized project, use `<HEXA> doctor --root <repo>` and `<HEXA> status --root <repo>`.
  Resume matching work rather than starting over.
- For an uninitialized project, inspect its actual stack and exact build, test, lint, and optional
  type-check commands before `init`; override inferred commands when needed. In an empty repository,
  bootstrap the smallest suitable scaffold first; task-scoped write observations apply after
  initialization and task creation. Do not initialize just to answer a read-only question.

## Work

Read [operating-loop.md](references/operating-loop.md) for the runtime command protocol before
material work. Pick the task kind internally: `change` for implementation/design artifacts,
`review` for findings without project changes, or `release` for an external action and its receipt.

- Infer reversible details from the repository. Ask only when an unresolved choice materially
  changes the product, scope, architecture, cost, or irreversible consequences.
- State observable acceptance criteria and take the smallest useful next step. Keep substantial
  design decisions in existing project documentation; a small fix needs no separate design document.
  Use [agent-lifecycle.md](references/agent-lifecycle.md) for substantial design or interrupted work.
- Route material commands through `<HEXA> run`. Before a host edit, use `<HEXA> policy-check
  --task-id <id> --path <path> --write` so evidence can distinguish your changes from existing work.
  Read-only discovery and harness control commands may use direct host tools.
- Treat a failed local check as feedback: inspect its retained output, fix the cause, and rerun the
  relevant check. Ordinary failures keep the task active. Stop and preserve evidence when a real
  budget, timeout, repeated-failure trip wire, or emergency stop blocks progress. Retries repeat
  identical argv and are only for plausible transient failures.
- Checkpoint meaningful milestones or pauses, not every edit. `complete` runs required sensors;
  do not duplicate the full suite immediately beforehand unless a release gate needs it. Use
  additional review only when deterministic checks cannot answer a material acceptance question.

## Authorization and completion

Continue project-local reversible work already covered by the user's goal. Native host permissions
remain authoritative. For external or hard-to-reverse actions, reuse existing authorization only
when it covers the exact action and target; otherwise finish local preparation and ask immediately
before execution. Never ask again merely because a new checkpoint was created.

Stage external actions with `prepare-external`, execute the identical argv once after authorization,
and verify the external result. Reconcile uncertain results before any retry. `--approved` records
authorization already given; `--reviewed` records inspection of an unknown local command. Neither
flag grants permission or bypasses a deny rule. See the operating loop for evidence and recovery.

Complete with passing required checks, evidence appropriate to the task kind, and no unresolved
requested action. Report the outcome, checks actually run, and material remaining limitations.
For a harness audit, use `audit` and [six-layer-contract.md](references/six-layer-contract.md).
For an observed recurring failure, use [failure-ratchet.md](references/failure-ratchet.md); never
invent learning records or unattended successes to make an audit green. Add no runtime layer until
an observed limitation requires it.
