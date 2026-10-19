# Integrated improvement implementation ledger

Development from 0.4.0. Release preparation was authorized after the initial implementation.
The installed plugin and PsOs checkout remain unchanged. No paid model call was made.
Publication and release status must be established from GitHub receipts, not this ledger.

## Scope rulings

- Reuse the task runner, policy, sensors, events, locks and budgets. The host supplies
  candidates; the runtime does not contain a model SDK or a second scheduler.
- A versioned, additive improvement record belongs to the existing task. Existing
  configuration and signed external-action formats remain unchanged.
- The first vertical slice compares **text artifacts as data** using a frozen local
  evaluator. General executable-code candidate isolation is not provided. Unverified
  independence blocks promotion; a copied directory is never called a sandbox.
- Host usage is recorded explicitly. Unknown usage blocks new experiments and promotion;
  it is never treated as free. No measured improvement claim from deterministic fixtures.
- Development uses Python 3.13 on macOS. Python 3.11 is available for the actual launcher
  rejection test. Windows and real model/host integration require separate evidence.

## Progress and observed failures

- Clean checkout and explicit local development venv prepared. Network dependencies needed
  the approved download route. The first ordinary bootstrap was interrupted; subsequent
  harness commands use only the explicit development runtime.
- Python 3.11 tests failed with SyntaxError and unchecked override, before implementation.
- macOS Python 3.13 skipped the editable `.pth` carrying a hidden filesystem flag. Tests
  now explicitly load `src`; wheel installation will be tested separately.

- Implemented the versioned record inside TaskState, not a separate sidecar, so reservations
  and usage commit atomically with the experiment. Configuration has one optional improvement
  section; old configuration/task files remain readable.
- Implemented the narrow `literal-text-v1` evaluator. This deliberately does not represent
  arbitrary program execution or real-model quality. Host generation/composition uses typed
  reservation/submission contracts; model dispatch is outside this preview.
- Python 3.12.13 fresh environment: 300 passed, 8 Linux-specific tests skipped, 86.50% coverage.
  Subsequent promotion-tripwire correction: all 23 improvement tests passed, including actual
  subprocess cancellation and CLI begin/request/submit/evaluate/promote/complete flow.
- Observed and fixed: cancelled capture could launch before checking its signal; terminal-task
  resume could roll back before rejecting terminal state; promotion failure did not fire the
  existing required-sensor tripwire. Each behavior was caught by a failing regression first.
- Ruff uses explicit py312 from project-root configuration, without `--config`; no inference
  workaround was needed. mypy's platform pruning on macOS required a runtime platform variable
  in the cross-platform launcher cache selection; strict type checking then passed.
- sdist initially selected the system interpreter due to UV_PYTHON. Selecting the dev venv
  fixed the environment; sdist and wheel then built successfully. The full-suite package
  checks include the generated dev lock and the new modules/docs.
- Primary skill validator passed. Manifest/version/single-skill checks are in the regression
  suite. No standalone improvement skill was added. Version remains 0.4.0.
- Review found two regressions with failing tests first: an unused optional field changed
  legacy wire serialization/configuration fingerprints, and repeated promotion/completion
  did not revalidate retained baseline evidence. The golden fixture comes from the unmodified
  0.4.0 base commit; both compatibility and evidence regressions now pass (28 focused tests).
- CI now runs the same required checks and cold/warm bootstrap on Linux, macOS and Windows.
  A configured job is not execution evidence; only its observed successful run qualifies.
- The local strict maturity audit is not yet passing: real failure-derived guide rules and
  evidence-complete unattended runs are missing. Do not fabricate them or release 1.0.0 by
  changing the version alone. General code optimization and real-model quality remain unproven.
