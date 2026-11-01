"""Tests for the bibliography input side: latex.py, bibtex.py, csljson.py.

Every expected non-ASCII string is written as a Python ``\\u`` escape, because
this file is pure ASCII. The one fixture that is raw UTF-8 is
``tests/fixtures/bib/unicode_names.bib``, and the test that reads it asserts
against escapes too.
"""

from __future__ import annotations

import os

import pytest

from bibauthors.entry import Entry
from bibauthors.latex import (
    decode_latex,
    is_brace_protected,
    strip_braces,
    strip_braces_report,
)
from bibauthors.parsers import ParseError, load_bibliography, sniff_format
from bibauthors.parsers.bibtex import parse_bibtex
from bibauthors.parsers.csljson import parse_csl_json

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BIB = os.path.join(FIXTURES, "bib")
CSL = os.path.join(FIXTURES, "csl")


def bib(name):
    return os.path.join(BIB, name)


def csl(name):
    return os.path.join(CSL, name)


def by_key(entries):
    return dict((entry.key, entry) for entry in entries)


# ---------------------------------------------------------------------------
# latex.py
# ---------------------------------------------------------------------------

ACCENT_CASES = [
    ("\\'e", "\u00e9"),
    ("\\'{e}", "\u00e9"),
    ("{\\'e}", "\u00e9"),
    ("{\\'{e}}", "\u00e9"),
    ('\\"o', "\u00f6"),
    ('\\"{o}', "\u00f6"),
    ('{\\"o}', "\u00f6"),
    ("\\`a", "\u00e0"),
    ("\\^u", "\u00fb"),
    ("\\~n", "\u00f1"),
    ("\\c{c}", "\u00e7"),
    ("\\c c", "\u00e7"),
    ("\\v{s}", "\u0161"),
    ("\\v{S}", "\u0160"),
    ("\\u{a}", "\u0103"),
    ("\\H{o}", "\u0151"),
    ("\\.{z}", "\u017c"),
    ("\\={o}", "\u014d"),
    ("\\d{a}", "\u1ea1"),
    ("\\b{a}", "a\u0331"),
    ("\\r{a}", "\u00e5"),
    ("\\k{a}", "\u0105"),
    ("\\t{oo}", "o\u0361o"),
    ("\\'{\\i}", "\u00ed"),
    ("\\^{\\j}", "\u0135"),
]

SPECIAL_CASES = [
    ("\\ss", "\u00df"),
    ("\\o", "\u00f8"),
    ("\\O", "\u00d8"),
    ("\\l", "\u0142"),
    ("\\L", "\u0141"),
    ("\\aa", "\u00e5"),
    ("\\AA", "\u00c5"),
    ("\\ae", "\u00e6"),
    ("\\AE", "\u00c6"),
    ("\\oe", "\u0153"),
    ("\\OE", "\u0152"),
    ("\\dh", "\u00f0"),
    ("\\DH", "\u00d0"),
    ("\\th", "\u00fe"),
    ("\\TH", "\u00de"),
    ("\\i", "\u0131"),
    ("\\j", "\u0237"),
    ("\\dj", "\u0111"),
    ("\\DJ", "\u0110"),
]

PUNCTUATION_CASES = [
    ("a---b", "a\u2014b"),
    ("a--b", "a\u2013b"),
    ("``quoted''", "\u201cquoted\u201d"),
    ("a~b", "a\u00a0b"),
    ("\\&", "&"),
    ("\\%", "%"),
    ("\\$", "$"),
    ("\\#", "#"),
    ("\\_", "_"),
    ("\\{", "{"),
    ("\\}", "}"),
]


@pytest.mark.parametrize("source,expected", ACCENT_CASES)
def test_decode_latex_accents(source, expected):
    assert decode_latex(source) == expected


@pytest.mark.parametrize("source,expected", SPECIAL_CASES)
def test_decode_latex_special_letters(source, expected):
    assert decode_latex(source) == expected


@pytest.mark.parametrize("source,expected", PUNCTUATION_CASES)
def test_decode_latex_dashes_quotes_and_escapes(source, expected):
    assert decode_latex(source) == expected


def test_decode_latex_keeps_surrounding_text():
    assert decode_latex("Jos\\'e Mart\\'{i}nez") == "Jos\u00e9 Mart\u00ednez"
    assert decode_latex("Stra{\\ss}er, Hans") == "Stra\u00dfer, Hans"
    assert decode_latex("Erd{\\H{o}}s, Paul") == "Erd\u0151s, Paul"
    assert decode_latex("{\\'E}mile Durkheim") == "\u00c9mile Durkheim"
    assert decode_latex("W{\\l}adys{\\l}aw") == "W\u0142adys\u0142aw"


def test_decode_latex_math_mode_strips_delimiters_only():
    assert decode_latex("x $a^2$ y") == "x a^2 y"
    assert decode_latex("$$n \\log n$$") == "n \\log n"
    # An unmatched dollar must not swallow the rest of the field.
    assert decode_latex("costs $5 to run") == "costs $5 to run"


def test_decode_latex_unknown_commands_keep_their_argument():
    assert decode_latex("\\textbf{Smith}") == "Smith"
    assert decode_latex("\\emph{x}") == "x"
    assert decode_latex("\\relax") == ""
    assert decode_latex("\\noopsort{1990}1990") == "19901990"
    assert decode_latex("a \\textbf{B} c") == "a B c"


@pytest.mark.parametrize(
    "source",
    [
        "",
        "{",
        "}",
        "\\",
        "\\'",
        "${",
        "{\\",
        "}{",
        "\\c",
        "\\t{",
        "{{{{",
        "a\\",
        "$",
        "\\'{",
        "~~~",
        "--",
        "\\u",
    ],
)
def test_decode_latex_is_total(source):
    """Malformed input must never raise, whatever it looks like."""
    assert isinstance(decode_latex(source), str)


def test_decode_latex_tolerates_non_strings():
    assert decode_latex(None) == ""
    assert decode_latex(2024) == "2024"


def test_strip_braces_preserves_inner_text():
    assert strip_braces("{Smith}") == "Smith"
    assert strip_braces("{The LIGO Collaboration}") == "The LIGO Collaboration"
    assert strip_braces("{{NASA}}") == "NASA"
    assert strip_braces("The {TeX}book") == "The TeXbook"
    assert strip_braces("unbalanced {brace") == "unbalanced brace"
    assert strip_braces("") == ""


def test_brace_protection_is_reported_separately():
    assert is_brace_protected("{The LIGO Scientific Collaboration}") is True
    assert is_brace_protected("{Barnes and Noble, Inc.}") is True
    assert is_brace_protected("  {Protected}  ") is True
    assert is_brace_protected("{A} and {B}") is False
    assert is_brace_protected("Smith") is False
    assert is_brace_protected("") is False
    text, protected = strip_braces_report("{The LIGO Scientific Collaboration}")
    assert text == "The LIGO Scientific Collaboration"
    assert protected is True


# ---------------------------------------------------------------------------
# bibtex.py: structure
# ---------------------------------------------------------------------------


def test_clean_file_parses_every_entry():
    entries = load_bibliography(bib("clean.bib"))
    assert [entry.key for entry in entries] == [
        "jacob2026memory",
        "smith2024scaling",
        "knuth1984tex",
        "qwen2024report",
    ]
    first = entries[0]
    assert first.entry_type == "article"
    assert first.title == "The Memory Wall at the Edge of Language"
    assert first.authors == ["Jacob, Manu Nicholas"]
    assert first.year == 2026
    assert first.doi == "10.1109/tc.2026.1234567"
    assert first.venue == "IEEE Transactions on Computers"
    assert first.source_line == 3
    assert first.source_file.endswith("clean.bib")
    assert first.raw["pages"] == "1\u201314"
    assert entries[1].url == "https://example.org/papers/scaling"
    assert entries[1].venue == "Proceedings of the Conference on Systems"
    assert entries[2].title == "The TeXbook"
    assert entries[2].venue == "Addison-Wesley"


def test_entry_type_lowercased_and_key_case_preserved():
    entries = parse_bibtex("@ARTICLE{MixedCaseKey, author = {A, B}, year = {2000}}")
    assert entries[0].entry_type == "article"
    assert entries[0].key == "MixedCaseKey"


def test_string_macros_expand_and_concatenate():
    # by_key would collapse the two alpha2020 entries, so index the first one.
    first = load_bibliography(bib("messy.bib"))[0]
    assert first.key == "alpha2020"
    assert first.venue == "IEEE Transactions on Computers (Special Issue)"
    # The second alpha2020 uses the other macro, undecorated.
    assert load_bibliography(bib("messy.bib"))[2].venue == "ACM Computing Surveys"
    text = (
        '@string{jmlr = "Journal of Machine Learning Research"}\n'
        "@article{k, journal = jmlr # \" 24\", author = {A, B}, year = 2020}\n"
    )
    assert parse_bibtex(text)[0].venue == "Journal of Machine Learning Research 24"


def test_bare_numeric_and_quoted_values_and_nested_braces():
    entries = load_bibliography(bib("messy.bib"))
    alpha = entries[0]
    assert alpha.year == 2020
    assert alpha.raw["pages"] == "1234"
    assert alpha.authors == ["Alpha, Ada", "Beta, Bob"]
    assert alpha.title == "A Quoted Title with nested braces inside"


def test_preamble_and_comment_blocks_are_skipped():
    keys = [entry.key for entry in load_bibliography(bib("messy.bib"))]
    assert "preamble" not in keys
    assert "comment" not in keys
    assert keys[0] == "alpha2020"


def test_stray_text_between_entries_is_ignored():
    text = (
        "Leftover prose from a merge. Contact: nobody@example.invalid\n"
        "@article{good, author = {A, B}, year = {2001}}\n"
        "More prose, and an @ sign on its own.\n"
    )
    entries = parse_bibtex(text)
    assert [entry.key for entry in entries] == ["good"]


def test_forward_crossref_inherits_missing_fields():
    entries = by_key(load_bibliography(bib("messy.bib")))
    child = entries["child2021"]
    # parent2021 appears AFTER child2021 in the file.
    assert child.authors == ["Parent, Pat"]
    assert child.year == 2021
    assert child.raw["booktitle"] == "Proceedings of the Parent Conference"
    assert child.raw["publisher"] == "Springer"
    assert child.raw["editor"] == "Delta, Dan"
    # The child's own fields win over the parent's.
    assert child.title == "The Child Paper"


def test_crossref_to_a_missing_parent_warns():
    entries = parse_bibtex("@inproceedings{orphan, title = {T}, crossref = {nowhere}}")
    assert any("not found" in w for w in entries[0].warnings)


@pytest.mark.parametrize("key", ["loopa", "loopb", "selfloop"])
def test_crossref_cycles_terminate_and_warn(key):
    entries = by_key(load_bibliography(bib("messy.bib")))
    assert any("cycle" in w for w in entries[key].warnings)


def test_duplicate_keys_are_all_kept_and_cross_warned():
    entries = [e for e in load_bibliography(bib("messy.bib")) if e.key == "alpha2020"]
    assert len(entries) == 2
    lines = sorted(entry.source_line for entry in entries)
    assert lines == [14, 27]
    first, second = entries[0], entries[1]
    assert "duplicate citation key 'alpha2020' also at line 27" in first.warnings
    assert "duplicate citation key 'alpha2020' also at line 14" in second.warnings
    assert second.title == "A Different Paper with the Same Citation Key"


def test_duplicate_key_warning_names_every_other_line():
    text = (
        "@article{dup, year = {2001}, author = {A, B}}\n"
        "@article{dup, year = {2002}, author = {A, B}}\n"
        "@article{dup, year = {2003}, author = {A, B}}\n"
    )
    entries = parse_bibtex(text)
    assert len(entries) == 3
    assert "duplicate citation key 'dup' also at line 2, 3" in entries[0].warnings
    assert "duplicate citation key 'dup' also at line 1, 3" in entries[1].warnings
    assert "duplicate citation key 'dup' also at line 1, 2" in entries[2].warnings


def test_missing_final_brace_still_yields_the_entry():
    entries = by_key(load_bibliography(bib("messy.bib")))
    last = entries["unterminated2022"]
    assert last.authors == ["Omega, Omar"]
    assert last.title == "An Entry Missing Its Final Brace"
    assert last.year == 2022
    assert any("missing its closing" in w for w in last.warnings)


def test_trailing_comma_before_the_closing_brace():
    entries = parse_bibtex("@misc{t, author = {A, B}, year = {1999},}")
    assert entries[0].authors == ["A, B"]
    assert entries[0].year == 1999


def test_crlf_line_endings_do_not_shift_line_numbers():
    with open(bib("messy.bib"), "rb") as handle:
        data = handle.read()
    assert b"\r\n" in data, "the fixture is supposed to contain CRLF"
    entries = load_bibliography(bib("messy.bib"))
    assert entries[0].source_line == 14


def test_bom_is_tolerated():
    text = "\ufeff@article{bomkey, author = {A, B}, year = {2024}}"
    assert parse_bibtex(text)[0].key == "bomkey"
    assert load_bibliography(text, is_text=True)[0].key == "bomkey"


def test_parenthesis_delimited_entry():
    entries = parse_bibtex("@article(paren, author = {A, B}, year = {2024})")
    assert entries[0].key == "paren"
    assert entries[0].year == 2024


def test_empty_and_garbage_free_input():
    assert parse_bibtex("") == []
    assert parse_bibtex("no entries here at all") == []


# ---------------------------------------------------------------------------
# bibtex.py: author lists
# ---------------------------------------------------------------------------


def test_and_splits_only_at_the_top_level():
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    assert entries["ligo2016"].authors == ["The LIGO Scientific Collaboration"]
    assert entries["barnes1999"].authors == ["Barnes and Noble, Inc."]
    assert len(entries["accents2026"].authors) == 10
    assert entries["lastfirst2020"].authors == ["Lastname, Firstname"]
    assert entries["vonneumann1945"].authors == ["von Neumann, John"]


def test_a_and_b_is_two_authors():
    entries = parse_bibtex("@misc{k, author = {A and B}, year = {2020}}")
    assert entries[0].authors == ["A", "B"]


def test_brace_protected_authors_are_recorded_as_a_hint():
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    assert entries["ligo2016"].raw["_braced_author_indices"] == "0"
    assert entries["barnes1999"].raw["_braced_author_indices"] == "0"
    # The hint has to stay a plain string, because Entry.raw is Dict[str, str].
    assert isinstance(entries["ligo2016"].raw["_braced_author_indices"], str)
    # Nothing protected, nothing recorded.
    assert "_braced_author_indices" not in entries["accents2026"].raw


def test_brace_protected_hint_tracks_the_right_indices():
    text = (
        "@misc{k, author = {Smith, Jane and {The LIGO Scientific Collaboration} "
        "and Doe, John and {Barnes and Noble, Inc.}}, year = {2020}}"
    )
    entry = parse_bibtex(text)[0]
    assert entry.authors == [
        "Smith, Jane",
        "The LIGO Scientific Collaboration",
        "Doe, John",
        "Barnes and Noble, Inc.",
    ]
    assert entry.raw["_braced_author_indices"] == "1,3"


def test_uppercase_and_separator_is_accepted():
    entries = parse_bibtex("@misc{k, author = {Smith, J. AND Doe, J.}, year = {2020}}")
    assert entries[0].authors == ["Smith, J.", "Doe, J."]


def test_semicolon_separator_only_without_a_top_level_and():
    entries = by_key(load_bibliography(bib("messy.bib")))
    assert entries["semicolons2023"].authors == [
        "Iota, Ida",
        "Kappa, Ken",
        "Lambda, Lee",
    ]
    # With a top-level " and " present the semicolon is left inside the name.
    mixed = parse_bibtex("@misc{k, author = {A; B and C}, year = {2020}}")[0]
    assert mixed.authors == ["A; B", "C"]


def test_multiline_author_field_keeps_word_order():
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    assert entries["accents2026"].authors[0] == "Bront\u00eb, Charlotte"
    assert entries["accents2026"].authors[3] == "Jos\u00e9 Mart\u00ednez"


@pytest.mark.parametrize("key", ["truncated2024", "etal2024"])
def test_truncation_marker_sets_the_flag_and_leaves_the_list(key):
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    entry = entries[key]
    assert entry.truncated is True
    assert entry.authors == ["Alpha, Ann", "Beta, Ben"]
    assert "others" not in entry.authors
    assert "et al." not in entry.authors


def test_untruncated_list_leaves_the_flag_alone():
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    assert entries["lastfirst2020"].truncated is False


def test_editor_only_entry_is_not_a_fabrication():
    entries = by_key(load_bibliography(bib("messy.bib")))
    entry = entries["editoronly1999"]
    assert entry.authors == []
    assert entry.raw["editor"] == "Epsilon, Eve"
    assert any("cited by its editor" in w for w in entry.warnings)


def test_entry_with_neither_author_nor_editor_warns():
    entry = parse_bibtex("@misc{k, title = {T}, year = {2020}}")[0]
    assert entry.authors == []
    assert "no author field" in entry.warnings


# ---------------------------------------------------------------------------
# bibtex.py: LaTeX-encoded names
# ---------------------------------------------------------------------------


def test_latex_encoded_author_names_decode():
    entries = by_key(load_bibliography(bib("latex_names.bib")))
    assert entries["accents2026"].authors == [
        "Bront\u00eb, Charlotte",
        "Erd\u0151s, Paul",
        "G\u00f6del, Kurt",
        "Jos\u00e9 Mart\u00ednez",
        "\u00c9mile Durkheim",
        "Stra\u00dfer, Hans",
        "W\u0142adys\u0142aw Szpilman",
        "Ng\u00f4, B\u0103o",
        "\u0160koda, Jan",
        "Ha\u00e7\u0131, Mehmet",
    ]


def test_raw_utf8_file_parses_without_escapes():
    entries = by_key(load_bibliography(bib("unicode_names.bib")))
    assert entries["unicode2026"].authors == [
        "M\u00fcller, J\u00fcrgen",
        "\u00d8stergaard, S\u00f8ren",
        "\u7530\u4e2d\u592a\u90ce",
        "\u0418\u0432\u0430\u043d\u043e\u0432, \u0418\u0432\u0430\u043d",
        "\u0141ukasiewicz, Jan",
    ]
    assert entries["mixed2025"].authors == [
        "Dvo\u0159\u00e1k, Anton\u00edn",
        "Erd\u0151s, P\u00e1l",
    ]
    assert entries["unicode2026"].year == 2026


# ---------------------------------------------------------------------------
# bibtex.py: years
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,expected",
    [
        ("{2024}", 2024),
        ("2024", 2024),
        ("{2024a}", 2024),
        ("{\\noopsort{1990}1990}", 1990),
        ('"1999"', 1999),
    ],
)
def test_year_spellings(field, expected):
    text = "@misc{k, author = {A, B}, year = %s}" % field
    assert parse_bibtex(text)[0].year == expected


def test_year_in_press_leaves_none_and_warns():
    entries = by_key(load_bibliography(bib("messy.bib")))
    entry = entries["inpress2026"]
    assert entry.year is None
    assert any("no 4-digit year" in w for w in entry.warnings)


def test_date_field_used_when_year_is_absent():
    entries = by_key(load_bibliography(bib("messy.bib")))
    entry = entries["datedonly2024"]
    assert entry.year == 2024
    assert entry.raw["date"] == "2024-12-19"
    assert not any("year" in w for w in entry.warnings)


def test_noopsort_year_from_the_fixture():
    entries = by_key(load_bibliography(bib("messy.bib")))
    assert entries["sorted1990"].year == 1990


# ---------------------------------------------------------------------------
# arXiv id extraction (done by Entry, fed by the parsers)
# ---------------------------------------------------------------------------


def test_arxiv_id_from_eprint():
    entries = by_key(load_bibliography(bib("clean.bib")))
    assert entries["qwen2024report"].arxiv_id == "2412.15115"


def test_arxiv_id_from_an_arxiv_doi():
    text = (
        "@misc{k, author = {A, B}, year = {2025}, "
        "doi = {10.48550/arXiv.2507.01413}}"
    )
    entry = parse_bibtex(text)[0]
    assert entry.doi == "10.48550/arxiv.2507.01413"
    assert entry.arxiv_id == "2507.01413"


def test_arxiv_id_from_a_url():
    text = "@misc{k, author = {A, B}, year = {2025}, url = {https://arxiv.org/abs/2507.01413v2}}"
    entry = parse_bibtex(text)[0]
    assert entry.url == "https://arxiv.org/abs/2507.01413v2"
    assert entry.arxiv_id == "2507.01413"


def test_arxiv_id_from_a_note():
    text = "@misc{k, author = {A, B}, year = {2025}, note = {arXiv:2507.01413}}"
    assert parse_bibtex(text)[0].arxiv_id == "2507.01413"


def test_old_style_arxiv_id():
    text = "@misc{k, author = {A, B}, year = {2007}, eprint = {cs.LG/0701001}}"
    assert parse_bibtex(text)[0].arxiv_id == "cs.LG/0701001"


def test_url_and_doi_fields_are_not_latex_decoded():
    text = (
        "@misc{k, author = {A, B}, year = {2024}, "
        "url = {http://example.org/~user/a_b-c~d}, "
        "doi = {10.1000/x\\_y}}"
    )
    entry = parse_bibtex(text)[0]
    assert entry.url == "http://example.org/~user/a_b-c~d"
    assert entry.doi == "10.1000/x_y"
    assert "\u00a0" not in entry.url


# Regression test for a real defect found while building this suite.
# extract_arxiv_id originally ran an unanchored 'NNNN.NNNNN' search over every
# candidate field, so an ordinary IEEE DOI such as 10.1109/TC.2024.3401234
# produced the phantom arXiv id 2024.34012 and sent the arXiv provider chasing
# a paper that does not exist. Real IEEE DOIs have exactly that shape, so this
# would have fired on a large share of real bibliographies. entry.py now
# requires either a 10.48550/arXiv.* DOI, an explicit arXiv marker, or a
# candidate that IS the bare id.
def test_non_arxiv_doi_must_not_produce_an_arxiv_id():
    entries = by_key(load_bibliography(bib("clean.bib")))
    assert entries["jacob2026memory"].arxiv_id is None


# ---------------------------------------------------------------------------
# The two real-world regression fixtures
# ---------------------------------------------------------------------------


def test_chimeric_fixture_parses_into_the_wrong_list_verbatim():
    """The 29 Oct 2026 case. The stored list is wrong on purpose."""
    entries = load_bibliography(bib("chimeric.bib"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.key == "collusion2025"
    assert entry.title == "Evaluating LLM Agent Collusion in Double Auctions"
    assert entry.year == 2025
    assert entry.arxiv_id == "2507.01413"
    assert entry.raw["archiveprefix"] == "arXiv"
    assert entry.truncated is False
    assert entry.authors == [
        "Agrawal, Kushal",
        "Liu, Andy",
        "Fried, Daniel",
        "Sycara, Katia",
        "Jacobs, Abigail",
        "Oesterheld, Caspar",
        "Conitzer, Vincent",
    ]


def test_wrong_year_fixture_stores_the_v2_year():
    entries = load_bibliography(bib("wrong_year.bib"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.key == "qwen25report"
    assert entry.title == "Qwen2.5 Technical Report"
    assert entry.arxiv_id == "2412.15115"
    # arXiv v1 is 2024-12-19; the bibliography says 2025, which is v2.
    assert entry.year == 2025
    assert entry.truncated is True
    assert entry.authors == ["Yang, An", "Yang, Baosong", "Zhang, Beichen"]


# ---------------------------------------------------------------------------
# csljson.py
# ---------------------------------------------------------------------------


def test_csl_bare_array():
    entries = load_bibliography(csl("sample.json"))
    assert [entry.key for entry in entries] == [
        "jacob2026memory",
        "ligo2016",
        "mueller2020",
        "undated2026",
    ]
    first = entries[0]
    assert first.entry_type == "article-journal"
    assert first.title == "The Memory Wall at the Edge of Language"
    assert first.authors == ["Jacob, Manu Nicholas"]
    assert first.year == 2026
    assert first.doi == "10.1109/tc.2026.1234567"
    assert first.venue == "IEEE Transactions on Computers"
    assert first.url == "https://doi.org/10.1109/TC.2026.1234567"
    assert first.source_file.endswith("sample.json")


def test_csl_literal_author_is_kept_whole():
    entries = by_key(load_bibliography(csl("sample.json")))
    assert entries["ligo2016"].authors == ["The LIGO Scientific Collaboration"]


def test_csl_family_given_order_and_unicode():
    entries = by_key(load_bibliography(csl("sample.json")))
    assert entries["mueller2020"].authors == [
        "M\u00fcller, J\u00fcrgen",
        "\u00d8stergaard, S\u00f8ren",
    ]
    assert entries["mueller2020"].title == "Ein Buch \u00fcber Umlaute"
    assert entries["mueller2020"].venue == "Beispiel Verlag"


def test_csl_issued_raw_and_string_date_parts():
    entries = by_key(load_bibliography(csl("sample.json")))
    assert entries["ligo2016"].year == 2016
    assert entries["mueller2020"].year == 2020
    assert entries["undated2026"].year is None
    assert any("year" in w for w in entries["undated2026"].warnings)


def test_csl_wrapped_in_references():
    entries = load_bibliography(csl("wrapped.json"))
    assert [entry.key for entry in entries] == ["collusion2025", "qwen25report"]
    entry = entries[0]
    assert entry.authors == [
        "Agrawal, Kushal",
        "Teo, Verona",
        "Vazquez, Juan J.",
        "Kunnavakkam, Sudarsh",
        "Srikanth, Vishak",
        "Liu, Andy",
    ]
    assert entry.year == 2025
    # The arXiv id comes out of the URL here.
    assert entry.arxiv_id == "2507.01413"
    second = entries[1]
    assert second.doi == "10.48550/arxiv.2412.15115"
    assert second.arxiv_id == "2412.15115"
    assert second.authors[2] == "the Qwen Team"


def test_csl_items_wrapper_is_also_accepted():
    text = '{"items": [{"id": "x", "title": "T", "author": [{"literal": "Group"}]}]}'
    entries = parse_csl_json(text)
    assert len(entries) == 1
    assert entries[0].key == "x"
    assert entries[0].authors == ["Group"]


def test_csl_missing_id_gets_a_placeholder_and_a_warning():
    entries = parse_csl_json('[{"title": "No Id Here"}]')
    assert entries[0].key == "csl-item-1"
    assert any("no 'id'" in w for w in entries[0].warnings)


def test_csl_malformed_json_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        parse_csl_json("{not json at all,}")
    assert "not valid JSON" in str(excinfo.value)


def test_csl_wrong_shape_raises_value_error():
    with pytest.raises(ValueError):
        parse_csl_json('{"unexpected": 42}')
    with pytest.raises(ValueError):
        parse_csl_json("[1, 2, 3]")


def test_csl_empty_text_is_empty_list():
    assert parse_csl_json("") == []
    assert parse_csl_json("   ") == []


# ---------------------------------------------------------------------------
# parsers/__init__.py: sniffing and errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@article{k,}", "bibtex"),
        ("  \n @misc{k,}", "bibtex"),
        ("% a comment first\n@misc{k,}", "bibtex"),
        ("\ufeff@misc{k,}", "bibtex"),
        ("[]", "csljson"),
        ("  [{}]", "csljson"),
        ('{"references": []}', "csljson"),
        ("\ufeff[]", "csljson"),
        ("hello world", None),
        ("", None),
    ],
)
def test_sniff_format_from_content(text, expected):
    assert sniff_format(text) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("refs.bib", "bibtex"),
        ("refs.bibtex", "bibtex"),
        ("refs.json", "csljson"),
        ("refs.txt", None),
    ],
)
def test_sniff_format_falls_back_to_the_extension(path, expected):
    assert sniff_format("nothing conclusive here", path) == expected


def test_load_bibliography_sniffs_both_formats_from_text():
    bibtex = load_bibliography(
        "@misc{k, author = {A, B}, year = {2020}}", is_text=True
    )
    assert bibtex[0].key == "k"
    assert bibtex[0].source_file == ""
    csljson = load_bibliography('[{"id": "k", "title": "T"}]', is_text=True)
    assert csljson[0].key == "k"


def test_load_bibliography_honours_an_explicit_format():
    entries = load_bibliography(bib("clean.bib"), fmt="bib")
    assert len(entries) == 4
    entries = load_bibliography(csl("sample.json"), fmt="csl-json")
    assert len(entries) == 4


def test_load_bibliography_rejects_an_unknown_format_name():
    with pytest.raises(ParseError):
        load_bibliography(bib("clean.bib"), fmt="endnote")


def test_parse_error_on_garbage():
    with pytest.raises(ParseError):
        load_bibliography("this is not a bibliography at all", is_text=True)
    with pytest.raises(ParseError):
        load_bibliography("", is_text=True)


def test_parse_error_on_a_missing_file():
    with pytest.raises(ParseError):
        load_bibliography(bib("there-is-no-such-file.bib"))


def test_parse_error_on_broken_json_through_the_dispatcher(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"references": [1, 2]}', encoding="utf-8")
    with pytest.raises(ParseError):
        load_bibliography(str(path))


def test_parse_error_is_a_value_error():
    assert issubclass(ParseError, ValueError)


def test_latin1_file_still_loads(tmp_path):
    path = tmp_path / "latin1.bib"
    raw = "@misc{k, author = {M\u00fcller, J}, year = {2020}}".encode("latin-1")
    path.write_bytes(raw)
    entries = load_bibliography(str(path))
    assert entries[0].authors == ["M\u00fcller, J"]


def test_every_entry_is_an_entry_instance():
    for name in ("clean.bib", "messy.bib", "latex_names.bib", "chimeric.bib"):
        for entry in load_bibliography(bib(name)):
            assert isinstance(entry, Entry)
            assert isinstance(entry.raw, dict)
            assert all(isinstance(value, str) for value in entry.raw.values())
            assert all(isinstance(name_, str) for name_ in entry.authors)
