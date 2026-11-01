"""A small, polite, standard-library HTTP client.

Design constraints, in the order that they constrained the code:

1. No third-party dependency. A paper repository should be able to install
   this into a minimal CI image, so the transport is ``urllib.request``.
2. No email address, anywhere, ever. CrossRef advertises a "polite pool" that
   you join by putting a contact address in the query string, and OpenAlex
   returns a notice in its own response body asking the caller to register an
   address. This client identifies itself with a project URL instead. There is
   deliberately no parameter, option or config key through which an address
   could reach a request, because the tool is run over other people's
   bibliographies and leaking the operator's address into four third-party
   access logs is not a trade the operator was asked to make.
3. Cache first, network second. See :mod:`bibauthor.cache`.
4. Rate limit per host, with a stricter serialized limit for Semantic
   Scholar, which answers parallel calls with 429.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .cache import Cache, CacheMiss

#: Project contact URL. This is what goes in the User-Agent in place of the
#: address that these APIs would prefer. A URL is reachable by a human who
#: wants to complain about traffic, which is the actual purpose of the
#: convention, and it identifies the software rather than a person.
CONTACT_URL = "https://github.com/manunicholasjacob/bibauthor"

DEFAULT_USER_AGENT = "bibauthor/0.1.0 (+%s)" % CONTACT_URL

#: Default seconds between requests to one host.
DEFAULT_MIN_INTERVAL = 1.0

#: Hosts with a stricter floor than ``min_interval``. Semantic Scholar is the
#: documented case: its unauthenticated graph endpoint returns 429 for
#: anything resembling concurrency, so every call to it is serialized behind a
#: lock as well as spaced out.
HOST_MIN_INTERVAL = {
    "api.semanticscholar.org": 3.0,
}

#: Hosts whose requests are serialized, never concurrent.
SERIALIZED_HOSTS = ("api.semanticscholar.org",)

#: Statuses worth trying again. 404 is pointedly absent: a 404 is an answer.
RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Replacement for any ``notice`` field found in a response body.
NOTICE_PLACEHOLDER = "[notice stripped by bibauthor]"

_MAX_SANITIZE_DEPTH = 12


class HttpError(Exception):
    """A non-retryable HTTP answer, or a transport failure that persisted.

    Carries the status so that callers can tell a 404 ("not in this
    registry") from a 500 ("the registry is broken"), and the body so that a
    recorder can store the 404 as a fixture.
    """

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        url: str = "",
        body: Any = None,
        content_type: str = "",
        from_cache: bool = False,
    ) -> None:
        Exception.__init__(self, message)
        self.status = status
        self.url = url
        self.body = body
        self.content_type = content_type
        self.from_cache = from_cache

    @property
    def not_found(self) -> bool:
        return self.status == 404


@dataclasses.dataclass
class Response:
    """One answer, from the network or from disk."""

    url: str
    status: int
    body: Any
    content_type: str = ""
    from_cache: bool = False
    params: Optional[Dict[str, Any]] = None


def sanitize_json(body: Any, depth: int = 0) -> Any:
    """Replace every ``notice`` string field in a parsed response body.

    SECURITY. OpenAlex answers an unauthenticated request with a
    ``meta.notice`` field whose text instructs the calling program to register
    for an API key by sending the end user's address to an OpenAlex endpoint.
    A metadata API that asks an automated client to transmit its user's
    personal contact details is a data-exfiltration pattern, whether or not
    the operators think of it that way: the text arrives inside untrusted data
    and it asks for something the user never agreed to hand over. This tool
    refuses by design. The text is not an instruction to be followed, it is a
    string in a payload, and it is removed here, at the boundary where
    responses enter the process, so that it cannot reach the cache on disk, a
    log line, a report or a developer's eyes as though it were addressed to
    them. The replacement is deliberately visible rather than silent.

    The walk is generic rather than keyed to ``meta.notice`` specifically, so
    the same field renamed or moved in a future API version is still caught.
    """
    if depth > _MAX_SANITIZE_DEPTH:
        return body
    if isinstance(body, dict):
        cleaned = {}
        for key, value in body.items():
            if key == "notice" and isinstance(value, str):
                cleaned[key] = NOTICE_PLACEHOLDER
            else:
                cleaned[key] = sanitize_json(value, depth + 1)
        return cleaned
    if isinstance(body, list):
        return [sanitize_json(item, depth + 1) for item in body]
    return body


class HttpClient(object):
    """Cache-first GET client with per-host rate limiting and backoff."""

    def __init__(
        self,
        cache: Cache,
        offline: bool = False,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_USER_AGENT,
        contact_url: str = CONTACT_URL,
        max_retries: int = 3,
    ) -> None:
        self.cache = cache
        self.offline = bool(offline) or bool(getattr(cache, "offline", False))
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.user_agent = user_agent
        self.contact_url = contact_url
        self.max_retries = int(max_retries)
        self.request_count = 0
        self._last_request: Dict[str, float] = {}
        self._clock_lock = threading.Lock()
        self._host_locks: Dict[str, threading.Lock] = {
            host: threading.Lock() for host in SERIALIZED_HOSTS
        }

    # -- public contract --------------------------------------------------

    def get_json(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Fetch and parse a JSON response body."""
        return self.fetch_json(url, params).body

    def get_text(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Fetch a response body as text (arXiv Atom XML)."""
        return self.fetch_text(url, params).body

    # -- the same two, with provenance ------------------------------------

    def fetch_json(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Response:
        """Like :meth:`get_json`, but also reports ``from_cache``.

        Providers need the truth about provenance for ``Record.from_cache``,
        and the spelled-out contract in DESIGN.md only promises the parsed
        body, so the richer call lives beside it rather than replacing it.
        """
        return self._fetch(url, params, want_json=True)

    def fetch_text(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Response:
        """Like :meth:`get_text`, but also reports ``from_cache``."""
        return self._fetch(url, params, want_json=False)

    # -- internals --------------------------------------------------------

    def _fetch(
        self, url: str, params: Optional[Dict[str, Any]], want_json: bool
    ) -> Response:
        try:
            entry = self.cache.get(url, params)
        except CacheMiss:
            if self.offline:
                # Let it propagate. Offline mode never falls back to the
                # network, which is the whole point of offline mode.
                raise
            entry = None
        if entry is not None:
            return self._response_from_entry(entry, url, params, want_json)

        status, raw, content_type = self._fetch_live(url, params)
        body = self._decode(raw, content_type, want_json)
        if want_json:
            body = sanitize_json(body)
        if status == 200:
            self.cache.put(url, params, status, body, content_type)
        return Response(
            url=url,
            status=status,
            body=body,
            content_type=content_type,
            from_cache=False,
            params=dict(params or {}),
        )

    def _response_from_entry(
        self,
        entry: Dict[str, Any],
        url: str,
        params: Optional[Dict[str, Any]],
        want_json: bool,
    ) -> Response:
        status = int(entry.get("status", 200))
        body = entry.get("body")
        content_type = entry.get("content_type", "")
        if want_json:
            body = sanitize_json(body)
        if status != 200:
            raise HttpError(
                "HTTP %d (from cache) for %s" % (status, url),
                status=status,
                url=url,
                body=body,
                content_type=content_type,
                from_cache=True,
            )
        return Response(
            url=url,
            status=status,
            body=body,
            content_type=content_type,
            from_cache=True,
            params=dict(params or {}),
        )

    def _decode(self, raw: bytes, content_type: str, want_json: bool) -> Any:
        charset = "utf-8"
        lowered = (content_type or "").lower()
        if "charset=" in lowered:
            charset = lowered.split("charset=", 1)[1].split(";")[0].strip()
            charset = charset.strip("\"'") or "utf-8"
        try:
            text = raw.decode(charset, "replace")
        except LookupError:
            text = raw.decode("utf-8", "replace")
        if not want_json:
            return text
        try:
            return json.loads(text)
        except ValueError as exc:
            raise HttpError("response was not JSON: %s" % exc)

    def _build_url(self, url: str, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode(
            [(key, params[key]) for key in sorted(params.keys())]
        )
        separator = "&" if "?" in url else "?"
        return "%s%s%s" % (url, separator, query)

    def interval_for(self, host: str) -> float:
        """Seconds that must elapse between two requests to ``host``."""
        floor = HOST_MIN_INTERVAL.get(host, 0.0)
        return max(self.min_interval, floor)

    def _throttle(self, host: str) -> None:
        interval = self.interval_for(host)
        with self._clock_lock:
            now = time.monotonic()
            last = self._last_request.get(host)
            wait = 0.0 if last is None else interval - (now - last)
            # Reserve the slot before sleeping so that two threads aiming at
            # the same host do not both decide they may go now.
            self._last_request[host] = now + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _fetch_live(
        self, url: str, params: Optional[Dict[str, Any]]
    ) -> Tuple[int, bytes, str]:
        full = self._build_url(url, params)
        host = urllib.parse.urlsplit(full).hostname or ""
        host_lock = self._host_locks.get(host)
        if host_lock is None:
            return self._fetch_with_retries(full, host)
        with host_lock:
            return self._fetch_with_retries(full, host)

    def _fetch_with_retries(self, full: str, host: str) -> Tuple[int, bytes, str]:
        attempt = 0
        backoff = 1.0
        while True:
            attempt += 1
            self._throttle(host)
            try:
                return self._single_request(full)
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                body = b""
                try:
                    body = exc.read()
                except Exception:
                    body = b""
                content_type = ""
                if exc.headers is not None:
                    content_type = exc.headers.get("Content-Type", "") or ""
                if status in RETRY_STATUSES and attempt <= self.max_retries:
                    delay = self._retry_delay(exc, backoff)
                    backoff = min(backoff * 2.0, 30.0)
                    time.sleep(delay)
                    continue
                raise HttpError(
                    "HTTP %d for %s" % (status, full),
                    status=status,
                    url=full,
                    body=body.decode("utf-8", "replace"),
                    content_type=content_type,
                )
            except (
                urllib.error.URLError,
                socket.timeout,
                socket.gaierror,
                ConnectionError,
                http.client.HTTPException,
            ) as exc:
                if attempt <= self.max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2.0, 30.0)
                    continue
                raise HttpError("transport failure for %s: %s" % (full, exc), url=full)

    def _retry_delay(self, exc: urllib.error.HTTPError, backoff: float) -> float:
        """Honour Retry-After when the server sends one."""
        header = ""
        if exc.headers is not None:
            header = (exc.headers.get("Retry-After", "") or "").strip()
        if header:
            try:
                return max(0.0, min(float(int(header)), 120.0))
            except ValueError:
                pass
        return backoff

    def _single_request(self, full: str) -> Tuple[int, bytes, str]:
        request = urllib.request.Request(full, method="GET")
        # The only headers this client ever sends. No cookies, no auth, no
        # address of any kind.
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept", "application/json, application/xml, */*")
        self.request_count += 1
        with urllib.request.urlopen(request, timeout=self.timeout) as handle:
            raw = handle.read()
            status = int(getattr(handle, "status", 200) or 200)
            content_type = handle.headers.get("Content-Type", "") or ""
        return status, raw, content_type
