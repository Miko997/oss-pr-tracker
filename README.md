# OSS PR Tracker

A local Python CLI that collects evidence about public pull requests authored by a
GitHub account. It shows lifecycle counts, possible attention items, and changes
since a saved scan in Markdown and JSON. GitHub requests are read-only REST GETs.

## Install and try it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Python 3.12 and 3.13 are
covered by the offline checks. From a checkout of this repository:

```bash
uv sync --locked
uv run oss-pr-tracker demo --output-dir reports/demo
uv run oss-pr-tracker render --input reports/demo/status.json --output-dir reports/rendered
```

Open `reports/demo/status.md`. The matching `status.json` stores the observed facts.
The demo is synthetic and intentionally includes incomplete evidence, so a
partial-evidence warning is expected even though the command succeeds. Both `demo`
and `render` work offline without credentials. Rendering preserves the original
observation times; it does not refresh GitHub data.

A shortened summary of the bundled **synthetic** example:

| Dimension | Counts across 8 example PRs |
| --- | --- |
| Lifecycle | 5 open, 1 draft, 1 merged, 1 closed unmerged |
| Attention | 1 author_action, 4 inspect, 1 waiting, 2 done |

## Scan an account

Replace `YOUR-LOGIN` with the GitHub username to scan:

```bash
uv run oss-pr-tracker scan --username YOUR-LOGIN --output-dir reports/current
uv run oss-pr-tracker scan --username YOUR-LOGIN --previous reports/current/status.json --output-dir reports/next
```

Use a new output directory to keep the previous evidence. Each run prints output
paths and counts; progress and warnings go to stderr. `--help` lists all options.

By default, the collection window includes **all discoverable open PRs regardless
of age, plus PRs closed within the last 90 days**. The cutoff is inclusive and fixed
at scan start. `--closed-since YYYY-MM-DD` selects a different UTC cutoff;
`--all-history` requests all discoverable closed history instead. These options are
mutually exclusive. Counts describe the displayed collection window, not lifetime
contributions. JSON dates use UTC; Markdown dates use Europe/Helsinki with an offset.

### Authentication

The default `--auth auto` uses an existing `GH_TOKEN`, then `GITHUB_TOKEN`, then the
GitHub CLI's existing credential for `github.com`. Without one, it attempts public
unauthenticated access. `--auth none` bypasses credential discovery. The tool does
not create credentials or offer a login flow, and authentication failures do not
silently switch identities. Tokens stay in memory and must not be put in command
arguments, reports, or committed files. No write permission is needed.

Unauthenticated requests have a much smaller allowance and can produce partial
reports. GitHub also applies separate search and secondary limits; response headers
and report warnings take precedence over assumed quotas. See
[GitHub's rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

## Interpret the evidence

**Attention flags are evidence to inspect, not proof that a PR is mergeable or
needs author action.** Read the current discussion, subsequent commits and check
details before responding or changing a branch.

- `author_action`: an observed conflict or a human change request tied to the
  current head. It does not establish that an old branch should be revived.
- `inspect`: failed/unknown checks, older feedback, draft decisions or incomplete
  evidence. A red check does not prove the patch caused the failure.
- `waiting`: no stronger reason was found; waiting for review is an inference.
- `done`: merged or closed without merging, not a claim of release availability.

Lifecycle, attention and collection quality are separate. No observed checks is not
passing CI; skipped, neutral, pending and successful results remain distinct.
Feedback timing does not prove a response is owed or a thread is unresolved.
Terminal PRs intentionally omit review/check enrichment.

Counts are split into **own-account repositories**, **other-owner repositories** and
**unknown ownership**, using the authoritative base repository owner and a
case-insensitive username comparison. Missing identity stays unknown. Other-owner
can include organizations the person controls; no organization control, independent
upstream work or open-source license is inferred. Overall merges are not verified
external contribution totals.

Merged rows show the actual target and compare it with the repository default
observed during collection. That default may have changed since the merge. A
non-default target can be a feature or integration branch; later inclusion in the
default branch or a release is not checked.

## Scope and limits

Only public authored PRs on `github.com` are supported. Private repositories,
Enterprise hosts, reviews on other authors' PRs, issue-only activity and off-GitHub
work are outside the inventory.

[GitHub search](https://docs.github.com/en/rest/search/search) returns at most 1,000
matches per query and can report incomplete results. Searches are not automatically
split. A complete report means no detected gaps within its stated scope, not an
exhaustive GitHub index. Search and detail reads are not atomic. Repeated head/base
changes are marked unstable instead of mixing evidence.

Checks are collected for the PR head in the base repository. Required-check policy,
test-merge-only checks, merge queues, workflow approval rights and release inclusion
are not established. The check-run API also limits accessible check suites.

Comparisons require the same username, schema version and live/demo mode. An absent
record means only “not observed in this scan/scope.” Older schema-1 snapshots load
without the optional default-branch field; newly learned metadata creates no false
status event. Report files are replaced individually, not as a transaction.

| Exit code | Meaning |
| --- | --- |
| 0 | Scan completed within scope, or demo/render succeeded; attention items may remain |
| 1 | Operational or output failure |
| 2 | Invalid arguments, configuration or snapshot |
| 3 | Usable live report with collection gaps |

## Development

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests use synthetic data and mock HTTP, block real sockets, and isolate credentials.
The minimal [CI workflow](.github/workflows/ci.yml) is configured for Python 3.12 and
3.13 with read-only permissions, no repository secrets and no live scans. Local
validation is recorded in [verification](docs/verification.md); it is not a claim
that GitHub CI has run. [Architecture](docs/architecture.md) explains the evidence
model. Generated reports, credentials, logs and build output belong outside Git and
package artifacts.

## License

Original project code is licensed under the [MIT License](LICENSE), copyright
2026 Miko Parkkinen. Dependencies retain their own licenses and notices; their code
is not bundled in the tracker wheel. See [dependency notices](docs/dependencies.md).
