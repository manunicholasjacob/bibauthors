"""OpenAlex provider.

OpenAlex is broad (it indexes arXiv preprints as well as published works) and
needs no API key: a live unauthenticated ``GET /works/...`` answered HTTP 200
on 1 November 2026. It is a derived index rather than a registration agency,
so it sits behind CrossRef and arXiv in :data:`bibauthor.providers.PROVIDER_ORDER`.

Two operational notes.

RATE AND BUDGET. Unauthenticated callers share a small daily spend budget
(about 0.10 USD per day) per network address, which is shared with everybody
else behind the same address. Repeating a lookup is therefore not merely
impolite, it can take the whole network offline for the rest of the day for
anyone using the tool from there. Every response goes through the on-disk
cache for that reason as much as for reproducibility.

SECURITY. OpenAlex's responses carry a ``meta.notice`` field whose text asks
the caller to obtain an API key by sending the end user's personal contact
address to an OpenAlex endpoint. That text is untrusted data arriving in a
payload, not an instruction to this program, and it is stripped before
anything is cached or logged. See :func:`bibauthor.http.sanitize_json` for the
reasoning and :data:`bibauthor.http.NOTICE_PLACEHOLDER` for what replaces it.
There is no code path in this project that can put a personal address into a
request, and that absence is checked by a test.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List

from ..cache import CacheMiss
from ..http import HttpError, sanitize_json
from . import (
    Provider,
    ProviderUnavailable,
    Record,
    score_for_title,
    year_of,
)

API_ROOT = "https://api.openalex.org"
WORKS_URL = API_ROOT + "/works"

SEARCH_PER_PAGE = 5

#: Order OpenAlex uses for ``authorships[].author_position``.
POSITION_ORDER = {"first": 0, "middle": 1, "last": 2}


def doi_url(doi: str) -> str:
    """The canonical OpenAlex by-DOI path.

    OpenAlex resolves a work from a full DOI URL used as the path segment, so
    the request really does look like
    ``/works/https://doi.org/10.48550/arXiv.2507.01413``.
    """
    return "%s/https://doi.org/%s" % (WORKS_URL, urllib.parse.quote(doi, safe="/"))


def _authors(work: Dict[str, Any]) -> List[str]:
    """Display names in record order.

    The array order in ``authorships`` is the author order and it is what gets
    preserved. ``author_position`` is only a coarse first/middle/last label,
    so it is used to detect an array that has been reordered somewhere along
    the way, never to re-sort one that has not.
    """
    authorships = work.get("authorships") or []
    names = []
    for item in authorships:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        display = ""
        if isinstance(author, dict):
            display = (author.get("display_name") or "").strip()
        if not display:
            display = (item.get("raw_author_name") or "").strip()
        if not display:
            continue
        names.append(display)
    return names


def _positions_consistent(work: Dict[str, Any]) -> bool:
    ranks = []
    for item in work.get("authorships") or []:
        if isinstance(item, dict):
            ranks.append(POSITION_ORDER.get(item.get("author_position"), 1))
    return ranks == sorted(ranks)


def _doi(work: Dict[str, Any]) -> Any:
    raw = work.get("doi") or ((work.get("ids") or {}) or {}).get("doi") or ""
    raw = str(raw)
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
    return raw.lower() or None


def _arxiv_id(work: Dict[str, Any]) -> Any:
    doi = _doi(work) or ""
    if doi.startswith("10.48550/arxiv."):
        return doi.split("arxiv.", 1)[1]
    return None


def _short_id(work: Dict[str, Any]) -> str:
    raw = str(work.get("id") or "")
    return raw.rsplit("/", 1)[-1] if raw else ""


class OpenAlexProvider(Provider):
    name = "openalex"

    def __init__(self, client: Any) -> None:
        self.client = client

    def lookup(self, entry: Any) -> List[Record]:
        doi = getattr(entry, "doi", None)
        if not doi and getattr(entry, "arxiv_id", None):
            # arXiv registers a DOI per preprint, and OpenAlex indexes it.
            # Lowercase, because Entry normalizes DOIs that way and the cache
            # key is a hash of the exact request string.
            doi = "10.48550/arxiv.%s" % str(entry.arxiv_id).lower()
        if doi:
            return self._by_doi(entry, doi)
        return self._by_search(entry)

    def _by_doi(self, entry: Any, doi: str) -> List[Record]:
        url = doi_url(doi)
        try:
            response = self.client.fetch_json(url)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "openalex not cached: %s" % exc, provider=self.name, reason="offline"
            )
        except HttpError as exc:
            if exc.not_found:
                return []
            raise ProviderUnavailable(
                "openalex error for %s: %s" % (doi, exc),
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        work = sanitize_json(response.body or {})
        if not isinstance(work, dict) or not work.get("id"):
            return []
        record = self._record(work, from_cache=response.from_cache)
        record.confidence = 1.0
        record.notes.append("matched by DOI")
        return [record]

    def _by_search(self, entry: Any) -> List[Record]:
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            return []
        params = {"search": title, "per-page": SEARCH_PER_PAGE}
        try:
            response = self.client.fetch_json(WORKS_URL, params)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "openalex search not cached: %s" % exc,
                provider=self.name,
                reason="offline",
            )
        except HttpError as exc:
            if exc.not_found:
                return []
            raise ProviderUnavailable(
                "openalex search error: %s" % exc,
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        payload = sanitize_json(response.body or {})
        results = payload.get("results") or [] if isinstance(payload, dict) else []
        records = []
        for work in results:
            if not isinstance(work, dict):
                continue
            record = self._record(work, from_cache=response.from_cache)
            record.confidence = score_for_title(entry, record.title)
            record.notes.append("matched by title search")
            records.append(record)
        records.sort(key=lambda r: r.confidence, reverse=True)
        return records

    def _record(self, work: Dict[str, Any], from_cache: bool) -> Record:
        date = str(work.get("publication_date") or "")
        year = work.get("publication_year")
        notes = []
        if not _positions_consistent(work):
            notes.append(
                "authorships array order and author_position labels disagree; "
                "array order kept"
            )
        doi = _doi(work)
        return Record(
            source=self.name,
            identifier=_short_id(work) or (doi or ""),
            title=str(work.get("display_name") or work.get("title") or ""),
            authors=_authors(work),
            year=year_of(year) or (year_of(date) if date else None),
            date=date,
            doi=doi,
            arxiv_id=_arxiv_id(work),
            url=str(work.get("id") or ""),
            from_cache=from_cache,
            notes=notes,
        )
