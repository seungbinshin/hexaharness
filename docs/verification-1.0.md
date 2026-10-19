# 1.0 candidate verification

The 1.0 candidate stabilizes the existing runtime contracts and retains text improvement and
host handoff as opt-in experimental capabilities, disabled by default. It does not claim useful
model-quality improvement, built-in provider dispatch or native filesystem/network isolation.

Before publication, the local macOS Python 3.12 suite passed **352 tests with 8 Linux-only skips**,
with **86.05% coverage** against the unchanged 85% requirement. Build, Ruff format/lint, strict
mypy, Windows-target mypy and the primary skill validator passed. Claude's native validator
accepted its plugin and marketplace manifests with existing informational documentation warnings;
Codex manifest version/layout remain covered by the distribution tests. A Claude validator must
not be treated as the Codex schema validator (the two hosts interpret manifest roots differently).

Two actual process-tree tests first failed: a one-second timeout waited more than six seconds
because a child ignored termination after its parent exited. The repaired POSIX path reaps the
remaining group, and output drainage has a finite timeout. Windows uses a private supervisor
that joins a kill-on-close job before creating the approved command. Native Windows CI must
verify the real job behavior before release; a macOS pass is not evidence for that branch.

The same native matrix performs cold/warm launcher checks, complete tests, combined coverage,
build, and a fresh wheel installation in a separate space/Unicode path. The published GitHub
release and its delivery receipt identify the exact final commit, run and downloaded artifact
hashes. This pre-publication note is not a substitute for those results.

No actual provider request ran. See [provider readiness](provider-readiness.md) for authentication,
per-call limit and remote cancellation/reconciliation findings, and
[release acceptance](release-readiness.md) for stable versus experimental gates.
