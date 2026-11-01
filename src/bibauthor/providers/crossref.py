"""CrossRef provider.

CrossRef is the registration agency for most journal and conference DOIs, so
for a published work it is the closest thing to a primary source this tool can
reach: the author list there was deposited by the publisher.

The important subtlety is what a 404 means. See
:data:`FOREIGN_REGISTRY_PREFIXES`.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from ..cache import CacheMiss
from ..http import HttpError
from . import (
    Provider,
    ProviderUnavailable,
    Record,
    first_author_surname,
    iso_date,
    score_for_title,
    year_of,
)

API_ROOT = "https://api.crossref.org"
WORKS_URL = API_ROOT + "/works"

#: Fields requested for search results. Asking for a subset keeps the
#: responses small enough to commit as fixtures and keeps CrossRef's load
#: down, which is the other half of being a polite client.
SEARCH_SELECT = "DOI,title,author,issued,score,container-title"

SEARCH_ROWS = 5

#: DOI prefixes registered with an agency other than CrossRef. The 10.48550
#: prefix belongs to arXiv, whose DOIs are registered through DataCite, so
#: CrossRef has never heard of them.
#:
#: VERIFIED: a live ``GET /works/10.48550/arXiv.2507.01413`` answers HTTP 404
#: with the body ``Resource not found.``
#:
#: This matters more than it looks. The tool exists to flag citations whose
#: author list cannot be confirmed, and "CrossRef has no record of this DOI"
#: looks exactly like that signal while being nothing of the kind. Asking
#: CrossRef about an arXiv DOI is asking the wrong registry, so the answer is
#: discarded as uninformative rather than reported: the provider raises
#: :class:`ProviderUnavailable`, which the verifier reads as "could not ask",
#: not as "not found". The arXiv and OpenAlex providers do know these DOIs.
FOREIGN_REGISTRY_PREFIXES = {
    "10.48550/": "arXiv (registered via DataCite, not CrossRef)",
}

WRONG_REGISTRY_REASON = "wrong-registry"


def foreign_registry(doi: str) -> Optional[str]:
    """Name the registry that owns ``doi`` when it is not CrossRef."""
    lowered = (doi or "").lower()
    for prefix, agency in FOREIGN_REGISTRY_PREFIXES.items():
        if lowered.startswith(prefix):
            return agency
    return None


def _author_display(author: Dict[str, Any]) -> str:
    """Build one display string from a CrossRef author object.

    CrossRef gives either ``{"given": ..., "family": ...}`` for a person or
    ``{"name": ...}`` for a group (a collaboration, consortium or committee).
    Person names become ``"family, given"``; group names are kept exactly as
    deposited, because a consortium name is not a surname and splitting it
    would be a lie the name matcher then has to unpick.
    """
    family = (author.get("family") or "").strip()
    given = (author.get("given") or "").strip()
    name = (author.get("name") or "").strip()
    if family and given:
        return "%s, %s" % (family, given)
    if family:
        return family
    if name:
        return name
    return (author.get("literal") or "").strip()


def _authors(work: Dict[str, Any]) -> List[str]:
    out = []
    for author in work.get("author") or []:
        if not isinstance(author, dict):
            continue
        display = _author_display(author)
        if display:
            out.append(display)
    return out


def _title(work: Dict[str, Any]) -> str:
    titles = work.get("title") or []
    if isinstance(titles, str):
        return titles
    for item in titles:
        if item:
            return str(item)
    return ""


def _container(work: Dict[str, Any]) -> str:
    titles = work.get("container-title") or []
    if isinstance(titles, str):
        return titles
    for item in titles:
        if item:
            return str(item)
    return ""


def _issued_date(work: Dict[str, Any]) -> str:
    issued = work.get("issued") or {}
    parts = issued.get("date-parts") or []
    if parts and isinstance(parts[0], list):
        return iso_date(parts[0])
    return ""


class CrossRefProvider(Provider):
    name = "crossref"

    def __init__(self, client: Any) -> None:
        self.client = client

    # -- the contract -----------------------------------------------------

    def lookup(self, entry: Any) -> List[Record]:
        doi = getattr(entry, "doi", None)
        if doi:
            return self._by_doi(entry, doi)
        return self._by_search(entry)

    # -- identifier path --------------------------------------------------

    def _by_doi(self, entry: Any, doi: str) -> List[Record]:
        agency = foreign_registry(doi)
        url = "%s/%s" % (WORKS_URL, urllib.parse.quote(doi, safe="/"))
        try:
            response = self.client.fetch_json(url)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "crossref not cached: %s" % exc, provider=self.name, reason="offline"
            )
        except HttpError as exc:
            if exc.not_found:
                if agency is not None:
                    # Not evidence of anything: see FOREIGN_REGISTRY_PREFIXES.
                    raise ProviderUnavailable(
                        "DOI %s belongs to %s, so a CrossRef 404 is the wrong "
                        "registry answering and is not evidence about this "
                        "citation" % (doi, agency),
                        provider=self.name,
                        reason=WRONG_REGISTRY_REASON,
                    )
                return []
            raise ProviderUnavailable(
                "crossref error for %s: %s" % (doi, exc),
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        work = (response.body or {}).get("message") or {}
        if not work:
            return []
        record = self._record(work, from_cache=response.from_cache)
        # An identifier hit is an identity, not a guess.
        record.confidence = 1.0
        record.notes.append("matched by DOI")
        return [record]

    # -- search path ------------------------------------------------------

    def _by_search(self, entry: Any) -> List[Record]:
        query = self.query_for(entry)
        if not query:
            return []
        params = {
            "query.bibliographic": query,
            "rows": SEARCH_ROWS,
            "select": SEARCH_SELECT,
        }
        try:
            response = self.client.fetch_json(WORKS_URL, params)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "crossref search not cached: %s" % exc,
                provider=self.name,
                reason="offline",
            )
        except HttpError as exc:
            if exc.not_found:
                return []
            raise ProviderUnavailable(
                "crossref search error: %s" % exc,
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        items = ((response.body or {}).get("message") or {}).get("items") or []
        records = []
        for work in items:
            if not isinstance(work, dict):
                continue
            record = self._record(work, from_cache=response.from_cache)
            record.confidence = score_for_title(entry, record.title)
            record.notes.append("matched by bibliographic search")
            records.append(record)
        records.sort(key=lambda r: r.confidence, reverse=True)
        return records

    @staticmethod
    def query_for(entry: Any) -> str:
        """Title plus first-author surname plus year, which is what
        ``query.bibliographic`` is designed to receive."""
        bits = []
        title = (getattr(entry, "title", "") or "").strip()
        if title:
            bits.append(title)
        surname = first_author_surname(entry)
        if surname:
            bits.append(surname)
        year = getattr(entry, "year", None)
        if year:
            bits.append(str(year))
        return " ".join(bits).strip()

    # -- shared parsing ---------------------------------------------------

    def _record(self, work: Dict[str, Any], from_cache: bool) -> Record:
        doi = (work.get("DOI") or "").lower() or None
        date = _issued_date(work)
        notes = []
        container = _container(work)
        if container:
            notes.append("container: %s" % container)
        return Record(
            source=self.name,
            identifier=doi or "",
            title=_title(work),
            authors=_authors(work),
            year=year_of(date) if date else None,
            date=date,
            doi=doi,
            url=("https://doi.org/%s" % doi) if doi else "",
            from_cache=from_cache,
            notes=notes,
        )
