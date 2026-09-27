"""Small versioned snapshot contract. Unknown and unavailable are explicit."""

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from . import __version__

SCHEMA_VERSION = 1
COLLECTIONS = ("reviews", "conversation", "inline", "check_runs", "commit_statuses")
LIFECYCLES = {"open", "draft", "merged", "closed_unmerged", "unknown"}
CATEGORIES = {"author_action", "inspect", "waiting", "done"}
QUALITY = {"complete", "partial", "unstable", "unavailable", "not_requested_terminal"}


def utcnow():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo else None
    except ValueError:
        return None


def clean(value, limit=500):
    if not isinstance(value, str):
        return ""
    value = "".join(" " if unicodedata.category(c).startswith("C") else c for c in value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email omitted]", value)
    return value[:limit]


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        return None
    try:
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.port:
            return None
        if "\\" in value:
            return None
        return quote(value, safe=":/?&=#%+;,@~!$*-._")
    except ValueError:
        return None


def login(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value)
        or "--" in value
    ):
        raise ValueError("Username must be a GitHub login (1–39 letters, digits, single hyphens).")
    return value


@dataclass
class Collection:
    status: str = "unavailable"
    items: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total: int | None = None
    pages: int = 0


@dataclass
class PullRequest:
    identity: str
    number: int
    url: str | None
    id: int | None = None
    title: str = "Details unavailable"
    author: str | None = None
    repository: str | None = None
    repository_id: int | None = None
    lifecycle: str = "unknown"
    state: str | None = None
    draft: bool | None = None
    merged: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    head_sha: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    mergeable: bool | None = None
    mergeable_state: str | None = None
    requested_reviewers: list[str] = field(default_factory=list)
    requested_teams: list[str] = field(default_factory=list)
    observed_at: str | None = None
    observations: list[str] = field(default_factory=list)
    coherent: bool = False
    collection_quality: str = "partial"
    warnings: list[str] = field(default_factory=list)
    collections: dict[str, Collection] = field(
        default_factory=lambda: {name: Collection() for name in COLLECTIONS}
    )
    attention: dict = field(default_factory=lambda: {"category": "inspect", "reasons": []})


@dataclass
class Snapshot:
    scan_id: str
    mode: str
    username: str
    started_at: str
    finished_at: str
    scope: dict
    schema_version: int = SCHEMA_VERSION
    application_version: str = __version__
    discovery: list[dict] = field(default_factory=list)
    pull_requests: list[PullRequest] = field(default_factory=list)
    complete: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    request_counts: dict = field(default_factory=lambda: {"requests": 0, "errors": 0})
    comparison: dict | None = None

    def to_dict(self):
        return asdict(self)


def lifecycle(d):
    if not all(k in d for k in ("state", "merged", "merged_at", "draft")):
        return "unknown"
    state, merged, when, draft = (d.get(k) for k in ("state", "merged", "merged_at", "draft"))
    if state == "closed" and merged is True and timestamp(when):
        return "merged"
    if state == "closed" and merged is False and when is None:
        return "closed_unmerged"
    if state == "open" and merged is False and when is None and type(draft) is bool:
        return "draft" if draft else "open"
    return "unknown"


def feedback(raw, kind):
    if type(raw.get("id")) is not int:
        raise ValueError("Feedback lacks a stable ID.")
    user = raw.get("user") or {}
    body = raw.get("body") or ""
    if not isinstance(body, str) or not isinstance(user, dict):
        raise ValueError("Malformed feedback.")
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    return {
        "id": raw["id"],
        "source": kind,
        "author": clean(user.get("login")) or None,
        "author_type": clean(user.get("type")) or None,
        "association": clean(raw.get("author_association")) or None,
        "created_at": raw.get("submitted_at") if kind == "reviews" else raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "url": safe_url(raw.get("html_url")),
        "body_digest": hashlib.sha256(body.encode()).hexdigest(),
        "excerpt": clean(body, 220),
        "state": clean(raw.get("state")) or None,
        "commit_id": raw.get("commit_id"),
    }


def strings(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_shapes(data):
    """Validate nested data used by the renderer and local comparison."""
    for key in ("warnings", "errors"):
        if not strings(data.get(key)):
            raise ValueError("Invalid snapshot warnings/errors.")
    for d in data["discovery"]:
        if (
            not isinstance(d["query"], str)
            or d["status"] not in QUALITY
            or type(d["fetched_count"]) is not int
            or type(d["pages"]) is not int
            or not strings(d["warnings"])
            or (d["reported_total"] is not None and type(d["reported_total"]) is not int)
        ):
            raise ValueError("Invalid discovery metadata.")
    for p in data["pull_requests"]:
        for key in (
            "title",
            "author",
            "repository",
            "url",
            "head_sha",
            "base_sha",
            "base_ref",
            "state",
            "mergeable_state",
            "created_at",
            "updated_at",
            "closed_at",
            "merged_at",
            "observed_at",
        ):
            if p.get(key) is not None and not isinstance(p[key], str):
                raise ValueError("Invalid PR factual field type.")
        for key in ("draft", "merged", "mergeable"):
            if p.get(key) is not None and type(p[key]) is not bool:
                raise ValueError("Invalid PR boolean field.")
        for key in ("requested_reviewers", "requested_teams", "warnings"):
            if not strings(p.get(key)):
                raise ValueError("Invalid PR list field.")
        if not strings(p.get("observations", [])):
            raise ValueError("Invalid observation notes.")
        reasons = p["attention"]["reasons"]
        if not isinstance(reasons, list) or not reasons:
            raise ValueError("Attention requires evidence reasons.")
        for reason in reasons:
            if reason["level"] not in CATEGORIES:
                raise ValueError("Invalid reason level.")
        for c in p["collections"].values():
            if not strings(c.get("warnings")) or type(c.get("pages")) is not int:
                raise ValueError("Invalid collection metadata.")
            for item in c["items"]:
                if type(item.get("id")) is not int:
                    raise ValueError("Invalid evidence identity.")
                for key, value in item.items():
                    if key in ("id", "app_id", "suite_id"):
                        if value is not None and type(value) is not int:
                            raise ValueError("Invalid evidence numeric field.")
                    elif value is not None and not isinstance(value, str):
                        raise ValueError("Invalid evidence scalar field.")
                if "body_digest" in item and not re.fullmatch(r"[a-f0-9]{64}", item["body_digest"]):
                    raise ValueError("Invalid feedback body digest.")
    for key in ("requests", "errors"):
        if type(data["request_counts"].get(key)) is not int:
            raise ValueError("Invalid request counts.")
    c = data.get("comparison")
    if c is not None:
        if (
            not isinstance(c["baseline_scan_id"], str)
            or not timestamp(c["baseline_finished_at"])
            or not strings(c["warnings"])
            or not isinstance(c["changes"], list)
        ):
            raise ValueError("Invalid comparison metadata.")
        for change in c["changes"]:
            if not all(isinstance(change[k], str) for k in ("identity", "kind", "message")):
                raise ValueError("Invalid comparison change.")
    if data["complete"] and (
        data["errors"]
        or any(d["status"] != "complete" for d in data["discovery"])
        or any(p["collection_quality"] != "complete" for p in data["pull_requests"])
    ):
        raise ValueError("Snapshot completeness contradicts its collection quality.")


def read_snapshot(data):
    """Validate the contract before construction; never infer missing quality fields."""
    try:
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported snapshot schema; expected schema_version 1.")
        for key in ("scan_id", "application_version", "username", "started_at", "finished_at"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"Missing or invalid snapshot field: {key}.")
        login(data["username"])
        if not timestamp(data["started_at"]) or not timestamp(data["finished_at"]):
            raise ValueError("Snapshot timestamps must include a timezone.")
        if data.get("mode") not in ("live", "demo") or type(data.get("complete")) is not bool:
            raise ValueError("Invalid snapshot mode or completeness.")
        scope = data["scope"]
        if scope.get("public_only") is not True or scope.get("open_included") is not True:
            raise ValueError("Only public authored-PR scopes are supported.")
        if type(scope.get("all_history")) is not bool:
            raise ValueError("Invalid scope all_history.")
        if not scope["all_history"] and not timestamp(scope.get("closed_since")):
            raise ValueError("Missing closed_since scope timestamp.")
        for key in ("pull_requests", "discovery", "warnings", "errors"):
            if not isinstance(data.get(key), list):
                raise ValueError(f"Invalid snapshot list: {key}.")
        prs = []
        identities = set()
        for p in data["pull_requests"]:
            for key in (
                "identity",
                "number",
                "url",
                "lifecycle",
                "collection_quality",
                "collections",
                "attention",
                "coherent",
                "warnings",
            ):
                if key not in p:
                    raise ValueError(f"Missing PR field: {key}.")
            if p["lifecycle"] not in LIFECYCLES or p["collection_quality"] not in QUALITY:
                raise ValueError("Invalid PR lifecycle or quality.")
            if type(p["number"]) is not int or p["number"] < 1:
                raise ValueError("Invalid PR number.")
            if not isinstance(p["identity"], str) or p["identity"] in identities:
                raise ValueError("Invalid or duplicate PR identity.")
            identities.add(p["identity"])
            if type(p["coherent"]) is not bool or not isinstance(p["warnings"], list):
                raise ValueError("Invalid PR coherence or warnings.")
            if p["attention"].get("category") not in CATEGORIES:
                raise ValueError("Invalid attention category.")
            if not isinstance(p["attention"].get("reasons"), list):
                raise ValueError("Invalid attention reasons.")
            for reason in p["attention"]["reasons"]:
                if not all(isinstance(reason.get(k), str) for k in ("code", "message", "level")):
                    raise ValueError("Malformed attention reason.")
            cols = {}
            for name in COLLECTIONS:
                c = p["collections"][name]
                if c.get("status") not in QUALITY or not isinstance(c.get("items"), list):
                    raise ValueError("Invalid evidence collection.")
                for item in c["items"]:
                    required = (
                        (
                            "id",
                            "source",
                            "created_at",
                            "updated_at",
                            "body_digest",
                            "excerpt",
                            "author",
                            "author_type",
                            "state",
                            "url",
                        )
                        if name in ("reviews", "conversation", "inline")
                        else (
                            (
                                "id",
                                "name",
                                "app_id",
                                "suite_id",
                                "status",
                                "conclusion",
                                "head_sha",
                                "url",
                            )
                            if name == "check_runs"
                            else ("id", "context", "state", "url")
                        )
                    )
                    if not isinstance(item, dict) or any(k not in item for k in required):
                        raise ValueError("Malformed evidence item.")
                cols[name] = Collection(**c)
            prs.append(PullRequest(**{**p, "collections": cols}))
        if not isinstance(data.get("request_counts"), dict):
            raise ValueError("Missing request counts.")
        if data.get("comparison") is not None:
            c = data["comparison"]
            if not isinstance(c, dict) or not isinstance(c.get("changes"), list):
                raise ValueError("Invalid comparison.")
        validate_shapes(data)
        return Snapshot(**{**data, "pull_requests": prs})
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed snapshot: a required field has an invalid shape.") from exc
