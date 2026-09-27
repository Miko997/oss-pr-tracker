# Local validation

Publication preparation for version 0.2.0 was validated locally on Linux with
Python **3.13.13** and **3.12.13**. The same **157 offline tests passed on each**.
No other Python version or operating system was tested in this pass.

Commands run from the project root:

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
git diff --check
```

The source distribution and wheel were rebuilt and their actual contents inspected.
They include the MIT license and synthetic demo, and exclude personal reports,
credentials, logs, caches and recovery material. Package metadata declares `MIT`.
A clean Python 3.12.13 environment installed the built wheel and ran the complete
offline suite. Installed help, demo and render also passed outside the source tree
with credential variables removed and network/process calls blocked by
`scripts/verify_installed.py`.

README demo/render commands were executed and their JSON outputs compared. Scan
command syntax and comparison behavior were exercised with synthetic HTTP responses;
no live scan was run for publication preparation. The README's eight-PR example
matches the bundled synthetic data.

The CI workflow configures tests, Ruff lint/format checks and a build on Python 3.12
and 3.13, with read-only permissions and pinned action revisions. Local workflow
validation is not a successful GitHub CI run; remote CI has not run.

Environment-specific command transcripts and private audit/recovery details stay
outside the tracked source. Previously saved live reports remain local and ignored.
