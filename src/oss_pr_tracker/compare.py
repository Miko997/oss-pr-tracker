"""Local snapshot comparison; disappearance never implies a lifecycle event."""

from .models import timestamp


def compatible(current, previous):
    if current.username.casefold() != previous.username.casefold():
        raise ValueError("Previous snapshot belongs to a different username.")
    if current.mode != previous.mode or current.schema_version != previous.schema_version:
        raise ValueError("Previous snapshot mode/schema is incompatible.")


def compare(current, previous):
    compatible(current, previous)
    before = {p.identity: p for p in previous.pull_requests}
    after = {p.identity: p for p in current.pull_requests}
    changes = []

    def add(p, kind, message, **extra):
        changes.append(
            {"identity": p.identity, "url": p.url, "kind": kind, "message": message, **extra}
        )

    def since(value):
        t = timestamp(value)
        baseline = timestamp(previous.finished_at)
        return t > baseline if t and baseline else None

    for identity, p in after.items():
        old = before.get(identity)
        if old is None:
            add(
                p,
                "newly_observed_pr",
                "Newly discovered in this scan; not necessarily newly created.",
                created_since_baseline=since(p.created_at),
            )
            continue
        for field in ("lifecycle", "head_sha"):
            if getattr(p, field) != getattr(old, field):
                add(p, field + "_changed", f"{field}: {getattr(old, field)} → {getattr(p, field)}.")
        for source in ("reviews", "conversation", "inline"):
            prior = {i["id"]: i for i in old.collections[source].items}
            for item in p.collections[source].items:
                previous_item = prior.get(item["id"])
                if previous_item is None:
                    add(
                        p,
                        "newly_observed_feedback",
                        f"Newly observed {source} #{item['id']}.",
                        source=source,
                        evidence_id=item["id"],
                        evidence_url=item["url"],
                        created_since_baseline=since(item.get("created_at")),
                    )
                elif item["body_digest"] != previous_item["body_digest"]:
                    add(
                        p,
                        "edited_feedback",
                        f"Body edited: {source} #{item['id']}.",
                        source=source,
                        evidence_id=item["id"],
                        evidence_url=item["url"],
                    )
                if previous_item and item.get("state") != previous_item.get("state"):
                    add(
                        p,
                        "review_state_changed",
                        f"{source} #{item['id']}: "
                        f"{previous_item.get('state')} → {item.get('state')}.",
                        evidence_url=item["url"],
                    )
        for source in ("check_runs", "commit_statuses"):
            # Compare the complete observed set, including replacement IDs; do not infer policy.
            fields = (
                ("id", "name", "app_id", "suite_id", "status", "conclusion", "head_sha")
                if (source == "check_runs")
                else ("id", "context", "state", "head_sha")
            )
            a = [tuple(i.get(k) for k in fields) for i in old.collections[source].items]
            b = [tuple(i.get(k) for k in fields) for i in p.collections[source].items]
            if a != b:
                add(
                    p,
                    "observed_checks_changed",
                    f"Observed {source} changed; "
                    f"collection quality {old.collections[source].status} → "
                    f"{p.collections[source].status}. No result inferred from absence.",
                )
    for identity in before.keys() - after.keys():
        add(before[identity], "not_observed", "Not observed in this scan/scope; no state inferred.")
    changed_scope = current.scope != previous.scope
    return {
        "baseline_scan_id": previous.scan_id,
        "baseline_finished_at": previous.finished_at,
        "scope_changed": changed_scope,
        "missing_record_conclusions_enabled": False,
        "warnings": (
            ["Date windows/scopes differ; comparisons use overlapping identities."]
            if changed_scope
            else []
        )
        + (
            ["Discovery or enrichment gaps may affect observations."]
            if not current.complete or not previous.complete
            else []
        ),
        "changes": sorted(changes, key=lambda c: (c["identity"], c["kind"], c["message"])),
    }
