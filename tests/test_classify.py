import pytest
from conftest import NOW, Scenario, check, comment, detail, review

from oss_pr_tracker.collect import scan
from oss_pr_tracker.models import lifecycle


def pr(**kwargs):
    return scan(Scenario(**kwargs).client(), "test-author", clock=lambda: NOW).pull_requests[0]


def codes(p):
    return {r["code"] for r in p.attention["reasons"]}


@pytest.mark.parametrize(
    ("reviews", "expected", "reason"),
    [
        ([review()], "author_action", "current_changes_requested"),
        ([review(sha="c" * 40)], "inspect", "old_changes_requested"),
        ([review(), review(2, "COMMENTED")], "author_action", "current_changes_requested"),
        ([review(), review(2, "APPROVED")], "inspect", "possible_follow_up"),
        ([review(state="APPROVED"), review(2, "DISMISSED")], "inspect", "dismissed_review"),
        ([review(state="DISMISSED")], "inspect", "dismissed_review"),
        ([review(state="PENDING")], "waiting", "inferred_review_wait"),
        ([review(state="UNKNOWN")], "inspect", "unknown_review_state"),
        ([review(user=None)], "inspect", "nonhuman_or_unknown_review"),
        ([review(user="robot", user_type="Bot")], "author_action", "current_changes_requested"),
    ],
)
def test_review_decisions(reviews, expected, reason):
    p = pr(reviews=reviews)
    assert p.attention["category"] == expected
    assert reason in codes(p)


def test_multiple_reviewers_and_bot():
    p = pr(reviews=[review(), review(2, "APPROVED", user="someone-else")])
    assert p.attention["category"] == "author_action"
    bot = review()
    bot["user"]["type"] = "Bot"
    p = pr(reviews=[bot])
    assert p.attention["category"] == "inspect"
    assert "current_changes_requested" not in codes(p)


@pytest.mark.parametrize("source", ["comments", "inline", "reviews"])
@pytest.mark.parametrize(
    ("external_at", "inspect"),
    [
        ("2026-09-19T23:59:59Z", False),
        ("2026-09-20T00:00:00Z", False),
        ("2026-09-20T00:00:01Z", True),
    ],
)
def test_follow_up_boundary_all_sources(source, external_at, inspect):
    external = comment(at=external_at)
    if source == "reviews":
        external = review(state="COMMENTED", submitted_at=external_at)
    kwargs = {source: [external]}
    kwargs.setdefault("comments", []).append(comment(99, "test-author"))
    p = pr(**kwargs)
    assert ("possible_follow_up" in codes(p)) == inspect
    assert p.attention["category"] == ("inspect" if inspect else "waiting")


def test_author_reply_does_not_clear_review_and_undated_is_inspection():
    p = pr(reviews=[review()], comments=[comment(9, "test-author")])
    assert "possible_follow_up" not in codes(p)
    assert p.attention["category"] == "author_action"
    assert "undated_feedback" in codes(pr(comments=[comment(at=None)]))


@pytest.mark.parametrize(
    ("conclusion", "category"),
    [
        ("success", "waiting"),
        ("neutral", "waiting"),
        ("skipped", "waiting"),
        ("failure", "inspect"),
        ("timed_out", "inspect"),
        ("cancelled", "inspect"),
        ("action_required", "inspect"),
        ("something-new", "inspect"),
        (None, "inspect"),
    ],
)
def test_check_conclusions_preserved(conclusion, category):
    p = pr(checks=[check(conclusion=conclusion)])
    assert p.attention["category"] == category
    assert p.collections["check_runs"].items[0]["conclusion"] == conclusion


def test_empty_pending_combined_status_and_running_check():
    p = pr()
    assert p.attention["category"] == "waiting"
    assert "status_pending" not in codes(p)
    p = pr(checks=[check(conclusion=None, status="in_progress")])
    assert "checks_pending" in codes(p)


@pytest.mark.parametrize(
    ("mergeable", "category"), [(None, "inspect"), (False, "author_action"), (True, "waiting")]
)
def test_mergeability(mergeable, category):
    assert pr(details={1: [detail(mergeable=mergeable)]}).attention["category"] == category


@pytest.mark.parametrize(
    ("state", "merged", "merged_at", "expected"),
    [
        ("closed", False, None, "closed_unmerged"),
        ("closed", True, "2026-09-20T00:00:00Z", "merged"),
        ("closed", None, None, "unknown"),
        ("closed", False, "2026-09-20T00:00:00Z", "unknown"),
        ("closed", True, None, "unknown"),
        ("open", True, None, "unknown"),
        ("closed", "false", None, "unknown"),
        ("closed", True, "invalid", "unknown"),
    ],
)
def test_lifecycle_requires_authoritative_consistent_fields(state, merged, merged_at, expected):
    assert lifecycle(detail(state=state, merged=merged, merged_at=merged_at)) == expected


def test_same_name_different_apps_suites_and_latest_rerun():
    p = pr(checks=[check(), check(2, app={"id": 10}, check_suite={"id": 20})])
    assert len(p.collections["check_runs"].items) == 2
    assert p.attention["category"] == "waiting"


def test_missing_merge_timestamp_field_stays_unknown():
    d = detail(state="closed")
    del d["merged_at"]
    assert lifecycle(d) == "unknown"


@pytest.mark.parametrize("state", ["APPROVED", "DISMISSED"])
def test_undated_substantive_review_suppresses_older_same_reviewer_action(state):
    p = pr(reviews=[review(), review(2, state, submitted_at=None)])
    assert p.attention["category"] == "inspect"
    assert "current_changes_requested" not in codes(p)
    p = pr(
        reviews=[review(), review(2, state, submitted_at=None), review(3, user="another-reviewer")]
    )
    assert p.attention["category"] == "author_action"
