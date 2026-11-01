"""arXiv provider.

arXiv is the authoritative source for a preprint's author list, and it is the
only provider here that can distinguish the author list of v1 from the author
list of the version you are reading. That distinction is a real finding: a
citation can name the v1 authors of a paper that gained authors by v3, or cite
a 2024 date for a work whose current version is 2025, and neither is
fabrication. The tool has to be able to say so.

HTTPS ONLY. Plain ``http://export.arxiv.org/api/query`` returned an empty body
in live testing, with no error and no redirect, which fails as a silent
"nothing found". The URL below is https and must stay https.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional

from ..cache import CacheMiss
from ..http import HttpError
from . import Provider, ProviderUnavailable, Record, score_for_title, year_of

QUERY_URL = "https://export.arxiv.org/api/query"

ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

SEARCH_MAX_RESULTS = 5

_ATOM = "{%s}" % ATOM_NS
_ARXIV = "{%s}" % ARXIV_NS

#: ``http://arxiv.org/abs/2412.15115v2`` -> ("2412.15115", "2")
_ABS_ID = re.compile(
    r"arxiv\.org/abs/([a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v(\d+))?",
    re.I,
)

_ERROR_ID = "arxiv.org/api/errors"


def _text(node: Optional[ElementTree.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return re.sub(r"\s+", " ", node.text).strip()


def split_arxiv_id(raw: str) -> Dict[str, Any]:
    """Pull the bare id and the version number out of an arXiv id URL."""
    found = _ABS_ID.search(raw or "")
    if not found:
        return {"id": "", "version": None}
    version = found.group(2)
    return {
        "id": found.group(1),
        "version": int(version) if version else None,
    }


class ArxivProvider(Provider):
    name = "arxiv"

    def __init__(self, client: Any) -> None:
        self.client = client

    def lookup(self, entry: Any) -> List[Record]:
        arxiv_id = getattr(entry, "arxiv_id", None)
        if arxiv_id:
            return self._by_id(entry, arxiv_id)
        return self._by_search(entry)

    # -- identifier path --------------------------------------------------

    def _by_id(self, entry: Any, arxiv_id: str) -> List[Record]:
        params = {"id_list": arxiv_id}
        text = self._fetch(params, "arxiv id %s" % arxiv_id)
        records = self._parse(text["body"], entry, from_cache=text["from_cache"])
        for record in records:
            record.confidence = 1.0
            record.notes.append("matched by arXiv id")
        return records

    # -- search path ------------------------------------------------------

    def _by_search(self, entry: Any) -> List[Record]:
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            return []
        params = {
            "search_query": 'all:"%s"' % title.replace('"', " "),
            "max_results": SEARCH_MAX_RESULTS,
        }
        text = self._fetch(params, "arxiv search")
        records = self._parse(text["body"], entry, from_cache=text["from_cache"])
        for record in records:
            record.confidence = score_for_title(entry, record.title)
            record.notes.append("matched by title search")
        records.sort(key=lambda r: r.confidence, reverse=True)
        return records

    # -- transport --------------------------------------------------------

    def _fetch(self, params: Dict[str, Any], what: str) -> Dict[str, Any]:
        try:
            response = self.client.fetch_text(QUERY_URL, params)
        except CacheMiss as exc:
            raise ProviderUnavailable(
                "arxiv not cached (%s): %s" % (what, exc),
                provider=self.name,
                reason="offline",
            )
        except HttpError as exc:
            if exc.not_found:
                return {"body": "", "from_cache": bool(exc.from_cache)}
            raise ProviderUnavailable(
                "arxiv error (%s): %s" % (what, exc),
                provider=self.name,
                reason="http-%s" % exc.status,
            )
        return {"body": response.body or "", "from_cache": response.from_cache}

    # -- parsing ----------------------------------------------------------

    def _parse(self, xml_text: str, entry: Any, from_cache: bool) -> List[Record]:
        if not (xml_text or "").strip():
            # An empty body is what plain http returns. Treat it as "could not
            # ask" rather than "no such paper", because the two answers look
            # identical and only one of them is safe to report.
            raise ProviderUnavailable(
                "arxiv returned an empty body",
                provider=self.name,
                reason="empty-body",
            )
        try:
            feed = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise ProviderUnavailable(
                "arxiv response was not parseable XML: %s" % exc,
                provider=self.name,
                reason="bad-xml",
            )
        records = []
        for node in feed.findall(_ATOM + "entry"):
            identifier = _text(node.find(_ATOM + "id"))
            if _ERROR_ID in identifier:
                # arXiv reports a bad or unknown id as a single error entry.
                return []
            record = self._record(node, from_cache=from_cache)
            if record is not None:
                records.append(record)
        return records

    def _record(self, node: ElementTree.Element, from_cache: bool) -> Optional[Record]:
        id_url = _text(node.find(_ATOM + "id"))
        split = split_arxiv_id(id_url)
        bare = split["id"]
        version = split["version"]

        # <published> is the v1 timestamp and never changes.
        # <updated> is the timestamp of the latest version in the response.
        published = _text(node.find(_ATOM + "published"))
        updated = _text(node.find(_ATOM + "updated"))

        authors = []
        for author in node.findall(_ATOM + "author"):
            display = _text(author.find(_ATOM + "name"))
            if display:
                authors.append(display)

        doi = _text(node.find(_ARXIV + "doi")) or None
        if doi:
            doi = doi.lower()

        notes = []
        if version:
            notes.append("arXiv version v%d" % version)
        if published and updated and published[:10] != updated[:10]:
            notes.append(
                "v1 dated %s, this version dated %s" % (published[:10], updated[:10])
            )
        journal_ref = _text(node.find(_ARXIV + "journal_ref"))
        if journal_ref:
            notes.append("journal_ref: %s" % journal_ref)
        primary = node.find(_ARXIV + "primary_category")
        if primary is not None and primary.get("term"):
            notes.append("primary_category: %s" % primary.get("term"))

        title = _text(node.find(_ATOM + "title"))
        if not bare and not title:
            return None

        return Record(
            source=self.name,
            identifier=("%sv%d" % (bare, version)) if (bare and version) else bare,
            title=title,
            authors=authors,
            year=year_of(updated[:4]) if updated else None,
            date=updated[:10] if updated else "",
            first_version_year=year_of(published[:4]) if published else None,
            first_version_date=published[:10] if published else "",
            doi=doi,
            arxiv_id=bare or None,
            url=id_url,
            from_cache=from_cache,
            notes=notes,
        )
