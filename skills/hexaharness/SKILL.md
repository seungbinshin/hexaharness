---
name: hexaharness
description: Start or deliberately rework a repository's production agent harness. Use when the user explicitly invokes HexaHarness or asks to initialize, audit, harden, or evolve all six harness layers as one project.
---

# HexaHarness

Act as the explicit entrypoint to the bundled `harness-engineering` discipline. Keep the user's
objective authoritative and do not turn an ordinary one-off task into a harness project.

1. Inspect the repository and run `hexa doctor --root <repo>` if HexaHarness is already initialized.
2. If it is not initialized and the user requested changes, run `hexa init --root <repo>`. Preserve
   existing `AGENTS.md`, `CLAUDE.md`, configuration, and unrelated work.
3. Load the companion `harness-engineering` skill and follow only the operating mode relevant to
   the request: initialize, execute, audit, or learn.
4. Treat `hexa` output as evidence, not as permission. Ask immediately before external writes,
   publishing, deployment, messages, destructive operations, or passing `--approved`.
5. End with the artifact or checkpoint, sensors actually run, unresolved risks, and the next safe
   action. Never claim production readiness from configuration alone.

If `hexa` is unavailable, report the missing deterministic control instead of silently replacing it
with conversational bookkeeping. Local development can run it with `uv run --project <plugin-root>
hexa`; installed users should place the `hexa` command on `PATH`.
