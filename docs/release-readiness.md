# 1.0 release acceptance

This ledger separates the stable product contract from opt-in experimental features. Version
metadata alone is not publication: a release requires an exact commit, fresh native CI, verified
artifacts and a verified GitHub tag/release. Do not report a queued run as a passing gate.

## Stable product boundary

One plugin, primary lifecycle skill, configuration and deterministic runtime remain. The host
owns reasoning, model credentials and native isolation. The runtime owns policy, finite command
execution, checkpoints and evidence. A provider SDK or research quality benchmark is not required
for those existing responsibilities. Native permissions remain authoritative.

| Required gate | Acceptance evidence |
|---|---|
| Execution and cancellation | Shell-free approved argv, finite timeout, STOP before/during execution, process-group/job descendant cleanup, bounded output drainage; native Linux/macOS/Windows tests |
| Approval and reconciliation | Exact task/argv HMAC binding, no automatic mutation retry, fresh independent receipt, uncertain results stay unresolved |
| Compatibility | Legacy absent-field wire/fingerprints, schema 1/2 reads, future schemas fail closed, strict integer exits, complete only with all required current sensors |
| Task continuation | Explicit task-only limits, inherited usage, source hashes, absolute deadline, unchanged global defaults and native authority |
| Evidence and recovery | Atomic checkpoints, current artifact/sensor digests, real STOP/recovery drill; no fabricated learning/outcome records |
| Distribution | Synchronized 1.0.0 package/manifests, locked dependencies, cold/warm launcher, fresh installed wheel in space/Unicode paths on all supported OSes, verified artifact digests |
| Consumer handoff | Versioned discovery, exact launcher, strict completion/sensor verification and pinned artifact; actual PsOs installation remains separately coordinated |

Linux-only anchored external transactions remain explicitly scoped to their existing native
metadata and reviewed-executor requirements. Windows support does not enable them or alter host
logon rights. Process lifetime cleanup does not provide filesystem/network isolation or prevent
hostile programs from using external services to escape a local process tree.

## Experimental features: opt-in and disabled by default

`HarnessConfig.improvement` defaults to null. Without explicit project configuration, `begin`
fails before creating experiment state; ordinary workflows never dispatch a model. Capability
metadata labels text improvement and host handoff experimental and reports both defaults disabled.

The fixed `literal-text-v1` evaluator handles bounded UTF-8 data only. Its required deterministic
gates remain baseline/evaluator identity, fresh attempt/output evidence, unknown-usage blocking,
conservative reservations, duplicate suppression, local rollback and current sensors. These tests
must pass even though the feature is experimental. Future code optimization, external semantic
judges and native code sandboxes remain unavailable.

A live supported provider must independently establish hard call/token/cost bounds, remote
cancellation and uncertain-result reconciliation before using the experimental host handoff.
The runtime's assertions are not independent provider certification. Fixture results stay labelled.
Useful real-model improvement claims additionally need independent held-out repeated trials.
Neither unverified provider support nor research performance is advertised as a 1.0 guarantee;
therefore those optional claims are not an indefinite blocker on the stable runtime release.
See [provider readiness](provider-readiness.md) for the actual connection findings.

## Publication and verification

The authorized publication route is the existing GitHub repository. Run the full matrix for the
final source and inspect its installation evidence and artifact hashes before merge/tag/release.
Do not lower coverage, omit Windows checks or substitute earlier-commit results. After publication,
read back the exact tag commit, release assets/digests and version. Keep installed consumer copies
unchanged until their own update is authorized and verified.

Public documents omit machine-specific paths, account details, credentials and runtime keys.
Historical verification notes identify the commits they cover and do not certify newer source.
