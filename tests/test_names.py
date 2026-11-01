"""Tests for the author-list matching engine.

Run with::

    PYTHONPATH=src python -m pytest tests/test_names.py -q

This file is pure ASCII on purpose, like every other source file in the
repository: non-ASCII name data is written as Python ``\\u`` escapes so the
test suite survives being opened with the wrong codec on Windows.

The tables below are the specification. Every row of
:data:`SAME_PERSON` is a pair of spellings that a bibliography can
legitimately use for one human being, and every row of
:data:`DIFFERENT_PERSON` is a pair that must stay apart. The second table is
the one that matters most: a false "these are the same person" hides a
fabricated author list, and a false "these are different people" accuses a
real one.
"""

from __future__ import annotations

import time

import pytest

from bibauthor.names import (
    ListComparison,
    NameComparison,
    PersonName,
    compare_author_lists,
    compare_names,
    fold,
    parse_name,
    parse_names,
)
from bibauthor.verdict import Thresholds, Verdict

TH = Thresholds()


def cmp_raw(a: str, b: str, th: Thresholds = TH) -> NameComparison:
    """Parse two display strings and compare them."""
    return compare_names(parse_name(a), parse_name(b), th)


def compare_raw_lists(cited, record, th: Thresholds = TH, truncated: bool = False):
    return compare_author_lists(parse_names(cited), parse_names(record), th, truncated)


# ---------------------------------------------------------------------------
# fold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Vazquez", "vazquez"),
        ("V\u00e1zquez", "vazquez"),
        ("Schm\u00eddhuber", "schmidhuber"),
        ("\u0141ukasz", "lukasz"),
        ("\u0142ukasz", "lukasz"),
        ("Str\u00f8m", "strom"),
        ("\u00c6gir", "aegir"),
        ("Wei\u00df", "weiss"),
        ("M\u00fcller", "muller"),
        ("O'Brien", "obrien"),
        ("Jean-Luc", "jeanluc"),
        ("J.J.", "jj"),
        ("J. J.", "j j"),
        ("  van   der  Berg ", "van der berg"),
        ("", ""),
    ],
)
def test_fold(raw, expected):
    assert fold(raw) == expected


def test_fold_drops_punctuation_without_inserting_space():
    # This is the choice that makes "J.J." and "JJ" the same key, and it is
    # why a separate space-squeezed comparison exists for "J. J.".
    assert fold("J.J. Vazquez") == "jj vazquez"
    assert fold("J. J. Vazquez") == "j j vazquez"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,family,given,suffix",
    [
        ("Manu Nicholas Jacob", "Jacob", "Manu Nicholas", ""),
        ("Jacob, Manu Nicholas", "Jacob", "Manu Nicholas", ""),
        ("Jacob, M. N.", "Jacob", "M. N.", ""),
        ("von Neumann, John", "von Neumann", "John", ""),
        ("John von Neumann", "von Neumann", "John", ""),
        ("Jan van der Berg", "van der Berg", "Jan", ""),
        ("Jan Van Der Berg", "Van Der Berg", "Jan", ""),
        ("van der Berg", "van der Berg", "", ""),
        ("Maria de la Cruz", "de la Cruz", "Maria", ""),
        ("Omar Al-Rashid", "Al-Rashid", "Omar", ""),
        ("Omar Al Rashid", "Al Rashid", "Omar", ""),
        ("Sean O'Brien", "O'Brien", "Sean", ""),
        ("John Smith Jr.", "Smith", "John", "Jr."),
        ("Smith, John, Jr.", "Smith", "John", "Jr."),
        ("Smith, Jr., John", "Smith", "John", "Jr."),
        ("John Smith III", "Smith", "John", "III"),
        ("Kaiming He", "He", "Kaiming", ""),
        ("He Kaiming", "Kaiming", "He", ""),
        ("Plato", "Plato", "", ""),
    ],
)
def test_parse_name(raw, family, given, suffix):
    parsed = parse_name(raw)
    assert parsed.family == family
    assert parsed.given == given
    assert parsed.suffix == suffix
    assert parsed.literal == raw
    assert parsed.is_group is False


def test_parse_name_dotted_initial_is_not_a_particle():
    # "D." must stay a middle initial even though single-letter particles
    # exist in the table.
    assert parse_name("John D. Smith").family == "Smith"
    assert parse_name("John D. Smith").given == "John D."


@pytest.mark.parametrize(
    "raw",
    [
        "The LIGO Scientific Collaboration",
        "LIGO Scientific Collaboration",
        "Gemini Team",
        "CERN",
        "et al. group",
        "BigScience Workshop",
        "National Institute of Standards",
    ],
)
def test_group_authors_are_detected(raw):
    parsed = parse_name(raw)
    assert parsed.is_group is True
    assert parsed.literal == raw


@pytest.mark.parametrize(
    "raw", ["Kushal Agrawal", "Agrawal", "Manu Nicholas Jacob", "Smith, John"]
)
def test_personal_names_are_not_groups(raw):
    assert parse_name(raw).is_group is False


def test_parse_names_keeps_order():
    parsed = parse_names(["B Beta", "A Alpha"])
    assert [p.family for p in parsed] == ["Beta", "Alpha"]


# ---------------------------------------------------------------------------
# same person
# ---------------------------------------------------------------------------

# (left, right, variant tags that must fire)
SAME_PERSON = [
    # 1. initials against full given names
    ("K. Agrawal", "Kushal Agrawal", ["initials"]),
    ("Agrawal, K.", "Kushal Agrawal", ["initials"]),
    # 2. multiple initials, spaced, unspaced, dotted
    ("J.J. Vazquez", "J. J. Vazquez", ["spacing"]),
    ("J.J. Vazquez", "JJ Vazquez", []),
    ("JJ Vazquez", "Juan J. Vazquez", ["initials"]),
    ("J. J. Vazquez", "Juan J. Vazquez", ["initials"]),
    # 3. middle names present on one side only
    ("Manu Nicholas Jacob", "Manu Jacob", ["middle_dropped"]),
    ("Manu Nicholas Jacob", "Manu N. Jacob", ["initials"]),
    ("Manu N. Jacob", "Manu Jacob", ["middle_dropped"]),
    # 4. a conflicting middle initial is weak evidence, not proof
    ("John Q. Smith", "John R. Smith", ["middle_initial_conflict"]),
    ("John Quincy Smith", "John Robert Smith", ["middle_initial_conflict"]),
    # 5. particles and compound surnames
    ("Jan van der Berg", "Jan Van Der Berg", []),
    ("Jan van der Berg", "Jan vanderBerg", ["spacing"]),
    ("Maria de la Cruz", "Maria De La Cruz", []),
    ("Omar Al-Rashid", "Omar Al Rashid", ["spacing"]),
    ("Omar Al-Rashid", "Omar AlRashid", []),
    ("Sean McDonald", "Sean MacDonald", ["mac_mc"]),
    ("Sean O'Brien", "Sean OBrien", []),
    ("Sean O'Brien", "Sean O Brien", ["spacing"]),
    # 6. hyphenated, spaced and run-together given names
    ("Jean-Luc Picard", "Jean Luc Picard", ["spacing"]),
    ("Jean-Luc Picard", "JeanLuc Picard", []),
    ("Xiao-Ming Li", "Xiaoming Li", []),
    ("Xiao-Ming Li", "Xiao Ming Li", ["spacing"]),
    # 7. diacritics, their ASCII folds and the German ue expansion
    ("Juan Vazquez", "Juan V\u00e1zquez", ["diacritics"]),
    ("Jurgen Schmidhuber", "Jurgen Schm\u00eddhuber", ["diacritics"]),
    ("Lukasz Kaiser", "\u0141ukasz Kaiser", ["diacritics"]),
    ("Hans Muller", "Hans M\u00fcller", ["diacritics"]),
    ("Hans M\u00fcller", "Hans Mueller", ["german_ue"]),
    ("Hans Muller", "Hans Mueller", ["german_ue"]),
    # 8. written order in the input string
    ("Jacob, Manu Nicholas", "Manu Nicholas Jacob", []),
    ("Jacob, Manu N.", "Manu Nicholas Jacob", ["initials"]),
    ("Vazquez, J. M.", "J. M. Vazquez", []),
    ("von Neumann, John", "John von Neumann", []),
    # 9. family name first
    ("Kaiming He", "He Kaiming", ["order_swapped"]),
    ("Kaiming He", "HE Kaiming", ["order_swapped"]),
    ("Andy Liu", "Liu Andy", ["order_swapped"]),
    ("\u4f55\u51ef\u660e", "\u4f55\u51ef\u660e", []),
    ("\u4f55\u51ef\u660e", "\u51ef\u660e\u4f55", ["order_swapped"]),
    # 10. group authors match group authors
    ("The LIGO Scientific Collaboration", "LIGO Scientific Collaboration",
     ["group_fuzzy"]),
    ("LIGO Scientific Collaboration", "The LIGO Collaboration", ["group_fuzzy"]),
    ("Gemini Team", "Gemini Team", []),
    ("CERN", "CERN", []),
    # 11. suffixes
    ("John Smith Jr.", "John Smith", ["suffix_dropped"]),
    ("John Smith Jr.", "John Smith III", ["suffix_conflict"]),
    # 12. transliteration variants
    ("Pyotr Tchaikovsky", "Pyotr Chaikovsky", ["transliteration"]),
    ("Ahmed Yusuf", "Ahmed Yousef", ["transliteration"]),
    ("Ahmed Yusuf", "Ahmed Yusif", ["transliteration"]),
    ("Wei Zhang", "Wei Chang", ["transliteration"]),
    ("Soo Kim", "Soo Gim", ["transliteration"]),
    # bare surnames still identify a person, weakly
    ("Agrawal", "Kushal Agrawal", ["surname_only"]),
]


@pytest.mark.parametrize(
    "left,right,tags", SAME_PERSON, ids=["%s|%s" % (a, b) for a, b, _ in SAME_PERSON]
)
def test_same_person(left, right, tags):
    forward = cmp_raw(left, right)
    assert forward.score >= TH.name_variant, (left, right, forward)
    missing = [tag for tag in tags if tag not in forward.variants]
    assert not missing, (left, right, forward.variants)
    # The matcher must be symmetric: which side came from the bibliography
    # and which from the record cannot change the answer.
    backward = cmp_raw(right, left)
    assert backward.score == pytest.approx(forward.score), (left, right)


def test_identical_names_are_exact_with_no_variants():
    result = cmp_raw("Kushal Agrawal", "Kushal Agrawal")
    assert result.exact is True
    assert result.variants == []
    assert result.score == pytest.approx(1.0)


def test_diacritics_are_a_variant_not_an_exact_match():
    # Both spellings are the same person, but the report should be able to
    # say that one of them lost its accents.
    result = cmp_raw("Juan Vazquez", "Juan V\u00e1zquez")
    assert result.exact is False
    assert "diacritics" in result.variants
    assert result.score >= TH.name_match


def test_initials_are_capped_at_the_threshold():
    result = cmp_raw("K. Agrawal", "Kushal Agrawal")
    assert result.score == pytest.approx(TH.initial_vs_full_given)
    assert result.score < TH.name_match


def test_conflicting_middle_initial_sits_between_the_thresholds():
    # Case 4: weak evidence of a different person, not proof. It must not
    # reach name_match, and it must not sink below name_variant either.
    result = cmp_raw("John Q. Smith", "John R. Smith")
    assert TH.name_variant <= result.score < TH.name_match
    assert "middle_initial_conflict" in result.variants


def test_suffix_conflict_sits_between_the_thresholds():
    # Case 11: "Jr." and "III" are probably father and son, so the pair is
    # downgraded, but a dropped suffix is nothing at all.
    conflict = cmp_raw("John Smith Jr.", "John Smith III")
    assert TH.name_variant <= conflict.score < TH.name_match
    dropped = cmp_raw("John Smith Jr.", "John Smith")
    assert dropped.score >= TH.name_match


def test_transliteration_never_reaches_name_match():
    # The consonant skeleton is deliberately weak evidence: it may promote a
    # pair to name_variant and no further.
    for left, right in [
        ("Pyotr Tchaikovsky", "Pyotr Chaikovsky"),
        ("Wei Zhang", "Wei Chang"),
        ("Soo Kim", "Soo Gim"),
        ("Ahmed Yusuf", "Ahmed Yousef"),
    ]:
        result = cmp_raw(left, right)
        assert TH.name_variant <= result.score < TH.name_match, (left, right, result)


def test_transliteration_requires_agreeing_given_names():
    # Without agreeing given names the skeleton is not consulted at all, so
    # two bare surnames never match on it.
    assert cmp_raw("Zhang", "Chang").score < TH.name_variant
    assert cmp_raw("Wei Zhang", "Hong Chang").score < TH.name_variant


def test_order_swap_is_capped():
    result = cmp_raw("Kaiming He", "He Kaiming")
    assert TH.name_variant <= result.score < TH.name_match
    assert "order_swapped" in result.variants


# ---------------------------------------------------------------------------
# different people
# ---------------------------------------------------------------------------

DIFFERENT_PERSON = [
    # Different first given names: the surname agreeing perfectly is not
    # enough, by design (case 4).
    ("John Smith", "Robert Smith"),
    ("J. Smith", "R. Smith"),
    ("John Smith", "Jane Smith"),
    # The swap rule must not marry unrelated two-token names. "Liu" does
    # meet "Liu" under the swap, but "Andy" never meets "Wei" (case 9).
    ("Andy Liu", "Liu Wei"),
    ("Kaiming He", "He Wei"),
    # Near-miss surnames. "Smythe" is a historical spelling of "Smith", but
    # a bibliography that says Smythe when the paper says Smith is reporting
    # a different person until a human says otherwise.
    ("J. Smith", "J. Smythe"),
    ("John Smith", "John Smythe"),
    # Agrawal and Aggarwal are distinct surnames, not spellings of one
    # surname: the consonant doubling and the extra vowel are the whole
    # difference between two large, unrelated families. The skeleton
    # deliberately does not collapse doubled consonants, so this pair stays
    # apart even though the given name is identical. See the comment on
    # _skeleton in names.py.
    ("Kushal Agrawal", "Kushal Aggarwal"),
    ("K. Agrawal", "K. Aggarwal"),
    # Particles are part of the surname, so swapping one changes the person.
    ("Jan van der Berg", "Jan van den Berg"),
    # Unrelated names from the real chimeric case below.
    ("Fried", "Verona Teo"),
    ("Sycara", "Juan J. Vazquez"),
    ("Jacobs", "Sudarsh Kunnavakkam"),
    ("Oesterheld", "Vishak Srikanth"),
    ("Conitzer", "Kushal Agrawal"),
    ("Liu", "Verona Teo"),
    # A group author is never a person, in either direction (case 10).
    ("Gemini Team", "Gemini Patel"),
    ("Gemini Patel", "Gemini Team"),
    ("CERN", "Cern Patel"),
    ("The LIGO Scientific Collaboration", "Ligo Scientific"),
    # Two groups with nothing distinctive in common.
    ("The ATLAS Collaboration", "The CMS Collaboration"),
    # Han characters against a romanization: out of scope, and guessing
    # would be worse than reporting it unmatched.
    ("\u4f55\u51ef\u660e", "Kaiming He"),
    ("\u4f55\u51ef\u660e", "\u738b\u5c0f\u660e"),
]


@pytest.mark.parametrize(
    "left,right",
    DIFFERENT_PERSON,
    ids=["%s|%s" % (a, b) for a, b in DIFFERENT_PERSON],
)
def test_different_person(left, right):
    result = cmp_raw(left, right)
    assert result.score < TH.name_variant, (left, right, result)


def test_smythe_does_not_reach_name_match():
    # Stated separately because it is the assertion that matters even if a
    # future tuning run moves this pair above name_variant.
    assert cmp_raw("J. Smith", "J. Smythe").score < TH.name_match


def test_bare_surnames_cannot_reach_name_match():
    # surname_only_penalty: two bare surnames, or a bare surname against a
    # full name, are evidence but never identification.
    both_bare = cmp_raw("Agrawal", "Agrawal")
    assert both_bare.score == pytest.approx(1.0 - TH.surname_only_penalty)
    assert both_bare.score < TH.name_match
    assert both_bare.score >= TH.name_variant
    assert "surname_only" in both_bare.variants

    one_bare = cmp_raw("Agrawal", "Kushal Agrawal")
    assert one_bare.score < TH.name_match
    assert one_bare.score >= TH.name_variant


def test_group_never_matches_a_personal_name():
    group = parse_name("The LIGO Scientific Collaboration")
    person = parse_name("Lee Scientific")
    assert group.is_group and not person.is_group
    assert compare_names(group, person, TH).score == 0.0
    assert compare_names(person, group, TH).score == 0.0

    # And at the list level, a cited group author is never satisfied by a
    # personal author on the record.
    result = compare_raw_lists(
        ["Gemini Team"], ["Gemini Patel", "Sundar Team", "Alice Example"]
    )
    assert result.pairs == []
    assert result.unmatched_cited == [0]
    assert result.verdict == Verdict.DISJOINT


def test_group_matches_group_by_distinctive_tokens():
    result = cmp_raw(
        "The LIGO Scientific Collaboration", "LIGO Scientific Collaboration"
    )
    assert result.score >= TH.name_variant
    assert "group_fuzzy" in result.variants
    # "Collaboration" on its own is not distinctive, so a group name made of
    # nothing else never reaches across to a different group. (Two identical
    # strings still match, because that is just string equality.)
    assert cmp_raw("The Collaboration", "The ATLAS Collaboration").score == 0.0
    assert cmp_raw("The Collaboration", "The Collaboration").score == 1.0


# ---------------------------------------------------------------------------
# list level
# ---------------------------------------------------------------------------

# arXiv 2507.01413. This is the real author list, verified against the arXiv
# and OpenAlex APIs on 29 Oct 2026.
TRUE_2507_01413 = [
    "Kushal Agrawal",
    "Verona Teo",
    "Juan J. Vazquez",
    "Sudarsh Kunnavakkam",
    "Vishak Srikanth",
    "Andy Liu",
]

# What a chimeric citation of that paper said instead: real title, real
# venue, real year, invented author list.
CITED_2507_01413 = [
    "Agrawal",
    "Liu",
    "Fried",
    "Sycara",
    "Jacobs",
    "Oesterheld",
    "Conitzer",
]


def test_real_chimeric_citation_is_partial_overlap():
    """The 29 Oct 2026 case, kept here as a regression test.

    PARTIAL_OVERLAP and not DISJOINT is the correct answer: two of the cited
    surnames, Agrawal and Liu, genuinely do occur on the paper, so "no cited
    author is on this work" would be a false statement. The finding the tool
    has to report is that the other five do not occur on it at all.
    """
    result = compare_raw_lists(CITED_2507_01413, TRUE_2507_01413)
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert len(result.unmatched_cited) == 5
    assert [CITED_2507_01413[i] for i in result.unmatched_cited] == [
        "Fried",
        "Sycara",
        "Jacobs",
        "Oesterheld",
        "Conitzer",
    ]
    # The two that did match, matched the right record authors.
    matched = {i: j for i, j, _cmp in result.pairs}
    assert matched == {0: 0, 1: 5}
    assert result.coverage == pytest.approx(2.0 / 7.0)
    assert result.truncated is False
    assert "surname_only" in result.variants
    assert any("did not match" in note for note in result.notes)


def test_true_author_list_of_the_same_paper_matches():
    # The same record compared against its own author list must come back
    # clean, otherwise the test above proves nothing.
    result = compare_raw_lists(TRUE_2507_01413, TRUE_2507_01413)
    assert result.verdict == Verdict.MATCH
    assert result.coverage == pytest.approx(1.0)
    assert result.unmatched_cited == []
    assert result.unmatched_record == []
    assert result.order_differs is False


def test_true_author_list_with_initials_matches_with_variants():
    cited = ["K. Agrawal", "V. Teo", "J. J. Vazquez", "S. Kunnavakkam",
             "V. Srikanth", "A. Liu"]
    result = compare_raw_lists(cited, TRUE_2507_01413)
    assert result.verdict == Verdict.MATCH_WITH_VARIANTS
    assert "initials" in result.variants
    assert result.order_differs is False
    assert result.coverage == pytest.approx(1.0)


def test_disjoint():
    # A constructed case: no cited name occurs on the record at all. This is
    # the only shape that earns DISJOINT.
    cited = ["Alice Example", "Bob Sample", "Carol Instance"]
    record = ["Zhen Qiu", "Marta Volkov", "Ingrid Nyberg"]
    result = compare_raw_lists(cited, record)
    assert result.verdict == Verdict.DISJOINT
    assert result.coverage == pytest.approx(0.0)
    assert result.pairs == []
    assert result.unmatched_cited == [0, 1, 2]
    assert result.unmatched_record == [0, 1, 2]
    assert any("check the citation by hand" in note for note in result.notes)


def test_disjoint_is_withheld_for_a_tiny_record():
    # A one-author record is too easy to get wrong, so nothing matching is
    # reported as PARTIAL_OVERLAP with a note instead of DISJOINT.
    result = compare_raw_lists(["Alice Example"], ["Zhen Qiu"])
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert any("too few to call DISJOINT" in note for note in result.notes)


def test_disjoint_is_withheld_when_the_cited_list_is_truncated():
    result = compare_raw_lists(
        ["Alice Example"], ["Zhen Qiu", "Marta Volkov"], truncated=True
    )
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert any("DISJOINT is withheld" in note for note in result.notes)


def test_order_differs():
    cited = ["Bob Sample", "Alice Example", "Carol Instance"]
    record = ["Alice Example", "Bob Sample", "Carol Instance"]
    result = compare_raw_lists(cited, record)
    assert result.verdict == Verdict.ORDER_DIFFERS
    assert result.order_differs is True
    assert result.unmatched_cited == []
    assert result.coverage == pytest.approx(1.0)
    assert {i: j for i, j, _cmp in result.pairs} == {0: 1, 1: 0, 2: 2}


def test_truncated_prefix():
    record = ["Alice Example", "Bob Sample", "Carol Instance", "Dan Fourth"]
    result = compare_raw_lists(record[:2], record, truncated=True)
    assert result.verdict == Verdict.TRUNCATED
    assert result.truncated is True
    assert result.order_differs is False
    assert result.unmatched_cited == []
    assert result.unmatched_record == [2, 3]
    assert result.coverage == pytest.approx(1.0)


def test_truncated_but_not_a_prefix_also_reports_order():
    # Case 14: the cited names are all on the record, but they are not its
    # opening run, so the order is reported separately from the verdict.
    record = ["Alice Example", "Bob Sample", "Carol Instance", "Dan Fourth"]
    result = compare_raw_lists(
        ["Carol Instance", "Alice Example"], record, truncated=True
    )
    assert result.verdict == Verdict.TRUNCATED
    assert result.order_differs is True
    assert any("not as its opening run" in note for note in result.notes)


def test_truncated_marker_on_a_complete_list_is_not_a_downgrade():
    record = ["Alice Example", "Bob Sample"]
    result = compare_raw_lists(record, record, truncated=True)
    assert result.verdict == Verdict.MATCH
    assert result.truncated is True
    assert any("covers every record author" in note for note in result.notes)


def test_truncated_list_with_a_fabricated_name_is_partial_overlap():
    record = ["Alice Example", "Bob Sample", "Carol Instance", "Dan Fourth"]
    result = compare_raw_lists(
        ["Alice Example", "Zachary Nobody"], record, truncated=True
    )
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert result.unmatched_cited == [1]


def test_empty_cited_list():
    result = compare_raw_lists([], ["Alice Example", "Bob Sample"])
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert result.coverage == pytest.approx(0.0)
    assert result.pairs == []
    assert any("no authors" in note for note in result.notes)


def test_empty_record_list():
    result = compare_raw_lists(["Alice Example"], [])
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert result.unmatched_cited == [0]
    assert any("nothing to compare with" in note for note in result.notes)


def test_coverage_counts_group_authors_as_comparable():
    cited = ["Gemini Team", "Alice Example"]
    record = ["Gemini Team", "Bob Sample"]
    result = compare_raw_lists(cited, record)
    # One of the two cited names matched, so coverage is one half, not one.
    assert result.coverage == pytest.approx(0.5)
    assert result.verdict == Verdict.PARTIAL_OVERLAP


def test_partial_coverage_threshold_is_reported_not_enforced():
    # Coverage below partial_coverage is still PARTIAL_OVERLAP; the
    # threshold only earns a note, because the alternative (DISJOINT) would
    # claim something false.
    result = compare_raw_lists(CITED_2507_01413, TRUE_2507_01413)
    assert result.coverage < TH.partial_coverage
    assert result.verdict == Verdict.PARTIAL_OVERLAP
    assert any("partial_coverage" in note for note in result.notes)


@pytest.mark.parametrize(
    "cited,record,truncated",
    [
        ([], ["Alice Example"], False),
        (["Alice Example"], [], False),
        (["Alice Example"], ["Alice Example"], False),
        (["Alice Example"], ["Zhen Qiu", "Marta Volkov"], False),
        (CITED_2507_01413, TRUE_2507_01413, False),
        (TRUE_2507_01413[:2], TRUE_2507_01413, True),
    ],
)
def test_never_returns_unverifiable(cited, record, truncated):
    # Only the verifier knows whether a record was found, so this function
    # must never claim UNVERIFIABLE.
    result = compare_raw_lists(cited, record, truncated=truncated)
    assert result.verdict != Verdict.UNVERIFIABLE
    assert result.verdict in Verdict.ALL
    assert isinstance(result, ListComparison)


def test_one_record_author_is_used_at_most_once():
    # Two cited bare surnames must not both claim the same record author.
    result = compare_raw_lists(
        ["Smith", "Smith"], ["John Smith", "Alice Example", "Bob Sample"]
    )
    used = [j for _i, j, _cmp in result.pairs]
    assert len(used) == len(set(used))
    assert len(result.unmatched_cited) == 1


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------


def _synthetic_authors(count: int):
    """Distinct, deterministic author names for the scale test.

    Built from syllables rather than numbered stems, so no two names come out
    near-identical by accident (which is what a "Family1" / "Family10" style
    generator does).
    """
    syllables = [c + v for c in "bcdfghjklmnpqrstvwz" for v in "aeiou"]
    size = len(syllables)
    names = []
    for i in range(count):
        low = i % size
        high = (i // size) % size
        given = syllables[low].capitalize() + syllables[high]
        family = (
            syllables[(low + 7) % size].capitalize()
            + syllables[(high + 3) % size]
            + syllables[(low * 3 + 5) % size]
        )
        names.append("%s %s" % (given, family))
    assert len(set(names)) == count
    return names


def test_two_hundred_authors_is_deterministic_and_quick():
    # The real DeepSeek-R1 record carries 200 authors, so a 200 x 200 score
    # matrix is a shape this engine has to survive.
    record = _synthetic_authors(200)
    cited = list(record)
    parsed_cited = parse_names(cited)
    parsed_record = parse_names(record)

    start = time.time()
    first = compare_author_lists(parsed_cited, parsed_record, TH)
    midpoint = time.time()
    second = compare_author_lists(parsed_cited, parsed_record, TH)
    end = time.time()

    assert first.verdict == Verdict.MATCH
    assert len(first.pairs) == 200
    assert first.coverage == pytest.approx(1.0)
    assert first.unmatched_cited == []
    assert first.unmatched_record == []
    # Every author matched the one in the same position, so no two of the
    # synthetic names are close enough to be confused for each other.
    assert [(i, j) for i, j, _c in first.pairs] == [(k, k) for k in range(200)]
    # Deterministic: identical pairings and identical scores on a second run.
    assert [(i, j) for i, j, _c in first.pairs] == [(i, j) for i, j, _c in second.pairs]
    assert [round(c.score, 9) for _i, _j, c in first.pairs] == [
        round(c.score, 9) for _i, _j, c in second.pairs
    ]
    # Generous, because this is a smoke test for quadratic blowup and not a
    # benchmark. A fatal implementation takes minutes, not seconds.
    assert (midpoint - start) < 30.0
    assert (end - midpoint) < 30.0


def test_two_hundred_authors_rotated_is_order_differs():
    record = _synthetic_authors(200)
    cited = record[100:] + record[:100]
    result = compare_raw_lists(cited, record)
    assert result.verdict == Verdict.ORDER_DIFFERS
    assert result.order_differs is True
    assert result.coverage == pytest.approx(1.0)


def test_two_hundred_author_record_with_a_truncated_citation():
    record = _synthetic_authors(200)
    result = compare_raw_lists(record[:3], record, truncated=True)
    assert result.verdict == Verdict.TRUNCATED
    assert result.order_differs is False
    assert len(result.unmatched_record) == 197


# ---------------------------------------------------------------------------
# thresholds are honoured
# ---------------------------------------------------------------------------


def test_lowering_name_variant_admits_more_pairs():
    loose = Thresholds(name_variant=0.60)
    pair = ("Kushal Agrawal", "Kushal Aggarwal")
    assert cmp_raw(*pair).score < TH.name_variant
    assert cmp_raw(pair[0], pair[1], loose).score >= loose.name_variant


def test_surname_only_penalty_is_configurable():
    strict = Thresholds(surname_only_penalty=0.40)
    result = compare_names(
        parse_name("Agrawal"), parse_name("Kushal Agrawal"), strict
    )
    assert result.score == pytest.approx(1.0 - 0.40)
    assert result.score < strict.name_variant
    # With bare surnames rejected outright, the real chimeric case has
    # nothing left to match and the record is large enough for DISJOINT.
    listed = compare_raw_lists(CITED_2507_01413, TRUE_2507_01413, th=strict)
    assert listed.verdict == Verdict.DISJOINT


def test_personname_is_frozen():
    name = parse_name("Kushal Agrawal")
    assert isinstance(name, PersonName)
    with pytest.raises(Exception):
        name.family = "Other"  # type: ignore[misc]
