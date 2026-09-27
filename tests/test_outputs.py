import json
from copy import deepcopy
from importlib.resources import files

import pytest
from conftest import NOW, Scenario, comment, detail

from oss_pr_tracker.cli import main
from oss_pr_tracker.collect import scan
from oss_pr_tracker.compare import compare
from oss_pr_tracker.models import feedback, read_snapshot
from oss_pr_tracker.report import markdown, write_outputs


def demo():
    return read_snapshot(json.loads(files("oss_pr_tracker").joinpath("demo_data.json").read_text()))


def test_deterministic_totals_escaping_and_no_release_claims():
    s = demo()
    text = markdown(s)
    assert text == markdown(s)
    assert text.startswith("DEMO — synthetic data; not a live GitHub status report.")
    assert "Observed PRs: **8**" in text and "author_action: 1/8" in text
    assert "feature/navigation" in text and "none&#95;observed (not success)" in text
    s.pull_requests[0].title = "<script>evil()</script>|\n`x`[click](javascript:evil)\x1b[31m"
    s.pull_requests[0].url = "javascript:evil()"
    text = markdown(s)
    assert "<script>" not in text and "\x1b" not in text
    assert "&lt;script&gt;" in text and "&#124;" in text and "&#96;" in text
    assert "](javascript:" not in text


def test_demo_and_render_no_auth_or_network_preserve_freshness(tmp_path, monkeypatch, capsys):
    def forbidden(*a, **k):
        raise AssertionError("Offline command invoked authentication or API.")

    monkeypatch.setattr("oss_pr_tracker.github.credentials", forbidden)
    monkeypatch.setattr("oss_pr_tracker.github.GitHub", forbidden)
    assert main(["demo", "--output-dir", str(tmp_path)]) == 0
    first = json.loads((tmp_path / "status.json").read_text())
    assert (
        main(
            [
                "render",
                "--input",
                str(tmp_path / "status.json"),
                "--output-dir",
                str(tmp_path / "render"),
            ]
        )
        == 0
    )
    assert first == json.loads((tmp_path / "render/status.json").read_text())
    assert str(tmp_path.resolve()) in capsys.readouterr().out


def test_comparison_digest_outside_excerpt_and_scan_not_acknowledgement():
    body = "x" * 300
    s = Scenario(comments=[comment(body=body + "a")])
    baseline = scan(s.client(), "test-author", clock=lambda: NOW)
    s.comments = [comment(body=body + "b")]
    current = scan(s.client(), "test-author", clock=lambda: NOW)
    old = baseline.pull_requests[0].collections["conversation"].items[0]
    new = current.pull_requests[0].collections["conversation"].items[0]
    assert old["excerpt"] == new["excerpt"] and old["body_digest"] != new["body_digest"]
    delta = compare(current, baseline)
    assert any(c["kind"] == "edited_feedback" for c in delta["changes"])
    assert current.pull_requests[0].attention["category"] == "inspect"
    assert (
        feedback(comment(body="a\r\nb"), "conversation")["body_digest"]
        == feedback(comment(body="a\nb"), "conversation")["body_digest"]
    )


def test_absence_scope_change_and_incompatible_comparisons():
    a, b = demo(), demo()
    b.pull_requests.pop()
    b.scope["closed_since"] = "2026-08-01T00:00:00Z"
    delta = compare(b, a)
    assert delta["scope_changed"] and not delta["missing_record_conclusions_enabled"]
    assert delta["changes"][0]["kind"] == "not_observed"
    for change in ({"username": "another-user"}, {"mode": "live"}):
        c = deepcopy(a)
        for k, v in change.items():
            setattr(c, k, v)
        with pytest.raises(ValueError):
            compare(c, a)


def test_feedback_creation_time_distinct_from_new_observation_and_state_change():
    a, b = demo(), demo()
    p = b.pull_requests[2]
    p.lifecycle = "merged"
    p.head_sha = "c" * 40
    p.collections["conversation"].items = [
        feedback(comment(at="2020-01-01T00:00:00Z"), "conversation")
    ]
    changes = compare(b, a)["changes"]
    assert {"lifecycle_changed", "head_sha_changed", "newly_observed_feedback"} <= {
        c["kind"] for c in changes
    }
    observation = next(c for c in changes if c["kind"] == "newly_observed_feedback")
    assert observation["created_since_baseline"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(schema_version=99),
        lambda d: d.pop("started_at"),
        lambda d: d.update(mode="made-up"),
        lambda d: d["pull_requests"][0].pop("collections"),
        lambda d: d["pull_requests"][0]["collections"]["reviews"].update(items=[{}]),
        lambda d: d["pull_requests"][0]["attention"].update(reasons=[]),
        lambda d: d["pull_requests"][0]["attention"]["reasons"][0].update(level="made-up"),
        lambda d: d["pull_requests"][0].update(repository=[]),
        lambda d: d["discovery"][0].pop("query"),
        lambda d: d.update(comparison={"changes": []}),
    ],
)
def test_invalid_snapshots_are_input_errors(tmp_path, capsys, change):
    data = demo().to_dict()
    change(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    assert main(["render", "--input", str(path), "--output-dir", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out/status.md").exists()
    assert "Input error:" in capsys.readouterr().err


def test_write_failure_never_announces_pair_and_baseline_read_first(tmp_path, monkeypatch, capsys):
    assert main(["demo", "--output-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    # Baseline shares destination: read it before replacement.
    assert (
        main(["demo", "--previous", str(tmp_path / "status.json"), "--output-dir", str(tmp_path)])
        == 0
    )
    assert json.loads((tmp_path / "status.json").read_text())["comparison"]["changes"] == []
    capsys.readouterr()
    import os

    original = os.replace

    def replace(source, dest):
        if str(dest).endswith(".md"):
            raise OSError("synthetic failure")
        return original(source, dest)

    monkeypatch.setattr(os, "replace", replace)
    assert main(["demo", "--output-dir", str(tmp_path)]) == 1
    result = capsys.readouterr()
    assert not result.out and "pair was not successfully written" in result.err
    assert not list(tmp_path.glob(".status*"))


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["scan"],
        ["scan", "--username", "a is:private"],
        ["scan", "--username", "bad--login"],
        ["scan", "--username", "ok", "--closed-since", "2026-99-01"],
        ["scan", "--username", "ok", "--all-history", "--closed-since", "2026-01-01"],
    ],
)
def test_argument_errors(args):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2


def test_help_and_version(capsys):
    for arg in ("--help", "--version"):
        with pytest.raises(SystemExit) as e:
            main([arg])
        assert e.value.code == 0
    assert "0.2.0" in capsys.readouterr().out


def test_scan_exit_codes_and_secret_absent_everywhere(tmp_path, monkeypatch, capsys):
    import oss_pr_tracker.github as github

    secret = "synthetic-secret-that-must-never-leak"
    monkeypatch.setenv("GH_TOKEN", secret)
    real = github.GitHub
    for failure, expected in ((None, 0), (403, 3), (401, 1)):
        scenario = Scenario({1: [detail(title="A " + secret + " title")]})
        if failure == 403:
            scenario.overrides["/repos/example/project/pulls/1/reviews"] = 403
        if failure == 401:
            scenario.overrides["/search/issues"] = 401
        monkeypatch.setattr(
            github,
            "GitHub",
            lambda token, scenario=scenario: real(
                token, transport=__import__("httpx").MockTransport(scenario.handle)
            ),
        )
        target = tmp_path / str(expected)
        assert main(["scan", "--username", "test-author", "--output-dir", str(target)]) == expected
        result = capsys.readouterr()
        assert secret not in result.out + result.err
        if expected != 1:
            assert (
                secret
                not in (target / "status.json").read_text() + (target / "status.md").read_text()
            )


def test_roundtrip_snapshot_and_individual_atomic_files(tmp_path):
    snapshot = demo()
    paths = write_outputs(snapshot, tmp_path)
    assert len(paths) == 2
    assert read_snapshot(json.loads(paths[0].read_text())).to_dict() == snapshot.to_dict()
    assert snapshot.scan_id in paths[1].read_text()


def test_unknown_creation_time_stays_unknown_in_comparison():
    a, b = demo(), demo()
    a.pull_requests.pop()
    b.pull_requests[-1].created_at = None
    delta = compare(b, a)
    assert delta["changes"][0]["created_since_baseline"] is None
    b.comparison = delta
    assert "Created since baseline: unknown" in markdown(b)
