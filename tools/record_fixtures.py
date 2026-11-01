"""Record the provider fixtures that the offline test suite runs against.

Run this to refresh ``tests/fixtures/cache``:

    PYTHONPATH=src PYTHONIOENCODING=utf-8 python -X utf8 tools/record_fixtures.py

It is committed so that the recording is reproducible and auditable: anyone
can see exactly which requests were made, and re-run them.

What it does and does not do:

  * Read-only HTTP GETs to four public metadata APIs. Nothing else. No POST,
    no authentication, no API key, and no contact address of any kind in any
    request. See :mod:`bibauthors.http` for why.
  * Serial, never parallel, with at least one second between calls to a host
    and at least three seconds for Semantic Scholar, which is the limit that
    service documents for unauthenticated callers.
  * Writes one JSON file per request plus ``INDEX.json``, all pure ASCII.
  * Records non-200 answers too, when the fixture exists to pin down what a
    particular failure means. The CrossRef 404 on an arXiv DOI is the case
    that matters: the test suite has to be able to assert the wrong-registry
    behaviour without a network.

The URLs and parameters are built with the provider modules' own helpers
rather than retyped, because the cache key is a hash of the request. If the
recorder and the provider disagree by one character the fixture is dead
weight and the test fails offline for a reason that has nothing to do with
the code under test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from bibauthors.cache import Cache, utc_now_stamp  # noqa: E402
from bibauthors.entry import Entry, normalize_doi  # noqa: E402
from bibauthors.http import HttpClient, HttpError, sanitize_json  # noqa: E402
from bibauthors.providers import arxiv as arxiv_provider  # noqa: E402
from bibauthors.providers import build_providers  # noqa: E402
from bibauthors.providers import crossref as crossref_provider  # noqa: E402
from bibauthors.providers import openalex as openalex_provider  # noqa: E402
from bibauthors.providers import semanticscholar as s2_provider  # noqa: E402

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "cache")
INDEX_PATH = os.path.join(FIXTURE_DIR, "INDEX.json")

QWEN_TITLE = "Qwen2.5 Technical Report"
CHIMERIC_ARXIV_ID = "2507.01413"
QWEN_ARXIV_ID = "2412.15115"
ARXIV_DOI = "10.48550/arXiv.%s" % CHIMERIC_ARXIV_ID

#: Published works chosen to stress the name matcher. Each one is here for a
#: specific author shape, not because it is famous.
NAME_STRESS_DOIS = [
    (
        "10.1038/nature16961",
        "particle surname: the author list contains a Dutch tussenvoegsel "
        "name, so the matcher has to keep the particle with the surname",
    ),
    (
        "10.1038/s41586-021-03819-2",
        "heavy diacritics across several alphabets, including a caron and an "
        "acute, so the matcher has to fold them to ASCII without merging "
        "distinct people",
    ),
    (
        "10.1109/CVPR.2016.90",
        "romanized CJK given/family names, where the given name is a single "
        "token and family-name-first order is common in the wild",
    ),
    (
        "10.1103/PhysRevLett.116.061102",
        "very large consortium author list, with a collaboration group name "
        "deposited as a CrossRef {name} object rather than {given, family}",
    ),
]

NO_DOI_ENTRY = Entry(
    key="vaswani2017attention",
    entry_type="inproceedings",
    title="Attention is all you need",
    authors=[
        "Vaswani, Ashish",
        "Shazeer, Noam",
        "Parmar, Niki",
        "Uszkoreit, Jakob",
        "Jones, Llion",
        "Gomez, Aidan N.",
        "Kaiser, Lukasz",
        "Polosukhin, Illia",
    ],
    year=2017,
)


def crossref_doi_url(doi: str) -> str:
    import urllib.parse

    return "%s/%s" % (
        crossref_provider.WORKS_URL,
        urllib.parse.quote(doi, safe="/"),
    )


def specs():
    """Every request to record, in the order it gets made.

    Each spec is ``(name, kind, url, params, description)`` where ``kind`` is
    ``"json"`` or ``"text"``.
    """
    out = []

    # 1 + 2: arXiv by id. The chimeric case and the two-version case.
    out.append(
        (
            "arxiv-2507.01413",
            "text",
            arxiv_provider.QUERY_URL,
            {"id_list": CHIMERIC_ARXIV_ID},
            "arXiv Atom feed for 2507.01413, the chimeric-citation case: the "
            "true author list is the one the tool must recover",
        )
    )
    out.append(
        (
            "arxiv-2412.15115",
            "text",
            arxiv_provider.QUERY_URL,
            {"id_list": QWEN_ARXIV_ID},
            "arXiv Atom feed for 2412.15115 (Qwen2.5 Technical Report), used "
            "for the v1-versus-latest-version date split and for a large "
            "author list",
        )
    )

    # 3: OpenAlex by arXiv DOI.
    out.append(
        (
            "openalex-doi-10.48550-arxiv.2507.01413",
            "json",
            openalex_provider.doi_url(normalize_doi(ARXIV_DOI)),
            None,
            "OpenAlex work fetched by the arXiv DOI, which CrossRef does not "
            "hold; also the fixture that proves meta.notice never reaches disk",
        )
    )

    # 4: OpenAlex title search.
    out.append(
        (
            "openalex-search-qwen",
            "json",
            openalex_provider.WORKS_URL,
            {"search": QWEN_TITLE, "per-page": openalex_provider.SEARCH_PER_PAGE},
            "OpenAlex title search for the Qwen2.5 Technical Report, the "
            "no-identifier path",
        )
    )

    # 5: the CrossRef 404 on an arXiv DOI. Recorded deliberately.
    out.append(
        (
            "crossref-404-10.48550-arxiv.2507.01413",
            "json",
            crossref_doi_url(normalize_doi(ARXIV_DOI)),
            None,
            "CrossRef HTTP 404 for an arXiv DOI: the wrong-registry case, "
            "recorded with status 404 so the test can assert that it is not "
            "treated as evidence about the citation",
        )
    )

    # 6: Semantic Scholar by arXiv id.
    out.append(
        (
            "s2-arxiv-2507.01413",
            "json",
            "%s/arXiv:%s" % (s2_provider.PAPER_URL, CHIMERIC_ARXIV_ID),
            {"fields": s2_provider.FIELDS},
            "Semantic Scholar paper record for arXiv:2507.01413, the "
            "abbreviated-given-name case",
        )
    )

    # 7: four CrossRef DOIs chosen for author-name shapes.
    for doi, why in NAME_STRESS_DOIS:
        normalized = normalize_doi(doi)
        out.append(
            (
                "crossref-doi-%s" % normalized.replace("/", "-"),
                "json",
                crossref_doi_url(normalized),
                None,
                "CrossRef work %s. %s" % (normalized, why),
            )
        )

    # 8: one CrossRef bibliographic search, for an entry with no DOI.
    out.append(
        (
            "crossref-search-attention",
            "json",
            crossref_provider.WORKS_URL,
            {
                "query.bibliographic": crossref_provider.CrossRefProvider.query_for(
                    NO_DOI_ENTRY
                ),
                "rows": crossref_provider.SEARCH_ROWS,
                "select": crossref_provider.SEARCH_SELECT,
            },
            "CrossRef query.bibliographic search for an entry with no DOI",
        )
    )
    return out


def record(client: HttpClient, cache: Cache, spec):
    name, kind, url, params, description = spec
    try:
        if kind == "json":
            response = client.fetch_json(url, params)
        else:
            response = client.fetch_text(url, params)
        status = response.status
        note = "ok"
    except HttpError as exc:
        status = exc.status or 0
        if status == 0:
            raise
        # Store the failure verbatim. A 404 is an answer and some of the
        # answers are the point of the fixture.
        body = exc.body
        if kind == "json":
            try:
                body = sanitize_json(json.loads(body))
            except (TypeError, ValueError):
                body = str(body)
        cache.put(url, params, status, body, exc.content_type)
        note = "recorded non-200"
    return {
        "name": name,
        "url": url,
        "params": dict(params or {}),
        "status": status,
        "kind": kind,
        "file": os.path.basename(cache.path_for(url, params)),
        "description": description,
        "note": note,
    }


def write_index(cache: Cache, rows) -> None:
    index = {
        "generated_by": "tools/record_fixtures.py",
        "recorded_at": utc_now_stamp(),
        "note": (
            "Recorded read-only HTTP GETs against four public metadata APIs. "
            "No request carried an API key or any contact address. OpenAlex "
            "meta.notice fields are stripped before anything is written."
        ),
        "fixtures": sorted(rows, key=lambda row: row["name"]),
    }
    with open(INDEX_PATH, "w", encoding="ascii", newline="\n") as handle:
        json.dump(index, handle, ensure_ascii=True, sort_keys=True, indent=1)
        handle.write("\n")


def self_check() -> None:
    """Replay the fixtures offline and print what each provider parsed.

    This is the step that catches a fixture that recorded cleanly but does
    not contain what it was recorded for.
    """
    cache = Cache(FIXTURE_DIR, offline=True)
    client = HttpClient(cache, offline=True)
    providers = {p.name: p for p in build_providers(client)}

    checks = [
        (
            "arxiv",
            Entry(key="chimeric", title="", arxiv_id=CHIMERIC_ARXIV_ID),
        ),
        ("arxiv", Entry(key="qwen", title=QWEN_TITLE, arxiv_id=QWEN_ARXIV_ID)),
        ("openalex", Entry(key="chimeric", arxiv_id=CHIMERIC_ARXIV_ID)),
        ("openalex", Entry(key="qwen", title=QWEN_TITLE)),
        ("crossref", Entry(key="arxivdoi", doi=ARXIV_DOI)),
        ("semanticscholar", Entry(key="chimeric", arxiv_id=CHIMERIC_ARXIV_ID)),
        ("crossref", NO_DOI_ENTRY),
    ]
    for doi, _why in NAME_STRESS_DOIS:
        checks.append(("crossref", Entry(key=doi, doi=doi)))

    for provider_name, entry in checks:
        provider = providers[provider_name]
        try:
            records = provider.lookup(entry)
        except Exception as exc:  # noqa: BLE001 - this is a report, not a guard
            print("  %-16s %-22s %s: %s" % (provider_name, entry.key,
                                            type(exc).__name__, exc))
            continue
        if not records:
            print("  %-16s %-22s no records" % (provider_name, entry.key))
            continue
        top = records[0]
        print(
            "  %-16s %-22s conf=%.3f year=%s v1=%s authors=%d"
            % (
                provider_name,
                entry.key,
                top.confidence,
                top.year,
                top.first_version_date or "-",
                len(top.authors),
            )
        )
        print("      title: %s" % top.title[:88])
        shown = top.authors[:6]
        for author in shown:
            print("      author: %s" % author.encode("ascii", "backslashreplace")
                  .decode("ascii"))
        if len(top.authors) > len(shown):
            print("      ... %d more" % (len(top.authors) - len(shown)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="do not touch the network; replay the existing fixtures offline",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=1.5,
        help="seconds between requests to one host (floor is 1.0)",
    )
    args = parser.parse_args(argv)

    if args.check_only:
        print("offline self-check:")
        self_check()
        return 0

    if not os.path.isdir(FIXTURE_DIR):
        os.makedirs(FIXTURE_DIR)

    cache = Cache(FIXTURE_DIR, offline=False)
    client = HttpClient(
        cache,
        offline=False,
        min_interval=max(1.0, args.min_interval),
        max_retries=3,
    )

    rows = []
    for spec in specs():
        name = spec[0]
        print("recording %s ..." % name)
        started = time.monotonic()
        row = record(client, cache, spec)
        print(
            "  status=%s %s (%.1fs) -> %s"
            % (row["status"], row["note"], time.monotonic() - started, row["file"])
        )
        rows.append(row)

    write_index(cache, rows)
    print("wrote %s" % INDEX_PATH)
    print("live requests made: %d" % client.request_count)
    print("offline self-check:")
    self_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
