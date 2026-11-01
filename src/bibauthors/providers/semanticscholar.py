"""Semantic Scholar provider. Secondary source, deliberately.

Two reasons it ranks last and gets its confidence discounted.

RATE. The unauthenticated graph endpoint rate-limits hard and answers
anything resembling a parallel call with 429. Every request to it is
serialized behind a per-host lock with a 3 second floor, both enforced in
:mod:`bibauthors.http`.

FIDELITY. Semantic Scholar abbreviates given names. VERIFIED: it reports
``"J. V\u00e1zquez"`` where arXiv reports ``"Juan J. Vazquez"`` for the same
author of the same paper. That is exactly the variant the name matcher exists
to absorb, so it is not normalized here: a provider that quietly expanded
initials would be inventing data, and it would hide from the matcher the one
case it most needs to see. Instead the record is marked as coming from a
derived index and its confidence is multiplied by
:data:`SECONDARY_CONFIDENCE`, so that when two providers disagree about a
paper's identity the primary one wins the tie.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List

from ..cache import CacheMiss
from ..http import HttpError
from . import Provider, ProviderUnavailable, Record, score_for_title, year_of

API_ROOT = "https://api.semanticscholar.org/graph/v1"
PAPER_URL = API_ROOT + "/paper"
SEARCH_URL = PAPER_URL + "/search"

FIELDS = "title,year,authors,externalIds,publicationDate"

SEARCH_LIMIT = 5

#: Multiplier applied to every confidence from this provider.
SECONDARY_CONFIDENCE = 0.95


def _authors(paper: Dict[str, Any]) -> List[str]:
    out = []
    for author in paper.get("authors") or []:
        if isinstance(author, dict):
            name = (author.get("name") or "").strip()
        else:
            name = str(author or "").strip()
        if name:
            out.append(name)
    return out


def _external(paper: Dict[str, Any], key: str) -> Any:
    ids = paper.get("externalIds") or {}
    if not isinstance(ids, dict):
        return None
    value = ids.get(key)
    return str(value) if value else None


class SemanticScholarProvider(Provider):
    name = "semanticscholar"

    def __init__(self, client: Any) -> None:
        self.client = client

    def lookup(self, entry: Any) -> List[Record]:
        doi = getattr(entry, "doi", None)
        arxiv_id = getattr(entry, "arxiv_id", None)
        if doi:
            return self._by_identifier(entry, "DOI:%s" % doi, "DOI")
        if arxiv_id:
            return self._by_identifier(entry, "arXiv:%s" % arxiv_id, "arXiv id")
        return self._by_search(entry)

    def _by_identifier(self, entry: Any, token: str, label: str) -> List[Record]:
        url = "%s/%s" % (PAPER_URL, urllib.parse.quote(token, safe=":/."))
        params = {"fields": FIELDS}
        try:
            response = self.client.fetch_json(url, params)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "semanticscholar not cached: %s" % exc,
                provider=self.name,
                reason="offline",
            )
        except HttpError as exc:
            if exc.not_found:
                return []
            raise ProviderUnavailable(
                "semanticscholar error for %s: %s" % (token, exc),
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        paper = response.body or {}
        if not isinstance(paper, dict) or not paper.get("paperId"):
            return []
        record = self._record(paper, from_cache=response.from_cache)
        record.confidence = 1.0 * SECONDARY_CONFIDENCE
        record.notes.append("matched by %s (secondary source)" % label)
        return [record]

    def _by_search(self, entry: Any) -> List[Record]:
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            return []
        params = {"query": title, "fields": FIELDS, "limit": SEARCH_LIMIT}
        try:
            response = self.client.fetch_json(SEARCH_URL, params)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "semanticscholar search not cached: %s" % exc,
                provider=self.name,
                reason="offline",
            )
        except HttpError as exc:
            if exc.not_found:
                return []
            raise ProviderUnavailable(
                "semanticscholar search error: %s" % exc,
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        payload = response.body or {}
        papers = payload.get("data") or [] if isinstance(payload, dict) else []
        records = []
        for paper in papers:
            if not isinstance(paper, dict):
                continue
            record = self._record(paper, from_cache=response.from_cache)
            record.confidence = score_for_title(entry, record.title)
            record.confidence = record.confidence * SECONDARY_CONFIDENCE
            record.notes.append("matched by title search (secondary source)")
            records.append(record)
        records.sort(key=lambda r: r.confidence, reverse=True)
        return records

    def _record(self, paper: Dict[str, Any], from_cache: bool) -> Record:
        doi = _external(paper, "DOI")
        arxiv_id = _external(paper, "ArXiv")
        date = str(paper.get("publicationDate") or "")
        paper_id = str(paper.get("paperId") or "")
        return Record(
            source=self.name,
            identifier=paper_id,
            title=str(paper.get("title") or ""),
            authors=_authors(paper),
            year=year_of(paper.get("year")) or (year_of(date) if date else None),
            date=date,
            doi=doi.lower() if doi else None,
            arxiv_id=arxiv_id,
            url=(
                "https://www.semanticscholar.org/paper/%s" % paper_id
                if paper_id
                else ""
            ),
            from_cache=from_cache,
            notes=["given names may be abbreviated by this source"],
        )
