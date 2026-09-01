# Contributing

1. Open an issue describing the observable problem, supported inputs, non-goals, and verification
   evidence.
2. Keep changes inside the smallest owning module. New infrastructure must address a demonstrated
   failure or requirement.
3. Add or update tests for behavioral invariants, including adversarial cases where appropriate.
4. Run the complete local sensor suite before opening a pull request.

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov
uv build
```

Changes to a skill also require `quick_validate.py`; changes to `.codex-plugin/plugin.json` require
the plugin validator. Never include credentials or runtime `.hexaharness/` evidence in a commit.
