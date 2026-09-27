"""Discovery, normalization and one bounded revision-consistency retry."""

import re
from datetime import timedelta
from uuid import uuid4

from .classify import classify
from .github import APIError, api_url
from .models import (
    COLLECTIONS,
    Collection,
    PullRequest,
    Snapshot,
    clean,
    feedback,
    iso,
    lifecycle,
    login,
    safe_url,
    timestamp,
    utcnow,
)


class Excluded(Exception):
    pass


def normalized_detail(d, username, observed_at):
    if not isinstance(d, dict):
        raise APIError("Malformed PR detail response.")
    base = d.get("base") if isinstance(d.get("base"), dict) else {}
    repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    user = d.get("user") if isinstance(d.get("user"), dict) else {}
    if (
        repo.get("private") is not False
        or not isinstance(user.get("login"), str)
        or (user["login"].casefold() != username.casefold())
    ):
        raise Excluded("Public visibility or requested authorship could not be confirmed.")
    head = d.get("head") if isinstance(d.get("head"), dict) else {}
    if (
        any(type(x) is not int for x in (d.get("id"), d.get("number"), repo.get("id")))
        or not isinstance(repo.get("full_name"), str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo["full_name"])
    ):
        raise APIError("PR detail lacks valid repository identity or PR identity.")
    p = PullRequest(
        identity=f"{repo['id']}:{d['number']}",
        id=d["id"],
        number=d["number"],
        url=safe_url(d.get("html_url")),
        title=clean(d.get("title")),
        author=user["login"],
        repository=repo["full_name"],
        repository_id=repo["id"],
        lifecycle=lifecycle(d),
        state=clean(d.get("state")) or None,
        draft=d.get("draft") if type(d.get("draft")) is bool else None,
        merged=d.get("merged") if type(d.get("merged")) is bool else None,
        observed_at=observed_at,
        mergeable=d.get("mergeable") if type(d.get("mergeable")) is bool else None,
        mergeable_state=clean(d.get("mergeable_state")) or None,
        base_ref=clean(base.get("ref")) or None,
    )
    for k in ("created_at", "updated_at", "closed_at", "merged_at"):
        value = d.get(k)
        setattr(p, k, iso(timestamp(value)) if timestamp(value) else None)
        if value is not None and not timestamp(value):
            p.warnings.append(f"Invalid {k}.")
    for name, value in (("head_sha", head.get("sha")), ("base_sha", base.get("sha"))):
        if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{40,64}", value):
            setattr(p, name, value)
        else:
            p.warnings.append(f"Missing or invalid {name}.")
    for source, target, key in (
        ("requested_reviewers", "requested_reviewers", "login"),
        ("requested_teams", "requested_teams", "slug"),
    ):
        values = d.get(source)
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            p.warnings.append(f"Missing or invalid {source}.")
        else:
            if any(not isinstance(v.get(key), str) or not v[key] for v in values):
                p.warnings.append(f"Malformed identity in {source}; valid entries retained.")
            setattr(
                p,
                target,
                [clean(v.get(key)) for v in values if isinstance(v.get(key), str) and v[key]],
            )
    if p.lifecycle == "unknown":
        p.warnings.append("Missing or contradictory authoritative lifecycle fields.")
    if not p.created_at or not p.updated_at or not p.base_ref or not p.title or not p.url:
        p.warnings.append("Required factual fields are missing.")
    if p.lifecycle in ("merged", "closed_unmerged") and not p.closed_at:
        p.warnings.append("Closure timestamp is missing.")
    return p


def signature(p):
    return (p.identity, p.head_sha, p.base_sha, p.base_ref, p.lifecycle, p.draft)


def normalize_collection(c, kind, sha):
    items = []
    for raw in c.items:
        raw = dict(raw)
        for key in (
            "status",
            "state",
            "conclusion",
            "created_at",
            "submitted_at",
            "updated_at",
            "started_at",
            "completed_at",
            "commit_id",
        ):
            if raw.get(key) is not None and not isinstance(raw[key], str):
                c.warnings.append(f"Invalid evidence field: {key}.")
                raw[key] = None
        for key in ("created_at", "submitted_at", "updated_at", "started_at", "completed_at"):
            if raw.get(key) is not None:
                if timestamp(raw[key]):
                    raw[key] = iso(timestamp(raw[key]))
                else:
                    c.warnings.append(f"Invalid evidence timestamp: {key}.")
                    raw[key] = None
        try:
            if kind in ("reviews", "conversation", "inline"):
                item = feedback(raw, kind)
                if kind == "reviews" and item["state"] not in (
                    "PENDING",
                    "COMMENTED",
                    "APPROVED",
                    "CHANGES_REQUESTED",
                    "DISMISSED",
                ):
                    c.warnings.append("Unknown formal review state.")
                if not timestamp(item["created_at"]) and item["state"] != "PENDING":
                    c.warnings.append("Feedback creation/submission time is missing or invalid.")
                if item["updated_at"] is not None and not timestamp(item["updated_at"]):
                    c.warnings.append("Feedback update time is invalid.")
            elif kind == "check_runs":
                app, suite = raw.get("app") or {}, raw.get("check_suite") or {}
                if not isinstance(app, dict) or not isinstance(suite, dict):
                    raise ValueError
                item = {
                    "id": raw["id"],
                    "name": clean(raw.get("name")) or "(unnamed)",
                    "app_id": app.get("id"),
                    "app": clean(app.get("slug")) or None,
                    "suite_id": suite.get("id"),
                    "status": raw.get("status"),
                    "conclusion": raw.get("conclusion"),
                    "head_sha": raw.get("head_sha"),
                    "url": safe_url(raw.get("html_url")) or safe_url(raw.get("details_url")),
                    "created_at": raw.get("started_at"),
                    "updated_at": raw.get("completed_at"),
                }
                for key in ("app_id", "suite_id"):
                    if type(item[key]) is not int:
                        c.warnings.append(f"Missing or malformed check {key}.")
                        item[key] = None
                if item["head_sha"] != sha:
                    c.warnings.append("Check belongs to a different head; excluded.")
                    continue
                if item["status"] not in (
                    "queued",
                    "in_progress",
                    "completed",
                    "waiting",
                    "pending",
                    "requested",
                ) or item["conclusion"] not in (
                    None,
                    "success",
                    "failure",
                    "neutral",
                    "cancelled",
                    "timed_out",
                    "action_required",
                    "skipped",
                    "stale",
                    "startup_failure",
                ):
                    c.warnings.append("Unknown check status/conclusion.")
                if item["status"] == "completed" and item["conclusion"] is None:
                    c.warnings.append("Completed check has no conclusion.")
            else:
                item = {
                    "id": raw["id"],
                    "context": clean(raw.get("context")) or "(unnamed)",
                    "state": raw.get("state"),
                    "url": safe_url(raw.get("target_url")),
                    "created_at": raw.get("created_at"),
                    "updated_at": raw.get("updated_at"),
                    "head_sha": sha,
                }
                if item["state"] not in ("success", "pending", "failure", "error"):
                    c.warnings.append("Unknown commit status state.")
            items.append(item)
        except (ValueError, KeyError, TypeError):
            c.warnings.append("Malformed evidence record; omitted.")
    # GitHub filter=latest selects replacements. Preserve app/suite IDs and distinct returned runs.
    c.items = sorted(items, key=lambda i: i["id"])
    c.warnings = list(dict.fromkeys(c.warnings))
    if c.warnings and c.status == "complete":
        c.status = "partial"
    return c


def enrich(client, p):
    root = f"/repos/{p.repository}"
    endpoints = {
        "reviews": (f"{root}/pulls/{p.number}/reviews", None, None),
        "conversation": (f"{root}/issues/{p.number}/comments", None, None),
        "inline": (f"{root}/pulls/{p.number}/comments", None, None),
        "check_runs": (
            f"{root}/commits/{p.head_sha}/check-runs",
            "check_runs",
            {"filter": "latest"},
        ),
        "commit_statuses": (f"{root}/commits/{p.head_sha}/status", "statuses", None),
    }
    for kind, (path, key, params) in endpoints.items():
        if kind in ("check_runs", "commit_statuses") and not p.head_sha:
            p.collections[kind] = Collection(warnings=["Head SHA is unavailable."])
        else:
            c = client.collection(path, key=key, params=params)
            p.collections[kind] = normalize_collection(c, kind, p.head_sha)


def finish(p):
    if p.lifecycle in ("merged", "closed_unmerged"):
        p.collections = {name: Collection(status="not_requested_terminal") for name in COLLECTIONS}
        p.coherent = True
    if p.collection_quality != "unstable":
        p.collection_quality = (
            "complete"
            if not p.warnings
            and (
                p.coherent
                and all(
                    c.status in ("complete", "not_requested_terminal")
                    for c in p.collections.values()
                )
            )
            else "partial"
        )
    p.attention = classify(p)
    return p


def collect_pr(client, path, username, clock):
    d, _ = client.get(path)
    p = normalized_detail(d, username, iso(clock()))
    for attempt in range(2):
        if p.lifecycle in ("merged", "closed_unmerged"):
            return finish(p)
        enrich(client, p)
        try:
            d, _ = client.get(f"/repos/{p.repository}/pulls/{p.number}")
            fresh = normalized_detail(d, username, iso(clock()))
        except APIError as exc:
            p.warnings.append("Final detail refresh failed: " + str(exc))
            p.coherent = False
            return finish(p)
        if signature(p) == signature(fresh):
            fresh.observations = p.observations
            fresh.collections = p.collections
            fresh.warnings = list(dict.fromkeys(p.warnings + fresh.warnings))
            fresh.coherent = True
            return finish(fresh)
        if attempt == 0:
            p = fresh
            p.observations.append(
                "Revision or lifecycle changed during collection; recollected once."
            )
        else:
            fresh.collection_quality = "unstable"
            fresh.warnings.append(
                "Revision changed twice; evidence discarded to avoid mixing revisions."
            )
            fresh.collections = {
                n: Collection(
                    status="unstable",
                    warnings=["Discarded evidence after repeated revision change."],
                )
                for n in COLLECTIONS
            }
            return finish(fresh)
    raise AssertionError("Unreachable")


def scan(client, username, *, closed_since=None, all_history=False, clock=utcnow, progress=None):
    login(username)
    start = clock().replace(microsecond=0)
    cutoff = None if all_history else closed_since or start - timedelta(days=90)
    if cutoff:
        cutoff = cutoff.replace(microsecond=0)
    scope = {
        "public_only": True,
        "open_included": True,
        "all_history": all_history,
        "closed_since": iso(cutoff) if cutoff else None,
    }
    snap = Snapshot(
        scan_id=str(uuid4()),
        mode="live",
        username=username,
        started_at=iso(start),
        finished_at=iso(start),
        scope=scope,
    )
    found = {}
    for state in ("open", "closed"):
        query = f"is:pr is:public author:{username} is:{state}"
        if state == "closed" and cutoff:
            query += f" closed:>={iso(cutoff)}"
        c = client.collection(
            "/search/issues",
            key="items",
            search=True,
            params={"q": query, "sort": "created", "order": "asc"},
        )
        snap.discovery.append(
            {
                "query": query,
                "reported_total": c.total,
                "fetched_count": len(c.items),
                "pages": c.pages,
                "status": c.status,
                "warnings": c.warnings,
            }
        )
        for item in c.items:
            # Only public search locators survive; no raw title/body or private payload is saved.
            number = item.get("number")
            pr = item.get("pull_request") or {}
            path = pr.get("url") if isinstance(pr, dict) else None
            url = safe_url(item.get("html_url"))
            try:
                api_url(path)
                if (
                    type(number) is not int
                    or number < 1
                    or not re.fullmatch(
                        r"https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls/\d+",
                        path,
                    )
                    or not path.endswith("/" + str(number))
                ):
                    raise APIError("Invalid discovery locator.")
            except APIError:
                snap.errors.append("A search result has an invalid PR locator; omitted.")
                continue
            entry = found.setdefault(
                item["id"], {"number": number, "path": path, "url": url, "states": set()}
            )
            entry["states"].add(state)
    results = {}
    if progress:
        progress(f"Discovered {len(found)} unique public PR locators; collecting details.")
    for index, (found_id, item) in enumerate(found.items(), 1):
        if progress and (index == 1 or index % 20 == 0 or index == len(found)):
            progress(f"Collecting PR {index}/{len(found)}.")
        try:
            p = collect_pr(client, item["path"], username, clock)
        except Excluded:
            snap.errors.append(
                f"Excluded {item['url'] or 'public search result'}: "
                "public visibility or requested authorship not confirmed."
            )
            continue
        except APIError as exc:
            p = PullRequest(
                identity=f"discovery:{found_id}",
                number=item["number"],
                url=item["url"],
                observed_at=iso(clock()),
                warnings=[str(exc)],
            )
            finish(p)
        actual_state = (
            "closed"
            if p.lifecycle in ("merged", "closed_unmerged")
            else ("open" if p.lifecycle in ("open", "draft") else "unknown")
        )
        if actual_state != "unknown" and actual_state not in item["states"]:
            snap.warnings.append(f"{p.url}: lifecycle changed since discovery; retained.")
        elif (
            cutoff
            and item["states"] == {"closed"}
            and actual_state == "closed"
            and timestamp(p.closed_at)
            and timestamp(p.closed_at) < cutoff
        ):
            snap.warnings.append(f"{p.url}: authoritative closure is before cutoff; omitted.")
            continue
        results[p.identity] = p
    snap.pull_requests = sorted(results.values(), key=lambda p: (p.repository or "", p.number))
    snap.finished_at = iso(clock())
    snap.complete = (
        not snap.errors
        and all(d["status"] == "complete" for d in snap.discovery)
        and all(p.collection_quality == "complete" for p in snap.pull_requests)
    )
    snap.request_counts = {
        **client.counts,
        "wait_seconds": client.waited,
        "last_rate_limit": client.rate,
    }
    return snap
