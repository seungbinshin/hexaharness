# Changelog

## 0.2.1 - 2026-09-05

- Simplified the primary skill and removed a duplicate reference router. Small changes no longer
  require separate design documents or redundant final sensor runs; existing exact authorization
  remains valid across checkpoints.
- Kept ordinary local command failures active for agent repair; repeated failures now distinguish
  full commands and reset after success or a different failure.
- Excluded durable approval waits from execution wall time and checked budgets before recording an
  external action as started.
- Added review and release task kinds so fresh findings or verified publication receipts can
  complete work without artificial source edits.
- Applied task execution budgets to sensors and checked reported token/cost limits before commands.
- Stopped interpreting arguments to allowed local commands as unrelated executables.
- Detected Node package managers and existing scripts; omitted unconfigured optional type checks.
- Updated English and Korean usage documentation and synchronized both plugin manifests.

## 0.2.0 - 2026-09-01

- Made the primary skill implicitly invocable for software project design, implementation,
  verification, and maintenance.
- Moved the six-layer companion discipline under the primary skill so Codex and Claude expose one
  consistent automatic entrypoint.
- Added a plugin-bundled launcher that prepares and reuses a private, version-locked CLI runtime.
- Changed the default autonomy boundary so project-local reversible work is allowed while external
  publication and destructive operations remain human-gated; unregistered local commands now have
  a separate, auditable agent-review boundary.
- Made empty-repository initialization design-first, rejected placeholder verification commands,
  and made forced refresh preserve learned evidence while updating managed project guidance.
- Added concurrent checkpoint protection, crash-safe one-time task- and phase-bound external
  actions, in-flight emergency stop handling, artifact and guidance fingerprints, and
  evidence-aware audits.
- Bound completion to task-scoped pre-write content deltas, de-duplicated unattended artifact
  claims, and bound concurrent failure learning to stable runtime evidence.
- Rejected stale in-memory configuration at execution and completion boundaries, required
  phase-fresh evidence for external-action verification, and added a nonce-retiring cancellation
  path when a user declines an unexecuted action.
- Closed subprocess, local dependency locator, Git global-option, multicall, and inline-interpreter
  policy bypasses; made secret redaction command-context aware across flags, headers, URLs,
  environment values, and nested JSON.
- Human-gated cloud and infrastructure operations unless proven read-only, preserved those gates
  through transparent and opaque command wrappers, blocked staged-action policy drift, and made
  source distributions exclude build-time temporary archives and local runtime evidence.
- Added installable Codex and Claude Code marketplace manifests and an agent-first Korean README.

## 0.1.0 - 2026-09-01

- Added the deterministic `hexa` CLI and six-layer project scaffold.
- Added bounded command execution, policy decisions, checkpoints, sensors, audits, trip wires, and
  failure learning.
- Added portable HexaHarness and Harness Engineering skills.
- Added Codex/ChatGPT and Claude Code plugin manifests.
