"""The verifier: entry in, graded verdict out.

Flow for one entry:

1. Ask the enabled providers, in order, for records about the work.
2. Keep only records that plausibly ARE the cited work (identifier hit, or
   title similarity at or above ``thresholds.title_similarity``). This guard
   exists because comparing an author list against the wrong paper is the
   fastest way to manufacture a false accusation.
3. Pick the best record: highest confidence, ties broken by provider order.
4. Compare the author lists with :func:`bibauthors.names.compare_author_lists`.
5. Note any year disagreement separately, including the arXiv v1 case.

An entry with no usable record is UNVERIFIABLE. That is a distinct outcome
from a mismatch, and it has its own exit code, because "I could not check
this" and "this is wrong" are different statements and conflating them is how
a checker ends up crying wolf.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cache import Cache
from .config import Config
from .entry import Entry
from .http import HttpClient
from .names import ListComparison, PersonName, compare_author_lists, parse_names
from .providers import (
    PROVIDER_ORDER,
    Provider,
    ProviderUnavailable,
    Record,
    build_providers,
    title_similarity,
)
from .verdict import SEVERITY, Thresholds, Verdict, is_mismatch

#: Report schema version. Bump on any breaking change to the JSON shape.
SCHEMA_VERSION = "1.0"


@dataclasses.dataclass
class YearFinding:
    """A date disagreement, which is a separate axis from the author list."""

    kind: str  # "year_mismatch" | "arxiv_v1_year" | "year_missing"
    cited_year: Optional[int]
    record_year: Optional[int]
    first_version_year: Optional[int] = None
    message: str = ""


@dataclasses.dataclass
class EntryResult:
    """Everything the tool concluded about one bibliography entry."""

    entry: Entry
    verdict: str = Verdict.UNVERIFIABLE
    comparison: Optional[ListComparison] = None
    record: Optional[Record] = None
    candidates: List[Record] = dataclasses.field(default_factory=list)
    year_findings: List[YearFinding] = dataclasses.field(default_factory=list)
    provider_notes: List[str] = dataclasses.field(default_factory=list)
    unavailable: List[str] = dataclasses.field(default_factory=list)

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.verdict, 0)

    @property
    def key(self) -> str:
        return self.entry.key

    def is_mismatch(self, th: Thresholds) -> bool:
        return is_mismatch(self.verdict, th)


@dataclasses.dataclass
class Report:
    """The whole run."""

    results: List[EntryResult] = dataclasses.field(default_factory=list)
    config: Config = dataclasses.field(default_factory=Config)
    sources: List[str] = dataclasses.field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    schema_version: str = SCHEMA_VERSION

    def counts(self) -> Dict[str, int]:
        out = {v: 0 for v in Verdict.ALL}
        for result in self.results:
            out[result.verdict] = out.get(result.verdict, 0) + 1
        return out

    @property
    def mismatches(self) -> List[EntryResult]:
        th = self.config.thresholds
        return [r for r in self.results if r.is_mismatch(th)]

    @property
    def unverifiable(self) -> List[EntryResult]:
        return [r for r in self.results if r.verdict == Verdict.UNVERIFIABLE]

    def exit_code(self) -> int:
        """0 clean, 1 author mismatch, 2 unverifiable present.

        A mismatch wins over an unverifiable entry when both are present:
        the mismatch is the finding the tool exists to report.
        """
        if self.mismatches:
            return 1
        if self.unverifiable:
            return 2
        return 0


def _now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _identifier_hit(entry: Entry, record: Record) -> bool:
    """Did we find this record BY an identifier the entry gave us?"""
    if entry.doi and record.doi and entry.doi.lower() == record.doi.lower():
        return True
    if entry.arxiv_id and record.arxiv_id:
        return entry.arxiv_id.lower() == record.arxiv_id.lower()
    return False


def _usable(entry: Entry, record: Record, th: Thresholds) -> Tuple[bool, str]:
    """May this record be used to judge the entry's author list?

    An identifier hit is always usable: the entry told us which work it meant.
    Otherwise the titles must agree closely, because a search result is a
    guess and judging an author list against a guessed work is exactly how a
    checker invents a fabrication that is not there.
    """
    if _identifier_hit(entry, record):
        return True, "identifier"
    if not entry.title or not record.title:
        return False, "no title to compare"
    similarity = title_similarity(entry.title, record.title)
    if similarity >= th.title_similarity:
        return True, "title similarity %.2f" % similarity
    return False, "title similarity %.2f below %.2f" % (similarity, th.title_similarity)


def _rank(entry: Entry, record: Record) -> Tuple[float, float, int]:
    """Sort key for choosing among usable records. Higher is better."""
    try:
        order = PROVIDER_ORDER.index(record.source)
    except ValueError:
        order = len(PROVIDER_ORDER)
    identifier_bonus = 1.0 if _identifier_hit(entry, record) else 0.0
    return (identifier_bonus, record.confidence, -order)


def _year_findings(entry: Entry, record: Record) -> List[YearFinding]:
    """Compare the stored year against the record, v1 date included."""
    out: List[YearFinding] = []
    if entry.year is None:
        out.append(
            YearFinding(
                kind="year_missing",
                cited_year=None,
                record_year=record.year,
                message="entry has no year; %s says %s"
                % (record.source, record.year),
            )
        )
        return out

    first = record.first_version_year
    if first is not None and first != entry.year and record.year == entry.year:
        # The stored year matches the latest version but not v1. This is the
        # arXiv case: v1 in one year, a revision in the next. Citing the
        # revision year is defensible, so this is a note, not a mismatch.
        out.append(
            YearFinding(
                kind="arxiv_v1_year",
                cited_year=entry.year,
                record_year=record.year,
                first_version_year=first,
                message=(
                    "stored year %d matches the latest version (%s), but v1 is "
                    "%d (%s). Decide which version you mean to cite."
                    % (entry.year, record.date or "n/a", first,
                       record.first_version_date or "n/a")
                ),
            )
        )
        return out

    candidates = [y for y in (record.year, first) if y is not None]
    if candidates and entry.year not in candidates:
        nearest = min(candidates, key=lambda y: abs(y - entry.year))
        out.append(
            YearFinding(
                kind="year_mismatch",
                cited_year=entry.year,
                record_year=record.year,
                first_version_year=first,
                message=(
                    "stored year %d, %s says %s"
                    % (
                        entry.year,
                        record.source,
                        " / ".join(str(y) for y in candidates),
                    )
                    + (" (off by %d)" % abs(nearest - entry.year))
                ),
            )
        )
    return out


class Verifier:
    """Holds the providers and the config; verifies entries one at a time."""

    def __init__(
        self,
        config: Optional[Config] = None,
        providers: Optional[Sequence[Provider]] = None,
        client: Optional[HttpClient] = None,
    ) -> None:
        self.config = config or Config()
        self.config.validate()
        if providers is not None:
            self.providers = list(providers)
            self.client = client
        else:
            if client is None:
                cache = Cache(self.config.cache_dir, offline=self.config.offline)
                client = HttpClient(
                    cache,
                    offline=self.config.offline,
                    min_interval=self.config.min_interval,
                    timeout=self.config.timeout,
                )
            self.client = client
            self.providers = build_providers(client, self.config.providers)

    # -- one entry ---------------------------------------------------------

    def verify_entry(self, entry: Entry) -> EntryResult:
        th = self.config.thresholds
        result = EntryResult(entry=entry)

        if entry.key in self.config.ignore_keys:
            result.verdict = Verdict.UNVERIFIABLE
            result.provider_notes.append("skipped: key is in ignore_keys")
            return result

        records: List[Record] = []
        for provider in self.providers:
            try:
                found = provider.lookup(entry)
            except ProviderUnavailable as exc:
                result.unavailable.append(provider.name)
                result.provider_notes.append(
                    "%s could not be asked (%s): %s"
                    % (provider.name, exc.reason or "unavailable", exc)
                )
                continue
            except Exception as exc:  # defensive: one bad provider is not fatal
                result.unavailable.append(provider.name)
                result.provider_notes.append(
                    "%s failed: %s: %s"
                    % (provider.name, type(exc).__name__, exc)
                )
                continue
            if not found:
                result.provider_notes.append("%s: no record found" % provider.name)
                continue
            records.extend(found)
            if self.config.stop_after_confident and any(
                _identifier_hit(entry, r) and r.authors for r in found
            ):
                # An identifier hit with a real author list settles it. Stop
                # here rather than spending someone else's rate limit.
                break

        result.candidates = records

        usable: List[Record] = []
        for record in records:
            ok, why = _usable(entry, record, th)
            if ok:
                usable.append(record)
            else:
                result.provider_notes.append(
                    "%s record %s rejected: %s" % (record.source, record.identifier, why)
                )

        usable = [r for r in usable if r.authors]
        if not usable:
            result.verdict = Verdict.UNVERIFIABLE
            if not records:
                result.provider_notes.append(
                    "no provider returned a record for this entry"
                )
            else:
                result.provider_notes.append(
                    "records were found but none was usable for an author check"
                )
            return result

        usable.sort(key=lambda r: _rank(entry, r), reverse=True)
        record = usable[0]
        result.record = record

        cited: List[PersonName] = parse_names(entry.authors)
        authoritative: List[PersonName] = parse_names(record.authors)
        comparison = compare_author_lists(
            cited, authoritative, th, cited_truncated=entry.truncated
        )
        result.comparison = comparison
        result.verdict = comparison.verdict

        if self.config.check_years:
            result.year_findings = _year_findings(entry, record)

        return result

    # -- a whole bibliography ---------------------------------------------

    def verify_entries(
        self, entries: Sequence[Entry], sources: Optional[Sequence[str]] = None
    ) -> Report:
        report = Report(config=self.config, started_at=_now())
        report.sources = list(sources or sorted({e.source_file for e in entries if e.source_file}))
        limit = self.config.max_entries
        for index, entry in enumerate(entries):
            if limit is not None and index >= limit:
                break
            report.results.append(self.verify_entry(entry))
        report.finished_at = _now()
        return report


# ---------------------------------------------------------------------------
# convenience front doors (the stable public API)
# ---------------------------------------------------------------------------


def verify_entries(
    entries: Sequence[Entry],
    config: Optional[Config] = None,
    providers: Optional[Sequence[Provider]] = None,
    client: Optional[HttpClient] = None,
) -> Report:
    """Verify a list of already-parsed entries."""
    return Verifier(config=config, providers=providers, client=client).verify_entries(
        entries
    )


def verify_entry(
    entry: Entry,
    config: Optional[Config] = None,
    providers: Optional[Sequence[Provider]] = None,
    client: Optional[HttpClient] = None,
) -> EntryResult:
    """Verify a single entry."""
    return Verifier(config=config, providers=providers, client=client).verify_entry(
        entry
    )


def verify_bibliography(
    path_or_text: Any,
    config: Optional[Config] = None,
    fmt: Optional[str] = None,
    is_text: bool = False,
    providers: Optional[Sequence[Provider]] = None,
    client: Optional[HttpClient] = None,
) -> Report:
    """Parse a bibliography and verify every entry in it.

    ``path_or_text`` is a path by default, or the bibliography text itself
    when ``is_text=True``. ``fmt`` is ``"bibtex"`` or ``"csljson"``; it is
    sniffed when omitted.
    """
    from .parsers import load_bibliography

    entries = load_bibliography(path_or_text, fmt=fmt, is_text=is_text)
    return Verifier(config=config, providers=providers, client=client).verify_entries(
        entries
    )
