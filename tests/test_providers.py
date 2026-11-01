"""Offline provider tests.

Every test here runs against ``tests/fixtures/cache`` with
``Cache(..., offline=True)``. Nothing in this file may touch the network: if a
request is not in the recorded fixtures the cache raises ``CacheMiss``, the
provider turns that into ``ProviderUnavailable``, and the test fails loudly
rather than quietly going online and passing on someone else's server.

Run with:

    PYTHONPATH=src PYTHONIOENCODING=utf-8 python -X utf8 -m pytest tests/test_providers.py -q
"""

from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from bibauthor.cache import Cache, CacheMiss, canonical_request  # noqa: E402
from bibauthor.entry import Entry  # noqa: E402
from bibauthor.http import (  # noqa: E402
    CONTACT_URL,
    DEFAULT_USER_AGENT,
    HOST_MIN_INTERVAL,
    NOTICE_PLACEHOLDER,
    SERIALIZED_HOSTS,
    HttpClient,
    HttpError,
    sanitize_json,
)
from bibauthor.providers import (  # noqa: E402
    PROVIDER_ORDER,
    ProviderUnavailable,
    Record,
    build_providers,
    normalize_title,
    title_similarity,
)
from bibauthor.providers.arxiv import ArxivProvider, split_arxiv_id  # noqa: E402
from bibauthor.providers.crossref import (  # noqa: E402
    WRONG_REGISTRY_REASON,
    CrossRefProvider,
    foreign_registry,
)
from bibauthor.providers.openalex import OpenAlexProvider  # noqa: E402
from bibauthor.providers.semanticscholar import (  # noqa: E402
    SECONDARY_CONFIDENCE,
    SemanticScholarProvider,
)

FIXTURE_DIR = os.path.join(HERE, "fixtures", "cache")

CHIMERIC_ARXIV_ID = "2507.01413"
CHIMERIC_TITLE = "Evaluating LLM Agent Collusion in Double Auctions"
CHIMERIC_AUTHORS = [
    "Kushal Agrawal",
    "Verona Teo",
    "Juan J. Vazquez",
    "Sudarsh Kunnavakkam",
    "Vishak Srikanth",
    "Andy Liu",
]
QWEN_ARXIV_ID = "2412.15115"
QWEN_TITLE = "Qwen2.5 Technical Report"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def offline_client():
    cache = Cache(FIXTURE_DIR, offline=True)
    return HttpClient(cache, offline=True)


@pytest.fixture(scope="module")
def providers(offline_client):
    return {p.name: p for p in build_providers(offline_client)}


def fixture_files():
    return sorted(
        path
        for path in glob.glob(os.path.join(FIXTURE_DIR, "*.json"))
        if os.path.basename(path) != "INDEX.json"
    )


def read_fixture_body(url, params=None):
    return Cache(FIXTURE_DIR, offline=True).get(url, params)["body"]


class StubClient(object):
    """Minimal stand-in for :class:`HttpClient`, for paths with no fixture.

    Used only where the behaviour under test is a failure mode that would be
    dishonest to fake as a recorded response (a 404 on a DOI that really does
    exist, for instance).
    """

    def __init__(self, error):
        self.error = error

    def fetch_json(self, url, params=None):
        raise self.error

    def fetch_text(self, url, params=None):
        raise self.error


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_cache_key_is_stable_and_order_independent():
    cache = Cache(FIXTURE_DIR, offline=True)
    url = "https://api.example.org/works"
    expected = "7a3cea012b4505b26c0cf422819f273b9996091f204adbc64926f57dc78c72cb"
    assert cache.key(url, {"rows": 5, "select": "DOI"}) == expected
    # Parameter order must not change the key.
    assert cache.key(url, {"select": "DOI", "rows": 5}) == expected
    # And the key must be the digest of the documented canonical string.
    canonical = canonical_request(url, {"rows": 5, "select": "DOI"})
    assert canonical == "GET|%s|rows=5&select=DOI" % url
    assert (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected
    )
    assert cache.path_for(url, {"rows": 5, "select": "DOI"}).endswith(
        expected + ".json"
    )


def test_cache_miss_raises_and_offline_never_writes(tmp_path):
    cache = Cache(str(tmp_path / "nothing-here"), offline=True)
    with pytest.raises(CacheMiss):
        cache.get("https://api.example.org/works")
    cache.put("https://api.example.org/works", None, 200, {"a": 1}, "application/json")
    assert not os.path.isdir(str(tmp_path / "nothing-here"))


def test_ttl_is_ignored_when_ttl_days_is_none():
    ancient = {"recorded_at": "1999-01-01T00:00:00Z", "body": {}}
    assert Cache(FIXTURE_DIR).is_expired(ancient) is False
    assert Cache(FIXTURE_DIR, ttl_days=30).is_expired(ancient) is True


def test_every_fixture_file_is_well_formed():
    files = fixture_files()
    assert len(files) >= 11
    for path in files:
        with io.open(path, encoding="ascii") as handle:
            entry = json.load(handle)
        assert set(["url", "params", "status", "body"]) <= set(entry)
        assert isinstance(entry["status"], int)


# ---------------------------------------------------------------------------
# http client identity: no address, anywhere
# ---------------------------------------------------------------------------


def test_user_agent_carries_a_contact_url_and_no_address():
    assert DEFAULT_USER_AGENT == "bibauthor/0.1.0 (+%s)" % CONTACT_URL
    assert CONTACT_URL.startswith("https://")
    assert "mailto" not in DEFAULT_USER_AGENT
    assert "@" not in DEFAULT_USER_AGENT


EMAIL_SHAPE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")


def test_no_source_file_can_send_an_address():
    """A security property, so it is a test and not a convention.

    CrossRef's "polite pool" and OpenAlex's own response text both ask for a
    contact address. This tool is run over other people's bibliographies, and
    quietly spreading the operator's address through four third-party access
    logs is not a trade the operator agreed to. The guard is textual on
    purpose: it fails if anybody adds the parameter back.
    """
    offenders = []
    for path in sorted(glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True)):
        with io.open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        if "mailto" in text.lower():
            offenders.append((path, "mailto"))
        found = EMAIL_SHAPE.findall(text)
        if found:
            offenders.append((path, found))
    assert offenders == []


def test_semantic_scholar_is_rate_limited_harder_and_serialized():
    client = HttpClient(Cache(FIXTURE_DIR, offline=True), offline=True)
    assert HOST_MIN_INTERVAL["api.semanticscholar.org"] >= 3.0
    assert "api.semanticscholar.org" in SERIALIZED_HOSTS
    assert client.interval_for("api.semanticscholar.org") >= 3.0
    assert client.interval_for("api.crossref.org") >= 1.0
    # A stricter per-host floor must not be lowered by a smaller global value.
    lax = HttpClient(Cache(FIXTURE_DIR, offline=True), offline=True, min_interval=0.1)
    assert lax.interval_for("api.semanticscholar.org") >= 3.0


def test_cached_non_200_is_raised_as_an_http_error(offline_client):
    url = "https://api.crossref.org/works/10.48550/arxiv.2507.01413"
    with pytest.raises(HttpError) as excinfo:
        offline_client.fetch_json(url)
    assert excinfo.value.status == 404
    assert excinfo.value.not_found is True
    assert excinfo.value.from_cache is True


def test_offline_client_never_falls_back_to_the_network(offline_client):
    with pytest.raises(CacheMiss):
        offline_client.fetch_json("https://api.crossref.org/works/10.9999/nope")


# ---------------------------------------------------------------------------
# title similarity
# ---------------------------------------------------------------------------


def test_normalize_title_folds_case_punctuation_and_diacritics():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("Zi\u0301dek's Method") == normalize_title("Zidek s Method")
    assert normalize_title("  Deep   Residual-Learning ") == "deep residual learning"
    assert normalize_title("") == ""


def test_title_similarity_behaviour():
    assert title_similarity("Attention Is All You Need", "attention is all you need") == 1.0
    assert title_similarity("", "anything") == 0.0
    assert title_similarity("anything", "") == 0.0
    # A subtitle on one side only must stay well above the 0.80 gate.
    assert (
        title_similarity(
            "Qwen2.5 Technical Report",
            "Qwen2.5 Technical Report: A Family of Models",
        )
        > 0.8
    )
    # Two different papers must stay well below it.
    assert (
        title_similarity(
            "Evaluating LLM Agent Collusion in Double Auctions",
            "Observation of Gravitational Waves from a Binary Black Hole Merger",
        )
        < 0.5
    )
    for pair in [("a", "b"), ("same", "same"), ("Deep Learning", "Deep Learning")]:
        score = title_similarity(*pair)
        assert 0.0 <= score <= 1.0


def test_build_providers_order_and_validation(offline_client):
    built = build_providers(offline_client)
    assert [p.name for p in built] == list(PROVIDER_ORDER)
    subset = build_providers(offline_client, enabled=["openalex", "crossref"])
    assert [p.name for p in subset] == ["crossref", "openalex"]
    with pytest.raises(ValueError):
        build_providers(offline_client, enabled=["dblp"])


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def test_split_arxiv_id():
    assert split_arxiv_id("http://arxiv.org/abs/2412.15115v2") == {
        "id": "2412.15115",
        "version": 2,
    }
    assert split_arxiv_id("http://arxiv.org/abs/2507.01413") == {
        "id": "2507.01413",
        "version": None,
    }
    assert split_arxiv_id("nonsense") == {"id": "", "version": None}


def test_arxiv_parses_the_chimeric_case(providers):
    entry = Entry(key="chimeric", title=CHIMERIC_TITLE, arxiv_id=CHIMERIC_ARXIV_ID)
    records = providers["arxiv"].lookup(entry)
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, Record)
    assert record.source == "arxiv"
    assert record.title == CHIMERIC_TITLE
    assert record.authors == CHIMERIC_AUTHORS
    assert record.arxiv_id == CHIMERIC_ARXIV_ID
    assert record.identifier == "2507.01413v1"
    assert record.year == 2025
    assert record.date == "2025-07-02"
    assert record.confidence == 1.0
    assert record.from_cache is True
    assert any("primary_category" in note for note in record.notes)


def test_arxiv_splits_v1_from_the_latest_version(providers):
    """2412.15115 is v1 2024-12-19 and v2 2025-01-03.

    A citation that says 2024 is citing v1 and is not wrong, so the record has
    to carry both dates rather than only the one arXiv currently serves.
    """
    entry = Entry(key="qwen", title=QWEN_TITLE, arxiv_id=QWEN_ARXIV_ID)
    records = providers["arxiv"].lookup(entry)
    assert len(records) == 1
    record = records[0]
    assert record.identifier == "2412.15115v2"
    assert record.first_version_date == "2024-12-19"
    assert record.first_version_year == 2024
    assert record.date == "2025-01-03"
    assert record.year == 2025
    assert record.first_version_year != record.year
    assert any("v1 dated 2024-12-19" in note for note in record.notes)
    assert any(note == "arXiv version v2" for note in record.notes)
    # A large author list, and arXiv's own group prefix is preserved verbatim
    # rather than guessed at.
    assert len(record.authors) >= 40
    assert record.authors[0] == "Qwen"
    assert "An Yang" in record.authors


def test_arxiv_empty_body_is_unavailable_not_not_found(offline_client):
    """Plain http returns an empty body. That must never read as "no such paper"."""
    provider = ArxivProvider(offline_client)
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider._parse("", Entry(key="x"), from_cache=False)
    assert excinfo.value.reason == "empty-body"


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------


def test_crossref_particle_surname(providers):
    entry = Entry(key="alphago", doi="10.1038/nature16961")
    records = providers["crossref"].lookup(entry)
    assert len(records) == 1
    record = records[0]
    assert record.source == "crossref"
    assert record.doi == "10.1038/nature16961"
    assert record.year == 2016
    assert record.confidence == 1.0
    assert record.from_cache is True
    assert record.authors[0] == "Silver, David"
    # The particle stays attached to the surname, in "family, given" form.
    assert "van den Driessche, George" in record.authors


def test_crossref_diacritics(providers):
    entry = Entry(key="alphafold", doi="10.1038/s41586-021-03819-2")
    records = providers["crossref"].lookup(entry)
    record = records[0]
    assert record.year == 2021
    assert len(record.authors) == 34
    assert "\u017d\u00eddek, Augustin" in record.authors
    assert "Jumper, John" == record.authors[0]


def test_crossref_cjk_names(providers):
    entry = Entry(key="resnet", doi="10.1109/CVPR.2016.90")
    records = providers["crossref"].lookup(entry)
    record = records[0]
    assert record.authors == [
        "He, Kaiming",
        "Zhang, Xiangyu",
        "Ren, Shaoqing",
        "Sun, Jian",
    ]
    assert record.year == 2016
    assert record.date == "2016-06"


def test_crossref_consortium_author_list(providers):
    entry = Entry(key="gw150914", doi="10.1103/PhysRevLett.116.061102")
    records = providers["crossref"].lookup(entry)
    record = records[0]
    assert len(record.authors) > 1000
    # A group author arrives as {"name": ...} and is kept exactly as deposited:
    # a collaboration name is not a surname and must not be split into one.
    group = "LIGO Scientific Collaboration and Virgo Collaboration"
    assert group in record.authors
    assert record.authors[0] == "Abbott, B.\u2009P."


def test_crossref_search_path_scores_by_title(providers):
    entry = Entry(
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
    assert entry.doi is None
    records = providers["crossref"].lookup(entry)
    assert len(records) == 5
    assert all(r.source == "crossref" for r in records)
    # Sorted by confidence, and every hit here is a title-identical candidate,
    # so the identifier is the only thing separating them. That is exactly why
    # the verifier must not treat a search hit as an identity.
    assert records == sorted(records, key=lambda r: r.confidence, reverse=True)
    assert records[0].confidence == 1.0
    assert records[0].title == "Attention Is All You Need"
    assert records[0].authors[0] == "Vaswani, Ashish"
    assert all("bibliographic search" in " ".join(r.notes) for r in records)
    assert len({r.doi for r in records}) == 5


def test_crossref_404_on_an_arxiv_doi_is_the_wrong_registry(providers):
    """A 404 here is CrossRef saying "not mine", not "no such paper".

    arXiv DOIs under 10.48550 are registered through DataCite. Reporting this
    404 as a failed verification would manufacture a finding out of asking the
    wrong registry a question, so the provider refuses to answer at all.
    """
    entry = Entry(key="chimeric", doi="10.48550/arXiv.2507.01413")
    assert foreign_registry(entry.doi) is not None
    with pytest.raises(ProviderUnavailable) as excinfo:
        providers["crossref"].lookup(entry)
    assert excinfo.value.reason == WRONG_REGISTRY_REASON
    assert excinfo.value.provider == "crossref"
    message = str(excinfo.value)
    assert "wrong registry" in message
    assert "not evidence" in message


def test_crossref_404_on_an_ordinary_doi_is_a_clean_not_found():
    """The same status on a DOI CrossRef really owns means "not found"."""
    error = HttpError("HTTP 404", status=404, url="x", body="Resource not found.")
    provider = CrossRefProvider(StubClient(error))
    entry = Entry(key="missing", doi="10.1234/does-not-exist")
    assert foreign_registry(entry.doi) is None
    assert provider.lookup(entry) == []


def test_crossref_non_404_errors_are_unavailable():
    error = HttpError("HTTP 503", status=503, url="x", body="")
    provider = CrossRefProvider(StubClient(error))
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.lookup(Entry(key="e", doi="10.1234/x"))
    assert excinfo.value.reason == "http-503"


def test_crossref_query_is_title_plus_surname_plus_year():
    entry = Entry(
        key="e",
        title="Attention is all you need",
        authors=["Vaswani, Ashish"],
        year=2017,
    )
    assert (
        CrossRefProvider.query_for(entry)
        == "Attention is all you need Vaswani 2017"
    )


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def test_openalex_by_doi_preserves_author_order(providers):
    entry = Entry(key="chimeric", title=CHIMERIC_TITLE, arxiv_id=CHIMERIC_ARXIV_ID)
    records = providers["openalex"].lookup(entry)
    assert len(records) == 1
    record = records[0]
    assert record.source == "openalex"
    assert record.authors == CHIMERIC_AUTHORS
    assert record.doi == "10.48550/arxiv.2507.01413"
    assert record.arxiv_id == CHIMERIC_ARXIV_ID
    assert record.year == 2025
    assert record.confidence == 1.0
    assert record.from_cache is True
    assert record.identifier.startswith("W")


def test_openalex_search_path(providers):
    entry = Entry(key="qwen", title=QWEN_TITLE)
    assert entry.doi is None and entry.arxiv_id is None
    records = providers["openalex"].lookup(entry)
    assert records
    top = records[0]
    assert top.title == QWEN_TITLE
    assert top.confidence >= 0.9
    assert len(top.authors) >= 40
    assert records == sorted(records, key=lambda r: r.confidence, reverse=True)


def test_openalex_notice_is_stripped_before_it_reaches_disk():
    """SECURITY. OpenAlex asks the caller, inside the response body, to send
    the end user's address to an OpenAlex endpoint. That is untrusted data, it
    is refused, and it must not survive into the cache, a log or a report."""
    body = read_fixture_body(
        "https://api.openalex.org/works",
        {"search": QWEN_TITLE, "per-page": 5},
    )
    assert body["meta"]["notice"] == NOTICE_PLACEHOLDER


def test_no_fixture_file_carries_the_notice_or_an_api_key_prompt():
    for path in fixture_files() + [os.path.join(FIXTURE_DIR, "INDEX.json")]:
        with io.open(path, encoding="ascii") as handle:
            text = handle.read()
        lowered = text.lower()
        assert "api-keys" not in lowered, path
        assert "api_key" not in lowered, path
        assert "apikey" not in lowered, path
        assert "mailto" not in lowered, path
        assert not EMAIL_SHAPE.search(text), path


def test_sanitize_json_replaces_notice_fields_at_any_depth():
    payload = {
        "meta": {"count": 1, "notice": "Please register by sending us an address"},
        "results": [{"nested": {"notice": "another one"}}],
        "keep": "untouched",
    }
    cleaned = sanitize_json(payload)
    assert cleaned["meta"]["notice"] == NOTICE_PLACEHOLDER
    assert cleaned["results"][0]["nested"]["notice"] == NOTICE_PLACEHOLDER
    assert cleaned["meta"]["count"] == 1
    assert cleaned["keep"] == "untouched"
    # The original is not mutated in place.
    assert payload["meta"]["notice"] != NOTICE_PLACEHOLDER


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------


def test_semanticscholar_is_a_discounted_secondary_source(providers):
    entry = Entry(key="chimeric", title=CHIMERIC_TITLE, arxiv_id=CHIMERIC_ARXIV_ID)
    records = providers["semanticscholar"].lookup(entry)
    assert len(records) == 1
    record = records[0]
    assert record.source == "semanticscholar"
    assert record.title == CHIMERIC_TITLE
    assert record.arxiv_id == CHIMERIC_ARXIV_ID
    assert record.year == 2025
    assert record.from_cache is True
    # An identifier hit, discounted so a primary source wins a tie.
    assert record.confidence == pytest.approx(SECONDARY_CONFIDENCE)
    assert record.confidence < 1.0
    # VERIFIED: it abbreviates given names. Left alone on purpose, because the
    # name matcher is what is supposed to absorb that variant.
    assert "J. V\u00e1zquez" in record.authors
    assert "Juan J. Vazquez" not in record.authors
    assert len(record.authors) == len(CHIMERIC_AUTHORS)


def test_semanticscholar_404_is_a_clean_not_found():
    error = HttpError("HTTP 404", status=404, url="x", body="")
    provider = SemanticScholarProvider(StubClient(error))
    assert provider.lookup(Entry(key="e", doi="10.1234/x")) == []


# ---------------------------------------------------------------------------
# the "could not ask" contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(PROVIDER_ORDER))
def test_offline_cache_miss_raises_provider_unavailable(providers, name):
    """Never report "not found" when the truth is "no question was asked"."""
    entry = Entry(
        key="uncached",
        title="A Paper That Was Never Recorded Into The Fixtures",
        doi="10.1234/uncached-on-purpose",
    )
    with pytest.raises(ProviderUnavailable) as excinfo:
        providers[name].lookup(entry)
    assert excinfo.value.provider == name
    assert excinfo.value.reason == "offline"
    # CacheMiss must not escape lookup(): the verifier only handles the one.
    assert not isinstance(excinfo.value, CacheMiss)


def test_provider_unavailable_is_not_an_empty_list(providers):
    """The two outcomes are different types on purpose, so no caller can
    accidentally treat "could not ask" as "nothing found"."""
    assert issubclass(ProviderUnavailable, Exception)
    arxiv = ArxivProvider(HttpClient(Cache(FIXTURE_DIR, offline=True), offline=True))
    # No identifier and no title: nothing to ask, so a clean empty answer.
    assert arxiv.lookup(Entry(key="blank")) == []
    assert OpenAlexProvider(arxiv.client).lookup(Entry(key="blank")) == []
    assert CrossRefProvider(arxiv.client).lookup(Entry(key="blank")) == []
    assert SemanticScholarProvider(arxiv.client).lookup(Entry(key="blank")) == []
