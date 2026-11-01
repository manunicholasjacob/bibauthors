"""Provider protocol, the shared :class:`Record`, and the registry.

A provider turns an :class:`bibauthor.entry.Entry` into zero or more
:class:`Record` objects: authoritative-ish statements about who wrote the work
the entry claims to cite. Providers do not judge anything. They fetch, they
parse, they score how confident they are that they found the right work, and
they hand the result to the verifier.

Three rules hold for every provider, and they exist because the tool's job is
to accuse people of fabricating citations:

  * A clean "not found" returns ``[]``. It is never an exception, because a
    work being absent from one index is ordinary.
  * Not being able to ask at all raises :class:`ProviderUnavailable`. Offline
    with a cold cache is the common case. The verifier must never report
    "no record found" when the truth is "no question was asked", because the
    first reads as faint evidence against the citation and the second is
    evidence of nothing.
  * ``Record.confidence`` says how sure the provider is that this record is
    the cited work: 1.0 for an identifier hit, title similarity for a search
    hit. The verifier uses it to refuse to compare author lists across two
    different papers.

DBLP is not implemented: it sits behind a bot wall (Anubis) and does not
answer programmatic fetches, so there is no polite way to query it.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

#: Lookup order. CrossRef and arXiv are the strongest primary sources for the
#: identifiers bibliographies actually carry; OpenAlex is broad but derived;
#: Semantic Scholar is last because it is a secondary aggregator and because
#: it is the slowest to query politely.
PROVIDER_ORDER = ("crossref", "arxiv", "openalex", "semanticscholar")


class ProviderUnavailable(Exception):
    """The provider could not be asked, so its silence means nothing.

    Raised for an offline cache miss, for a persistent transport failure, and
    for the wrong-registry case where a 404 carries no information (see
    :mod:`bibauthor.providers.crossref`).
    """

    def __init__(self, message: str, provider: str = "", reason: str = "") -> None:
        Exception.__init__(self, message)
        self.provider = provider
        self.reason = reason


@dataclasses.dataclass
class Record:
    """One authoritative statement about a work's author list."""

    source: str
    identifier: str
    title: str = ""
    authors: List[str] = dataclasses.field(default_factory=list)
    year: Optional[int] = None
    date: str = ""
    first_version_year: Optional[int] = None
    first_version_date: str = ""
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: str = ""
    from_cache: bool = False
    confidence: float = 0.0
    notes: List[str] = dataclasses.field(default_factory=list)


class Provider(object):
    """Interface. Subclasses set ``name`` and implement :meth:`lookup`."""

    name = ""

    def lookup(self, entry: Any) -> List[Record]:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# title comparison
# ---------------------------------------------------------------------------

_PUNCTUATION = re.compile(r"[^0-9a-z\s]+")
_SPACES = re.compile(r"\s+")


def normalize_title(text: str) -> str:
    """Fold a title down to lowercase ASCII words separated by single spaces.

    Titles arrive with LaTeX leftovers, smart quotes, subtitle colons,
    trailing periods and inconsistent capitalisation. None of that should
    change whether two titles are judged the same work.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", str(text))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.casefold()
    folded = folded.replace("-", " ").replace("/", " ")
    folded = _PUNCTUATION.sub(" ", folded)
    return _SPACES.sub(" ", folded).strip()


def title_similarity(a: str, b: str) -> float:
    """Similarity of two titles in ``0..1``.

    ``difflib.SequenceMatcher`` on the normalized strings, with a token-set
    ratio taken as well and the better of the two returned. The second term
    exists because indexes routinely append or drop a subtitle, and a pure
    character ratio punishes that far more than it deserves.
    """
    left = normalize_title(a)
    right = normalize_title(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    char_ratio = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens)
        token_ratio = overlap / float(min(len(left_tokens), len(right_tokens)))
        # A subtitle that only one side carries should not dominate, but a
        # genuinely different paper will not share most of its words either.
        token_ratio = token_ratio * 0.97
    else:
        token_ratio = 0.0
    return max(0.0, min(1.0, max(char_ratio, token_ratio)))


# ---------------------------------------------------------------------------
# small shared helpers for the provider modules
# ---------------------------------------------------------------------------


def first_author_surname(entry: Any) -> str:
    """Best guess at the first cited author's surname, for search queries."""
    authors = list(getattr(entry, "authors", None) or [])
    if not authors:
        return ""
    raw = str(authors[0]).strip()
    if not raw:
        return ""
    if "," in raw:
        return raw.split(",", 1)[0].strip()
    return raw.split()[-1].strip()


def year_of(value: Any) -> Optional[int]:
    """Parse a four-digit year out of anything, or return None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 3000 else None
    found = re.search(r"(1[6-9]\d{2}|20\d{2}|21\d{2})", str(value))
    if not found:
        return None
    return int(found.group(1))


def iso_date(parts: Sequence[Any]) -> str:
    """Build a partial ISO date from a CrossRef-style date-parts list."""
    numbers = []
    for item in list(parts)[:3]:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            break
    if not numbers:
        return ""
    if len(numbers) == 1:
        return "%04d" % numbers[0]
    if len(numbers) == 2:
        return "%04d-%02d" % (numbers[0], numbers[1])
    return "%04d-%02d-%02d" % (numbers[0], numbers[1], numbers[2])


def score_for_title(entry: Any, title: str) -> float:
    """Confidence for a search hit: how well the titles agree."""
    return title_similarity(getattr(entry, "title", "") or "", title or "")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def build_providers(client: Any, enabled: Optional[Sequence[str]] = None) -> List[Provider]:
    """Instantiate providers in :data:`PROVIDER_ORDER`.

    ``enabled`` is an optional subset of provider names; unknown names raise
    ``ValueError`` rather than being ignored, because a typo in a config file
    silently disabling a source would quietly weaken every verdict.
    """
    from . import arxiv as arxiv_module
    from . import crossref as crossref_module
    from . import openalex as openalex_module
    from . import semanticscholar as s2_module

    classes: Dict[str, Any] = {
        "crossref": crossref_module.CrossRefProvider,
        "arxiv": arxiv_module.ArxivProvider,
        "openalex": openalex_module.OpenAlexProvider,
        "semanticscholar": s2_module.SemanticScholarProvider,
    }
    if enabled is None:
        wanted = list(PROVIDER_ORDER)
    else:
        wanted = [str(name).strip().lower() for name in enabled]
        unknown = [name for name in wanted if name not in classes]
        if unknown:
            raise ValueError(
                "unknown provider(s): %s (known: %s)"
                % (", ".join(sorted(unknown)), ", ".join(PROVIDER_ORDER))
            )
    return [classes[name](client) for name in PROVIDER_ORDER if name in wanted]


__all__ = [
    "PROVIDER_ORDER",
    "Provider",
    "ProviderUnavailable",
    "Record",
    "build_providers",
    "first_author_surname",
    "iso_date",
    "normalize_title",
    "score_for_title",
    "title_similarity",
    "year_of",
]
