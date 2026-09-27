import json
from copy import deepcopy
from importlib.resources import files

import pytest
from conftest import NOW, Scenario, detail

from oss_pr_tracker.collect import scan
from oss_pr_tracker.compare import compare
from oss_pr_tracker.models import PullRequest, read_snapshot
from oss_pr_tracker.report import counts, markdown, ownership_group, target_branch_relation


def demo():
    return read_snapshot(json.loads(files("oss_pr_tracker").joinpath("demo_data.json").read_text()))


@pytest.mark.parametrize(
    ("repository", "repo_id", "expected"),
    [
        ("Test-Author/project", 42, "own_account"),
        ("test-author/fork", 42, "own_account"),
        ("controlled-organization/project", 42, "other_owner"),
        ("unrelated/project", 42, "other_owner"),
        (None, None, "unknown"),
        ("test-author/project", None, "unknown"),
        ("malformed", 42, "unknown"),
        ("test-author/project/extra", 42, "unknown"),
    ],
)
def test_ownership_uses_only_authoritative_base_owner(repository, repo_id, expected):
    p = PullRequest(
        identity="example", number=1, url=None, repository=repository, repository_id=repo_id
    )
    assert ownership_group(p, "test-author") == expected


def test_lifecycle_counts_partition_overall_totals_without_claiming_upstream_control():
    s = demo()
    s.pull_requests[0].repository = "sample-contributor/own"
    s.pull_requests[3].repository = "SAMPLE-CONTRIBUTOR/own"
    s.pull_requests[4].repository = None
    s.pull_requests[4].repository_id = None
    summary = counts(s)
    groups = summary["by_repository_owner"]
    assert groups["own_account"]["total"] == 2
    assert groups["own_account"]["lifecycle"]["merged"] == 1
    assert groups["other_owner"]["total"] == 5
    assert groups["unknown"]["lifecycle"]["closed_unmerged"] == 1
    assert sum(g["total"] for g in groups.values()) == len(s.pull_requests)
    for life, total in summary["lifecycle"].items():
        assert sum(g["lifecycle"][life] for g in groups.values()) == total
    report = markdown(s)
    assert "| own-account repositories | 2 |" in report
    assert "| other-owner repositories | 5 |" in report
    assert "| unknown ownership | 1 |" in report
    assert "organizations controlled by that person" in report
    assert "not external open-source contribution counts" in report


@pytest.mark.parametrize("all_history", [False, True])
def test_collection_window_is_explicit_and_never_lifetime_totals(all_history):
    s = demo()
    s.scope["all_history"] = all_history
    text = markdown(s)
    assert "all open PRs regardless of age" in text
    assert "collection-window counts, not lifetime contribution totals" in text
    if all_history:
        assert "all discoverable closed history" in text
    else:
        assert "2026-06-29T12:00:00Z; inclusive" in text


@pytest.mark.parametrize(
    ("target", "default", "label"),
    [
        ("main", "main", "default branch"),
        ("feature/navigation", "main", "non-default branch"),
        ("integration", "rolling", "non-default branch"),
        ("Main", "main", "non-default branch"),
        ("main", None, "unknown"),
        (None, "main", "unknown (target branch not recorded)"),
    ],
)
def test_target_branch_relation_is_evidence_based(target, default, label):
    s = demo()
    p = s.pull_requests[3]
    p.base_ref = target
    p.repository_default_branch = default
    assert target_branch_relation(p).startswith(label)
    text = markdown(s)
    if target:
        assert target.replace("_", "&#95;") in text
    assert "Target vs observed default" in text
    assert "later inclusion in the default branch are not established" in text


def test_live_default_branch_normalized_and_snapshot_roundtrips():
    d = detail()
    d["base"]["repo"]["default_branch"] = "main"
    s = scan(Scenario({1: [d]}).client(), "test-author", clock=lambda: NOW)
    p = s.pull_requests[0]
    assert p.repository_default_branch == "main"
    assert p.base_ref == "feature/stack"
    assert target_branch_relation(p) == "non-default branch (observed default: main)"
    assert read_snapshot(s.to_dict()).to_dict() == s.to_dict()


def test_old_snapshot_metadata_absence_does_not_create_status_changes():
    raw = demo().to_dict()
    for p in raw["pull_requests"]:
        p.pop("repository_default_branch", None)
    old = read_snapshot(raw)
    current = deepcopy(old)
    for p in current.pull_requests:
        assert p.repository_default_branch is None
        p.repository_default_branch = "main"
    assert compare(current, old)["changes"] == []
    assert counts(current) == counts(old)
    assert "unknown (default branch not recorded)" in markdown(old)
    assert old.schema_version == current.schema_version == 1


def test_invalid_new_field_is_rejected_but_missing_is_allowed():
    raw = demo().to_dict()
    raw["pull_requests"][0]["repository_default_branch"] = {"unexpected": "shape"}
    with pytest.raises(ValueError, match="factual field"):
        read_snapshot(raw)
