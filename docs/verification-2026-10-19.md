# Candidate verification checkpoint — 2026-10-19

This records the pre-publication checkpoint for the unreleased improvement work. The package and
plugin versions remain 0.4.0. It is not a 1.0 release announcement or a provider-quality claim.

## Verified locally

- Build, Ruff, strict mypy and the complete macOS regression suite passed: **349 passed,
  8 skipped**, **86.83%** branch-inclusive coverage, exceeding the existing 85% threshold.
- The 48 improvement/host tests passed, including six reproduced and repaired cases where stored
  request, adapter, provider, receipt or usage data changed after settlement.
  A further delayed-reservation test confirms storage time cannot extend the host-call deadline.
- Legacy absent-field JSON/fingerprints remain covered. No approval-key rotation or project-wide
  budget increase was needed. Task continuations retain earlier usage and an absolute deadline.
- A fresh wheel installation in a separate space/Unicode path completed the deterministic text
  candidate flow. Import origin was inside installed site-packages, and the resulting checkpoint
  and passing sensor exits were independently checked. It made no provider call. Matrix CI now
  repeats this installation test on each supported OS for the published source commit.
- The skill validator passed. Five recorded learning rules match five actual failure tasks and
  retained evidence digests. The real STOP drill blocked a harmless command and preserved its
  checkpoint on recovery.

The strict project audit before publication is **11 pass, 1 warning, 0 fail**: two current,
evidence-complete unattended outcomes are retained. A third must be real completed work with a
distinct, still-current artifact; editing notes solely to satisfy the counter is not evidence.
This project's maturity audit does not certify a provider, consumer integration or OS sandbox.

Some local files temporarily had no resident data, coinciding with evidence being rejected.
Metadata changes during hydration are a plausible cause; a before/after trace for each rejection
was not captured. After reads stabilized, all five original learning digests and their source
events matched; no evidence was rewritten and no validation was relaxed.

## Remaining release conditions

The earlier [three-platform CI](https://github.com/seungbinshin/hexaharness/actions/runs/39273874878)
passed for commit `b7c1f464c33715070b75db0f1d7ca611ce6fed8c`. That result does not cover subsequent
changes. The candidate's exact published commit needs fresh matrix/installation results and
artifact digests, recorded in its PR and delivery evidence.

The implemented host boundary has deterministic request/receipt tests, but no actual provider
dispatch evidence. An authenticated session and reported usage do not establish enforceable
per-call token/cost ceilings, cancellation or reconciliation. A live probe stays undispatched
until those conditions are known. Fixture receipts are labelled and cannot serve as that proof.

Literal text checks establish only their specified data properties. Useful model-quality gains
require independent held-out repeated trials. General code optimization and a native code sandbox
remain unsupported, as reported by `hexa capabilities`. No release may imply those features exist.

Consumer installation and PsOs integration are separate operations after a reviewed, verified
release. See [release acceptance](release-readiness.md), [host handoff](host-adapter.md),
[API integration](api-contract.md) and [task budgets](task-budgets.md).
