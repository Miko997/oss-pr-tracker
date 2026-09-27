# OSS PR Tracker

OSS PR Tracker is a local Python CLI for public pull requests authored by a selected
GitHub account. It answers what merged, what remains open, what may need attention,
and what changed since a saved scan. Every API request is GET. It never posts
comments, changes PRs, retries workflows, or writes to GitHub.

## Quick start

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/guides/projects/).
From this project directory:

```bash
uv sync --locked
uv run oss-pr-tracker demo --output-dir reports/demo
uv run oss-pr-tracker scan --username Miko997 --output-dir reports/current
```

Open `reports/current/status.md` for the action list and evidence. The matching
`status.json` is a versioned snapshot suitable for later comparison. Commands print
absolute output paths and observed counts. Scan progress and completeness warnings
go to stderr. The synthetic demo deliberately includes incomplete CI evidence, so
its report shows partial data while the successful offline command exits 0.

A standard scan includes **all discoverable public open PRs regardless of age**, plus
PRs closed in the last 90 days. The inclusive cutoff is calculated once in UTC at
second precision. Human dates use Europe/Helsinki with a numeric offset; JSON uses
UTC. Explicit dates mean midnight UTC.

```bash
uv run oss-pr-tracker scan --username Miko997 --closed-since 2026-01-01 --output-dir reports/since-january
uv run oss-pr-tracker scan --username Miko997 --all-history --output-dir reports/history
uv run oss-pr-tracker scan --username Miko997 --previous reports/earlier/status.json --output-dir reports/current
uv run oss-pr-tracker scan --username Miko997 --auth none --output-dir reports/public-only
uv run oss-pr-tracker render --input reports/current/status.json --output-dir reports/rendered
uv run oss-pr-tracker --help
uv run oss-pr-tracker --version
```

`--closed-since` and `--all-history` are mutually exclusive. `--username` is required
and validated as a GitHub login. `demo` also accepts `--previous` for a demo baseline.
`render` and `demo` do not authenticate or access the network. Rendering preserves
the snapshot's original times and live/demo identity; it does not refresh evidence.

For installation without uv, create a project-local Python 3.12+ virtual environment
and run `python -m pip install .` inside it. That fallback resolves dependency ranges;
the supplied `uv.lock` is the reproducible dependency resolution used for validation.
No license or publication decision has been made for this local project.

## Reading the report

The summary and both output formats describe the same observed PRs. Lifecycle,
attention, and collection quality are separate:

| Dimension | Values and interpretation |
| --- | --- |
| Lifecycle | open, draft, merged, closed_unmerged, unknown |
| Attention | author_action, inspect, waiting, done |
| Quality | complete, partial, unstable; each evidence collection has its own status |

- **Author action** means a coherent conflict observation or a human change request
  tied to the current head. The instruction is to address or respond to the review;
  it is not a claim that every suggested change is correct.
- **Inspect** includes failed or unknown checks, draft decisions, old-head review
  requests, ambiguous feedback, and incomplete collection. Check failures do not
  prove the patch caused the failure.
- **Waiting** means no stronger reason was found in the collected evidence. Review
  waiting is an inference, and pending checks retain their actual state.
- **Done** means authoritative merged or closed-without-merging state. The report
  names the actual target branch. Merging into a feature branch proves neither
  release inclusion nor availability in the default branch.

A comment after the author's most recent visible comment/review is a *possible*
follow-up. PR creation is the fallback boundary. This does not establish that a
response is owed, that feedback is unread, or that a review thread is unresolved.
Pushes and offline conversations may already have addressed it. A prior scan is
not an acknowledgement. Bot feedback remains visible without being treated as a
maintainer requirement. Dismissed reviews retain their chronology.

Empty successful check collections mean `none_observed`, not passing CI. Unavailable
collections remain unavailable. Success, neutral, skipped, pending, cancelled,
action-required and failure results are kept distinct. Unknown values trigger
inspection. Terminal PRs intentionally omit feedback/CI collection and say
`not_requested_terminal`.

## Existing credentials and HTTP limits

`--auth auto` checks `GH_TOKEN`, then `GITHUB_TOKEN`, then captures the output of
`gh auth token --hostname github.com` in memory with a five-second timeout. If none
is available it uses unauthenticated public access. `--auth none` bypasses that lookup.
There is no token argument, login flow, credential creation, or credential file.
An authentication rejection is reported and never causes a silent identity switch.

The origin is fixed to `https://api.github.com`; requests pin API version
`2026-03-10`, use a descriptive User-Agent, and have five-second connect and
15-second network-operation timeouts. Requests are sequential. Up to two retries
are allowed for transient transport/5xx failures. Rate limits honor the advertised
retry/reset time. The total retry sleep budget is 30 seconds per scan. A secondary
limit without usable timing stops collection. Further requests are suppressed once
a rate-limit or authentication stop is recorded. Redirects are limited to three;
redirects and pagination reject credentials, ports, or hosts outside the exact API
origin. Pagination loops and safety ceilings produce partial data.

An unchanged open PR needs at least seven ordinary reads: details, three feedback
sources, checks, statuses, and final details. Unauthenticated access normally allows
60 ordinary requests/hour; authenticated personal access normally allows 5,000, with
separate search limits. Actual response headers take precedence. Public-only scans
can therefore legitimately end with a useful partial report. See
[GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

## Scope and limits

This is an authored-PR inventory, not a record of all contributions. Reviews on
other people's PRs, issue comments without an authored PR, coauthorship on another
person's PR, and off-GitHub work are outside scope. Public visibility alone does
not establish an open-source license.

Search exposes at most 1,000 matches per query and can report `incomplete_results`.
The tool records the requested queries, reported totals, unique fetched counts,
pages and gaps. It does not split large searches automatically. A complete scan
means retrieved without detected gaps within the stated scope, not a guarantee
about GitHub's index. Search results and subsequent reads are not atomic.

Open-PR evidence is collected for the exact head SHA in the base repository. A final
detail refresh detects head/base/lifecycle/draft changes and permits one recollection.
Repeated changes discard mixed-revision evidence and mark the record unstable.
The tool does not establish required-check policy, test-merge-only results, merge
queues, deployment environments, or workflow approval rights. The check-run endpoint
is limited to the most recent 1,000 check suites on a reference; ordinary pagination
does not remove that ceiling. No extra suite traversal is implemented. The API's
`filter=latest` selects check replacements; distinct returned app/suite/run identities
are preserved rather than guessed to be duplicates.

Private repositories and GitHub Enterprise are unsupported. Author and explicitly
public base-repository visibility are checked before enrichment and again after it.
An excluded/private/unknown-visibility response is discarded, leaving a sanitized
warning tied to its previously public search URL. A failed detail request leaves
only that locator and an incomplete placeholder, without trusting the search title.

Snapshots are historical evidence, not an HTTP cache. Comparisons require the same
username, schema version and live/demo mode. Different date windows compare common
identities; an absent record only means “not observed in this scan/scope.” New
observations are distinguished from events known to have happened after the baseline.
Feedback body digests detect edits outside the short excerpt. No additional API
requests are made for comparison.

Each report file is replaced through a temporary file and atomic rename. The pair
is **not transactional**: if the second replacement fails, one file may be newer.
Both carry scan identity and timestamps; success is announced only after both writes.
A supplied baseline is read before any output replacement.

| Exit | Meaning |
| --- | --- |
| 0 | Collection completed within stated scope, or demo/render succeeded; attention items may remain |
| 1 | Fatal operational failure or output failure; no successful report pair announced |
| 2 | Invalid arguments, configuration or snapshot |
| 3 | Usable live report produced with discovery or required-enrichment gaps |

## Development and verification

```bash
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests use in-process `httpx.MockTransport`, block real socket connections, remove
environment credentials, stub credential discovery, and inject clocks/retry sleeps.
Pytest plugin auto-loading is disabled in this project to avoid unrelated system
plugins. Do not use live API results as regression fixtures. Synthetic demo JSON is
included in the wheel; generated reports and virtual environments are ignored.

See [architecture](docs/architecture.md) and the actual [verification record](docs/verification.md).
