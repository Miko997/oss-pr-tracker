# Validation record

The initial local implementation was tested on Python 3.13.13 and an installed wheel
on Python 3.12.13. All 137 offline tests passed, along with Ruff lint/format checks,
package build, and offline installed-console help/demo/render checks. Live evidence
and the detailed historical personal validation note are kept under ignored
`reports/`, outside version control.

Reproduce the project checks with:

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

`scripts/verify_installed.py` checks the installed console script outside the source
tree with credentials removed and network/process calls blocked. See README for
scope and interpretation limits. Local validation only; remote CI has not run.
