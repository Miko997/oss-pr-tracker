"""GET-only GitHub boundary with bounded retries and validated URL following."""

import math
import os
import shutil
import subprocess
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from . import __version__
from .models import Collection

ORIGIN = "https://api.github.com"
API_VERSION = "2026-03-10"
WAIT_BUDGET = 30.0


class APIError(Exception):
    """Only fixed, sanitized messages cross this boundary."""


def api_url(value):
    if not isinstance(value, str):
        raise APIError("Rejected an unsafe API URL.")
    try:
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.netloc != "api.github.com"
            or u.username
            or u.password
            or u.fragment
            or "\\" in value
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise APIError("Rejected an unsafe API URL.") from None


def credentials(mode):
    if mode == "none":
        return None, "none"
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key].strip(), key
    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run(
                [gh, "auth", "token", "--hostname", "github.com"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), "existing gh credential"
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
    return None, "unauthenticated"


class GitHub:
    def __init__(self, token=None, *, transport=None, sleep=time.sleep, now=time.time):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"oss-pr-tracker/{__version__} (read-only local CLI)",
        }
        if token:
            if any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise APIError("Existing credential has an invalid format; no request sent.")
            headers["Authorization"] = "Bearer " + token
        self._token = token
        self._client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(15, connect=5),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        self.sleep, self.now = sleep, now
        self.waited = 0.0
        self.counts = {"requests": 0, "errors": 0}
        self.halted = None
        self.rate = {}

    def close(self):
        self._client.close()

    def _scrub(self, value):
        if isinstance(value, str) and self._token:
            return value.replace(self._token, "[credential redacted]")
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        return value

    def _wait(self, seconds):
        if seconds is None or not math.isfinite(seconds) or seconds < 0:
            return False
        if self.waited + seconds > WAIT_BUDGET:
            return False
        self.sleep(seconds)
        self.waited += seconds
        return True

    def _retry_delay(self, response):
        h = response.headers
        delays = []
        if h.get("retry-after"):
            try:
                delays.append(float(h["retry-after"]))
            except ValueError:
                try:
                    delays.append(parsedate_to_datetime(h["retry-after"]).timestamp() - self.now())
                except (ValueError, TypeError, OverflowError):
                    return None
        if h.get("x-ratelimit-remaining") == "0" and h.get("x-ratelimit-reset"):
            try:
                delays.append(float(h["x-ratelimit-reset"]) - self.now() + 1)
            except ValueError:
                return None
        if any(not math.isfinite(d) for d in delays):
            return None
        return max(0.0, max(delays)) if delays else None

    def get(self, url, params=None):
        url = api_url(ORIGIN + url if url.startswith("/") and not url.startswith("//") else url)
        if self.halted:
            raise APIError(self.halted)
        visited = set()
        redirects = 0
        retries = 0
        while True:
            request = self._client.build_request("GET", url, params=params)
            target = api_url(str(request.url))
            if target in visited:
                raise APIError("API redirect loop detected.")
            self.counts["requests"] += 1
            try:
                response = self._client.send(request)
            except (httpx.RequestError, httpx.InvalidURL):
                self.counts["errors"] += 1
                if retries < 2 and self._wait(2**retries):
                    retries += 1
                    continue
                raise APIError(
                    "Network timeout or transport failure after bounded attempts."
                ) from None
            self.rate = {
                k: response.headers[k]
                for k in (
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset",
                    "x-ratelimit-resource",
                )
                if k in response.headers
            }
            if response.status_code in (301, 302, 303, 307, 308):
                if redirects >= 3 or not response.headers.get("location"):
                    self.counts["errors"] += 1
                    raise APIError("API redirect limit or missing location.")
                visited.add(target)
                url = api_url(urljoin(target, response.headers["location"]))
                params = None
                redirects += 1
                continue
            if response.status_code != 200:
                self.counts["errors"] += 1
                # Error bodies are used only to recognize rate limiting, never emitted.
                limited = response.status_code == 429 or (
                    response.status_code == 403
                    and (
                        response.headers.get("x-ratelimit-remaining") == "0"
                        or "retry-after" in response.headers
                        or "rate limit" in response.text[:4096].lower()
                    )
                )
                if limited:
                    delay = self._retry_delay(response)
                    if retries < 2 and self._wait(delay):
                        retries += 1
                        continue
                    self.halted = (
                        "Rate limit: advertised wait exceeds the 30-second scan budget, "
                        "retry budget exhausted, or no usable retry time was supplied."
                    )
                    raise APIError(self.halted)
                if response.status_code == 401:
                    self.halted = (
                        "Authentication rejected (401); existing credential was not replaced."
                    )
                    raise APIError(self.halted)
                if 500 <= response.status_code <= 599 and retries < 2:
                    delay = (
                        self._retry_delay(response)
                        if "retry-after" in response.headers
                        else 2**retries
                    )
                    if self._wait(delay):
                        retries += 1
                        continue
                meanings = {
                    403: "Permission denied",
                    404: "Resource missing or inaccessible",
                    422: "Query or request rejected",
                    410: "API version or resource unavailable",
                }
                raise APIError(
                    f"{meanings.get(response.status_code, 'HTTP failure')} "
                    f"({response.status_code})."
                )
            try:
                return self._scrub(response.json()), response
            except (ValueError, UnicodeError):
                self.counts["errors"] += 1
                raise APIError("Malformed JSON response.") from None

    def collection(self, path, *, key=None, params=None, search=False):
        result = Collection(status="complete")
        params = {"per_page": 100, **(params or {})}
        url, seen, ids = path, set(), set()
        while url:
            try:
                if url in seen:
                    raise APIError("Pagination loop detected.")
                seen.add(url)
                if result.pages >= (10 if search else 1000):
                    raise APIError("Pagination safety limit reached; collection truncated.")
                data, response = self.get(url, params)
                params = None
                if key:
                    if not isinstance(data, dict) or type(data.get("total_count")) is not int:
                        raise APIError("Invalid wrapped collection or missing total_count.")
                    if data["total_count"] < 0:
                        raise APIError("Invalid collection total_count.")
                    if result.total is not None and result.total != data["total_count"]:
                        result.warnings.append("Collection total changed while paginating.")
                    result.total = max(result.total or 0, data["total_count"])
                    items = data.get(key)
                else:
                    items = data
                if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                    raise APIError("Invalid collection response shape.")
                if search:
                    if type(data.get("incomplete_results")) is not bool:
                        raise APIError("Missing search incomplete_results flag.")
                    if data["incomplete_results"]:
                        result.warnings.append("Search returned incomplete_results=true.")
                    if result.total > 1000:
                        result.warnings.append(
                            "Search exceeds 1,000-result cap; narrow closed history."
                        )
                result.pages += 1
                for item in items:
                    if type(item.get("id")) is not int:
                        result.warnings.append("A collection record lacks a stable ID; omitted.")
                    elif item["id"] not in ids:
                        ids.add(item["id"])
                        result.items.append(item)
                next_link = response.links.get("next", {}).get("url")
                if next_link:
                    next_link = api_url(urljoin(str(response.url), next_link))
                url = next_link
            except APIError as exc:
                result.warnings.append(str(exc))
                result.status = "partial" if result.pages else "unavailable"
                break
        if result.total is not None and len(result.items) != result.total:
            result.warnings.append("Fetched unique count differs from the reported total.")
        if result.warnings and result.status == "complete":
            result.status = "partial"
        result.warnings = list(dict.fromkeys(result.warnings))
        return result
