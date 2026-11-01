#!/usr/bin/env python3
"""Print the measured accuracy of bibauthor against the labelled eval sets.

Run it from the repository root:

    PYTHONPATH=src python -X utf8 tools/evaluate.py

Everything it prints comes from running the shipped code in
``src/bibauthor`` over ``tests/fixtures/eval``, offline, against the recorded
API responses in ``tests/fixtures/cache``. No network call is made and no
source file is modified.

The output is the source of the published accuracy table, so it prints the
failures as prominently as the successes: every disagreement between the
label and the tool appears in full, with the score that produced it.

Sections, in order:

1. the overall table for the name-pair set
2. the per-category table
3. the name-pair confusion matrix
4. every name-pair disagreement
5. the entry-level table and its 7 x 7 confusion matrix
6. every entry-level disagreement
7. the two cost-asymmetric rates
8. a sweep of ``name_variant`` from 0.50 to 0.95

This file is pure ASCII on purpose: it is run on consoles whose code page is
not UTF-8. Names that contain non-ASCII characters are printed with their
characters replaced by a ``\\uXXXX`` escape rather than risking an encoding
error mid-table.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bibauthor.verdict import MISMATCH_VERDICTS, Thresholds, Verdict  # noqa: E402

from test_accuracy import (  # noqa: E402
    BASELINE_ENTRY_ACCURACY,
    BASELINE_ENTRY_FALSE_ACCUSATION_RATE,
    BASELINE_ENTRY_MISS_RATE,
    BASELINE_PAIR_ACCURACY,
    BASELINE_PAIR_FALSE_ACCUSATION_RATE,
    BASELINE_PAIR_MISS_RATE,
    BASELINE_PAIR_PRECISION,
    BASELINE_PAIR_RECALL,
    CORRECT_LIST_VERDICTS,
    KNOWN_ENTRY_FAILURES,
    KNOWN_PAIR_FAILURES,
    evaluate_entries,
    evaluate_name_pairs,
    load_entry_cases,
    load_name_pairs,
)

WIDTH = 78
SWEEP = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

#: Short labels for the seven verdicts, for the confusion-matrix header.
SHORT = {
    Verdict.MATCH: "MAT",
    Verdict.MATCH_WITH_VARIANTS: "MWV",
    Verdict.ORDER_DIFFERS: "ORD",
    Verdict.TRUNCATED: "TRU",
    Verdict.PARTIAL_OVERLAP: "PAR",
    Verdict.DISJOINT: "DIS",
    Verdict.UNVERIFIABLE: "UNV",
}


def ascii_safe(text: str) -> str:
    """Replace every non-ASCII character with its ``\\uXXXX`` escape."""
    out = []
    for ch in str(text):
        if ord(ch) < 128:
            out.append(ch)
        else:
            out.append("\\u%04x" % ord(ch))
    return "".join(out)


def rule(char: str = "-") -> None:
    print(char * WIDTH)


def heading(text: str) -> None:
    print("")
    rule("=")
    print(text)
    rule("=")


def main() -> int:
    pairs = evaluate_name_pairs()
    entries = evaluate_entries()
    th = Thresholds()

    print("bibauthor measured accuracy")
    rule("=")
    print("name pairs      : %s" % os.path.join("tests", "fixtures", "eval",
                                                "name_pairs.json"))
    print("entries         : %s" % os.path.join("tests", "fixtures", "eval",
                                                "entries.json"))
    print("record fixtures : %s" % os.path.join("tests", "fixtures", "cache"))
    print("thresholds      : name_variant=%.2f name_match=%.2f "
          "partial_coverage=%.2f" % (th.name_variant, th.name_match,
                                     th.partial_coverage))
    print("                  surname_only_penalty=%.2f "
          "initial_vs_full_given=%.2f" % (th.surname_only_penalty,
                                          th.initial_vs_full_given))
    print("mode            : offline, recorded responses only, no network")

    # -- 1. overall -------------------------------------------------------
    heading("1. NAME PAIRS, OVERALL")
    overall = pairs["overall"]
    print("The decision scored is 'same person', taken as "
          "score >= name_variant (%.2f)." % th.name_variant)
    print("A FALSE NEGATIVE is the false accusation: one human being called "
          "two.")
    print("")
    print("  pairs                      %d" % overall["n"])
    print("  labelled same              %d" % overall["positives"])
    print("  labelled different         %d" % overall["negatives"])
    print("")
    print("  true positives  (TP)       %d" % overall["tp"])
    print("  false positives (FP)       %d   two people merged into one"
          % overall["fp"])
    print("  false negatives (FN)       %d   one person split in two"
          % overall["fn"])
    print("  true negatives  (TN)       %d" % overall["tn"])
    print("")
    print("  precision                  %.4f" % overall["precision"])
    print("  recall                     %.4f" % overall["recall"])
    print("  F1                         %.4f" % overall["f1"])
    print("  accuracy                   %.4f" % overall["accuracy"])

    # -- 2. per category --------------------------------------------------
    heading("2. NAME PAIRS, PER CATEGORY")
    header = ("%-31s %4s %4s %4s %4s %4s %4s %7s %7s"
              % ("category", "n", "same", "TP", "FP", "FN", "TN", "acc", "F1"))
    print(header)
    rule()
    for name in pairs["categories"]:
        m = pairs["by_category"][name]
        print("%-31s %4d %4d %4d %4d %4d %4d %7.4f %7.4f"
              % (name, m["n"], m["positives"], m["tp"], m["fp"], m["fn"],
                 m["tn"], m["accuracy"], m["f1"]))
    rule()
    print("%-31s %4d %4d %4d %4d %4d %4d %7.4f %7.4f"
          % ("ALL", overall["n"], overall["positives"], overall["tp"],
             overall["fp"], overall["fn"], overall["tn"],
             overall["accuracy"], overall["f1"]))

    # -- 3. pair confusion matrix -----------------------------------------
    heading("3. NAME PAIRS, CONFUSION MATRIX")
    print("                   predicted same   predicted different")
    print("  label same       %14d   %19d" % (overall["tp"], overall["fn"]))
    print("  label different  %14d   %19d" % (overall["fp"], overall["tn"]))

    # -- 4. pair disagreements --------------------------------------------
    heading("4. EVERY NAME-PAIR DISAGREEMENT (%d of %d)"
            % (len(pairs["disagreements"]), overall["n"]))
    if not pairs["disagreements"]:
        print("  none")
    else:
        print("kind       = FALSE ACCUSATION (label same, called different)")
        print("           | MISS            (label different, called same)")
        rule()
        for row in pairs["disagreements"]:
            kind = ("FALSE ACCUSATION" if row["label"] == "same" else "MISS")
            print("%-12s %-16s %s" % (row["id"], kind, row["category"]))
            print("    a         : %s" % ascii_safe(row["a"]))
            print("    b         : %s" % ascii_safe(row["b"]))
            print("    label     : %s" % row["label"])
            print("    predicted : %s" % row["predicted"])
            print("    score     : %.4f  (threshold %.2f)"
                  % (row["score"], th.name_variant))
            print("    variants  : %s" % (", ".join(row["variants"]) or "none"))
            print("    reason    : %s" % row["reason"])
            print("    label note: %s" % ascii_safe(row["note"]))
            if row["id"] in KNOWN_PAIR_FAILURES:
                print("    recorded  : yes, xfail(strict=True) in "
                      "tests/test_accuracy.py")
            else:
                print("    recorded  : NO. This disagreement is new since the "
                      "baseline was taken.")
            rule()

    # -- 5. entries -------------------------------------------------------
    heading("5. ENTRY LEVEL")
    print("  entries                    %d" % entries["n"])
    print("  verdict matches label      %d" % entries["correct"])
    print("  exact-match accuracy       %.4f" % entries["accuracy"])
    print("")
    print("Expected verdict counts in the set:")
    counts = {}
    for case in load_entry_cases():
        counts[case["expected_verdict"]] = counts.get(
            case["expected_verdict"], 0) + 1
    for verdict in Verdict.ALL:
        print("  %-22s %d" % (verdict, counts.get(verdict, 0)))

    print("")
    print("Confusion matrix. Rows are the ground-truth label, columns are "
          "what the")
    print("tool returned. %s" % ", ".join(
        "%s=%s" % (SHORT[v], v) for v in Verdict.ALL))
    print("")
    print("  %-22s %s" % ("expected \\ actual",
                          " ".join("%4s" % SHORT[v] for v in Verdict.ALL)))
    rule()
    for expected in Verdict.ALL:
        row = entries["confusion"][expected]
        print("  %-22s %s" % (expected,
                              " ".join("%4d" % row[a] for a in Verdict.ALL)))

    # -- 6. entry disagreements -------------------------------------------
    heading("6. EVERY ENTRY-LEVEL DISAGREEMENT (%d of %d)"
            % (len(entries["disagreements"]), entries["n"]))
    if not entries["disagreements"]:
        print("  none")
    else:
        for row in entries["disagreements"]:
            if (row["expected"] in CORRECT_LIST_VERDICTS
                    and row["actual"] in MISMATCH_VERDICTS):
                kind = "FALSE ACCUSATION"
            elif (row["expected"] in MISMATCH_VERDICTS
                  and row["actual"] not in MISMATCH_VERDICTS):
                kind = "MISS"
            else:
                kind = "GRADE DISAGREEMENT"
            print("%-10s %s" % (row["id"], kind))
            print("    bib key   : %s" % row["key"])
            print("    expected  : %s" % row["expected"])
            print("    actual    : %s" % row["actual"])
            print("    record    : %s, %d authors (cited %d)"
                  % (row["record_source"] or "none", row["record_authors"],
                     row["cited_authors"]))
            if row["coverage"] is not None:
                print("    coverage  : %.4f" % row["coverage"])
            if row["unmatched_cited"]:
                print("    unmatched : %s"
                      % "; ".join(ascii_safe(n)
                                  for n in row["unmatched_cited"]))
            print("    basis     : %s" % ascii_safe(row["label_basis"]))
            if row["id"] in KNOWN_ENTRY_FAILURES:
                print("    recorded  : yes, xfail(strict=True) in "
                      "tests/test_accuracy.py")
            else:
                print("    recorded  : NO. New since the baseline was taken.")
            rule()

    # -- 7. the two rates that matter -------------------------------------
    heading("7. THE TWO RATES THE DESIGN PREMISE RESTS ON")
    print("A false accusation costs trust and can cost a colleague their "
          "reputation.")
    print("A miss costs a fabrication going unflagged. The tool is tuned to "
          "prefer")
    print("the second, so these two numbers are reported separately and never "
          "as")
    print("one accuracy figure.")
    print("")
    print("Name pairs")
    print("  false accusation rate      %.4f   (%d of %d pairs that are "
          "genuinely one person were called two)"
          % (overall["false_accusation_rate"], overall["fn"],
             overall["positives"]))
    print("  miss rate                  %.4f   (%d of %d pairs that are "
          "genuinely two people were called one)"
          % (overall["miss_rate"], overall["fp"], overall["negatives"]))
    print("")
    print("Entries")
    print("  false accusation rate      %.4f   (%d of %d entries whose author "
          "list is genuinely correct were given PARTIAL_OVERLAP or DISJOINT)"
          % (entries["false_accusation_rate"], len(entries["false_accusations"]),
             entries["correct_list_entries"]))
    print("  miss rate                  %.4f   (%d of %d entries whose author "
          "list is genuinely wrong were NOT given a mismatch verdict)"
          % (entries["miss_rate"], len(entries["misses"]),
             entries["wrong_list_entries"]))
    if entries["false_accusations"]:
        print("")
        print("  false accusations:")
        for row in entries["false_accusations"]:
            print("    %-10s %s -> %s" % (row["id"], row["expected"],
                                          row["actual"]))
    if entries["misses"]:
        print("")
        print("  misses:")
        for row in entries["misses"]:
            print("    %-10s %s -> %s" % (row["id"], row["expected"],
                                          row["actual"]))

    # -- 8. threshold sweep ------------------------------------------------
    heading("8. name_variant SWEEP, 0.50 TO 0.95")
    print("Everything else held at its default. Precision and recall are for "
          "the")
    print("'same person' decision on the 154 labelled pairs. FA = false "
          "accusation")
    print("rate, MISS = miss rate.")
    print("")
    print("%12s %4s %4s %4s %4s %9s %9s %9s %9s %9s"
          % ("name_variant", "TP", "FP", "FN", "TN", "precision", "recall",
             "F1", "FA", "MISS"))
    rule()
    for value in SWEEP:
        sweep_th = Thresholds(name_variant=value,
                              name_match=max(value, Thresholds().name_match))
        sweep_th.validate()
        m = evaluate_name_pairs(sweep_th)["overall"]
        print("%12.2f %4d %4d %4d %4d %9.4f %9.4f %9.4f %9.4f %9.4f"
              % (value, m["tp"], m["fp"], m["fn"], m["tn"], m["precision"],
                 m["recall"], m["f1"], m["false_accusation_rate"],
                 m["miss_rate"]))
    rule()
    print("The shipped default is name_variant=%.2f." % Thresholds().name_variant)

    # -- footer ------------------------------------------------------------
    heading("BASELINES RECORDED IN tests/test_accuracy.py")
    print("These are floors and ceilings for regression detection, not "
          "targets.")
    print("")
    print("  %-42s %.4f  (now %.4f)"
          % ("pair accuracy floor", BASELINE_PAIR_ACCURACY,
             overall["accuracy"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("pair precision floor", BASELINE_PAIR_PRECISION,
             overall["precision"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("pair recall floor", BASELINE_PAIR_RECALL, overall["recall"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("entry accuracy floor", BASELINE_ENTRY_ACCURACY,
             entries["accuracy"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("pair false accusation rate ceiling",
             BASELINE_PAIR_FALSE_ACCUSATION_RATE,
             overall["false_accusation_rate"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("pair miss rate ceiling", BASELINE_PAIR_MISS_RATE,
             overall["miss_rate"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("entry false accusation rate ceiling",
             BASELINE_ENTRY_FALSE_ACCUSATION_RATE,
             entries["false_accusation_rate"]))
    print("  %-42s %.4f  (now %.4f)"
          % ("entry miss rate ceiling", BASELINE_ENTRY_MISS_RATE,
             entries["miss_rate"]))
    print("")
    print("Pairs in the set: %d. Entries in the set: %d."
          % (len(load_name_pairs()), len(load_entry_cases())))
    print("Recorded pair failures: %d. Recorded entry failures: %d."
          % (len(KNOWN_PAIR_FAILURES), len(KNOWN_ENTRY_FAILURES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
