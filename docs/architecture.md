# Architecture

The flow is: **GET API reads → normalized evidence → deterministic classification →
Markdown and JSON**. The application is a synchronous local CLI. A database,
service, LLM, or plugin framework would add moving parts without helping this
one-shot inventory and explicit snapshot-comparison workflow.

`github.py` owns credential selection, a GET-only HTTP interface, origin validation,
redirects, retries, and pagination. Credentials stay in memory. Error messages use
fixed descriptions rather than raw server bodies, headers or exception dumps. The
active credential is redacted from returned strings before normalization. Requests
use the supported `2026-03-10` API contract. No write endpoint, login flow, workflow
retry, scheduler, or remote publication code exists.

`collect.py` queries all public open PRs and a separate closure-date window. Search
IDs deduplicate discovery; details establish the base repository ID and PR number
as final identity. A discovery-only placeholder has an explicitly provisional
identity. Invalid or failed details never inherit an authoritative lifecycle from
search. Private/unknown visibility or unexpected authorship excludes the response
and any evidence collected for it.

Open/draft PRs collect formal reviews, conversation comments, inline comments, check
runs, and combined commit-status contexts. Terminal PRs need only details. Every
collection carries status, page count, known total, compact items and warnings.
Failures on later pages retain earlier evidence. Search caps, changing totals,
incomplete-results flags and pagination limits cannot become a complete collection.
Error and request counters distinguish scanner failures from an observed CI failure.
The scan continues through permitted work after ordinary endpoint errors; rate-limit
and authentication stops suppress further HTTP requests.

PR details are refreshed after enrichment. A head/base/lifecycle/draft change triggers
one recollection. A second change discards revision-dependent collections and leaves
an unstable observation. This bounds consistency work but does not claim atomicity.
All check queries use the head SHA in the base repository, even if `updated_at` stayed
unchanged. The check API selects latest attempts; run, app and suite IDs remain distinct.

`models.py` defines the version 1 dataclasses and snapshot validation. Unknown nullable
facts remain distinct from false values. Collection quality is separate from lifecycle
and attention: a merged PR can be done while other metadata is incomplete; a concrete
author action can coexist with a failed evidence endpoint. Loading a saved snapshot
validates required fields and rejects unsupported versions before any output write.

`classify.py` is pure. It tracks substantive decisions per reviewer; later comments
do not erase a change request. A dismissal keeps its original chronological position
and prompts inspection instead of reviving older authority. Current-head human change
requests and coherent conflicts support author action. Old-head requests, bots,
unknown enums, unavailable evidence, draft decisions and ambiguous timing produce
inspection as appropriate. Feedback timing is a structural heuristic only. Checks
never establish merge policy or blame for failures.

`compare.py` is also pure. It compares stable PR/evidence IDs and body digests, records
newly observed evidence separately from known creation times, and never deduces state
from absence. A rolling closure window cannot manufacture a deletion event. Re-rendering
uses the saved scan identity and observation times without credential or HTTP access.

`report.py` renders both outputs from one snapshot. It escapes external Markdown/HTML,
removes controls, validates display URLs, and uses Europe/Helsinki explicitly. Missing
timezone data is labeled as a UTC fallback. Feedback bodies are hashed after line-ending
normalization, reduced to bounded excerpts, and discarded. Email addresses in external
text are omitted. Body digests describe content changes, not intent.

Output replacement is atomic per file, not across the pair. The CLI reports a write
failure without printing success paths if either replacement fails. Live reports,
local environments and built distributions are outside the tracked source. Only
synthetic fixture data is packaged. No public license has been selected.

Future additions should extend normalized evidence and its tests before classification
or presentation. A schema change needs explicit version/compatibility handling. A new
provider, private scope, persistence layer, notification system or branch-policy reader
would require a separate design decision; none is hidden in this version.

## API references checked during implementation

- [Version support](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
- [Search](https://docs.github.com/en/rest/search/search#search-issues-and-pull-requests)
- [Search qualifiers](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [PR details](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)
- [Formal reviews](https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request)
- [Conversation comments](https://docs.github.com/en/rest/issues/comments#list-issue-comments)
- [Inline comments](https://docs.github.com/en/rest/pulls/comments#list-review-comments-on-a-pull-request)
- [Check runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)
- [Combined statuses](https://docs.github.com/en/rest/commits/statuses#get-the-combined-status-for-a-specific-reference)
- [Pagination](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)
- [Retry/rate-limit practice](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

## Ownership and branch reporting (0.2.0)

Ownership classification is a small pure reporting function over the authoritative
base repository ID/name and the scanned username. It compares account logins only;
it does not interpret organization control. Incomplete base identity produces the
unknown group. Each lifecycle count partitions the same observed inventory, so
group totals reconcile with the overall counts.

`repository_default_branch` is nullable, captured from the already-fetched public
base repository object. It costs no additional API call and is metadata at observation
time, not proof of a historical default branch or current release inclusion. The
reader accepts missing values in schema 1. Comparison ignores the new metadata,
preventing its appearance in a fresh scan from becoming a fabricated status event.
