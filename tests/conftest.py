import socket
from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest

from oss_pr_tracker.github import GitHub

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
HEAD = "a" * 40
BASE = "b" * 40


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*a, **k):
        raise AssertionError("Tests must not use the network.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("oss_pr_tracker.github.shutil.which", lambda _: None)
    monkeypatch.setattr("oss_pr_tracker.github.time.sleep", forbidden)


def detail(number=1, **changes):
    d = {
        "id": 100 + number,
        "number": number,
        "title": "Synthetic change",
        "html_url": f"https://github.com/example/project/pull/{number}",
        "user": {"login": "test-author", "type": "User"},
        "base": {
            "repo": {"private": False, "id": 42, "full_name": "example/project"},
            "ref": "feature/stack",
            "sha": BASE,
        },
        "head": {"sha": HEAD, "repo": None},
        "state": "open",
        "draft": False,
        "merged": False,
        "merged_at": None,
        "closed_at": None,
        "mergeable": True,
        "mergeable_state": "clean",
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-09-26T12:00:00Z",
        "requested_reviewers": [],
        "requested_teams": [],
    }
    d.update(changes)
    return d


def search_item(d):
    return {
        "id": d["id"],
        "number": d["number"],
        "state": d["state"],
        "html_url": d["html_url"],
        "title": "UNTRUSTED SEARCH TITLE",
        "pull_request": {
            "url": f"https://api.github.com/repos/example/project/pulls/{d['number']}"
        },
    }


def review(id=1, state="CHANGES_REQUESTED", sha=HEAD, user="reviewer", **changes):
    d = {
        "id": id,
        "state": state,
        "commit_id": sha,
        "user": {"login": user, "type": "User"} if user else None,
        "submitted_at": f"2026-09-{10 + id:02d}T00:00:00Z",
        "body": "Please inspect this assumption.",
        "html_url": f"https://github.com/example/project/pull/1#review-{id}",
        "author_association": "COLLABORATOR",
    }
    d.update(changes)
    return d


def comment(id=1, author="reviewer", at="2026-09-20T00:00:00Z", **changes):
    d = {
        "id": id,
        "user": {"login": author, "type": "User"} if author else None,
        "created_at": at,
        "updated_at": at,
        "body": "Thank you!",
        "html_url": f"https://github.com/example/project/pull/1#comment-{id}",
    }
    d.update(changes)
    return d


def check(id=1, conclusion="success", sha=HEAD, **changes):
    d = {
        "id": id,
        "name": "test",
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
        "app": {"id": 9, "slug": "ci"},
        "check_suite": {"id": 19},
        "html_url": "https://github.com/example/project/actions/runs/1",
    }
    d.update(changes)
    return d


class Scenario:
    def __init__(
        self, details=None, *, reviews=None, comments=None, inline=None, checks=None, statuses=None
    ):
        self.details = details or {1: [detail()]}
        self.reviews = reviews or []
        self.comments = comments or []
        self.inline = inline or []
        self.checks = checks or []
        self.statuses = statuses or []
        self.requests = []
        self.detail_calls = {}
        self.overrides = {}
        self.open_items = [
            search_item(ds[0]) for ds in self.details.values() if ds[0]["state"] == "open"
        ]
        self.closed_items = [
            search_item(ds[0]) for ds in self.details.values() if ds[0]["state"] == "closed"
        ]

    def handle(self, r):
        self.requests.append(r)
        assert r.method == "GET"
        assert r.url.host == "api.github.com" and r.url.scheme == "https"
        if r.url.path in self.overrides:
            value = self.overrides[r.url.path]
            return value(r) if callable(value) else httpx.Response(value)
        if r.url.path == "/search/issues":
            query = r.url.params["q"]
            items = self.open_items if "is:open" in query else self.closed_items
            return httpx.Response(
                200, json={"total_count": len(items), "incomplete_results": False, "items": items}
            )
        path = r.url.path
        if path.endswith("/reviews"):
            body = self.reviews
        elif path.endswith("/comments"):
            body = self.comments if "/issues/" in path else self.inline
        elif path.endswith("/check-runs"):
            assert r.url.params["filter"] == "latest"
            body = {"total_count": len(self.checks), "check_runs": self.checks}
        elif path.endswith("/status"):
            body = {
                "total_count": len(self.statuses),
                "state": "pending",
                "statuses": self.statuses,
            }
        elif "/pulls/" in path:
            number = int(path.rsplit("/", 1)[1])
            ds = self.details[number]
            index = self.detail_calls.get(number, 0)
            self.detail_calls[number] = index + 1
            body = ds[min(index, len(ds) - 1)]
        else:
            raise AssertionError(f"Unexpected request: {r.method} {r.url.path}")
        return httpx.Response(200, json=deepcopy(body))

    def client(self, **kwargs):
        return GitHub(transport=httpx.MockTransport(self.handle), sleep=lambda _: None, **kwargs)
