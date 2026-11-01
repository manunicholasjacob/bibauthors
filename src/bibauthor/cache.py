"""On-disk HTTP response cache, and the offline mode that makes CI honest.

Every provider response this tool ever sees arrives through :class:`Cache`.
That has two consequences worth stating up front.

First, reproducibility. A bibliography checker that talks to four live APIs
cannot be tested, because the APIs change under you and because a test suite
that hammers a free public service is rude. So the library can run with
``offline=True``, in which case a cache miss is a hard error and nothing is
fetched. The test suite runs that way against recorded fixtures, which means a
failing test is a real regression rather than someone else's outage.

Second, politeness. OpenAlex serves unauthenticated clients out of a small
shared daily budget per network address, and Semantic Scholar rate-limits
hard. Serving a repeated lookup from disk is the difference between a tool
that can be run in a loop and one that gets blocked.

Cache entry files are named by a hash of the request and written with
``ensure_ascii=True, sort_keys=True, indent=1`` so that they stay pure ASCII
and produce readable diffs when they are committed as fixtures.
"""

from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
from typing import Any, Dict, Optional


class CacheMiss(Exception):
    """Raised when a request is not in the cache.

    In offline mode this propagates out of the HTTP client and the providers
    turn it into :class:`bibauthor.providers.ProviderUnavailable`, which the
    verifier reports as "could not ask" rather than "not found". The
    distinction matters: a missing record is weak evidence about a citation,
    while a missing cache entry is no evidence at all.
    """


#: Timestamp format used for ``recorded_at``. Second resolution, UTC, with the
#: literal "Z" suffix, so the value sorts lexicographically and stays ASCII.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_stamp() -> str:
    """Current UTC time as an ISO 8601 second-resolution string."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime(TIMESTAMP_FORMAT)


def canonical_request(url: str, params: Optional[Dict[str, Any]]) -> str:
    """The string that gets hashed into a cache key.

    ``"GET|<url>|<k>=<v>&<k>=<v>"`` with the parameters sorted by name, so the
    key does not depend on dict ordering. Only GET is ever cached, and only
    GET is ever issued: this tool has no code path that writes to any API.
    """
    pairs = []
    for key in sorted((params or {}).keys()):
        pairs.append("%s=%s" % (key, (params or {})[key]))
    return "GET|%s|%s" % (url, "&".join(pairs))


class Cache(object):
    """A flat directory of JSON files, one per request.

    Parameters
    ----------
    directory:
        Where the entry files live. Created on first write unless offline.
    offline:
        When true, :meth:`get` still reads the directory but :meth:`put`
        becomes a no-op and nothing is ever created. A miss raises
        :class:`CacheMiss`.
    ttl_days:
        When None (the default) entries never expire and ``recorded_at`` is
        purely informational. Committed fixtures are meant to be read years
        after they were recorded, so ignoring the timestamp has to be the
        default rather than an option someone remembers to set.
    """

    def __init__(
        self,
        directory: str,
        offline: bool = False,
        ttl_days: Optional[int] = None,
    ) -> None:
        self.directory = str(directory)
        self.offline = bool(offline)
        self.ttl_days = ttl_days

    # -- keys and paths ---------------------------------------------------

    def key(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """sha256 hex digest of the canonical request string."""
        canonical = canonical_request(url, params)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def path_for(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Absolute-or-relative path of the entry file for this request."""
        return os.path.join(self.directory, self.key(url, params) + ".json")

    # -- reading ----------------------------------------------------------

    def get(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return the stored entry dict, or raise :class:`CacheMiss`.

        The returned dict has the keys ``url``, ``params``, ``status``,
        ``recorded_at``, ``content_type`` and ``body``. A stored non-200
        status is a legitimate cache entry: the 404 that CrossRef returns for
        an arXiv DOI is a fact worth recording, not an error to discard.
        """
        path = self.path_for(url, params)
        try:
            with open(path, "r", encoding="ascii") as handle:
                entry = json.load(handle)
        except IOError as exc:
            if exc.errno in (errno.ENOENT, errno.EISDIR, errno.ENOTDIR):
                raise CacheMiss("not cached: %s" % url)
            raise
        except ValueError as exc:
            raise CacheMiss("corrupt cache entry for %s: %s" % (url, exc))
        if not isinstance(entry, dict) or "body" not in entry:
            raise CacheMiss("malformed cache entry for %s" % url)
        if self.is_expired(entry):
            raise CacheMiss("cache entry expired for %s" % url)
        return entry

    def has(self, url: str, params: Optional[Dict[str, Any]] = None) -> bool:
        """True when :meth:`get` would succeed."""
        try:
            self.get(url, params)
        except CacheMiss:
            return False
        return True

    def is_expired(self, entry: Dict[str, Any]) -> bool:
        """TTL check. Always false when ``ttl_days`` is None."""
        if self.ttl_days is None:
            return False
        stamp = entry.get("recorded_at")
        if not stamp:
            # No timestamp and a TTL in force: treat as too old to trust.
            return True
        try:
            recorded = datetime.datetime.strptime(stamp, TIMESTAMP_FORMAT)
        except (TypeError, ValueError):
            return True
        recorded = recorded.replace(tzinfo=datetime.timezone.utc)
        age = datetime.datetime.now(datetime.timezone.utc) - recorded
        return age > datetime.timedelta(days=self.ttl_days)

    # -- writing ----------------------------------------------------------

    def put(
        self,
        url: str,
        params: Optional[Dict[str, Any]],
        status: int,
        body: Any,
        content_type: str,
    ) -> None:
        """Store one response. A no-op in offline mode.

        ``body`` is a parsed JSON value for JSON responses and a plain string
        for XML (arXiv). Strings are stored as JSON strings, so
        ``ensure_ascii=True`` escapes any non-ASCII character in the XML and
        the file on disk stays pure ASCII.
        """
        if self.offline:
            return
        entry = {
            "url": url,
            "params": dict(params or {}),
            "status": int(status),
            "recorded_at": utc_now_stamp(),
            "content_type": content_type or "",
            "body": body,
        }
        self.write_entry(self.path_for(url, params), entry)

    def write_entry(self, path: str, entry: Dict[str, Any]) -> None:
        """Serialize one entry to ``path`` in the committed fixture format."""
        if self.offline:
            return
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="ascii", newline="\n") as handle:
            json.dump(entry, handle, ensure_ascii=True, sort_keys=True, indent=1)
            handle.write("\n")
        os.replace(temporary, path)
