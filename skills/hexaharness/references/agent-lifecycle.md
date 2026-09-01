# Agent lifecycle

Use this reference to move a project forward without requiring the user to operate HexaHarness.
The host agent owns reasoning and implementation; the bundled runtime owns deterministic controls
and evidence.

## Choose the path

| Situation | Path | First durable output |
|---|---|---|
| Empty or new repository | Design | Product brief, acceptance criteria, and architecture outline |
| Existing project with a requested change | Implement | Scoped plan tied to current code and tests |
| Interrupted or continued work | Resume | Reconciled checkpoint and working-tree state |
| Bug or failed check | Repair | Reproduction evidence and smallest causal fix |
| Release or readiness request | Verify | Required sensor results plus unresolved risks |
| Ongoing upkeep | Maintain | Prioritized risk, dependency, or failure-derived improvement |
| Harness quality question | Audit | Twelve-check evidence report and ordered remedies |

## Discover and design

1. Read applicable project instructions and inspect the repository before asking questions.
2. Separate known facts, reversible defaults, and material unknowns. Ask only about material unknowns
   that affect observable behavior, architecture, compliance, budget, or irreversible operations.
3. Define the smallest release slice with users, inputs, outputs, non-goals, constraints, and
   testable acceptance criteria.
4. Record architecture decisions where the repository already keeps design documentation. Include
   rejected alternatives only when the tradeoff will matter later.
5. Split work into milestones with a verification signal for each. Keep the current milestone in
   the HexaHarness checkpoint; keep the fuller plan in a durable repository artifact.

## Implement and repair

1. Preserve unrelated work and establish the baseline with the narrowest relevant sensors.
2. Make coherent, reviewable changes. Update tests and documentation in the same milestone when they
   are part of the behavior contract.
3. Route material project commands through `<HEXA> run` so policy, budgets, and evidence apply.
   Before a host edit tool writes a target, evaluate that target with `<HEXA> policy-check --task-id
   <task-id> --path <path> --write`; direct host tools remain appropriate for read-only discovery and
   harness control commands. Do not wrap commands in a shell string. For an unregistered command
   marked for agent review, inspect the exact argv and underlying script before using `--reviewed`;
   do so only for project-local, reversible work already covered by the user's request. External or
   hard-to-reverse actions always require exact human approval and `--approved`.
4. On failure, classify the cause before acting: implementation defect, incorrect assumption,
   missing context, environment failure, unsafe action, or flaky/transient tool.
5. Repair the cause, then rerun the narrow check before the full required sensor set. Preserve the
   strongest artifact when a trip wire or budget ends the attempt.

## Resume and manage

1. Compare active checkpoints with the actual working tree and recent history. Repository state is
   authoritative when a stale checkpoint disagrees.
2. Continue from the smallest verified next step. Do not redo completed work merely to make the
   checkpoint look orderly.
3. Checkpoint after a meaningful milestone, before an external approval boundary, and before a
   planned pause. Do not checkpoint every file edit or command.
4. Keep task IDs, raw JSON, and routine policy decisions internal. Surface them only for recovery,
   audit, or debugging.
5. Convert only real repeated failures into guide, sensor, loop, permission, memory, or observability
   improvements. A proposal is not an active rule until implemented and verified.

## Delegate with typed handoffs

Use host-provided subagents only when parallel work or independent judgment adds signal. Give each
worker a bounded scope and require a structured return containing the task identity, artifact paths,
checks actually run, assumptions, and unresolved risks. Keep the shared HexaHarness checkpoint as
the source of truth rather than sharing free-form reasoning between workers.

For material review, assign a reviewer that did not produce the artifact. The reviewer reports
findings and evidence without silently rewriting the producer's work; the lifecycle operator routes
failures back for repair and applies the same retry and escalation bounds.

## Verify and hand off

1. Map each acceptance criterion to computational evidence where possible.
2. Run the configured required sensors and task-specific checks. If a semantic review is required,
   use an independent reviewer and label judgment separately from deterministic evidence.
3. Inspect the final diff for unrelated changes, sensitive material, generated noise, and missing
   documentation.
4. Complete the task only after required checks pass and all requested actions are finished. Report
   skipped checks and reasons explicitly.
5. If push, publish, deploy, deletion, shared permission changes, messages, billing, or another
   credential-authorized external mutation remains, use `prepare-external` to create a one-time
   task-bound checkpoint and keep the task active. Pause for approval immediately before the exact
   action. If approval is declined, cancel the pending action with a recorded reason. Otherwise run
   the identical argv once with `--approved`, verify the external result, and create task-attributed
   evidence after the verification phase begins before clearing the step. Reconcile any
   non-successful or uncertain return, including nonzero exit, timeout, stop, or interruption, with
   fresh post-transition evidence before any retry.
