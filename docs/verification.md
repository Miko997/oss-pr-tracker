# Validation record

Version 0.2.0 was tested locally on Python 3.13.13 from the source tree and Python
3.12.13 using an installed wheel. The same 157 offline tests passed on both.
Ruff lint and formatting, locked dependency synchronization, and the source/wheel
build passed. Installed-console help/demo/render checks also passed outside the
source tree on Python 3.12.13.

The 20 added test cases cover repository-owner classification, incomplete base
identity, reconciled lifecycle totals, collection-window wording, default and
non-default targets, missing branch metadata, normalization, and schema-1 snapshots
without the new field. Learning the default branch does not fabricate a status event.

Reproduce the project checks with:

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
git diff --check
```

`scripts/verify_installed.py` checks the installed console script outside the source
tree with credentials removed and network/process calls blocked. See README for
scope and interpretation limits. Local validation only; remote CI has not run.

An authenticated read-only scan and independent GitHub spot checks were also
completed. Personal counts, PR investigations, exact environment-specific commands,
and the historical verification note remain under ignored `reports/`, outside
version control. No Python versions beyond the two listed above were tested.
