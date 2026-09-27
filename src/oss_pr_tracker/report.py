"""Readable Markdown and atomic individual-file writes from one snapshot."""

import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .classify import PRIORITY
from .models import clean, safe_url, timestamp

LIMITATIONS = (
    "Observed head checks are collected in the base repository. Required-check policy, test-merge "
    "refs, merge queues, environments and workflow-approval rights are not established. "
    "The check-run endpoint covers at most the most recent 1,000 check suites on a reference. "
    "Neutral/skipped results are not passing tests. Public visibility does not establish an "
    "open-source license. Search and detail reads are not an atomic snapshot."
)


def escape(value):
    text = clean(str(value) if value is not None else "unknown", 5000)
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "|": "&#124;",
        "`": "&#96;",
        "[": "&#91;",
        "]": "&#93;",
        "*": "&#42;",
        "_": "&#95;",
        "\\": "&#92;",
    }
    return "".join(replacements.get(c, c) for c in text)


def link(label, url):
    url = safe_url(url)
    return f"[{escape(label)}]({url})" if url else escape(label)


def human(value):
    d = timestamp(value)
    if not d:
        return "unknown"
    try:
        return d.astimezone(ZoneInfo("Europe/Helsinki")).strftime(
            "%Y-%m-%d %H:%M:%S %z Europe/Helsinki"
        )
    except ZoneInfoNotFoundError:
        return d.strftime("%Y-%m-%d %H:%M:%S UTC (Europe/Helsinki timezone data unavailable)")


def counts(snap):
    return {
        "lifecycle": dict(sorted(Counter(p.lifecycle for p in snap.pull_requests).items())),
        "attention": dict(
            sorted(Counter(p.attention["category"] for p in snap.pull_requests).items())
        ),
    }


def check_summary(p):
    checks, statuses = p.collections["check_runs"], p.collections["commit_statuses"]
    summaries = []
    if checks.status not in ("complete", "not_requested_terminal"):
        summaries.append("checks " + checks.status)
    if statuses.status not in ("complete", "not_requested_terminal"):
        summaries.append("statuses " + statuses.status)
    observed = Counter((c.get("conclusion") or c.get("status") or "unknown") for c in checks.items)
    observed.update(c.get("state") or "unknown" for c in statuses.items)
    summaries.extend(f"{state}: {n}" for state, n in sorted(observed.items()))
    if not observed and checks.status == statuses.status == "complete":
        summaries.append("none_observed (not success)")
    return "; ".join(summaries) or "not requested for terminal PR"


def markdown(s):
    rows = []
    if s.mode == "demo":
        rows.append("DEMO — synthetic data; not a live GitHub status report.\n")
    completeness = (
        "RETRIEVED WITHOUT DETECTED GAPS WITHIN THE STATED SCOPE"
        if s.complete
        else "PARTIAL — observable collection gaps"
    )
    rows += [
        f"# OSS PR Tracker — {escape(s.username)}",
        "",
        f"**{completeness}**",
        "",
        f"Mode: {s.mode} · Scan: {escape(s.scan_id)}",
        f"Started: {human(s.started_at)} · Finished: {human(s.finished_at)}",
        "",
        "Scope: public PRs authored by this account; all open PRs regardless of age; "
        + (
            "all discoverable closed history."
            if s.scope["all_history"]
            else f"PRs closed since {human(s.scope['closed_since'])} "
            f"({s.scope['closed_since']}; inclusive)."
        ),
        "Reviews of other people’s PRs, issue-only activity and off-GitHub contributions "
        "are outside this inventory.",
        "",
    ]
    summary = counts(s)
    n = len(s.pull_requests)
    rows += [
        f"Observed PRs: **{n}**. These are collected counts, "
        "not a guarantee of an exhaustive GitHub index.",
        "",
        "| Dimension | Counts (same observed denominator) |",
        "| --- | --- |",
    ]
    for kind, values in summary.items():
        rows.append(f"| {kind} | " + ", ".join(f"{k}: {v}/{n}" for k, v in values.items()) + " |")
    rows += ["", LIMITATIONS, "", "## Your next actions", ""]
    ordered = sorted(
        s.pull_requests,
        key=lambda p: (PRIORITY[p.attention["category"]], p.repository or "", p.number),
    )
    actions = [p for p in ordered if p.attention["category"] in ("author_action", "inspect")]
    for p in actions[:12]:
        reason = min(p.attention["reasons"], key=lambda r: PRIORITY[r["level"]])
        rows.append(
            f"- **{p.attention['category']}** — "
            f"{link(f'{p.repository or "unknown repository"} #{p.number}', p.url)}: "
            + link(reason["message"], reason.get("url"))
        )
    if not actions:
        rows.append(
            "No author action detected in the collected records. "
            "Check completeness before drawing conclusions."
        )
    if len(actions) > 12:
        rows.append(f"- {len(actions) - 12} further items appear in the table and evidence below.")
    rows += [
        "",
        "## Open, draft and unknown PRs",
        "",
        "| Repository / PR | Title | Target branch | Lifecycle | Observed head checks | "
        "Reviews / feedback | Next step |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in ordered:
        if p.lifecycle in ("merged", "closed_unmerged"):
            continue
        summaries = []
        for name in ("reviews", "conversation", "inline"):
            c = p.collections[name]
            summaries.append(f"{name}: {len(c.items)} ({c.status})")
        reason = min(p.attention["reasons"], key=lambda r: PRIORITY[r["level"]])
        rows.append(
            "| "
            + " | ".join(
                [
                    link(f"{p.repository or 'unknown repository'} #{p.number}", p.url),
                    escape(p.title),
                    escape(p.base_ref),
                    p.lifecycle,
                    escape(check_summary(p)),
                    escape("; ".join(summaries)),
                    escape(p.attention["category"] + ": " + reason["message"]),
                ]
            )
            + " |"
        )
    for state, heading in (("merged", "Merged PRs"), ("closed_unmerged", "Closed without merging")):
        rows += [
            "",
            "## " + heading,
            "",
            "| PR | Title | Actual target branch | Date |",
            "| --- | --- | --- | --- |",
        ]
        for p in ordered:
            if p.lifecycle == state:
                rows.append(
                    f"| {link(f'{p.repository} #{p.number}', p.url)} | {escape(p.title)} | "
                    f"{escape(p.base_ref)} | "
                    f"{human(p.merged_at if state == 'merged' else p.closed_at)} |"
                )
    if s.comparison:
        c = s.comparison
        rows += [
            "",
            "## Changes since baseline",
            "",
            f"Baseline: {escape(c['baseline_scan_id'])} · {human(c['baseline_finished_at'])}",
            "",
        ]
        rows.extend("- " + escape(w) for w in c["warnings"])
        rows.extend(
            "- "
            + link(item["identity"], item.get("url"))
            + ": "
            + escape(item["message"])
            + (
                " Created since baseline: "
                + (
                    str(item["created_since_baseline"]).lower()
                    if item["created_since_baseline"] is not None
                    else "unknown"
                )
                + "."
                if "created_since_baseline" in item
                else ""
            )
            for item in c["changes"]
        )
        if not c["changes"]:
            rows.append("No supported changes detected in the collected evidence.")
    rows += [
        "",
        "## Evidence and collection limitations",
        "",
        f"API requests: {s.request_counts.get('requests', 0)}; "
        f"request errors: {s.request_counts.get('errors', 0)}.",
        "",
    ]
    for d in s.discovery:
        rows.append(
            f"- Query: {escape(d['query'])} — reported {d['reported_total']}, "
            f"fetched unique {d['fetched_count']}, {d['status']}."
        )
        rows.extend("  - " + escape(w) for w in d["warnings"])
    rows.extend("- " + escape(w) for w in s.errors + s.warnings)
    for p in ordered:
        rows += [
            "",
            "<details>",
            f"<summary>{escape(p.repository)} #{p.number} — "
            f"{escape(p.attention['category'])}; data {p.collection_quality}</summary>",
            "",
            f"{link(p.title, p.url)} · observed {human(p.observed_at)}",
            f"Head: {escape(p.head_sha)} · base: {escape(p.base_ref)} @ {escape(p.base_sha)}",
            f"Mergeable field: {escape(p.mergeable)}; "
            f"mergeable state: {escape(p.mergeable_state)}. "
            "True means no detected automatic-merge conflict; merge readiness is not established.",
            "Requested reviewers: "
            + escape(", ".join(p.requested_reviewers) or "none observed")
            + "; teams: "
            + escape(", ".join(p.requested_teams) or "none observed")
            + ".",
            "",
        ]
        if timestamp(p.updated_at):
            days = max(0, (timestamp(s.finished_at) - timestamp(p.updated_at)).days)
            rows.append(
                f"Last PR activity: {human(p.updated_at)} "
                f"({days} days before scan end; informational only)."
            )
        rows.extend(
            "- " + link(r["message"], r.get("url")) + f" [{escape(r['code'])}; "
            f"{human(r.get('at'))}; SHA {escape(r.get('sha'))}]"
            for r in p.attention["reasons"]
        )
        rows.extend("- " + escape(w) for w in p.warnings + p.observations)
        for name, c in p.collections.items():
            rows.append(f"- {name}: {c.status}; {len(c.items)} observed.")
            rows.extend("  - " + escape(w) for w in c.warnings)
            for item in c.items:
                if name in ("reviews", "conversation", "inline"):
                    desc = (
                        f"{item.get('author') or 'deleted/unknown author'} "
                        f"({item.get('author_type') or 'unknown'}, "
                        f"{item.get('association') or 'unknown'}); "
                        f"{item.get('state') or name}; {human(item.get('created_at'))}; "
                        f"updated {human(item.get('updated_at'))}; "
                        f"reviewed SHA {item.get('commit_id') or 'not supplied'}; "
                        f"{item.get('excerpt', '')}"
                    )
                else:
                    desc = (
                        f"{item.get('name') or item.get('context')}: "
                        f"{item.get('conclusion') or item.get('status') or item.get('state')}; "
                        f"SHA {item.get('head_sha')}; app {item.get('app_id')}; "
                        f"suite {item.get('suite_id')}"
                    )
                rows.append("  - " + link(desc, item.get("url")))
        rows += ["", "</details>"]
    return "\n".join(rows) + "\n"


def write_outputs(snap, output_dir):
    directory = Path(output_dir).resolve()
    payloads = {
        "status.json": json.dumps(snap.to_dict(), indent=2, ensure_ascii=False) + "\n",
        "status.md": markdown(snap),
    }
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    # Each replacement is atomic. The pair is deliberately not advertised as transactional.
    for name, text in payloads.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory, prefix="." + name, delete=False
            ) as f:
                temporary = Path(f.name)
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            destination = directory / name
            os.replace(temporary, destination)
            paths.append(destination)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return paths
