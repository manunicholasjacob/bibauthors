"""Name parsing and the author-list matching engine.

This module answers one question: does the author list stored in a
bibliography belong to the work that was cited? The error class it exists to
catch is the chimeric citation, where a generated reference keeps a real
title, a real venue and a real year but invents the author list. Those are
hard to spot by eye, because every individual field looks plausible.

Design notes
------------

The matcher is deliberately asymmetric about cost. Telling a researcher that
a real citation is fabricated is worse than failing to flag a fabricated one,
so every rule here is built to admit legitimate variation:

  * initials against full given names ("K. Agrawal" / "Kushal Agrawal"),
  * middle names present on one side only,
  * particles and compound surnames ("van der Berg" / "vanderBerg"),
  * a compound surname that lost its hyphen ("Romera-Paredes" /
    "Romera Paredes"),
  * a surname particle moved behind the given name ("van den Driessche,
    George" / "Driessche, George van den"),
  * diacritics and their ASCII folds ("V\u00e1zquez" / "Vazquez"),
  * German "ue" expansion ("M\u00fcller" / "Mueller"),
  * family-name-first ordering ("Kaiming He" / "He Kaiming"),
  * suffixes ("John Smith Jr." / "John Smith"),
  * a small, deliberately incomplete transliteration skeleton
    ("Tchaikovsky" / "Chaikovsky").

Every one of those rules leaves a tag in ``NameComparison.variants``, so the
report can always say *why* two spellings were treated as one person. Rules
also carry a ceiling: a pair that needed a rule can reach
``Thresholds.name_variant`` but, with the single exception of pure
punctuation and spacing differences, never ``Thresholds.name_match``. That
keeps "same authors, written differently" visibly distinct from "identical".

What the module refuses to do is guess. A group author never matches a
personal name, a bare surname never reaches ``name_match`` on its own, and
the transliteration skeleton only fires when the given names already agree.

Scoring
-------

A pair score is ``0.65 * family + 0.35 * given``, minus
``Thresholds.surname_only_penalty`` when either side has no given name at
all, and then clamped down by the ceiling of every variant rule that fired.
The weights matter: a pair whose given names disagree on the first given name
scores at most 0.65, which is below the default ``name_variant`` of 0.70, so
"John Smith" and "Robert Smith" are different people no matter how perfectly
the surname agrees.
"""

from __future__ import annotations

import dataclasses
import difflib
import functools
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from .verdict import Thresholds, Verdict

__all__ = [
    "PersonName",
    "NameComparison",
    "ListComparison",
    "parse_name",
    "parse_names",
    "fold",
    "compare_names",
    "compare_author_lists",
]

# Relative weight of the surname in a pair score. The surname carries most of
# the identifying information in a bibliography, and the given name is the
# field most often abbreviated away.
_FAMILY_WEIGHT = 0.65
_GIVEN_WEIGHT = 1.0 - _FAMILY_WEIGHT

# Ceilings per variant rule. A rule that fires clamps the pair score, so the
# strength of the evidence is visible in the number and not only in the tags.
_CAP_SPACING = 1.0  # punctuation or spacing only: no evidence lost
_CAP_DIACRITICS = 1.0
_CAP_MIDDLE_DROPPED = 0.95
_CAP_SUFFIX_DROPPED = 0.95
_CAP_ORDER_SWAPPED = 0.90
_CAP_GERMAN_UE = 0.90
_CAP_SPELLING = 0.90
_CAP_MAC_MC = 0.88
_CAP_SUFFIX_CONFLICT = 0.85
_CAP_MIDDLE_CONFLICT = 0.85
_CAP_TRANSLITERATION = 0.80
_CAP_GROUP_FUZZY = 0.95
# A surname particle that the bibliography moved behind the given name
# ("Driessche, George van den" for "van den Driessche, George"). The tokens
# are all still there, in the wrong field, so this is the same strength of
# evidence as a family-name-first ordering and carries the same ceiling.
_CAP_PARTICLE_MOVED = 0.90
# A compound surname written without its hyphen ("Bernardino Romera Paredes"
# for "Bernardino Romera-Paredes"). Lower than _CAP_PARTICLE_MOVED because
# the moved token is an ordinary word rather than a known particle, so the
# rule is inferring where the surname starts rather than reading it off a
# particle list.
_CAP_COMPOUND_SURNAME = 0.85
# Two surnames whose consonants agree and whose vowels differ in exactly one
# place. Weaker evidence than a named consonant substitution, so it sits
# below _CAP_TRANSLITERATION and, like it, can never reach name_match.
_CAP_VOWEL_ROMANIZATION = 0.78

# Surname fuzz. Two surnames that are not reachable by any named rule are
# only allowed to count when they are near identical; anything looser is
# discounted hard, because a plausible-looking surname is exactly what a
# fabricated author list is made of.
_SPELLING_FLOOR = 0.93
_FUZZY_DISCOUNT = 0.55

# Letters that carry a stroke or a bar, or are ligatures. NFKD does not
# decompose these, so diacritic stripping has to special case them. Checked
# against unicodedata: "\u0141" normalizes to itself under NFKD.
_SPECIAL_LETTERS = {
    "\u0141": "l",  # L with stroke
    "\u0142": "l",
    "\u00d8": "o",  # O with stroke
    "\u00f8": "o",
    "\u0110": "d",  # D with stroke
    "\u0111": "d",
    "\u0126": "h",  # H with stroke
    "\u0127": "h",
    "\u0166": "t",  # T with stroke
    "\u0167": "t",
    "\u00de": "th",  # thorn
    "\u00fe": "th",
    "\u00d0": "d",  # eth
    "\u00f0": "d",
    "\u00c6": "ae",
    "\u00e6": "ae",
    "\u0152": "oe",
    "\u0153": "oe",
    "\u00df": "ss",
    "\u1e9e": "ss",
    "\u0131": "i",  # dotless i
    "\u0130": "i",
    "\u00c5": "a",
    "\u00e5": "a",
}

# Lowercase surname particles. Matched case insensitively, because
# bibliographies capitalize them inconsistently ("van der Berg", "Van Der
# Berg"), and never on a token that ends in a period, because that period
# marks an initial ("John D. Smith").
#
# Two known hazards, both accepted on purpose:
#   * a given name spelled like a particle ("Al Gore") parses as a two-word
#     surname;
#   * the single-letter particle "o", which is here so that "O Brien" parses
#     the same way as "O'Brien", swallows a dotless middle initial, so
#     "John O Smith" parses with the surname "O Smith".
# Both are parse-level only: the matcher compares the folded surname with
# spaces removed, so the two spellings of an Irish surname still meet.
_PARTICLES = frozenset(
    [
        "van",
        "von",
        "der",
        "den",
        "de",
        "del",
        "dela",
        "della",
        "degli",
        "di",
        "da",
        "do",
        "dos",
        "das",
        "du",
        "la",
        "le",
        "les",
        "lo",
        "ter",
        "ten",
        "af",
        "av",
        "zu",
        "zur",
        "op",
        "bin",
        "ibn",
        "ben",
        "al",
        "el",
        "o",
        "abu",
        "san",
        "santa",
        "st",
        "saint",
    ]
)

# Generational and honorific suffixes. Bare "I" and "V" are left out on
# purpose: they are far more likely to be a given-name initial.
_SUFFIXES = frozenset(
    ["jr", "jnr", "sr", "snr", "ii", "iii", "iv", "vi", "vii", "viii", "ix"]
)

# Words that mark a corporate, consortium or collaboration author.
_GROUP_WORDS = frozenset(
    [
        "collaboration",
        "collaborations",
        "consortium",
        "consortia",
        "team",
        "teams",
        "group",
        "groups",
        "project",
        "programme",
        "program",
        "institute",
        "institutes",
        "institution",
        "laboratory",
        "laboratories",
        "university",
        "universite",
        "universitat",
        "college",
        "school",
        "society",
        "association",
        "committee",
        "council",
        "foundation",
        "organization",
        "organisation",
        "initiative",
        "network",
        "working",
        "workshop",
        "taskforce",
        "corporation",
        "company",
        "incorporated",
        "authors",
        "centre",
        "center",
        "agency",
        "ministry",
        "department",
        "observatory",
        "survey",
        "study",
    ]
)

# Tokens that carry no identity for a group author, so an overlap made only
# of these does not count as a match.
_GROUP_STOPWORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "for",
        "a",
        "an",
        "at",
        "in",
        "on",
        "et",
        "al",
        "collaboration",
        "collaborations",
        "consortium",
        "team",
        "teams",
        "group",
        "groups",
        "project",
        "authors",
        "working",
    ]
)

_VOWELS = frozenset("aeiou")


@dataclasses.dataclass(frozen=True)
class PersonName:
    """One author, split into the parts the matcher compares.

    ``literal`` is always populated with the name as supplied, so a report can
    quote the bibliography back to its author verbatim.
    """

    family: str = ""
    given: str = ""
    suffix: str = ""
    literal: str = ""
    is_group: bool = False

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.literal


@dataclasses.dataclass
class NameComparison:
    """The result of comparing two :class:`PersonName` values."""

    score: float
    exact: bool
    variants: List[str] = dataclasses.field(default_factory=list)
    reason: str = ""


@dataclasses.dataclass
class ListComparison:
    """The result of comparing a cited author list against a record."""

    verdict: str
    pairs: List[Tuple[int, int, NameComparison]] = dataclasses.field(
        default_factory=list
    )
    unmatched_cited: List[int] = dataclasses.field(default_factory=list)
    unmatched_record: List[int] = dataclasses.field(default_factory=list)
    order_differs: bool = False
    truncated: bool = False
    coverage: float = 0.0
    variants: List[str] = dataclasses.field(default_factory=list)
    notes: List[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# folding
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=8192)
def fold(s: str) -> str:
    """Casefold, strip diacritics to ASCII, drop punctuation, squeeze spaces.

    The result is the canonical key the matcher compares. Punctuation is
    removed without inserting a space, so "J.J." folds to "jj" and
    "Jean-Luc" folds to "jeanluc"; a separate compacted comparison catches
    the spaced spellings ("J. J.", "Jean Luc").

    Non-spacing marks are dropped after NFKD, and the stroked or ligature
    letters that NFKD leaves alone are mapped through
    :data:`_SPECIAL_LETTERS`.
    """
    if not s:
        return ""
    out = []  # type: List[str]
    for ch in s.casefold():
        special = _SPECIAL_LETTERS.get(ch)
        if special is not None:
            out.append(special)
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        for part in decomposed:
            if unicodedata.combining(part):
                continue
            if part.isspace():
                out.append(" ")
            elif part.isalnum():
                out.append(part.casefold())
            # anything else (punctuation, symbols) is dropped outright
    return " ".join("".join(out).split())


@functools.lru_cache(maxsize=8192)
def _light(s: str) -> str:
    """Like :func:`fold` but keeps diacritics.

    Used only to decide whether a pair needed the diacritic-stripping step,
    so the report can tag it.
    """
    if not s:
        return ""
    out = []  # type: List[str]
    for ch in s.casefold():
        if ch.isspace():
            out.append(" ")
        elif ch.isalnum():
            out.append(ch)
    return " ".join("".join(out).split())


def _compact(s: str) -> str:
    return s.replace(" ", "")


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
    )


def _is_cjk_only(name: "PersonName") -> bool:
    text = (name.family + name.given).strip()
    if not text:
        return False
    return all(_is_cjk_char(ch) for ch in text if not ch.isspace())


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _looks_like_group(raw: str, tokens: Sequence[str]) -> bool:
    """Heuristic consortium or institution detector.

    Two signals: a group word anywhere in the name, or a single all-capitals
    token that reads as an acronym. The acronym rule is restricted to short
    tokens, or tokens with no vowel, so that an all-capitals surname such as
    "SMITH" is still read as a person. It is still a heuristic: a single
    all-capitals short surname ("LEE") is misread as an acronym, which is why
    the bibliography parsers are expected to hand names over in their natural
    case.
    """
    folded = fold(raw)
    if not folded:
        return False
    words = folded.split()
    for word in words:
        if word in _GROUP_WORDS:
            return True
    if len(tokens) == 1:
        token = tokens[0].strip(".,;")
        letters = [ch for ch in token if ch.isalpha()]
        if len(letters) >= 2 and token.isupper():
            if len(letters) <= 4 or not any(ch.casefold() in _VOWELS for ch in letters):
                return True
    return False


def _split_suffixes(tokens: List[str]) -> Tuple[List[str], str]:
    """Pull trailing generational suffixes off a token list."""
    suffix_parts = []  # type: List[str]
    while len(tokens) > 1:
        candidate = fold(tokens[-1])
        if candidate in _SUFFIXES:
            suffix_parts.insert(0, tokens.pop().strip(",").strip())
        else:
            break
    return tokens, " ".join(suffix_parts)


def _family_from_tokens(tokens: Sequence[str]) -> Tuple[str, str]:
    """Split "Firstname Middlename Lastname" into (family, given).

    The family name is the trailing run of tokens that begins at the first
    particle, or the last token when there is no particle. A particle in
    first position means the whole string is a surname ("de la Cruz").
    """
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    for index, token in enumerate(tokens):
        if token.endswith("."):
            continue  # an initial, never a particle
        if fold(token) in _PARTICLES and index < len(tokens) - 1:
            if index == 0:
                return " ".join(tokens), ""
            return " ".join(tokens[index:]), " ".join(tokens[:index])
    return tokens[-1], " ".join(tokens[:-1])


def parse_name(raw: str) -> PersonName:
    """Parse one display string into a :class:`PersonName`.

    Handles both written orders, "Lastname, Firstname" and "Firstname
    Lastname", plus the BibTeX three-part form "von Last, Jr, First".
    """
    literal = (raw or "").strip()
    text = literal.strip("{} \t\r\n")
    if not text:
        return PersonName(literal=literal)

    tokens = text.replace("\u00a0", " ").split()
    if _looks_like_group(text, tokens):
        return PersonName(family=text, literal=literal, is_group=True)

    if "," in text:
        parts = [part.strip() for part in text.split(",")]
        parts = [part for part in parts if part]
        if not parts:
            return PersonName(literal=literal)
        if len(parts) == 1:
            family_tokens, suffix = _split_suffixes(parts[0].split())
            family, given = _family_from_tokens(family_tokens)
            return PersonName(
                family=family, given=given, suffix=suffix, literal=literal
            )
        suffix = ""
        given = ""
        family_part = parts[0]
        rest = parts[1:]
        tail = []  # type: List[str]
        for part in rest:
            if fold(part) in _SUFFIXES:
                suffix = (suffix + " " + part).strip()
            else:
                tail.append(part)
        if tail:
            given = tail[0]
            # "Smith, John, Jr" already handled; anything further is noise.
        family_tokens, extra_suffix = _split_suffixes(family_part.split())
        if extra_suffix:
            suffix = (suffix + " " + extra_suffix).strip()
        family = " ".join(family_tokens)
        return PersonName(family=family, given=given, suffix=suffix, literal=literal)

    tokens, suffix = _split_suffixes(tokens)
    family, given = _family_from_tokens(tokens)
    return PersonName(family=family, given=given, suffix=suffix, literal=literal)


def parse_names(raws: Sequence[str]) -> List[PersonName]:
    """Parse a sequence of display strings, keeping the order."""
    return [parse_name(raw) for raw in raws]


# ---------------------------------------------------------------------------
# given names
# ---------------------------------------------------------------------------

# A given-name unit is ("i", letter) for an initial or ("n", folded) for a
# written-out name.
_Unit = Tuple[str, str]


@functools.lru_cache(maxsize=8192)
def _given_units(given: str) -> Tuple[_Unit, ...]:
    """Split a given-name string into initials and written-out names.

    "Juan J." gives (name juan, initial j); "J.J.", "J. J." and "JJ" all give
    (initial j, initial j); "Jean-Luc" gives (name jean, name luc). The
    all-capitals rule is a heuristic: a short all-capitals chunk is read as a
    run of initials, which is how bibliographies write "JJ Vazquez".
    """
    if not given:
        return ()
    chunks = []  # type: List[str]
    buffer = []  # type: List[str]
    for ch in given:
        if ch.isspace() or ch in "-\u2010":
            if buffer:
                chunks.append("".join(buffer))
                buffer = []
        else:
            buffer.append(ch)
    if buffer:
        chunks.append("".join(buffer))

    units = []  # type: List[_Unit]
    for chunk in chunks:
        if "." in chunk:
            parts = [part for part in chunk.split(".") if part.strip()]
        else:
            parts = [chunk]
        for part in parts:
            folded = fold(part)
            if not folded:
                continue
            if len(folded) == 1:
                units.append(("i", folded))
            elif part.isupper() and len(folded) <= 3 and folded.isalpha():
                for letter in folded:
                    units.append(("i", letter))
            else:
                units.append(("n", folded))
    return tuple(units)


def _units_compatible(a: _Unit, b: _Unit) -> Tuple[bool, Optional[str]]:
    """Can these two given-name units be the same name?"""
    kind_a, value_a = a
    kind_b, value_b = b
    if kind_a == "n" and kind_b == "n":
        if value_a == value_b:
            return True, None
        if _compact(value_a) == _compact(value_b):
            return True, "spacing"
        # One written-out name contained in the other is not enough: "Jan"
        # and "Janet" are different people.
        return False, None
    if kind_a == "i" and kind_b == "i":
        return (value_a == value_b), ("initials" if value_a == value_b else None)
    letter = value_a if kind_a == "i" else value_b
    full = value_b if kind_a == "i" else value_a
    if full[:1] == letter:
        return True, "initials"
    return False, None


def _compare_given(
    given_a: str, given_b: str
) -> Tuple[float, List[str], List[float], str]:
    """Compare two given-name strings.

    Returns (score, tags, caps, reason). A score of 0.0 means the first given
    names disagree, which the weights turn into a non-match on its own.
    """
    fa, fb = fold(given_a), fold(given_b)
    if not fa and not fb:
        return 1.0, [], [], "no given names on either side"
    if not fa or not fb:
        # Handled by the surname-only penalty in compare_names.
        return 1.0, [], [], "given name missing on one side"

    tags = []  # type: List[str]
    caps = []  # type: List[float]
    if _light(given_a) != _light(given_b) and fa == fb:
        tags.append("diacritics")
        caps.append(_CAP_DIACRITICS)

    if fa == fb:
        return 1.0, tags, caps, "given names identical"
    if _compact(fa) == _compact(fb):
        tags.append("spacing")
        caps.append(_CAP_SPACING)
        return 1.0, tags, caps, "given names differ only in spacing"
    if _de_expand(fa) == _de_expand(fb):
        tags.append("german_ue")
        caps.append(_CAP_GERMAN_UE)
        return 1.0, tags, caps, "given names agree after ue expansion"

    units_a = _given_units(given_a)
    units_b = _given_units(given_b)
    if not units_a or not units_b:
        return 1.0, tags, caps, "given name missing on one side"

    ok, tag = _units_compatible(units_a[0], units_b[0])
    if not ok:
        return 0.0, [], [], "first given names differ"
    if tag:
        tags.append(tag)
        # -1.0 is the placeholder for th.initial_vs_full_given, which is
        # resolved later because the ceiling is configurable.
        caps.append(-1.0 if tag == "initials" else _CAP_SPACING)

    rest_a = units_a[1:]
    rest_b = units_b[1:]
    reason = "given names agree"
    if rest_a and rest_b:
        shared = min(len(rest_a), len(rest_b))
        conflict = False
        for index in range(shared):
            ok, tag = _units_compatible(rest_a[index], rest_b[index])
            if not ok:
                conflict = True
                break
            if tag:
                tags.append(tag)
                if tag == "initials":
                    caps.append(-1.0)
                else:
                    caps.append(_CAP_SPACING)
        if conflict:
            # A middle name or initial that disagrees is weak evidence of a
            # different person, not proof: journals and bibliographies drop,
            # abbreviate and mangle middle names constantly. It downgrades
            # the pair below name_match but it must not sink it below
            # name_variant on its own.
            tags.append("middle_initial_conflict")
            caps.append(_CAP_MIDDLE_CONFLICT)
            reason = "middle names disagree"
        elif len(rest_a) != len(rest_b):
            tags.append("middle_dropped")
            caps.append(_CAP_MIDDLE_DROPPED)
            reason = "middle name present on one side only"
    elif rest_a or rest_b:
        tags.append("middle_dropped")
        caps.append(_CAP_MIDDLE_DROPPED)
        reason = "middle name present on one side only"

    return 1.0, tags, caps, reason


def _de_expand(folded: str) -> str:
    """Collapse the German vowel-plus-e spellings of umlauted vowels.

    "mueller" becomes "muller" so it meets the ASCII fold of "M\u00fcller".
    The rule is blunt and will also fire on an "oe" that was never an umlaut,
    which is why it is tagged and capped rather than treated as identity.
    """
    out = folded
    for pair, single in (("ue", "u"), ("oe", "o"), ("ae", "a")):
        out = out.replace(pair, single)
    return out


# ---------------------------------------------------------------------------
# surnames
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=8192)
def _romanize(folded: str) -> str:
    """Apply the romanization consonant substitutions, vowels left in place.

    Deliberately incomplete. The table below covers only the handful of
    substitutions that show up constantly in bibliographies when a name has
    been romanized through different conventions:

        tch / ch   Tchaikovsky  Chaikovsky
        zh  / ch   Zhang        Chang
        g   / k    Kim          Gim
        v   / f    Yusuf        Yusov
        ck  / k    Dyck         Dyk
        ph  / f    Stephan      Stefan
        c, q / k   Cohen        Kohen
        x   / ks   Xu           Ksu

    "z / s" used to be on that list, for "Zayed" against "Sayed". It is gone
    on purpose: in every romanization system that matters z and s stand for
    two different letters, and keeping the substitution made "Reyes" and
    "Reyez" one surname. Doubled consonants are NOT collapsed, also
    deliberately: collapsing them makes "Agrawal" and "Aggarwal" identical,
    and those are different families.
    """
    text = folded.replace(" ", "")
    text = text.replace("tch", "ch")
    text = text.replace("zh", "ch")
    text = text.replace("ck", "k")
    text = text.replace("ph", "f")
    text = text.replace("ch", "\x01")  # protect the digraph from c -> k
    text = text.replace("c", "k")
    text = text.replace("q", "k")
    text = text.replace("g", "k")
    text = text.replace("v", "f")
    text = text.replace("x", "ks")
    return text


@functools.lru_cache(maxsize=8192)
def _skeleton(folded: str) -> str:
    """Consonant skeleton: :func:`_romanize`, then every vowel dropped.

    "y" is kept, because dropping it collapses genuinely different surnames
    ("Smith" and "Smythe").

    The skeleton on its own is NOT evidence of anything. Dropping every
    vowel makes "Chen" and "Chan", "Johnson" and "Johnsen", "Nguyen" and
    "Nguyan", "O'Brien" and "O'Brian" and "Zhang" and "Zheng" identical, and
    those are different surnames: one of them is exactly the chimeric
    citation this module exists to catch. So the skeleton is only a
    precondition here, and :func:`_transliteration_match` decides on the
    vowels.
    """
    return "".join(ch for ch in _romanize(folded) if ch not in _VOWELS)


# Vowel digraphs that romanize one vowel, folded so that the same sound
# written two ways counts as one run ("Yusuf" / "Yousef"). Kept tiny on
# purpose; it is not a vowel-equivalence theory.
_VOWEL_RUN_FOLD = {
    "ou": "u",
    "oo": "u",
    "ee": "i",
    "ie": "i",
    "ei": "i",
}


def _vowel_runs(text: str) -> List[str]:
    """The maximal vowel runs of a romanized surname, in order and folded."""
    runs = []  # type: List[str]
    buffer = []  # type: List[str]
    for ch in text:
        if ch in _VOWELS:
            buffer.append(ch)
        elif buffer:
            runs.append("".join(buffer))
            buffer = []
    if buffer:
        runs.append("".join(buffer))
    return [_VOWEL_RUN_FOLD.get(run, run) for run in runs]


def _transliteration_match(
    a: str, b: str
) -> Optional[Tuple[List[str], float, str]]:
    """Can two compacted surnames be one surname romanized two ways?

    Returns ``(tags, ceiling, reason)`` or ``None``. There are two tiers,
    and both are narrower than simply dropping the vowels. Both tiers tag
    themselves ``transliteration``, so a report can still say in one word
    why the pair was treated as one person; the second adds its own tag
    beside it, because it is weaker and carries a lower ceiling:

      * ``transliteration``, ceiling ``_CAP_TRANSLITERATION`` (0.80): the two
        spellings become the same string once the consonant substitutions in
        :func:`_romanize` are applied, with every vowel still in place.
        "Tchaikovsky" / "Chaikovsky", "Zhang" / "Chang", "Kim" / "Gim",
        "Gaddafi" / "Qaddafi".

      * ``transliteration`` plus ``vowel_romanization``, ceiling
        ``_CAP_VOWEL_ROMANIZATION`` (0.78):
        the consonants agree and the vowels differ in exactly one run.
        "Gorbachev" / "Gorbachov", "Yusuf" / "Yousef", "Yusuf" / "Yusif".
        Three guards keep this off a one-character corruption of a real
        surname:
          - both spellings need at least two vowel runs, so a single-vowel
            surname ("Chen" / "Chan", "Zhang" / "Zheng") is never reached;
          - the one differing run has to be a single vowel on both sides, so
            a changed digraph ("O'Brien" / "O'Brian") does not count;
          - neither of the two vowels may be "a". Of the five vowels "a" is
            the one romanization systems agree on, so an "a" against a
            non-"a" is a different surname ("Nguyen" / "Nguyan").
        Everything else, a dropped vowel included, is refused.

    The caller only consults this when the given names already agree, and
    neither ceiling can reach name_match.
    """
    if a == b or not a or not b:
        return None
    if abs(len(a) - len(b)) > 3:
        return None
    skel_a, skel_b = _skeleton(a), _skeleton(b)
    if len(skel_a) < 2 or skel_a != skel_b:
        return None
    ra, rb = _romanize(a), _romanize(b)
    if ra == rb:
        return (
            ["transliteration"],
            _CAP_TRANSLITERATION,
            "surnames agree after a known romanization substitution",
        )
    runs_a, runs_b = _vowel_runs(ra), _vowel_runs(rb)
    if len(runs_a) != len(runs_b) or len(runs_a) < 2:
        return None
    differing = [(x, y) for x, y in zip(runs_a, runs_b) if x != y]
    if len(differing) != 1:
        return None
    left, right = differing[0]
    if len(left) != 1 or len(right) != 1:
        return None
    if "a" in (left, right):
        return None
    return (
        ["transliteration", "vowel_romanization"],
        _CAP_VOWEL_ROMANIZATION,
        "surnames agree on their consonants and differ in one vowel",
    )


def _mc_normalize(folded: str) -> str:
    text = _compact(folded)
    if text.startswith("mac") and len(text) > 5:
        return "mc" + text[3:]
    return text


def _compare_family(
    family_a: str, family_b: str, given_agree: bool
) -> Tuple[float, List[str], List[float], str]:
    """Compare two surnames. Returns (score, tags, caps, reason)."""
    fa, fb = fold(family_a), fold(family_b)
    if not fa and not fb:
        return 1.0, [], [], "no surnames"
    if not fa or not fb:
        return 0.0, [], [], "surname missing on one side"

    tags = []  # type: List[str]
    caps = []  # type: List[float]
    if _light(family_a) != _light(family_b) and fa == fb:
        tags.append("diacritics")
        caps.append(_CAP_DIACRITICS)

    if fa == fb:
        return 1.0, tags, caps, "surnames identical"

    if _compact(fa) == _compact(fb):
        tags.append("spacing")
        caps.append(_CAP_SPACING)
        return 1.0, tags, caps, "surnames differ only in spacing or particles"

    if _de_expand(_compact(fa)) == _de_expand(_compact(fb)):
        tags.append("german_ue")
        caps.append(_CAP_GERMAN_UE)
        return 1.0, tags, caps, "surnames agree after ue expansion"

    if _mc_normalize(fa) == _mc_normalize(fb):
        # Mc and Mac are related spellings, not the same spelling. Plenty of
        # McDonalds and MacDonalds are unrelated people.
        tags.append("mac_mc")
        caps.append(_CAP_MAC_MC)
        return 1.0, tags, caps, "Mc and Mac spelling variant"

    if given_agree:
        romanization = _transliteration_match(_compact(fa), _compact(fb))
        if romanization is not None:
            # Tried before the generic spelling fuzz below, so a pair that is
            # explainable as a romanization difference is reported as one and
            # gets the lower ceiling.
            new_tags, cap, reason = romanization
            tags.extend(new_tags)
            caps.append(cap)
            return 1.0, tags, caps, reason

    ratio = difflib.SequenceMatcher(None, _compact(fa), _compact(fb)).ratio()
    if ratio >= _SPELLING_FLOOR:
        tags.append("spelling")
        caps.append(_CAP_SPELLING)
        return ratio, tags, caps, "surnames differ by a near-identical spelling"

    # Not reachable by any rule: discount hard. A surname that merely looks
    # similar is the signature of an invented author list.
    return ratio * _FUZZY_DISCOUNT, [], [], "surnames differ"


# ---------------------------------------------------------------------------
# group authors
# ---------------------------------------------------------------------------


def _compare_groups(a: PersonName, b: PersonName) -> NameComparison:
    fa, fb = fold(a.family or a.literal), fold(b.family or b.literal)
    if fa and fa == fb:
        return NameComparison(1.0, True, [], "identical group author")
    tokens_a = set(fa.split()) - _GROUP_STOPWORDS
    tokens_b = set(fb.split()) - _GROUP_STOPWORDS
    if not tokens_a or not tokens_b:
        # Nothing distinctive on one side ("The Collaboration").
        return NameComparison(0.0, False, [], "group author has no distinctive name")
    shared = tokens_a & tokens_b
    if not shared:
        return NameComparison(0.0, False, [], "group authors share no distinctive name")
    coverage = len(shared) / float(min(len(tokens_a), len(tokens_b)))
    score = min(_CAP_GROUP_FUZZY, coverage)
    return NameComparison(
        score,
        False,
        ["group_fuzzy"],
        "group authors share their distinctive name",
    )


# ---------------------------------------------------------------------------
# CJK names
# ---------------------------------------------------------------------------


def _compare_cjk(a: PersonName, b: PersonName) -> NameComparison:
    """Compare two names written entirely in CJK ideographs.

    Such a name has no reliable family/given split, so it is compared as a
    character sequence, in either order: a two-character surname followed by
    a one-character given name is written both ways in the wild.
    """
    sa = _compact(fold(a.family + a.given))
    sb = _compact(fold(b.family + b.given))
    if sa and sa == sb:
        return NameComparison(1.0, True, [], "identical CJK name")
    if not sa or not sb:
        return NameComparison(0.0, False, [], "empty CJK name")
    if len(sa) == len(sb):
        for split in range(1, len(sa)):
            if sa[split:] + sa[:split] == sb:
                return NameComparison(
                    _CAP_ORDER_SWAPPED,
                    False,
                    ["order_swapped"],
                    "same CJK characters, family name on the other side",
                )
    return NameComparison(0.0, False, [], "different CJK names")


# ---------------------------------------------------------------------------
# pair comparison
# ---------------------------------------------------------------------------


def _resolve_caps(caps: Sequence[float], th: Thresholds) -> List[float]:
    """Turn cap placeholders into numbers.

    ``-1.0`` stands for the initials ceiling, which lives in the thresholds
    so that it stays configurable.
    """
    out = []  # type: List[float]
    for cap in caps:
        if cap < 0.0:
            out.append(th.initial_vs_full_given)
        else:
            out.append(cap)
    return out


def _compare_direct(a: PersonName, b: PersonName, th: Thresholds) -> NameComparison:
    given_score, given_tags, given_caps, given_reason = _compare_given(a.given, b.given)
    both_given = bool(fold(a.given)) and bool(fold(b.given))
    # "Already agree" means both sides actually have a given name and it
    # matched. A missing given name is not agreement, so a bare surname can
    # never unlock the transliteration skeleton.
    given_agree = (
        both_given
        and given_score >= 1.0
        and "middle_initial_conflict" not in given_tags
    )
    family_score, family_tags, family_caps, family_reason = _compare_family(
        a.family, b.family, given_agree
    )

    tags = list(family_tags) + [tag for tag in given_tags if tag not in family_tags]
    caps = list(family_caps) + list(given_caps)

    if a.suffix or b.suffix:
        sa, sb = fold(a.suffix), fold(b.suffix)
        if sa and sb and sa != sb:
            tags.append("suffix_conflict")
            caps.append(_CAP_SUFFIX_CONFLICT)
        elif sa != sb:
            tags.append("suffix_dropped")
            caps.append(_CAP_SUFFIX_DROPPED)

    score = _FAMILY_WEIGHT * family_score + _GIVEN_WEIGHT * given_score

    surname_only = bool(fold(a.family) or fold(b.family)) and (
        not fold(a.given) or not fold(b.given)
    )
    if surname_only:
        # A bare surname is weak identification. It can still match, because
        # bibliographies are full of bare surnames, but it must never reach
        # name_match on its own.
        score -= th.surname_only_penalty
        if "surname_only" not in tags:
            tags.append("surname_only")

    for cap in _resolve_caps(caps, th):
        score = min(score, cap)
    score = max(0.0, min(1.0, score))

    deduped = []  # type: List[str]
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)

    exact = not deduped and family_score >= 1.0 and given_score >= 1.0
    if exact:
        reason = "identical"
    elif given_score <= 0.0:
        reason = given_reason
    elif family_score < 1.0:
        reason = family_reason
    else:
        reason = given_reason
    return NameComparison(score, exact, deduped, reason)


def _is_initial_token(token: str) -> bool:
    """Is this given-name token an initial rather than a word?"""
    return token.endswith(".") or len(fold(token)) <= 1


def _regrouped(name: PersonName) -> List[Tuple[PersonName, List[str]]]:
    """Arrangements that move trailing given-name tokens onto the surname.

    Two real bibliography spellings put surname tokens on the given name,
    and :func:`_family_from_tokens` has no way to tell:

      * a compound surname written without its hyphen, so "Bernardino
        Romera-Paredes" is deposited as "Bernardino Romera Paredes" and
        parses as given "Bernardino Romera" plus family "Paredes";
      * a Dutch or German particle moved behind the given name, which is a
        legitimate BibTeX style, so "van den Driessche, George" is written
        "Driessche, George van den" and parses as given "George van den".

    Both are repaired by the same move, so this yields the arrangements with
    the last one, two, ... trailing given tokens pushed onto the front of
    the family name. The leftmost given token is always kept, so a name is
    never reduced to a bare surname. An initial is never moved: the trailing
    token of "John D. Smith" is a middle initial, not part of the surname,
    and moving it would marry "George W. Bush" to "George H. W. Bush".
    """
    tokens = name.given.split()
    out = []  # type: List[Tuple[PersonName, List[str]]]
    for count in range(1, len(tokens)):
        moved = tokens[len(tokens) - count :]
        if _is_initial_token(moved[0]):
            break
        out.append(
            (
                dataclasses.replace(
                    name,
                    given=" ".join(tokens[: len(tokens) - count]),
                    family=" ".join(moved + name.family.split()),
                ),
                moved,
            )
        )
    return out


def _compare_regrouped(
    a: PersonName, b: PersonName, th: Thresholds
) -> Optional[NameComparison]:
    """Best score reachable by moving given tokens onto either surname.

    An arrangement only counts when the two surnames then meet exactly
    (:func:`_strongly_equal`, so fold or compact equality and nothing
    fuzzier). That keeps the rule from inventing a surname boundary that
    happens to look similar, and it keeps the score symmetric, because the
    surname fuzz is the one comparison in this module that is not.

    The ceiling is ``_CAP_PARTICLE_MOVED`` (0.90) when every moved token is
    a known surname particle, and ``_CAP_COMPOUND_SURNAME`` (0.85)
    otherwise. Neither reaches name_match.
    """
    options_a = [(a, [])] + _regrouped(a)  # type: List[Tuple[PersonName, List[str]]]
    options_b = [(b, [])] + _regrouped(b)  # type: List[Tuple[PersonName, List[str]]]
    best = None  # type: Optional[NameComparison]
    for left, moved_left in options_a:
        for right, moved_right in options_b:
            moved = moved_left + moved_right
            if not moved:
                continue  # the plain arrangement is _compare_direct's job
            if not _strongly_equal(left.family, right.family):
                continue
            inner = _compare_direct(left, right, th)
            if all(fold(token) in _PARTICLES for token in moved):
                tag, cap = "particle_moved", _CAP_PARTICLE_MOVED
                reason = "same name with the surname particle behind the given name"
            else:
                tag, cap = "compound_surname", _CAP_COMPOUND_SURNAME
                reason = "same name with the compound surname split differently"
            variants = [tag] + [item for item in inner.variants if item != tag]
            candidate = NameComparison(min(inner.score, cap), False, variants, reason)
            if best is None or candidate.score > best.score:
                best = candidate
    return best


def _strongly_equal(left: str, right: str) -> bool:
    fl, fr = fold(left), fold(right)
    if not fl or not fr:
        return False
    return fl == fr or _compact(fl) == _compact(fr)


def compare_names(a: PersonName, b: PersonName, th: Thresholds) -> NameComparison:
    """Score how likely it is that two parsed names are the same person.

    The score runs from 0.0 to 1.0. At or above ``th.name_match`` the two
    spellings are the same name; at or above ``th.name_variant`` they are the
    same person written differently, and every rule that was needed to get
    there is listed in ``variants``. Below ``th.name_variant`` they are
    treated as different people.

    A group author is only ever compared with another group author.
    """
    if a.is_group or b.is_group:
        if a.is_group and b.is_group:
            return _compare_groups(a, b)
        return NameComparison(0.0, False, [], "group author versus personal name")

    if _is_cjk_only(a) and _is_cjk_only(b):
        return _compare_cjk(a, b)
    if _is_cjk_only(a) != _is_cjk_only(b):
        # One side is in Han characters and the other is romanized. Nothing
        # in this module can bridge that, and guessing would be worse than
        # reporting it as unmatched.
        return NameComparison(0.0, False, [], "Han characters versus romanization")

    direct = _compare_direct(a, b, th)
    if direct.score >= th.name_variant:
        return direct

    # Family-name-first ordering ("He Kaiming" for "Kaiming He"). Both halves
    # have to agree after the swap, which is what keeps "Andy Liu" and "Liu
    # Wei" apart: "liu" meets "liu", but "andy" never meets "wei".
    if a.given and b.given:
        if _strongly_equal(a.family, b.given) and _strongly_equal(a.given, b.family):
            tags = ["order_swapped"]
            if _light(a.family) != _light(b.given) and fold(a.family) == fold(b.given):
                tags.append("diacritics")
            return NameComparison(
                min(_CAP_ORDER_SWAPPED, 1.0),
                False,
                tags,
                "same name with the family name on the other side",
            )

    # Last resort: the surname may have lost a hyphen or had its particle
    # moved, so the parser put part of it on the given name. Only accepted
    # when it beats the direct comparison, so a pair this rule cannot
    # explain is still reported with the direct score and reason.
    regrouped = _compare_regrouped(a, b, th)
    if regrouped is not None and regrouped.score > direct.score:
        return regrouped
    return direct


# ---------------------------------------------------------------------------
# list comparison
# ---------------------------------------------------------------------------


def _candidate_pairs(
    cited: Sequence[PersonName], record: Sequence[PersonName], th: Thresholds
) -> List[Tuple[float, int, int, NameComparison]]:
    """Every pair that clears the name_variant floor.

    Quadratic in the list lengths, which is fine: the largest real author
    lists are a few hundred names (the DeepSeek-R1 record has 200), and the
    folding helpers are memoized, so a 200 x 200 matrix is thousands of
    cached string comparisons rather than millions of fresh ones.
    """
    out = []  # type: List[Tuple[float, int, int, NameComparison]]
    for i, left in enumerate(cited):
        for j, right in enumerate(record):
            cmp = compare_names(left, right, th)
            if cmp.score >= th.name_variant:
                out.append((cmp.score, i, j, cmp))
    # Deterministic: score descending, then both indices ascending.
    out.sort(key=lambda item: (-item[0], item[1], item[2]))
    return out


def _assign(
    candidates: Sequence[Tuple[float, int, int, NameComparison]],
) -> Dict[int, Tuple[int, NameComparison]]:
    """Greedy maximum-weight assignment with a repair pass.

    The greedy pass takes pairs in score order. The repair pass then tries to
    place each still-unmatched cited name by moving the record name it wants
    off a lower-scoring pair, which is only accepted when the displaced cited
    name has somewhere else to go. Both passes are deterministic, and the
    repair pass is bounded, so the whole thing stays well behaved on long
    author lists.
    """
    chosen = {}  # type: Dict[int, Tuple[int, NameComparison]]
    taken_record = {}  # type: Dict[int, int]
    by_cited = {}  # type: Dict[int, List[Tuple[float, int, NameComparison]]]
    for score, i, j, cmp in candidates:
        by_cited.setdefault(i, []).append((score, j, cmp))
        if i in chosen or j in taken_record:
            continue
        chosen[i] = (j, cmp)
        taken_record[j] = i

    for _ in range(2):
        improved = False
        for i in sorted(by_cited):
            if i in chosen:
                continue
            for _score, j, cmp in by_cited[i]:
                holder = taken_record.get(j)
                if holder is None:
                    chosen[i] = (j, cmp)
                    taken_record[j] = i
                    improved = True
                    break
                # Can the current holder move somewhere free?
                moved = False
                for _s2, j2, cmp2 in by_cited.get(holder, []):
                    if j2 != j and j2 not in taken_record:
                        chosen[holder] = (j2, cmp2)
                        taken_record[j2] = holder
                        chosen[i] = (j, cmp)
                        taken_record[j] = i
                        moved = True
                        break
                if moved:
                    improved = True
                    break
        if not improved:
            break
    return chosen


def compare_author_lists(
    cited: Sequence[PersonName],
    record: Sequence[PersonName],
    th: Thresholds,
    cited_truncated: bool = False,
) -> ListComparison:
    """Compare a cited author list against an authoritative one.

    ``cited_truncated`` says that the bibliography entry ended in "et al." or
    "and others", so the cited list is known to be short on purpose.

    Never returns ``UNVERIFIABLE``: that verdict means "no record was found",
    and only the caller knows whether a lookup succeeded.
    """
    cited = list(cited)
    record = list(record)
    notes = []  # type: List[str]

    if not cited:
        notes.append("the entry lists no authors, so there is nothing to check")
        return ListComparison(
            verdict=Verdict.PARTIAL_OVERLAP,
            truncated=cited_truncated,
            coverage=0.0,
            unmatched_record=list(range(len(record))),
            notes=notes,
        )

    if not record:
        notes.append("the record lists no authors, so there is nothing to compare with")
        return ListComparison(
            verdict=Verdict.PARTIAL_OVERLAP,
            unmatched_cited=list(range(len(cited))),
            truncated=cited_truncated,
            coverage=0.0,
            notes=notes,
        )

    candidates = _candidate_pairs(cited, record, th)
    chosen = _assign(candidates)

    pairs = []  # type: List[Tuple[int, int, NameComparison]]
    for i in sorted(chosen):
        j, cmp = chosen[i]
        pairs.append((i, j, cmp))

    matched_cited = sorted(chosen)
    matched_record = sorted(j for j, _cmp in chosen.values())
    unmatched_cited = [i for i in range(len(cited)) if i not in chosen]
    unmatched_record = [j for j in range(len(record)) if j not in set(matched_record)]

    variants = []  # type: List[str]
    for _i, _j, cmp in pairs:
        for tag in cmp.variants:
            if tag not in variants:
                variants.append(tag)
    variants.sort()

    record_order = [j for _i, j, _cmp in pairs]
    order_differs = any(
        record_order[k] > record_order[k + 1] for k in range(len(record_order) - 1)
    )

    comparable = len(cited)
    coverage = len(matched_cited) / float(comparable) if comparable else 0.0
    all_matched = not unmatched_cited
    all_exact = all(cmp.exact for _i, _j, cmp in pairs)

    if cited_truncated and all_matched and unmatched_record:
        is_prefix = record_order == list(range(len(record_order)))
        if not is_prefix:
            order_differs = True
            notes.append(
                "the cited list matches the record but not as its opening run"
            )
        notes.append(
            "the cited list is truncated: %d of %d record authors are not cited"
            % (len(unmatched_record), len(record))
        )
        verdict = Verdict.TRUNCATED
    elif all_matched:
        if cited_truncated:
            notes.append(
                "the cited list is marked truncated but covers every record author"
            )
        if order_differs:
            notes.append("the cited authors are the record authors in a different order")
            verdict = Verdict.ORDER_DIFFERS
        elif all_exact and not variants:
            verdict = Verdict.MATCH
        else:
            notes.append(
                "every cited author matched, with spelling variants: %s"
                % ", ".join(variants)
            )
            verdict = Verdict.MATCH_WITH_VARIANTS
    else:
        notes.append(
            "%d of %d cited authors did not match any author on the record: %s"
            % (
                len(unmatched_cited),
                len(cited),
                # Semicolons, not commas: an author name may itself be
                # written "Family, Given", and a comma-joined list of those
                # is unreadable.
                "; ".join(cited[i].literal or cited[i].family for i in unmatched_cited),
            )
        )
        if coverage > th.disjoint_max_coverage:
            if coverage < th.partial_coverage:
                notes.append(
                    "coverage %.2f is below the partial_coverage threshold %.2f"
                    % (coverage, th.partial_coverage)
                )
            verdict = Verdict.PARTIAL_OVERLAP
        elif cited_truncated:
            notes.append(
                "no cited author matched, but the cited list is truncated, so "
                "DISJOINT is withheld"
            )
            verdict = Verdict.PARTIAL_OVERLAP
        elif len(record) < th.disjoint_min_record_authors:
            notes.append(
                "no cited author matched, but the record lists only %d author(s), "
                "which is too few to call DISJOINT" % len(record)
            )
            verdict = Verdict.PARTIAL_OVERLAP
        else:
            notes.append(
                "no cited author appears on this record: check the citation by hand"
            )
            verdict = Verdict.DISJOINT

    return ListComparison(
        verdict=verdict,
        pairs=pairs,
        unmatched_cited=unmatched_cited,
        unmatched_record=unmatched_record,
        order_differs=order_differs,
        truncated=cited_truncated,
        coverage=coverage,
        variants=variants,
        notes=notes,
    )
