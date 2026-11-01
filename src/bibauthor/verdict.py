"""Verdicts, severities and the tunable thresholds.

The verdict vocabulary is deliberately graded rather than boolean. A
bibliography is full of legitimate variation (initials, diacritics,
transliteration, "et al."), and a tool that answers only yes or no either
cries wolf or stays silent. The grades separate "this is the same author list
written differently" from "this author list does not belong to this work".

The cost asymmetry is explicit and it is encoded in the defaults: wrongly
telling a researcher that a real citation is fabricated is worse than failing
to flag a fabricated one, because the first costs trust in the tool and can
cost an accusation against a colleague. DISJOINT is therefore hard to reach:
see :func:`bibauthor.names.compare_author_lists` and the README.
"""

from __future__ import annotations

import dataclasses
from typing import Dict


class Verdict(object):
    """String constants, not an ``enum.Enum``.

    Plain strings keep the JSON report encoder-free and keep the schema
    readable to anything that consumes it.
    """

    MATCH = "MATCH"
    MATCH_WITH_VARIANTS = "MATCH_WITH_VARIANTS"
    ORDER_DIFFERS = "ORDER_DIFFERS"
    TRUNCATED = "TRUNCATED"
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    DISJOINT = "DISJOINT"
    UNVERIFIABLE = "UNVERIFIABLE"

    ALL = (
        MATCH,
        MATCH_WITH_VARIANTS,
        ORDER_DIFFERS,
        TRUNCATED,
        PARTIAL_OVERLAP,
        DISJOINT,
        UNVERIFIABLE,
    )


SEVERITY = {
    Verdict.MATCH: 0,
    Verdict.MATCH_WITH_VARIANTS: 0,
    Verdict.TRUNCATED: 1,
    Verdict.ORDER_DIFFERS: 2,
    Verdict.UNVERIFIABLE: 2,
    Verdict.PARTIAL_OVERLAP: 3,
    Verdict.DISJOINT: 4,
}  # type: Dict[str, int]

#: One line of explanation per verdict, reused by the human report and the docs.
DESCRIPTIONS = {
    Verdict.MATCH: "cited authors and record authors agree exactly",
    Verdict.MATCH_WITH_VARIANTS: "same authors, written differently",
    Verdict.ORDER_DIFFERS: "same authors, different order",
    Verdict.TRUNCATED: "cited list is a truncated prefix of the record",
    Verdict.PARTIAL_OVERLAP: "some cited authors are not on this work",
    Verdict.DISJOINT: "no cited author is on this work: check by hand",
    Verdict.UNVERIFIABLE: "no authoritative record found to compare against",
}

#: Verdicts that mean "an author mismatch was found" (exit code 1).
MISMATCH_VERDICTS = (Verdict.DISJOINT, Verdict.PARTIAL_OVERLAP)


@dataclasses.dataclass
class Thresholds:
    """Every number the matcher uses. All of it is configurable.

    name_match
        Pair score at or above this counts as the same name with no variant
        rules needed worth reporting.
    name_variant
        Pair score at or above this counts as the same person, reached by one
        or more variant rules (initials, diacritics, particles, order). Below
        this, two names are treated as different people. This is the single
        most consequential number in the tool: lowering it hides real
        fabrications, raising it manufactures them.
    partial_coverage
        Fraction of cited authors that must be matched for PARTIAL_OVERLAP
        rather than something stronger.
    title_similarity
        How close the record title must be to the cited title before the
        record is allowed to drive a mismatch verdict. Guards against
        comparing against the wrong paper.
    disjoint_min_record_authors
        A record with fewer known authors than this can never produce
        DISJOINT. One-author records are too easy to get wrong.
    disjoint_max_coverage
        Coverage must be at or below this for DISJOINT. The default 0.0 means
        a single matched author is enough to downgrade to PARTIAL_OVERLAP.
    surname_only_penalty
        Subtracted when one side has no given name at all, so bare surnames
        cannot reach name_match on their own.
    initial_vs_full_given
        Ceiling for a given-name pair that agrees only on the initial.
    allow_order_differs_as_pass
        When true, ORDER_DIFFERS does not fail the run.
    promote_order_differs / promote_truncated
        When true, those verdicts count as mismatches for the exit code.
    """

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

    def validate(self) -> None:
        if not 0.0 < self.name_variant <= self.name_match <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 < name_variant <= name_match <= 1 "
                "(got name_variant=%r, name_match=%r)"
                % (self.name_variant, self.name_match)
            )
        if not 0.0 <= self.partial_coverage <= 1.0:
            raise ValueError("partial_coverage must be in [0, 1]")
        if not 0.0 <= self.title_similarity <= 1.0:
            raise ValueError("title_similarity must be in [0, 1]")
        if self.disjoint_min_record_authors < 1:
            raise ValueError("disjoint_min_record_authors must be at least 1")


def worst(verdicts) -> str:
    """Return the most severe verdict in an iterable, MATCH if empty."""
    found = list(verdicts)
    if not found:
        return Verdict.MATCH
    return max(found, key=lambda v: SEVERITY.get(v, 0))


def is_mismatch(verdict: str, th: Thresholds) -> bool:
    """Does this verdict mean the run failed with an author mismatch?"""
    if verdict in MISMATCH_VERDICTS:
        return True
    if verdict == Verdict.ORDER_DIFFERS and th.promote_order_differs:
        return True
    if verdict == Verdict.TRUNCATED and th.promote_truncated:
        return True
    return False
