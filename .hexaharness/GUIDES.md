# Failure-derived guides

This file is rendered from `guide-rules.yaml`. Add rules through the HexaHarness learning
workflow; do not edit the rendered entries directly.

## guide-20261019t152427z-5091 (active)

- Added: 2026-10-19T15:24:27.904760Z
- Source task: 20261019t142103z-review-0-4-compatibility-sta-e2cd
- Evidence: .hexaharness/artifacts/20261019t142103z-review-0-4-compatibility-sta-e2cd/20261019T142342310726Z-command-attempt-1-4f74741842.log
- Failure class: missing-context
- Observed failure: Optional fields changed legacy wire bytes and promotion accepted missing baseline evidence.
- Rule: For new optional contracts, preserve absent-field legacy bytes and fingerprints, and test baseline evidence again at repeat promotion and final completion; a previous successful attempt is insufficient.
- Verification: uv run pytest tests/test_compatibility.py tests/test_improvement.py

## guide-20261019t152428z-6615 (active)

- Added: 2026-10-19T15:24:28.223695Z
- Source task: 20261019t143347z-fix-windows-ci-cold-bootstra-58c9
- Evidence: .hexaharness/artifacts/20261019t143347z-fix-windows-ci-cold-bootstra-58c9/20261019T143414267591Z-command-attempt-1-f027de409e.log
- Failure class: missing-context
- Observed failure: The exclusive bootstrap lock held its creation descriptor open, preventing Windows unlink.
- Rule: When changing bootstrap locking, distinguish pathname ownership from an open descriptor lock; validate close/unlink and replacement ownership on Windows, not just POSIX.
- Verification: uv run pytest tests/test_bootstrap.py::test_install_lock_closes_creation_handle_before_work

## guide-20261019t152428z-64fc (active)

- Added: 2026-10-19T15:24:28.479872Z
- Source task: 20261019t143726z-fix-actual-windows-type-chec-72c4
- Evidence: .hexaharness/artifacts/20261019t143726z-fix-actual-windows-type-chec-72c4/20261019T143734349785Z-command-attempt-1-0075b4fe2e.log
- Failure class: missing-context
- Observed failure: Windows-target mypy found nine unavailable POSIX API references despite a passing Mac check.
- Rule: A passing host-platform type check is not cross-platform evidence. Validate platform-specific imports and APIs against each supported target and retain the native CI result.
- Verification: uv run mypy --platform win32 src skills/hexaharness/scripts/hexa.py

## guide-20261019t152428z-1844 (active)

- Added: 2026-10-19T15:24:28.745609Z
- Source task: 20261019t144449z-keep-first-use-installer-dia-8310
- Evidence: .hexaharness/artifacts/20261019t144449z-keep-first-use-installer-dia-8310/20261019T144508068762Z-command-attempt-1-c5275ed767.log
- Failure class: missing-context
- Observed failure: First-use installer diagnostics contaminated machine-readable CLI stdout.
- Rule: Treat bootstrap and child output as part of the CLI contract: keep stdout machine-readable, diagnostics on stderr, and verify exact cold/warm output plus the true child exit code.
- Verification: uv run pytest tests/test_bootstrap.py::test_first_use_installer_does_not_pollute_cli_stdout

## guide-20261019t152428z-bdb0 (active)

- Added: 2026-10-19T15:24:28.999900Z
- Source task: 20261019t144937z-preserve-optional-improvemen-f8dc
- Evidence: .hexaharness/artifacts/20261019t144937z-preserve-optional-improvemen-f8dc/20261019T144955613556Z-command-attempt-1-b888cade33.log
- Failure class: missing-context
- Observed failure: Forced initialization silently removed the configured improvement strategy.
- Rule: Before extending configuration, test force-initialization against existing optional settings and active work; rebuilding defaults must not silently discard a user-selected strategy.
- Verification: uv run pytest tests/test_improvement.py::test_forced_initialization_preserves_active_improvement_config
