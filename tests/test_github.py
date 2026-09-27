import json
import subprocess

import httpx
import pytest

from oss_pr_tracker.github import API_VERSION, APIError, GitHub, credentials


def client(handler, **kwargs):
    return GitHub(transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/a",
        "http://api.github.com/a",
        "https://user@api.github.com/a",
        "https://api.github.com:444/a",
        "https://api.github.com:443/a",
        "https://api.github.com\\@evil.example/a",
    ],
)
@pytest.mark.parametrize("kind", ["redirect", "pagination"])
def test_url_boundary_never_sends_credentials_off_origin(target, kind):
    seen = []

    def handle(r):
        seen.append(r)
        assert r.headers["authorization"] == "Bearer synthetic-secret"
        if kind == "redirect":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, json=[{"id": 1}], headers={"link": f'<{target}>; rel="next"'})

    c = client(handle, token="synthetic-secret")
    if kind == "redirect":
        with pytest.raises(APIError):
            c.get("/test")
    else:
        result = c.collection("/test")
        assert result.status == "partial" and len(result.items) == 1
    assert len(seen) == 1


def test_same_origin_redirect_and_redirect_loop_are_bounded():
    seen = []

    def handle(r):
        seen.append(r)
        if r.url.path == "/new":
            return httpx.Response(200, json={"value": 1})
        return httpx.Response(301, headers={"location": "/new"})

    c = client(handle)
    assert c.get("/old")[0] == {"value": 1}
    assert len(seen) == 2
    c = client(lambda _: httpx.Response(301, headers={"location": "/same"}))
    with pytest.raises(APIError, match="loop"):
        c.get("/same")
    assert c.counts["requests"] == 1


def test_paginated_wrapped_search_duplicates_and_counts():
    def handle(r):
        assert r.method == "GET" and r.headers["x-github-api-version"] == API_VERSION
        if "page=2" in str(r.url):
            return httpx.Response(
                200,
                json={
                    "items": [{"id": 1}, {"id": 2}],
                    "total_count": 2,
                    "incomplete_results": False,
                },
            )
        assert r.url.params["per_page"] == "100"
        return httpx.Response(
            200,
            json={"items": [{"id": 1}], "total_count": 2, "incomplete_results": False},
            headers={"link": '<https://api.github.com/search/issues?page=2>; rel="next"'},
        )

    c = client(handle)
    result = c.collection("/search/issues", key="items", search=True)
    assert result.status == "complete" and len(result.items) == 2 and result.pages == 2


@pytest.mark.parametrize(
    ("total", "incomplete", "expected"),
    [(0, False, "complete"), (1001, False, "partial"), (0, True, "partial")],
)
def test_search_empty_cap_incomplete(total, incomplete, expected):
    c = client(
        lambda _: httpx.Response(
            200, json={"items": [], "total_count": total, "incomplete_results": incomplete}
        )
    )
    result = c.collection("/search/issues", key="items", search=True)
    assert result.status == expected and result.total == total


def test_later_page_error_and_loop_preserve_records():
    for failure in (True, False):

        def handle(r, failure=failure):
            if "page=2" in str(r.url) and failure:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"link": '<https://api.github.com/test?page=2>; rel="next"'},
            )

        result = client(handle).collection("/test")
        assert result.status == "partial" and len(result.items) == 1


@pytest.mark.parametrize("payload", ["not json", "{}", '{"items":[]}'])
def test_malformed_responses(payload):
    result = client(lambda _: httpx.Response(200, text=payload)).collection(
        "/test", key="items", search=True
    )
    assert result.status == "unavailable"


@pytest.mark.parametrize(
    ("code", "reason"),
    [(401, "Authentication"), (403, "Permission"), (404, "missing"), (422, "rejected")],
)
def test_nonretry_errors_are_sanitized(code, reason):
    c = client(lambda _: httpx.Response(code, text="synthetic-secret"), token="synthetic-secret")
    with pytest.raises(APIError, match=reason) as e:
        c.get("/test")
    assert "synthetic-secret" not in str(e.value) and c.counts["requests"] == 1


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "2"}, [2]),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "102"}, [3]),
        ({"retry-after": "Sun, 27 Sep 2026 12:00:02 GMT"}, [2]),
    ],
)
def test_rate_wait_respects_header(headers, expected):
    calls, waits = [], []

    def handle(r):
        calls.append(r)
        return (
            httpx.Response(429, headers=headers)
            if len(calls) == 1
            else httpx.Response(200, json={})
        )

    now = 1790510400 if "GMT" in headers.get("retry-after", "") else 100
    c = client(handle, sleep=waits.append, now=lambda: now)
    c.get("/test")
    assert waits == expected and len(calls) == 2


@pytest.mark.parametrize(
    "headers", [{"retry-after": "99"}, {}, {"retry-after": "nan"}, {"retry-after": "invalid"}]
)
def test_rate_budget_halts_further_requests(headers):
    waits = []
    c = client(lambda _: httpx.Response(429, headers=headers), sleep=waits.append)
    with pytest.raises(APIError, match="Rate limit"):
        c.get("/test")
    with pytest.raises(APIError):
        c.get("/another")
    assert c.counts["requests"] == 1 and not waits


def test_transient_and_timeout_retries_are_finite():
    for timeout in (False, True):
        waits = []

        def handle(r, timeout=timeout):
            if timeout:
                raise httpx.ReadTimeout("synthetic-secret", request=r)
            return httpx.Response(503)

        c = client(handle, sleep=waits.append)
        with pytest.raises(APIError) as e:
            c.get("/test")
        assert waits == [1, 2] and c.counts["requests"] == 3
        assert "synthetic-secret" not in str(e.value)


def test_success_payload_scrubs_known_secret():
    c = client(
        lambda _: httpx.Response(200, json={"body": "x synthetic-secret y"}),
        token="synthetic-secret",
    )
    assert "synthetic-secret" not in json.dumps(c.get("/test")[0])


def test_auth_none_order_and_bounded_gh_capture(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "first")
    monkeypatch.setenv("GITHUB_TOKEN", "second")
    assert credentials("none") == (None, "none")
    assert credentials("auto") == ("first", "GH_TOKEN")
    monkeypatch.delenv("GH_TOKEN")
    assert credentials("auto") == ("second", "GITHUB_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN")
    assert credentials("auto") == (None, "unauthenticated")
    monkeypatch.setattr("oss_pr_tracker.github.shutil.which", lambda _: "/synthetic/gh")

    def run(cmd, **kwargs):
        assert cmd[1:] == ["auth", "token", "--hostname", "github.com"]
        assert kwargs["capture_output"] and kwargs["timeout"] == 5
        return subprocess.CompletedProcess(cmd, 0, "captured\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert credentials("auto") == ("captured", "existing gh credential")


def test_pagination_supports_github_repository_id_alias():
    def handle(r):
        if r.url.path.startswith("/repositories/"):
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(
            200,
            json=[{"id": 1}],
            headers={
                "link": "<https://api.github.com/repositories/42/pulls/1/reviews?page=2>; "
                'rel="next"'
            },
        )

    result = client(handle).collection("/repos/example/project/pulls/1/reviews")
    assert result.status == "complete" and len(result.items) == 2
