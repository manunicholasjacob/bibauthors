"""bibauthors: verify bibliography AUTHOR LISTS against authoritative metadata.

Every citation checker matches on title and DOI. This one checks the author
list, because the most dangerous hallucinated citation is the chimeric one: a
real title, a real venue, a real year, and an invented author list. It passes
a title-matching checker with a clean report and it looks real at a glance.

The short version of the API:

    from bibauthors import verify_bibliography, Config, Verdict

    report = verify_bibliography("refs.bib", Config(offline=True))
    for result in report.results:
        if result.verdict == Verdict.DISJOINT:
            print(result.entry.key, "needs a manual look")
    raise SystemExit(report.exit_code())

A DISJOINT verdict is a prompt to check an entry by hand. It is not an
accusation of fabrication.

Author: Manu Nicholas Jacob, Independent Researcher.
ORCID 0009-0007-6589-6572. MIT licensed.
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "Manu Nicholas Jacob"
__license__ = "MIT"

from .cache import Cache, CacheMiss
from .config import Config
from .entry import Entry, extract_arxiv_id, normalize_doi
from .http import HttpClient
from .names import (
    ListComparison,
    NameComparison,
    PersonName,
    compare_author_lists,
    compare_names,
    fold,
    parse_name,
    parse_names,
)
from .parsers import ParseError, load_bibliography, parse_bibtex, parse_csl_json
from .providers import Provider, ProviderUnavailable, Record, build_providers
from .report import format_github_annotations, format_json, format_text, to_dict
from .verdict import DESCRIPTIONS, SEVERITY, Thresholds, Verdict
from .verify import (
    SCHEMA_VERSION,
    EntryResult,
    Report,
    Verifier,
    YearFinding,
    verify_bibliography,
    verify_entries,
    verify_entry,
)

__all__ = [
    "__version__",
    # front doors
    "verify_bibliography",
    "verify_entries",
    "verify_entry",
    "Verifier",
    # input
    "load_bibliography",
    "parse_bibtex",
    "parse_csl_json",
    "ParseError",
    "Entry",
    "normalize_doi",
    "extract_arxiv_id",
    # name matching
    "PersonName",
    "parse_name",
    "parse_names",
    "fold",
    "compare_names",
    "compare_author_lists",
    "NameComparison",
    "ListComparison",
    # verdicts and tuning
    "Verdict",
    "Thresholds",
    "SEVERITY",
    "DESCRIPTIONS",
    "Config",
    # results
    "EntryResult",
    "Report",
    "YearFinding",
    "SCHEMA_VERSION",
    # output
    "format_text",
    "format_json",
    "format_github_annotations",
    "to_dict",
    # sources
    "Provider",
    "Record",
    "ProviderUnavailable",
    "build_providers",
    "HttpClient",
    "Cache",
    "CacheMiss",
]
