"""Local command journeys; demo/render never enter authentication or HTTP code."""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from . import __version__
from .compare import compare
from .github import APIError
from .models import clean, login, read_snapshot
from .report import counts, write_outputs


def valid_login(value):
    try:
        return login(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def cutoff(value):
    try:
        d = date.fromisoformat(value)
        if d.isoformat() != value:
            raise ValueError
        return datetime(d.year, d.month, d.day, tzinfo=UTC)
    except ValueError:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD (midnight UTC, inclusive).") from None


def parser():
    p = argparse.ArgumentParser(description="Read-only public authored-PR evidence reports.")
    p.add_argument("--version", action="version", version=__version__)
    subs = p.add_subparsers(dest="command", required=True)
    scan = subs.add_parser("scan", help="Fetch current public GitHub PR evidence using GET only.")
    scan.add_argument("--username", required=True, type=valid_login)
    group = scan.add_mutually_exclusive_group()
    group.add_argument(
        "--closed-since",
        type=cutoff,
        help="Inclusive date at midnight UTC; all open PRs remain included.",
    )
    group.add_argument(
        "--all-history", action="store_true", help="Closed history within disclosed search limits."
    )
    scan.add_argument(
        "--auth",
        choices=("auto", "none"),
        default="auto",
        help="auto: existing environment/gh credentials, else public unauthenticated.",
    )
    scan.add_argument(
        "--previous",
        type=Path,
        help="Compatible baseline snapshot; read before output replacement.",
    )
    demo = subs.add_parser("demo", help="Offline synthetic report; no credentials or network.")
    demo.add_argument("--previous", type=Path)
    render = subs.add_parser(
        "render", help="Render saved facts without changing their observation time."
    )
    render.add_argument("--input", required=True, type=Path)
    for sub in (scan, demo, render):
        sub.add_argument("--output-dir", type=Path, default=Path("reports/current"))
    return p


def load(path):
    try:
        return read_snapshot(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Unable to read a valid JSON snapshot from the input path.") from None


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        previous = load(args.previous) if getattr(args, "previous", None) else None
        if args.command == "render":
            snap = load(args.input)
        elif args.command == "demo":
            snap = read_snapshot(
                json.loads(files("oss_pr_tracker").joinpath("demo_data.json").read_text())
            )
        else:
            if previous and (
                previous.mode != "live" or previous.username.casefold() != args.username.casefold()
            ):
                raise ValueError("Previous snapshot must be live and belong to the same username.")
            from .collect import scan
            from .github import GitHub, credentials

            token, source = credentials(args.auth)
            print(f"Collecting public authored PRs; authentication: {source}.", file=sys.stderr)
            client = GitHub(token)
            try:
                snap = scan(
                    client,
                    args.username,
                    closed_since=args.closed_since,
                    all_history=args.all_history,
                    progress=lambda message: print(message, file=sys.stderr),
                )
            finally:
                client.close()
            if not any(d["pages"] for d in snap.discovery) and not snap.pull_requests:
                warnings = list(dict.fromkeys(w for d in snap.discovery for w in d["warnings"]))
                raise APIError("No usable discovery was produced. " + " ".join(warnings))
        if previous:
            snap.comparison = compare(snap, previous)
        paths = write_outputs(snap, args.output_dir)
        for path in paths:
            print(clean(str(path), 4000))
        print(json.dumps({"observed_prs": len(snap.pull_requests), **counts(snap)}, sort_keys=True))
        if not snap.complete:
            print(
                "Completeness warning: partial evidence; inspect report limitations.",
                file=sys.stderr,
            )
        return 3 if args.command == "scan" and not snap.complete else 0
    except ValueError as exc:
        print("Input error: " + clean(str(exc)), file=sys.stderr)
        return 2
    except APIError as exc:
        print("Operational failure: " + clean(str(exc)), file=sys.stderr)
        return 1
    except OSError:
        print(
            "Output failure: report pair was not successfully written; individual files may have "
            "been replaced. Check permissions and available storage.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
