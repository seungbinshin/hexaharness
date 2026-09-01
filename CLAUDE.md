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
- Keep project-local reversible work autonomous. Ask immediately before push, publish, deploy,
  material deletion, shared permission changes, messages, billing, or another credential-authorized
  external mutation. Let the skill stage and bind that exact action internally before asking.
<!-- hexaharness:end -->
