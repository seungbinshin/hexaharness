@AGENTS.md

For repeated HexaHarness workflow decisions, also read `.hexaharness/GUIDES.md` when it exists.

<!-- hexaharness:start -->
## HexaHarness lifecycle

- Project: `HexaHarness`
- Language: `Python 3.12+`
- Required commands:
  - Build: `uv build`
  - Test: `uv run pytest`
  - Lint: `uv run ruff check .`
  - Type check: `uv run mypy src skills/hexaharness/scripts/hexa.py`
- Use the HexaHarness skill automatically for material project planning, implementation,
  verification, resumption, and maintenance when it is available.
- Let the skill operate its bundled CLI internally; do not require the user to manage task IDs or
  routine harness commands.
- Read `.hexaharness/GUIDES.md` for active failure-derived rules.
- Keep project-local reversible work autonomous. External or hard-to-reverse actions require
  authorization for the exact action and target. Reuse applicable authorization already given;
  otherwise finish local preparation and ask immediately before execution. Let the skill stage
  and bind the action internally.
<!-- hexaharness:end -->
