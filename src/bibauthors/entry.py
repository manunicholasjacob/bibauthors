"""The bibliography-side record: one citation as the author wrote it."""

from __future__ import annotations

import dataclasses
import re
from typing import Dict, List, Optional

_ARXIV_NEW = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
_ARXIV_OLD = re.compile(r"([a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?", re.I)
_ARXIV_MARKED = re.compile(
    r"(?:arxiv\s*[:/]\s*|arxiv\.org/(?:abs|pdf)/)"
    r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?",
    re.I,
)
_DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"{}]+")

TRUNCATION_MARKERS = ("et al", "et al.", "others", "and others")


def normalize_doi(raw: Optional[str]) -> Optional[str]:
    """Reduce any DOI spelling to a bare lowercase ``10.x/y`` string."""
    if not raw:
        return None
    text = raw.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "DOI:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :]
    found = _DOI.search(text)
    if not found:
        return None
    doi = found.group(0).rstrip(".,;)")
    return doi.lower()


def extract_arxiv_id(*candidates: Optional[str]) -> Optional[str]:
    """Pull a bare arXiv id out of any of the strings given, in order.

    Accepts the modern ``2507.01413`` form and the pre-2007
    ``cs.LG/0701001`` form, with or without a version suffix. The version
    suffix is dropped: version handling belongs to the arXiv provider, which
    needs the v1 date separately.

    A candidate is only mined for a bare id when the whole candidate IS the
    id, or when the candidate carries an explicit arXiv marker (``arXiv:`` or
    an ``arxiv.org/abs/`` URL), or when it is a ``10.48550/arXiv.*`` DOI.

    That strictness is the whole point of this function. An unanchored
    ``\\d{4}\\.\\d{4,5}`` search matches the inside of ordinary publisher DOIs:
    a real IEEE DOI such as ``10.1109/TC.2024.3401234`` would otherwise yield
    the phantom arXiv id ``2024.34012`` and send the arXiv provider looking
    for a paper that does not exist. A large share of real bibliographies
    carry DOIs of that shape, so an unanchored match is not an edge case.
    """
    for candidate in candidates:
        if not candidate:
            continue
        text = candidate.strip()

        doi = normalize_doi(text)
        if doi and doi.startswith("10.48550/arxiv."):
            return doi.split("arxiv.", 1)[1]

        marked = _ARXIV_MARKED.search(text)
        if marked:
            return marked.group(1)

        if doi:
            # A DOI from any other registry tells us nothing about arXiv.
            continue

        whole = _ARXIV_NEW.fullmatch(text) or _ARXIV_OLD.fullmatch(text)
        if whole:
            return whole.group(1)
    return None


def looks_truncated(authors: List[str]) -> bool:
    """True when the stored author list admits it is incomplete."""
    if not authors:
        return False
    tail = authors[-1].strip().lower().rstrip(".").strip()
    return tail in {m.strip().rstrip(".") for m in TRUNCATION_MARKERS}


@dataclasses.dataclass
class Entry:
    """One bibliography entry, normalized but not yet verified.

    ``authors`` holds one raw display string per author in the order cited,
    with LaTeX escapes already decoded and outer braces stripped. The word
    order inside each string is left alone, so ``"Jacob, Manu"`` stays in that
    form; :func:`bibauthors.names.parse_name` understands both orders.
    """

    key: str
    entry_type: str = "misc"
    title: str = ""
    authors: List[str] = dataclasses.field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    venue: str = ""
    url: str = ""
    truncated: bool = False
    source_file: str = ""
    source_line: Optional[int] = None
    raw: Dict[str, str] = dataclasses.field(default_factory=dict)
    warnings: List[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        self.doi = normalize_doi(self.doi)
        if not self.arxiv_id:
            self.arxiv_id = extract_arxiv_id(
                self.raw.get("eprint"),
                self.raw.get("arxiv"),
                self.doi,
                self.url,
                self.raw.get("note"),
                self.raw.get("journal"),
                self.raw.get("howpublished"),
            )
        if not self.truncated:
            self.truncated = looks_truncated(self.authors)
        if self.truncated:
            self.authors = [
                a
                for a in self.authors
                if a.strip().lower().rstrip(".").strip()
                not in {m.strip().rstrip(".") for m in TRUNCATION_MARKERS}
            ]

    @property
    def label(self) -> str:
        """Short identifier for reports."""
        where = ""
        if self.source_line:
            where = ":%d" % self.source_line
        return "%s%s" % (self.key, where)

    def has_identifier(self) -> bool:
        return bool(self.doi or self.arxiv_id)
