# bibauthor design and internal contracts

This file is the contract between the modules. Keep the dataclass fields and
function signatures below stable; everything else is an implementation detail.

Target: Python 3.9+. Standard library only for the core; `requests` optional
(fall back to `urllib.request`). No hard third-party dependency for the
library path, because paper repositories should be able to install it into a
minimal CI image.

Every source file in this repository is pure ASCII. Non-ASCII test data lives
only in `tests/fixtures/bib/unicode_names.bib`; everywhere else, write
non-ASCII characters as Python `\u` escapes or JSON `\u` escapes.

## Layout

    src/bibauthor/
      __init__.py        public API re-exports
      entry.py           Entry, Author source record from a bibliography
      names.py           name parsing and the matching engine
      verdict.py         Verdict enum, Thresholds, EntryResult, Report
      config.py          config loading (TOML / dict) -> Config
      cache.py           on-disk HTTP cache, offline mode
      http.py            polite HTTP client (UA, contact URL, rate limit)
      providers/
        __init__.py      Provider protocol, Record, PROVIDERS registry
        crossref.py      openalex.py  arxiv.py  semanticscholar.py
      parsers/
        __init__.py      load_bibliography(path|text, fmt) dispatch
        bibtex.py        parse_bibtex(text) -> list[Entry]
        csljson.py       parse_csl_json(text) -> list[Entry]
      verify.py          the verifier that ties it together
      report.py          human text report and JSON report
      cli.py             argparse front end, exit codes

## entry.py

    @dataclasses.dataclass
    class Entry:
        key: str                  # bibtex citation key, or CSL id
        entry_type: str           # "article", "misc", "inproceedings", ...
        title: str = ""
        authors: List[str] = []   # one raw display string per author, in cited order
        year: Optional[int] = None
        doi: Optional[str] = None
        arxiv_id: Optional[str] = None   # bare id, e.g. "2507.01413", no "arXiv:" prefix
        venue: str = ""
        url: str = ""
        truncated: bool = False   # the stored author list ended in "et al." / "and others"
        source_file: str = ""
        source_line: Optional[int] = None
        raw: Dict[str, str] = {}  # original field map, lowercased keys
        warnings: List[str] = []  # parse-level notes (duplicate key, bad escape, ...)

`authors` holds author names exactly as the bibliography stated them, with
LaTeX escapes already decoded to Unicode and outer braces stripped, but with
the original word order (so "Lastname, Firstname" stays in that form; the name
parser deals with it).

## names.py

    @dataclasses.dataclass(frozen=True)
    class PersonName:
        family: str = ""          # surname, including particles ("van der Berg")
        given: str = ""           # given names as written, may be initials
        suffix: str = ""          # "Jr.", "III"
        literal: str = ""         # the name as supplied, always populated
        is_group: bool = False    # consortium / collaboration / institution

    def parse_name(raw: str) -> PersonName
    def fold(s: str) -> str
        # casefold, strip diacritics to ASCII, drop punctuation, squeeze spaces

    @dataclasses.dataclass
    class NameComparison:
        score: float              # 0.0 .. 1.0
        exact: bool               # identical after fold, no variant rules needed
        variants: List[str]       # rule tags that fired, e.g. ["initials", "diacritics"]
        reason: str               # short human phrase

    def compare_names(a: PersonName, b: PersonName,
                      th: "Thresholds") -> NameComparison

    @dataclasses.dataclass
    class ListComparison:
        verdict: str                       # a Verdict value (see verdict.py)
        pairs: List[Tuple[int, int, NameComparison]]   # (cited idx, record idx, cmp)
        unmatched_cited: List[int]
        unmatched_record: List[int]
        order_differs: bool
        truncated: bool
        coverage: float                    # matched cited names / comparable cited names
        variants: List[str]                # union of rule tags that fired
        notes: List[str]

    def compare_author_lists(cited: Sequence[PersonName],
                             record: Sequence[PersonName],
                             th: "Thresholds",
                             cited_truncated: bool = False) -> ListComparison

Matching is a maximum-weight bipartite assignment over the score matrix with a
floor at `th.name_variant`. Greedy-with-backtracking is acceptable for the list
sizes involved (hundreds of authors at most); it must be deterministic.

`compare_author_lists` sets the verdict by the rules in README.md ("Verdicts
and thresholds"). It never returns UNVERIFIABLE; only the verifier can, because
only the verifier knows whether a record was found at all.

## verdict.py

    class Verdict:                 # plain string constants, not enum.Enum,
        MATCH = "MATCH"            # so the JSON report needs no encoder
        MATCH_WITH_VARIANTS = "MATCH_WITH_VARIANTS"
        ORDER_DIFFERS = "ORDER_DIFFERS"
        TRUNCATED = "TRUNCATED"
        PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
        DISJOINT = "DISJOINT"
        UNVERIFIABLE = "UNVERIFIABLE"

    SEVERITY = {...}   # MATCH 0, MATCH_WITH_VARIANTS 0, TRUNCATED 1,
                       # ORDER_DIFFERS 2, UNVERIFIABLE 2,
                       # PARTIAL_OVERLAP 3, DISJOINT 4

    @dataclasses.dataclass
    class Thresholds:
        name_match: float = 0.92
        name_variant: float = 0.70
        partial_coverage: float = 0.50
        title_similarity: float = 0.80
        disjoint_min_record_authors: int = 2
        disjoint_max_coverage: float = 0.0
        surname_only_penalty: float = 0.18
        initial_vs_full_given: float = 0.90
        allow_order_differs_as_pass: bool = False
        promote_order_differs: bool = False
        promote_truncated: bool = False

`verdict.py` is the authoritative list, not this file. The last two fields are
what `--strict` sets, and they are what `is_mismatch` consults when deciding
whether a verdict should drive exit code 1.

## providers/__init__.py

    @dataclasses.dataclass
    class Record:
        source: str                  # "crossref" | "openalex" | "arxiv" | "semanticscholar"
        identifier: str              # DOI, OpenAlex id, arXiv id, S2 paper id
        title: str = ""
        authors: List[str] = []      # display strings in record order
        year: Optional[int] = None
        date: str = ""               # ISO date if known
        first_version_year: Optional[int] = None   # arXiv v1 year; None elsewhere
        first_version_date: str = ""
        doi: Optional[str] = None
        arxiv_id: Optional[str] = None
        url: str = ""
        from_cache: bool = False
        confidence: float = 0.0      # title/id agreement with the Entry, 0..1
        notes: List[str] = []

    class Provider:
        name: str
        def lookup(self, entry: Entry) -> List[Record]: ...

Provider rules:
  * CrossRef: `GET https://api.crossref.org/works/{doi}` first; otherwise
    `GET /works?query.bibliographic=...&rows=5`. arXiv DOIs under the
    `10.48550/` prefix are usually NOT in CrossRef (verified 404 on
    10.48550/arXiv.2507.01413), so a 404 there is not evidence of fabrication.
  * OpenAlex: `GET /works/https://doi.org/{doi}` or `GET /works?search=...`.
    Works with no API key. Strip and ignore `meta.notice`; never send any
    email address, ever. See SECURITY note in README.
  * arXiv: `GET https://export.arxiv.org/api/query?id_list=...` (https only;
    plain http returned an empty body in testing). Atom XML. `<published>` is
    the v1 date, `<updated>` the latest version.
  * Semantic Scholar: `GET https://api.semanticscholar.org/graph/v1/paper/...`.
    Serialize all calls, min interval 3 s, back off on 429. Secondary source.
  * DBLP: not implemented. It is behind a bot wall (Anubis) and does not answer
    programmatic fetches.

## cache.py / http.py

Cache entry file: `<cache_dir>/<sha256(method|url|sorted params)>.json`

    {"url": "...", "params": {...}, "status": 200, "recorded_at": "...",
     "content_type": "application/json", "body": <parsed json or string>}

Written with `json.dump(..., ensure_ascii=True, sort_keys=True, indent=1)` so
cache files stay pure ASCII and diff cleanly.

`HttpClient(cache, offline=False, min_interval=1.0, user_agent=..., ...)`
  * offline and cache miss -> raise `CacheMiss`
  * the verifier turns `CacheMiss` into an UNVERIFIABLE result with a note

## Exit codes (cli.py)

    0  clean: every entry MATCH or MATCH_WITH_VARIANTS
    1  author mismatch: any DISJOINT or PARTIAL_OVERLAP (or ORDER_DIFFERS /
       TRUNCATED when the config promotes them to failures)
    2  no mismatch, but at least one UNVERIFIABLE entry
    3  usage or input error (file missing, unparseable, bad config)

Rationale for 1 over 2: a mismatch is the finding the tool exists to report, so
it must win when both are present.
