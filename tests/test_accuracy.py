"""Measured accuracy of the name matcher and the verdict engine.

This module is an evaluation harness, not a unit-test file. It loads two
hand-labelled fixture sets, runs the shipped code over them with the default
:class:`bibauthors.verdict.Thresholds`, and reports what it finds.

Three rules govern everything here, and they are the point of the file:

1. Nothing under ``src/`` is touched. The numbers describe the code as it is.
2. Every label in ``tests/fixtures/eval`` was set from the ground truth (is
   this the same human being? is this the real author list of this work?)
   before the code was run, and no label was revised afterwards because the
   code disagreed. A disagreement is a result, and it is reported as one.
3. Cases that currently fail are not deleted or softened. They are listed in
   :data:`KNOWN_PAIR_FAILURES` and :data:`KNOWN_ENTRY_FAILURES` and marked
   ``xfail(strict=True)``, so they stay visible in the test output and so a
   future improvement turns them into XPASS rather than passing silently.

The accuracy assertions are floors, not targets. They exist to catch a
regression, and they must never be raised to make a bad number look good.

Everything runs offline against the recorded fixtures in
``tests/fixtures/cache``. No network call is made.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pytest

from bibauthors.config import Config
from bibauthors.names import compare_names, parse_name
from bibauthors.parsers import load_bibliography
from bibauthors.verdict import MISMATCH_VERDICTS, Thresholds, Verdict
from bibauthors.verify import Verifier

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(HERE, "fixtures", "eval")
CACHE_DIR = os.path.join(HERE, "fixtures", "cache")
NAME_PAIRS_PATH = os.path.join(EVAL_DIR, "name_pairs.json")
ENTRIES_PATH = os.path.join(EVAL_DIR, "entries.json")

#: Verdicts that mean "the cited author list is the right one". The entry-set
#: false-accusation rate is measured against these.
CORRECT_LIST_VERDICTS = (
    Verdict.MATCH,
    Verdict.MATCH_WITH_VARIANTS,
    Verdict.ORDER_DIFFERS,
    Verdict.TRUNCATED,
)

# ---------------------------------------------------------------------------
# measured baseline, 1 Nov 2026, default Thresholds()
#
# These are floors recorded from a real run of the shipped code, not goals.
# A number below the floor is a regression. A number above it is an
# improvement that should be accompanied by moving the floor up and by
# removing the matching ids from the KNOWN_*_FAILURES lists below.
# ---------------------------------------------------------------------------

# Pairs: 154 cases, 84 TP / 14 FP / 5 FN / 51 TN.
BASELINE_PAIR_ACCURACY = 0.8765  # 135 / 154
BASELINE_PAIR_PRECISION = 0.8570  # 84 / 98
BASELINE_PAIR_RECALL = 0.9437  # 84 / 89
# Entries: 27 cases, 26 agree with the label.
BASELINE_ENTRY_ACCURACY = 0.9629  # 26 / 27

#: Ceilings, measured the same way. The two numbers the design premise of this
#: tool rests on: a false accusation is worse than a miss.
BASELINE_PAIR_FALSE_ACCUSATION_RATE = 0.0563  # 5 / 89 same-labelled pairs
BASELINE_PAIR_MISS_RATE = 0.2155  # 14 / 65 different-labelled pairs
BASELINE_ENTRY_FALSE_ACCUSATION_RATE = 0.0589  # 1 / 17 correct-list entries
BASELINE_ENTRY_MISS_RATE = 0.0  # 0 / 8 wrong-list entries

# History, so a future change can see which way the numbers have moved.
# First measurement, 1 Nov 2026, before the compound-surname and
# transliteration-skeleton fixes: pair accuracy 0.8312, precision 0.8058,
# recall 0.9326, pair false-accusation rate 0.0674, pair miss rate 0.3077,
# entry accuracy 0.9259, entry false-accusation rate 0.1176.
# Eight labelled cases were wrong then and are right now: the pairs part-10,
# apos-06, hyph-01, comma-06, near-03, near-06 and near-10, and the entry
# entry-08. Nothing that was right became wrong.

#: Name pairs the shipped matcher gets wrong against the ground-truth label.
#: Every one of these is a result, not a bug in the label. Five are false
#: accusations (a real person split in two) and fourteen are misses (two
#: people merged into one). The reasons say which rule in names.py produced
#: the answer, so a future change can be judged against them.
#:
#: Several of the misses are deliberate policy, not oversight: a contradicting
#: middle initial (midc-*) and a contradicting generational suffix (suf-*) are
#: scored as weak evidence that two spellings are different people, never as
#: proof, because the cost asymmetry says so. They are listed here anyway,
#: because a deliberate choice with a measured cost is still a measured cost.
KNOWN_PAIR_FAILURES: Dict[str, str] = {
    # --- false accusations: label "same", matcher says "different" --------
    "part-04": (
        "FALSE ACCUSATION. Dropping the Dutch tussenvoegsel leaves "
        "'van Beethoven' against 'Beethoven'; the surname fuzz lands at "
        "0.656, below name_variant. names.py has no rule for a dropped "
        "particle"
    ),
    "trans-05": (
        "FALSE ACCUSATION. 'Lev' and 'Leo' are the Russian and English "
        "forms of one given name; _compare_given has no given-name "
        "equivalence table, so the first given names simply differ"
    ),
    "trans-06": (
        "FALSE ACCUSATION. 'Lobachevsky' and 'Lobachevskii' differ by two "
        "characters, which puts the ratio at 0.90, below the 0.93 "
        "_SPELLING_FLOOR, and the consonant skeleton does not collapse the "
        "'y' / 'ii' ending"
    ),
    "cjks-03": (
        "FALSE ACCUSATION by documented design. compare_names returns 0.0 "
        "for Han characters against a romanization rather than guess. The "
        "refusal is defensible; the cost is that a Chinese-language "
        "bibliography entry for a real author is unmatchable"
    ),
    "grp-05": (
        "FALSE ACCUSATION. 'Qwen Team' is detected as a group and the bare "
        "token 'Qwen' that arXiv deposits is not, and a group is never "
        "compared with a personal name"
    ),
    # --- misses: label "different", matcher says "same" ------------------
    "midc-01": (
        "MISS. George W. Bush and George H. W. Bush are father and son. The "
        "_CAP_MIDDLE_CONFLICT ceiling of 0.85 is above name_variant, so a "
        "middle-initial conflict never separates two people on its own"
    ),
    "midc-02": (
        "MISS. Michael I. Jordan and Michael B. Jordan are two real, "
        "distinct people; same 0.85 middle-conflict ceiling"
    ),
    "midc-03": "MISS. Same 0.85 middle-conflict ceiling",
    "midc-04": "MISS. Same 0.85 middle-conflict ceiling",
    "mc-04": (
        "MISS. _mc_normalize treats Mc and Mac as one surname at a 0.88 "
        "ceiling. names.py says in a comment that plenty of McDonalds and "
        "MacDonalds are unrelated, and then matches them anyway"
    ),
    "mc-05": "MISS. Same _mc_normalize rule at 0.88",
    "mc-06": "MISS. Same _mc_normalize rule at 0.88",
    "hyph-07": (
        "MISS. 'Hans-Peter' and 'Hans-Jurgen' agree on the first given-name "
        "unit, so the second unit disagreeing is only a "
        "middle_initial_conflict at the 0.85 ceiling"
    ),
    "hyph-08": "MISS. Same first-unit-agrees, second-unit-conflicts path",
    "trans-08": (
        "MISS. The skeleton maps 'Zhang' and 'Chang' together (zh -> ch), "
        "which is defensible for Wade-Giles but merges two surnames that are "
        "distinct in modern use"
    ),
    "suf-04": (
        "MISS. _CAP_SUFFIX_CONFLICT is 0.85, above name_variant, so Jr. "
        "against Sr. does not separate a father from his son"
    ),
    "suf-05": "MISS. Same 0.85 suffix-conflict ceiling (III against II)",
    "suf-06": "MISS. Same 0.85 suffix-conflict ceiling",
    "near-04": (
        "MISS. 'Johnson' and 'Johnsen' differ by one vowel in one run, which "
        "is exactly the shape of the vowel_romanization rule (ceiling 0.78), "
        "and it is structurally identical to 'Gorbachev' / 'Gorbachov', which "
        "is labelled SAME. No position-independent vowel rule separates the "
        "two, so fixing this one would turn that one into a false accusation"
    ),
}

#: Entries whose verdict differs from the ground-truth expectation. The one
#: remaining case is a false accusation: a correct citation given a mismatch
#: verdict, caused by a defect in the deposited record rather than by the
#: bibliography.
KNOWN_ENTRY_FAILURES: Dict[str, str] = {
    "entry-23": (
        "FALSE ACCUSATION caused by a defect in the RECORD, not the "
        "citation. CrossRef deposits Aidan N. Gomez as family 'N.Gomez', "
        "given 'Aidan'. The correctly spelled 'Gomez, Aidan N.' scores 0.909 "
        "against it, just under the 0.93 _SPELLING_FLOOR, so a correct "
        "bibliography of Attention Is All You Need is reported as "
        "PARTIAL_OVERLAP"
    ),
}

#: The score of one pair depends on which side is passed first, because
#: ``difflib.SequenceMatcher.ratio`` is not symmetric. It does not change any
#: decision in this set, but it is a real defect and it is recorded here
#: rather than hidden. See :func:`test_the_matcher_score_is_symmetric`.
KNOWN_ASYMMETRIC_PAIRS = ("diffp-07",)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_name_pairs() -> List[Dict[str, Any]]:
    """The labelled name-pair set, in file order."""
    with open(NAME_PAIRS_PATH, "r", encoding="ascii") as handle:
        return json.load(handle)


def load_entry_cases() -> List[Dict[str, Any]]:
    """The labelled entry-level set, in file order."""
    with open(ENTRIES_PATH, "r", encoding="ascii") as handle:
        return json.load(handle)


def offline_config(thresholds: Optional[Thresholds] = None) -> Config:
    """A Config that can only ever read the committed fixtures."""
    return Config(
        thresholds=thresholds or Thresholds(),
        cache_dir=CACHE_DIR,
        offline=True,
        min_interval=0.0,
    )


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return numerator / float(denominator)


def binary_metrics(tp: int, fp: int, fn: int, tn: int) -> Dict[str, Any]:
    """Counts and rates for the "same person" decision.

    The positive class is "same person", so a false NEGATIVE is the false
    accusation: a pair that is genuinely one human being, called two.
    """
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = 0.0
    if precision + recall:
        f1 = 2.0 * precision * recall / (precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "n": tp + fp + fn + tn,
        "positives": tp + fn,
        "negatives": fp + tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": _ratio(tp + tn, tp + fp + fn + tn),
        # The two numbers that matter for this tool.
        "false_accusation_rate": _ratio(fn, tp + fn),
        "miss_rate": _ratio(fp, fp + tn),
    }


# ---------------------------------------------------------------------------
# the name-pair evaluation
# ---------------------------------------------------------------------------

_PAIR_CACHE: Dict[str, Any] = {}


def evaluate_name_pairs(thresholds: Optional[Thresholds] = None) -> Dict[str, Any]:
    """Run ``compare_names`` over every labelled pair and score the result.

    A pair is predicted "same" when the score reaches ``name_variant``, which
    is the decision the list matcher itself makes in
    :func:`bibauthors.names.compare_author_lists`.

    The returned dict holds ``rows`` (one per pair, with the score and the
    variant tags), ``overall``, ``by_category`` and ``disagreements``.
    """
    use_default = thresholds is None
    if use_default and "default" in _PAIR_CACHE:
        return _PAIR_CACHE["default"]
    th = thresholds or Thresholds()

    rows: List[Dict[str, Any]] = []
    for case in load_name_pairs():
        left = parse_name(case["a"])
        right = parse_name(case["b"])
        forward = compare_names(left, right, th)
        backward = compare_names(right, left, th)
        predicted = "same" if forward.score >= th.name_variant else "different"
        rows.append(
            {
                "id": case["id"],
                "a": case["a"],
                "b": case["b"],
                "category": case["category"],
                "label": case["label"],
                "note": case.get("note", ""),
                "score": forward.score,
                "reverse_score": backward.score,
                "symmetric": abs(forward.score - backward.score) < 1e-9,
                "exact": forward.exact,
                "variants": list(forward.variants),
                "reason": forward.reason,
                "predicted": predicted,
                "correct": predicted == case["label"],
            }
        )

    def tally(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        tp = sum(1 for r in subset if r["label"] == "same" and r["predicted"] == "same")
        fn = sum(
            1 for r in subset if r["label"] == "same" and r["predicted"] == "different"
        )
        fp = sum(
            1 for r in subset if r["label"] == "different" and r["predicted"] == "same"
        )
        tn = sum(
            1
            for r in subset
            if r["label"] == "different" and r["predicted"] == "different"
        )
        return binary_metrics(tp, fp, fn, tn)

    categories = sorted({r["category"] for r in rows})
    result = {
        "thresholds": th,
        "rows": rows,
        "overall": tally(rows),
        "by_category": {
            name: tally([r for r in rows if r["category"] == name])
            for name in categories
        },
        "categories": categories,
        "disagreements": [r for r in rows if not r["correct"]],
        "asymmetric": [r for r in rows if not r["symmetric"]],
    }
    if use_default:
        _PAIR_CACHE["default"] = result
    return result


# ---------------------------------------------------------------------------
# the entry-level evaluation
# ---------------------------------------------------------------------------

_ENTRY_CACHE: Dict[str, Any] = {}


def evaluate_entries(thresholds: Optional[Thresholds] = None) -> Dict[str, Any]:
    """Verify every labelled entry offline and compare against the label.

    Returns ``rows``, ``accuracy``, a full ``confusion`` matrix over the seven
    verdicts, and the two asymmetric cost figures: ``false_accusations``
    (a genuinely correct author list given a mismatch verdict) and ``misses``
    (a genuinely wrong author list not given a mismatch verdict).
    """
    use_default = thresholds is None
    if use_default and "default" in _ENTRY_CACHE:
        return _ENTRY_CACHE["default"]
    th = thresholds or Thresholds()
    verifier = Verifier(config=offline_config(th))

    rows: List[Dict[str, Any]] = []
    for case in load_entry_cases():
        entries = load_bibliography(case["bibtex"], fmt="bibtex", is_text=True)
        assert len(entries) == 1, "case %s must hold exactly one entry" % case["id"]
        result = verifier.verify_entry(entries[0])
        comparison = result.comparison
        rows.append(
            {
                "id": case["id"],
                "key": entries[0].key,
                "expected": case["expected_verdict"],
                "actual": result.verdict,
                "correct": result.verdict == case["expected_verdict"],
                "label_basis": case.get("label_basis", ""),
                "requires_cache": case.get("requires_cache", ""),
                "coverage": comparison.coverage if comparison else None,
                "variants": list(comparison.variants) if comparison else [],
                "record_source": result.record.source if result.record else "",
                "record_authors": len(result.record.authors) if result.record else 0,
                "cited_authors": len(entries[0].authors),
                "unmatched_cited": (
                    [entries[0].authors[i] for i in comparison.unmatched_cited]
                    if comparison
                    else []
                ),
                "notes": list(comparison.notes) if comparison else [],
                "provider_notes": list(result.provider_notes),
            }
        )

    confusion: Dict[str, Dict[str, int]] = {
        expected: {actual: 0 for actual in Verdict.ALL} for expected in Verdict.ALL
    }
    for row in rows:
        confusion[row["expected"]][row["actual"]] += 1

    judgeable = [r for r in rows if r["expected"] != Verdict.UNVERIFIABLE]
    correct_lists = [r for r in judgeable if r["expected"] in CORRECT_LIST_VERDICTS]
    wrong_lists = [r for r in judgeable if r["expected"] in MISMATCH_VERDICTS]
    false_accusations = [r for r in correct_lists if r["actual"] in MISMATCH_VERDICTS]
    misses = [r for r in wrong_lists if r["actual"] not in MISMATCH_VERDICTS]

    result = {
        "thresholds": th,
        "rows": rows,
        "n": len(rows),
        "correct": sum(1 for r in rows if r["correct"]),
        "accuracy": _ratio(sum(1 for r in rows if r["correct"]), len(rows)),
        "confusion": confusion,
        "disagreements": [r for r in rows if not r["correct"]],
        "correct_list_entries": len(correct_lists),
        "wrong_list_entries": len(wrong_lists),
        "false_accusations": false_accusations,
        "misses": misses,
        "false_accusation_rate": _ratio(len(false_accusations), len(correct_lists)),
        "miss_rate": _ratio(len(misses), len(wrong_lists)),
    }
    if use_default:
        _ENTRY_CACHE["default"] = result
    return result


# ---------------------------------------------------------------------------
# per-case tests
# ---------------------------------------------------------------------------


def _pair_params() -> List[Any]:
    params = []
    for case in load_name_pairs():
        marks = []
        if case["id"] in KNOWN_PAIR_FAILURES:
            marks.append(
                pytest.mark.xfail(
                    strict=True, reason=KNOWN_PAIR_FAILURES[case["id"]]
                )
            )
        params.append(pytest.param(case["id"], marks=marks, id=case["id"]))
    return params


def _entry_params() -> List[Any]:
    params = []
    for case in load_entry_cases():
        marks = []
        if case["id"] in KNOWN_ENTRY_FAILURES:
            marks.append(
                pytest.mark.xfail(
                    strict=True, reason=KNOWN_ENTRY_FAILURES[case["id"]]
                )
            )
        params.append(pytest.param(case["id"], marks=marks, id=case["id"]))
    return params


@pytest.mark.parametrize("pair_id", _pair_params())
def test_name_pair_case(pair_id):
    """One assertion per labelled pair, so every failure names itself."""
    rows = {row["id"]: row for row in evaluate_name_pairs()["rows"]}
    row = rows[pair_id]
    assert row["predicted"] == row["label"], (
        "%s: %r vs %r is %s, matcher says %s (score %.4f, variants %s, %s)"
        % (
            row["id"],
            row["a"],
            row["b"],
            row["label"],
            row["predicted"],
            row["score"],
            row["variants"],
            row["reason"],
        )
    )


@pytest.mark.parametrize("entry_id", _entry_params())
def test_entry_case(entry_id):
    """One assertion per labelled entry."""
    rows = {row["id"]: row for row in evaluate_entries()["rows"]}
    row = rows[entry_id]
    assert row["actual"] == row["expected"], (
        "%s: expected %s, got %s (coverage %s, unmatched %s)"
        % (
            row["id"],
            row["expected"],
            row["actual"],
            row["coverage"],
            row["unmatched_cited"],
        )
    )


# ---------------------------------------------------------------------------
# aggregate floors
# ---------------------------------------------------------------------------


def test_name_pair_accuracy_has_not_dropped():
    """A floor, not a target. Raising it to flatter the tool is cheating."""
    overall = evaluate_name_pairs()["overall"]
    assert overall["accuracy"] >= BASELINE_PAIR_ACCURACY, overall
    assert overall["precision"] >= BASELINE_PAIR_PRECISION, overall
    assert overall["recall"] >= BASELINE_PAIR_RECALL, overall


def test_entry_accuracy_has_not_dropped():
    """A floor, not a target."""
    result = evaluate_entries()
    assert result["accuracy"] >= BASELINE_ENTRY_ACCURACY, result["accuracy"]


def test_false_accusation_rate_has_not_risen():
    """The cost-asymmetric number: a real author called an impostor."""
    pairs = evaluate_name_pairs()["overall"]
    assert (
        pairs["false_accusation_rate"] <= BASELINE_PAIR_FALSE_ACCUSATION_RATE
    ), pairs["false_accusation_rate"]
    entries = evaluate_entries()
    assert (
        entries["false_accusation_rate"] <= BASELINE_ENTRY_FALSE_ACCUSATION_RATE
    ), entries["false_accusation_rate"]


def test_miss_rate_has_not_risen():
    """The other side of the trade: a fabrication that was not flagged."""
    pairs = evaluate_name_pairs()["overall"]
    assert pairs["miss_rate"] <= BASELINE_PAIR_MISS_RATE, pairs["miss_rate"]
    entries = evaluate_entries()
    assert entries["miss_rate"] <= BASELINE_ENTRY_MISS_RATE, entries["miss_rate"]


# ---------------------------------------------------------------------------
# the set itself
# ---------------------------------------------------------------------------

REQUIRED_CATEGORIES = (
    "exact",
    "initials",
    "middle_name",
    "middle_initial_conflict",
    "particle",
    "mc_mac",
    "apostrophe",
    "hyphenated",
    "diacritics",
    "transliteration",
    "cjk_order",
    "cjk_script",
    "suffix",
    "comma_order",
    "group",
    "near_miss_surname",
    "different_person",
    "same_surname_different_person",
    "initial_collision",
)


def test_name_pair_set_composition():
    cases = load_name_pairs()
    assert len(cases) >= 120, len(cases)
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate pair ids"
    present = {c["category"] for c in cases}
    missing = [name for name in REQUIRED_CATEGORIES if name not in present]
    assert not missing, missing
    assert not present - set(REQUIRED_CATEGORIES), present - set(REQUIRED_CATEGORIES)
    same = sum(1 for c in cases if c["label"] == "same")
    # Roughly 60/40, checked loosely so the set can grow without a rewrite.
    assert 0.50 <= same / float(len(cases)) <= 0.70, same / float(len(cases))
    for category in ("near_miss_surname", "different_person",
                     "same_surname_different_person"):
        rows = [c for c in cases if c["category"] == category]
        assert rows, category
        assert all(c["label"] == "different" for c in rows), category


def test_entry_set_covers_every_verdict():
    cases = load_entry_cases()
    assert len(cases) >= 20, len(cases)
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate entry ids"
    expected = {c["expected_verdict"] for c in cases}
    missing = [v for v in Verdict.ALL if v not in expected]
    assert not missing, missing


def test_fixture_files_are_pure_ascii():
    for path in (NAME_PAIRS_PATH, ENTRIES_PATH,
                 os.path.join(EVAL_DIR, "README-DATA.txt")):
        with open(path, "rb") as handle:
            raw = handle.read()
        bad = [index for index, byte in enumerate(raw) if byte > 0x7F]
        assert not bad, "%s has non-ASCII bytes at %s" % (path, bad[:5])


def test_the_matcher_decision_is_symmetric_on_every_pair():
    """Which side came from the bibliography must not change the verdict."""
    th = Thresholds()
    flipped = []
    for row in evaluate_name_pairs()["rows"]:
        forward = row["score"] >= th.name_variant
        backward = row["reverse_score"] >= th.name_variant
        if forward != backward:
            flipped.append((row["id"], row["score"], row["reverse_score"]))
    assert not flipped, flipped


@pytest.mark.xfail(
    strict=True,
    reason=(
        "difflib.SequenceMatcher.ratio is not symmetric, so the surname fuzz "
        "score of diffp-07 ('Ashish Vaswani' against 'Noam Shazeer') is "
        "0.0511 one way and 0.1021 the other. Both are far below "
        "name_variant, so no decision changes, but the score itself is not "
        "a function of the unordered pair"
    ),
)
def test_the_matcher_score_is_symmetric_on_every_pair():
    """A stricter property than the one above, and it does not hold."""
    asymmetric = evaluate_name_pairs()["asymmetric"]
    assert not asymmetric, [
        (r["id"], r["score"], r["reverse_score"]) for r in asymmetric
    ]


def test_the_known_asymmetric_pairs_are_exactly_the_recorded_ones():
    """So a new asymmetry cannot hide behind the xfail above."""
    found = tuple(sorted(r["id"] for r in evaluate_name_pairs()["asymmetric"]))
    assert found == tuple(sorted(KNOWN_ASYMMETRIC_PAIRS)), found


def test_evaluation_runs_offline_without_a_network_client():
    """Guard against a fixture silently going to the network in CI."""
    config = offline_config()
    assert config.offline is True
    verifier = Verifier(config=config)
    assert verifier.client is not None
    assert verifier.client.offline is True
    assert verifier.client.request_count == 0
    evaluate_entries()
    assert verifier.client.request_count == 0
