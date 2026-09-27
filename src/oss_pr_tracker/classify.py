"""Deterministic interpretation of compact evidence; no network or policy guesses."""

from .models import COLLECTIONS, PullRequest, timestamp

PRIORITY = {"author_action": 0, "inspect": 1, "waiting": 2, "done": 3}


def classify(p: PullRequest):
    reasons = []

    def add(code, message, level="inspect", evidence=None):
        e = evidence or {}
        reasons.append(
            {
                "code": code,
                "message": message,
                "level": level,
                "url": e.get("url") or p.url,
                "at": e.get("updated_at") or e.get("created_at") or p.observed_at,
                "sha": e.get("commit_id") or e.get("head_sha") or p.head_sha,
            }
        )

    if p.lifecycle in ("merged", "closed_unmerged"):
        add("terminal", f"{p.lifecycle}; target branch: {p.base_ref or 'unknown'}.", "done")
        return {"category": "done", "reasons": reasons}
    if p.collection_quality != "complete" or any(
        p.collections[n].status != "complete" for n in COLLECTIONS
    ):
        add("incomplete_data", "Collection is incomplete or unstable; inspect the limitations.")
    if p.lifecycle == "unknown":
        add("unknown_lifecycle", "Authoritative lifecycle is not established.")
    if p.lifecycle == "draft":
        add("draft_decision", "Decide whether this draft is ready for review.")
    if p.coherent and p.lifecycle in ("open", "draft"):
        if p.mergeable is False:
            add(
                "merge_conflict",
                "Inspect and resolve the reported merge conflict.",
                "author_action",
            )
        elif p.mergeable is None:
            add("unknown_mergeability", "Conflict state is not established.")
    elif not p.coherent:
        add("incoherent_observation", "Current revision could not be confirmed.")

    reviews = p.collections["reviews"].items
    # Dismissals stay in chronological position: filtering them would resurrect older opinions.
    decisions = {}
    ambiguous_reviewers = {
        r["author"].casefold()
        for r in reviews
        if r.get("author")
        and r.get("author_type") == "User"
        and r.get("state") not in ("PENDING", "COMMENTED")
        and (
            not timestamp(r.get("created_at"))
            or r.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
        )
    }
    for r in sorted(reviews, key=lambda r: (r.get("created_at") or "", r["id"])):
        state = r.get("state")
        if state == "PENDING":
            continue
        if state not in ("COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            add("unknown_review_state", "Unknown review state; inspect this review.", evidence=r)
            continue
        if not timestamp(r.get("created_at")):
            add("undated_review", "Review chronology is ambiguous.", evidence=r)
            continue
        if not r.get("author") or r.get("author_type") != "User":
            if state in ("CHANGES_REQUESTED", "DISMISSED"):
                add("nonhuman_or_unknown_review", "Inspect bot or unidentified review.", evidence=r)
            continue
        if state != "COMMENTED":
            decisions[r["author"].casefold()] = r
    for reviewer, r in decisions.items():
        if reviewer in ambiguous_reviewers:
            add(
                "ambiguous_review_decision",
                "A substantive review has ambiguous chronology/state; inspect this opinion.",
                evidence=r,
            )
            continue
        if r["state"] == "CHANGES_REQUESTED":
            if p.coherent and r.get("commit_id") == p.head_sha and p.head_sha:
                add(
                    "current_changes_requested",
                    "Address or respond to this review.",
                    "author_action",
                    r,
                )
            else:
                add(
                    "old_changes_requested",
                    "Review predates or cannot be tied to the current head; "
                    "check whether addressed.",
                    evidence=r,
                )
        elif r["state"] == "DISMISSED":
            add(
                "dismissed_review",
                "Review was dismissed; effective opinion is uncertain.",
                evidence=r,
            )

    feedback = [
        r
        for name in ("reviews", "conversation", "inline")
        for r in p.collections[name].items
        if r.get("state") != "PENDING"
    ]
    boundary = timestamp(p.created_at)
    for r in feedback:
        if (r.get("author") or "").casefold() == (p.author or "").casefold() and p.author:
            times = [timestamp(r.get(k)) for k in ("created_at", "updated_at")]
            times = [t for t in times if t]
            if times:
                boundary = max([boundary] + times) if boundary else max(times)
    external = []
    for r in feedback:
        if (r.get("author") or "").casefold() == (p.author or "").casefold() and p.author:
            continue
        if r.get("author_type") == "Bot":
            continue
        if r.get("author_type") != "User" or not r.get("author"):
            add(
                "unknown_feedback_author",
                "Feedback author is unknown; inspect without assuming a duty.",
                evidence=r,
            )
            continue
        times = [timestamp(r.get(k)) for k in ("created_at", "updated_at")]
        times = [t for t in times if t]
        if not times or not boundary:
            add(
                "undated_feedback",
                "Feedback timing is ambiguous; response status not established.",
                evidence=r,
            )
        else:
            external.append((max(times), r))
    if external:
        when, newest = max(external, key=lambda item: (item[0], item[1]["id"]))
        if boundary and when > boundary:
            add(
                "possible_follow_up",
                "Latest feedback to inspect; response status not established. Possible follow-up "
                "only: pushes or offline conversations may have addressed it.",
                evidence=newest,
            )

    for c in p.collections["check_runs"].items:
        status, result = c.get("status"), c.get("conclusion")
        if (
            status in ("queued", "in_progress", "waiting", "pending", "requested")
            and result is None
        ):
            add("checks_pending", f"Observed check {c['name']} is {status}.", "waiting", c)
        elif status == "completed" and result in ("success", "neutral", "skipped"):
            continue
        else:
            add(
                "check_" + (result or "unknown"),
                f"Inspect observed check {c['name']}: {result or status or 'unknown'}.",
                evidence=c,
            )
    for c in p.collections["commit_statuses"].items:
        state = c.get("state")
        if state == "pending":
            add("status_pending", f"Observed status {c['context']} is pending.", "waiting", c)
        elif state != "success":
            add(
                "status_" + (state or "unknown"),
                f"Inspect observed status {c['context']}: {state or 'unknown'}.",
                evidence=c,
            )
    if not reasons:
        add(
            "inferred_review_wait",
            "No author action detected in collected evidence; likely awaiting review (inference).",
            "waiting",
        )
    category = min((r["level"] for r in reasons), key=PRIORITY.get)
    return {"category": category, "reasons": reasons}
