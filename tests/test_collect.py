import json
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from conftest import HEAD, NOW, Scenario, detail, review, search_item

from oss_pr_tracker.collect import scan


def run(s, **kwargs):
    return scan(s.client(), "test-author", clock=lambda: NOW, **kwargs)


def test_open_age_and_closed_cutoff_boundary_and_terminal_collections():
    cutoff = NOW - timedelta(days=90)
    s = Scenario(
        {
            1: [detail()],
            2: [detail(2, state="closed", closed_at=cutoff.isoformat())],
            3: [detail(3, state="closed", closed_at=(cutoff - timedelta(seconds=1)).isoformat())],
            4: [
                detail(
                    4,
                    state="closed",
                    merged=True,
                    merged_at=NOW.isoformat(),
                    closed_at=NOW.isoformat(),
                )
            ],
        }
    )
    snap = run(s)
    assert {p.number for p in snap.pull_requests} == {1, 2, 4}
    assert snap.complete
    assert snap.pull_requests[0].created_at.startswith("2020")
    queries = [r.url.params["q"] for r in s.requests if r.url.path == "/search/issues"]
    assert "closed:" not in queries[0] and "updated:" not in queries[1]
    assert "closed:>=2026-06-29T12:00:00Z" in queries[1]
    for p in snap.pull_requests[1:]:
        assert all(c.status == "not_requested_terminal" for c in p.collections.values())
        assert p.base_ref == "feature/stack"
    assert all("/pulls/4/" not in r.url.path for r in s.requests)


def test_all_history_and_explicit_cutoff():
    s = Scenario()
    run(s, all_history=True)
    assert all("closed:" not in r.url.params.get("q", "") for r in s.requests)
    s = Scenario()
    run(s, closed_since=NOW - timedelta(days=1))
    assert "closed:>=2026-09-26T12:00:00Z" in s.requests[1].url.params["q"]


@pytest.mark.parametrize("state", ["closed", "open"])
def test_close_reopen_discovery_race_retained_deduped(state):
    d = detail(state=state, closed_at=NOW.isoformat() if state == "closed" else None)
    s = Scenario({1: [d]})
    s.open_items = [search_item(d)]
    s.closed_items = [search_item(d)]
    snap = run(s)
    assert len(snap.pull_requests) == 1
    assert snap.pull_requests[0].lifecycle == ("closed_unmerged" if state == "closed" else "open")


@pytest.mark.parametrize("when", ["initial", "refresh"])
@pytest.mark.parametrize("bad", ["private", "unknown", "author"])
def test_visibility_or_authorship_exclusion_discards_all_content(when, bad):
    d = detail(title="EXCLUDED-SENSITIVE-TITLE")
    altered = deepcopy(d)
    if bad == "author":
        altered["user"]["login"] = "someone-else"
    else:
        altered["base"]["repo"]["private"] = True if bad == "private" else None
    s = Scenario(
        {1: [altered] if when == "initial" else [d, altered]},
        reviews=[review(body="EXCLUDED-FEEDBACK")],
    )
    snap = run(s)
    serialized = json.dumps(snap.to_dict())
    assert snap.pull_requests == [] and not snap.complete
    assert "EXCLUDED" not in serialized
    assert "public visibility or requested authorship" in serialized
    assert len(s.requests) == (3 if when == "initial" else 9)


def test_detail_failure_retains_only_locator_and_unknown():
    s = Scenario()
    s.overrides["/repos/example/project/pulls/1"] = 404
    snap = run(s)
    p = snap.pull_requests[0]
    assert p.lifecycle == "unknown" and p.attention["category"] == "inspect"
    assert p.identity == "discovery:101"
    assert "UNTRUSTED SEARCH TITLE" not in json.dumps(snap.to_dict())
    assert not snap.complete


@pytest.mark.parametrize("change", ["head", "base", "draft", "lifecycle"])
def test_bounded_recollection(change):
    d, newer = detail(), detail()
    if change == "head":
        newer["head"]["sha"] = "c" * 40
    elif change == "base":
        newer["base"]["sha"] = "c" * 40
        newer["base"]["ref"] = "feature/new"
    elif change == "draft":
        newer["draft"] = True
    else:
        newer.update(
            state="closed", merged=True, merged_at=NOW.isoformat(), closed_at=NOW.isoformat()
        )
    s = Scenario({1: [d, newer, newer]})
    snap = run(s)
    p = snap.pull_requests[0]
    assert p.coherent and snap.complete
    checks = [r.url.path for r in s.requests if r.url.path.endswith("/check-runs")]
    assert len(checks) == (1 if change == "lifecycle" else 2)
    if change == "head":
        assert HEAD in checks[0] and "c" * 40 in checks[1]
        assert p.head_sha == "c" * 40


def test_repeated_revision_changes_discard_checks_and_conflict_inference():
    a, b, c = detail(), detail(), detail(mergeable=False)
    b["head"]["sha"] = "c" * 40
    c["head"]["sha"] = "d" * 40
    s = Scenario({1: [a, b, c]})
    snap = run(s)
    p = snap.pull_requests[0]
    assert p.collection_quality == "unstable" and not snap.complete
    assert not p.collections["check_runs"].items
    assert p.attention["category"] == "inspect"
    assert s.detail_calls[1] == 3


def test_checks_refreshed_when_updated_at_unchanged_and_partial_not_green():
    s = Scenario()
    run(s)
    run(s)
    assert len([r for r in s.requests if r.url.path.endswith("/check-runs")]) == 2
    s.overrides[f"/repos/example/project/commits/{HEAD}/check-runs"] = 403
    snap = run(s)
    p = snap.pull_requests[0]
    assert p.collections["check_runs"].status == "unavailable"
    assert not snap.complete and p.attention["category"] == "inspect"


def test_refresh_failure_does_not_assert_conflict():
    s = Scenario({1: [detail(mergeable=False)]})
    calls = []

    def fail_refresh(r):
        calls.append(r)
        return (
            httpx.Response(200, json=detail(mergeable=False))
            if len(calls) == 1
            else httpx.Response(404)
        )

    s.overrides["/repos/example/project/pulls/1"] = fail_refresh
    p = run(s).pull_requests[0]
    assert not p.coherent and p.attention["category"] == "inspect"


@pytest.mark.parametrize(
    "kind", ["check_conclusion", "check_status", "commit_state", "review_time"]
)
def test_malformed_evidence_scalars_produce_partial_output(kind):
    from conftest import check

    s = Scenario()
    if kind.startswith("check"):
        c = check()
        c["conclusion" if kind == "check_conclusion" else "status"] = {"bad": "shape"}
        s.checks = [c]
    elif kind == "commit_state":
        s.statuses = [{"id": 1, "context": "test", "state": {"bad": "shape"}}]
    else:
        s.reviews = [review(submitted_at={"bad": "shape"}), review(2)]
    snap = run(s)
    assert not snap.complete
    assert any(r["code"] == "incomplete_data" for r in snap.pull_requests[0].attention["reasons"])


def test_fractional_clock_respects_displayed_cutoff_boundary():
    start = NOW.replace(microsecond=123456)
    d = detail(state="closed", closed_at="2026-06-29T12:00:00Z")
    snap = scan(Scenario({1: [d]}).client(), "test-author", clock=lambda: start)
    assert len(snap.pull_requests) == 1
    assert snap.scope["closed_since"] == "2026-06-29T12:00:00Z"


def test_successful_recollection_keeps_notice_and_real_quality_warning():
    first, second = detail(), detail()
    second["head"]["sha"] = "c" * 40
    second.pop("created_at")
    p = run(Scenario({1: [first, second, second]})).pull_requests[0]
    assert p.observations and p.collection_quality == "partial"


def test_nonstring_discovery_locator_is_partial_instead_of_crash():
    s = Scenario()
    s.open_items[0]["pull_request"]["url"] = 17
    snap = run(s)
    assert not snap.complete and not snap.pull_requests
    assert "invalid PR locator" in snap.errors[0]


@pytest.mark.parametrize("field", ["requested_reviewers", "requested_teams"])
def test_malformed_requested_identity_is_not_silently_empty(field):
    p = run(Scenario({1: [detail(**{field: [{}]})]})).pull_requests[0]
    assert p.collection_quality == "partial"
    assert any(field in w for w in p.warnings)


def test_malformed_check_identity_stays_partial_and_snapshot_roundtrips():
    from conftest import check

    from oss_pr_tracker.models import read_snapshot

    snap = run(Scenario(checks=[check(app={"id": {"bad": "shape"}}, check_suite={"id": "19"})]))
    assert not snap.complete
    assert snap.pull_requests[0].collections["check_runs"].items[0]["app_id"] is None
    assert read_snapshot(snap.to_dict()).to_dict() == snap.to_dict()


def test_malformed_lifecycle_field_is_unknown_and_renderable():
    from oss_pr_tracker.models import read_snapshot

    d = detail(state={"unexpected": "shape"})
    s = Scenario({1: [d]})
    s.open_items = [search_item(d)]
    snap = run(s)
    assert snap.pull_requests[0].lifecycle == "unknown" and not snap.complete
    assert read_snapshot(snap.to_dict()).pull_requests[0].state is None
