# bibauthors

[![PyPI](https://img.shields.io/pypi/v/bibauthors)](https://pypi.org/project/bibauthors/)
[![CI](https://github.com/manunicholasjacob/bibauthors/actions/workflows/ci.yml/badge.svg)](https://github.com/manunicholasjacob/bibauthors/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/bibauthors)](https://pypi.org/project/bibauthors/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Verify the **author list** of every entry in a bibliography against
authoritative metadata, and fail when the authors do not belong to the work.

Deliberately narrow. Every citation checker matches on title and DOI, and
most of them do that well. This one checks the author list, which is the part
they all do worst.

> A `DISJOINT` verdict is a prompt to check an entry by hand. It is **not** an
> accusation of fabrication. See [Limitations](#limitations).

- [The chimeric citation](#the-chimeric-citation)
- [Install](#install)
- [Usage](#usage)
- [Verdicts](#verdicts)
- [Thresholds](#thresholds)
- [Measured accuracy](#measured-accuracy)
- [Sources](#sources)
- [Related work](#related-work)
- [Limitations](#limitations)

## The chimeric citation

The most dangerous hallucinated citation is not the one that invents a paper.
An invented paper is easy to catch: no DOI resolves, no title matches, and
every existing tool flags it.

The dangerous one is the **chimeric citation**: a real title, a real venue, a
real year, and an **invented author list**. It passes a title-matching checker
with a clean report. It looks real on Google Scholar. A reader who recognises
the title has no reason to look twice, and a reader who recognises one of the
names has every reason to trust it.

This is not hypothetical. On 29 October 2026 one turned up in this author's
own bibliography. The entry for *Evaluating LLM Agent Collusion in Double
Auctions* (arXiv 2507.01413) carried the authors:

    Agrawal, Liu, Fried, Sycara, Jacobs, Oesterheld, Conitzer

The real author list is:

    Kushal Agrawal, Verona Teo, Juan J. Vazquez,
    Sudarsh Kunnavakkam, Vishak Srikanth, Andy Liu

Two of the seven surnames are real authors of the paper. Five are not. The
title, the arXiv id, the venue and the year were all correct. The checker in
use at the time reported the entry as "suspicious, found in 1 source", which
is the same label it gives every legitimate arXiv preprint, so the label
carried no signal about the actual error.

`bibauthors` reports that entry like this:

```
FAIL collusion2025:9              PARTIAL_OVERLAP      via arxiv
      title: Evaluating LLM Agent Collusion in Double Auctions
      record: http://arxiv.org/abs/2507.01413v1
      cited (7):
        [0] Agrawal, Kushal -> [0] Kushal Agrawal
        [1] Liu, Andy -> [5] Andy Liu
        [2] Fried, Daniel   <-- NOT ON THIS WORK
        [3] Sycara, Katia   <-- NOT ON THIS WORK
        [4] Jacobs, Abigail   <-- NOT ON THIS WORK
        [5] Oesterheld, Caspar   <-- NOT ON THIS WORK
        [6] Conitzer, Vincent   <-- NOT ON THIS WORK
      record authors not cited (4 of 6):
        [1] Verona Teo
        [2] Juan J. Vazquez
        [3] Sudarsh Kunnavakkam
        [4] Vishak Srikanth
      coverage: 29% of cited authors matched
```

That entry is committed as `tests/fixtures/bib/chimeric.bib`, and the test
suite asserts the verdict on every run.

## Install

```sh
pip install bibauthors
```

`bibauthors` has **no required runtime dependencies**. It is standard library
only, so it drops into a minimal CI image. (`tomli` is pulled in only on
Python 3.9 and 3.10, and only if you use a TOML config file.) Python 3.9+.

### As a GitHub Action

This is the form most paper repositories want. Add one step:

```yaml
name: bibliography
on: [push, pull_request]
permissions:
  contents: read
jobs:
  authors:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: manunicholasjacob/bibauthors@v0.1.0
        with:
          paths: refs.bib
```

Findings land as annotations on the pull request diff, at the line of the
offending entry. Inputs: `paths` (default `**/*.bib`), `offline`, `cache-dir`,
`providers`, `fail-on` (`mismatch`, `unverifiable` or `never`), `strict`,
`json-report`, `annotations`, `python-version`, `version`. Outputs:
`exit-code`, `mismatches`, `unverifiable`, `report-path`.

To keep CI hermetic and off the metadata APIs entirely, commit your cache and
run `offline: true` with `cache-dir` pointed at it.

### As a pre-commit hook

```yaml
repos:
  - repo: https://github.com/manunicholasjacob/bibauthors
    rev: v0.1.0
    hooks:
      - id: bibauthors
```

There is also `bibauthors-offline` for cache-only, network-free commits. Note
that pre-commit treats any non-zero exit as a failure, so an unverifiable
entry (exit 2) will block a commit there, unlike the Action's default policy.

## Usage

```sh
bibauthors refs.bib                          # human report
bibauthors refs.bib --json report.json       # machine-readable too
bibauthors refs.bib --offline                # cache only, no network
bibauthors refs.bib --strict                 # also fail on order and truncation
bibauthors refs.bib --set name_variant=0.75  # tune a threshold
bibauthors --list-thresholds                 # every knob and its default
cat refs.bib | bibauthors                    # reads stdin
```

Accepts BibTeX and CSL-JSON, sniffed by default.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean: every entry `MATCH` or `MATCH_WITH_VARIANTS` |
| 1 | author mismatch found |
| 2 | no mismatch, but at least one entry could not be verified |
| 3 | usage or input error |

Code 1 wins over code 2 when both apply: the mismatch is the finding the tool
exists to report. Code 2 is separate on purpose. "I could not check this" and
"this is wrong" are different statements, and a checker that conflates them
teaches you to ignore it.

### As a library

```python
from bibauthors import Config, Verdict, verify_bibliography

report = verify_bibliography("refs.bib", Config(offline=True))

for result in report.results:
    if result.verdict in (Verdict.DISJOINT, Verdict.PARTIAL_OVERLAP):
        print(result.entry.key, result.verdict)
        for index in result.comparison.unmatched_cited:
            print("  not on this work:", result.entry.authors[index])

raise SystemExit(report.exit_code())
```

The name matcher is usable on its own, without any network or bibliography:

```python
from bibauthors import Thresholds, compare_names, parse_name

a = parse_name("van der Berg, J.")
b = parse_name("Jan van der Berg")
result = compare_names(a, b, Thresholds())
print(result.score, result.variants)   # 0.9 ['initials']
```

Stable public API: `verify_bibliography`, `verify_entries`, `verify_entry`,
`Verifier`, `load_bibliography`, `parse_bibtex`, `parse_csl_json`, `Entry`,
`parse_name`, `parse_names`, `fold`, `compare_names`, `compare_author_lists`,
`PersonName`, `NameComparison`, `ListComparison`, `Verdict`, `Thresholds`,
`Config`, `EntryResult`, `Report`, `YearFinding`, `format_text`,
`format_json`, `format_github_annotations`, `to_dict`, `Provider`, `Record`,
`build_providers`, `HttpClient`, `Cache`.

The JSON report carries a `schema_version` (currently `1.0`), versioned
separately from the package. Within a major schema version, fields are added
but never renamed or removed.

## Verdicts

The verdict is graded, not boolean. A bibliography is full of legitimate
variation, and a tool that answers only yes or no either cries wolf or stays
silent.

| Verdict | Severity | Meaning | Fails? |
| ------- | -------- | ------- | ------ |
| `MATCH` | 0 | Cited authors and record authors agree exactly. | no |
| `MATCH_WITH_VARIANTS` | 0 | The same authors, written differently: initials, diacritics, particles, transliteration. | no |
| `TRUNCATED` | 1 | The cited list is a truncated prefix of the record, as `et al.` promises. | with `--strict` |
| `ORDER_DIFFERS` | 2 | The same authors, in a different order. Reported separately from a wrong author, because it usually means a transcription slip, not a fabrication. | with `--strict` |
| `UNVERIFIABLE` | 2 | No authoritative record was found to compare against. Not a finding about the entry. | exit 2 |
| `PARTIAL_OVERLAP` | 3 | Some cited authors are on the work and some are not. **This is where the real chimeric case lands**, because a fabricated list usually keeps a real name or two. | yes |
| `DISJOINT` | 4 | Not one cited author is on this work. | yes |

`DISJOINT` is deliberately hard to reach. It requires that nothing matched at
all, that the record lists at least `disjoint_min_record_authors` authors, that
the entry was not marked truncated, and that the record was identified by a
DOI or arXiv id or by a title similarity at or above `title_similarity`. If
nothing matched but any of those guards fails, the verdict falls back to
`PARTIAL_OVERLAP` with a note explaining the downgrade. A record found by
search with a weak title match is never allowed to drive a mismatch verdict
at all, because judging an author list against the wrong paper is the fastest
way to manufacture a false accusation.

Dates are a separate axis, reported as notes rather than verdicts. An arXiv
entry whose year matches the latest version but not v1 is surfaced like this:

```
      year: stored year 2025 matches the latest version (2025-01-03),
            but v1 is 2024 (2024-12-19). Decide which version you mean.
```

That is the second real case from the 29 October pass, committed as
`tests/fixtures/bib/wrong_year.bib` (the Qwen2.5 Technical Report, arXiv
2412.15115: v1 19 December 2024, v2 3 January 2025).

## Thresholds

Everything is configurable, from a `[tool.bibauthors]` table in
`pyproject.toml`, from a `bibauthors.toml`, or from repeatable `--set
NAME=VALUE` flags. `bibauthors --list-thresholds` prints them all.

| Threshold | Default | What it does |
| --------- | ------- | ------------ |
| `name_match` | 0.92 | At or above this, two names are the same with no variant worth reporting. |
| `name_variant` | 0.70 | At or above this, two names are the same person, reached by one or more variant rules. Below it, they are different people. |
| `partial_coverage` | 0.50 | Fraction of cited authors that must match before coverage is called adequate. |
| `title_similarity` | 0.80 | How close a record's title must be before the record may drive a mismatch verdict. |
| `disjoint_min_record_authors` | 2 | A record with fewer known authors can never produce `DISJOINT`. |
| `disjoint_max_coverage` | 0.0 | Coverage must be at or below this for `DISJOINT`. At the default, one matched author downgrades to `PARTIAL_OVERLAP`. |
| `surname_only_penalty` | 0.18 | Subtracted when one side has no given name, so a bare surname cannot reach `name_match` alone. |
| `initial_vs_full_given` | 0.90 | Ceiling for a given-name pair agreeing only on the initial. |
| `promote_order_differs` | false | Count `ORDER_DIFFERS` as a failure. |
| `promote_truncated` | false | Count `TRUNCATED` as a failure. |

`name_variant` is the consequential one. Lowering it hides real fabrications;
raising it manufactures them. The [measured accuracy](#measured-accuracy)
section includes a real sweep of it so the choice can be made on numbers
rather than taste.

The scoring model, for anyone who needs to reason about a number:
`score = 0.65 * family + 0.35 * given`, less `surname_only_penalty` when
either side has no given name, then clamped by the ceiling of every variant
rule that fired. The weights are load-bearing: a pair whose first given names
disagree can reach at most 0.65, which is below the default `name_variant`, so
"John Smith" against "Robert Smith" is a non-match on the given name alone no
matter how perfectly the surnames agree.

Variant rules and their ceilings: spacing and diacritics 1.0, dropped middle
name and dropped suffix 0.95, fuzzy group match 0.95, order swapped and
particle moved and German `ue` expansion and general spelling 0.90, initials
`initial_vs_full_given`, Mc/Mac 0.88, compound surname 0.85, conflicting
suffix and conflicting middle initial 0.85, transliteration 0.80, vowel
romanization 0.78.

## Measured accuracy

**These numbers come from a test set this project built. They are not an
independent benchmark, and no figure here is borrowed from any other
project.** Reproduce them with:

```sh
PYTHONPATH=src python tools/evaluate.py
```

### The set

| | |
| --- | --- |
| Name pairs | **154** labelled pairs: 89 the same human being, 65 different people |
| Categories | 19: exact, initials, middle_name, middle_initial_conflict, particle, mc_mac, apostrophe, hyphenated, diacritics, transliteration, cjk_order, cjk_script, suffix, comma_order, group, near_miss_surname, different_person, same_surname_different_person, initial_collision |
| Entries | **27** labelled bibliography entries, covering all 7 verdicts (5 MATCH, 5 MATCH_WITH_VARIANTS, 3 ORDER_DIFFERS, 4 TRUNCATED, 3 PARTIAL_OVERLAP, 5 DISJOINT, 2 UNVERIFIABLE) |
| Ground truth | Author lists from recorded CrossRef, OpenAlex, arXiv and Semantic Scholar responses, plus public record for the confusable-people pairs |
| Provenance | `tests/fixtures/eval/README-DATA.txt` |

The set is **small and deliberately weighted towards hard cases**. 34 of the
65 negative pairs are real confusable people or genuinely distinct near-miss
surnames; there are no easy negatives. One row moves pair accuracy by 0.65 of
a percentage point. **These figures are not an estimate of accuracy on an
ordinary bibliography, where the tool scores considerably higher.** They are a
measurement of where it breaks.

### Results, at the shipped defaults

| Metric | Name pairs (n=154) | Entries (n=27) |
| ------ | ------------------ | -------------- |
| Accuracy | 0.8766 (135/154) | 0.9630 (26/27) |
| Precision | 0.8571 | |
| Recall | 0.9438 | |
| F1 | 0.8984 | |
| **False-accusation rate** | **0.0562** (5 of 89) | **0.0588** (1 of 17) |
| **Miss rate** | **0.2154** (14 of 65) | **0.0000** (0 of 8) |

The last two rows are the ones that matter, and they are reported separately
and never averaged into one figure, because the costs are not symmetric. The
false-accusation rate is how often something genuinely correct is called
wrong. The miss rate is how often something genuinely wrong is not caught.

All 8 entries with a genuinely wrong author list were caught, including the
real chimeric case and four fully fabricated lists. One entry out of 17 with a
correct author list was wrongly given `PARTIAL_OVERLAP`, and the cause is
instructive: CrossRef deposits Aidan N. Gomez of *Attention Is All You Need*
with the family name `N.Gomez`, so a correctly spelled citation scores 0.909
against the record, just under the spelling floor. The defect is in the
deposited metadata, the bibliography is innocent, and the tool still reports
it. That is the shape most false accusations will take.

### Per category

| Category | n | Accuracy | | Category | n | Accuracy |
| -------- | - | -------- |-| -------- | - | -------- |
| apostrophe | 6 | 1.0000 | | initials | 10 | 1.0000 |
| cjk_order | 8 | 1.0000 | | mc_mac | 6 | 0.5000 |
| cjk_script | 6 | 0.8333 | | middle_initial_conflict | 6 | 0.3333 |
| comma_order | 6 | 1.0000 | | middle_name | 8 | 1.0000 |
| diacritics | 8 | 1.0000 | | near_miss_surname | 12 | 0.9167 |
| different_person | 12 | 1.0000 | | particle | 10 | 0.9000 |
| exact | 6 | 1.0000 | | same_surname_different_person | 10 | 1.0000 |
| group | 8 | 0.8750 | | suffix | 6 | 0.5000 |
| hyphenated | 8 | 0.7500 | | transliteration | 8 | 0.6250 |
| initial_collision | 10 | 1.0000 | | **ALL** | **154** | **0.8766** |

The three weak categories are honest and two of them are deliberate.
`middle_initial_conflict` (0.3333) and `suffix` (0.5000) are low because a
contradicting middle initial or generational suffix is scored as weak evidence
that two names are different people, never as proof. The consequence, measured:
George W. Bush and George H. W. Bush are treated as one person, as are the two
Michael Jordans, and so are John Smith Jr. and John Smith Sr. That is the cost
asymmetry doing exactly what it was set up to do, and it is a cost. `mc_mac`
(0.5000) is the same kind of choice applied to Mc/Mac. If you would rather pay
the other way, raise `name_variant` to 0.90; the sweep below shows what that
buys and what it costs.

### `name_variant` sweep, everything else held at default

| `name_variant` | Precision | Recall | F1 | False accusation | Miss |
| -------------- | --------- | ------ | -- | ---------------- | ---- |
| 0.50 | 0.6591 | 0.9775 | 0.7873 | 0.0225 | 0.6923 |
| 0.60 | 0.6591 | 0.9775 | 0.7873 | 0.0225 | 0.6923 |
| 0.65 | 0.7131 | 0.9775 | 0.8246 | 0.0225 | 0.5385 |
| **0.70 (default)** | **0.8571** | **0.9438** | **0.8984** | **0.0562** | **0.2154** |
| 0.75 | 0.8571 | 0.9438 | 0.8984 | 0.0562 | 0.2154 |
| 0.80 | 0.8632 | 0.9213 | 0.8913 | 0.0787 | 0.2000 |
| 0.85 | 0.8681 | 0.8876 | 0.8778 | 0.1124 | 0.1846 |
| 0.90 | 1.0000 | 0.8764 | 0.9341 | 0.1236 | 0.0000 |
| 0.95 | 1.0000 | 0.5843 | 0.7376 | 0.4157 | 0.0000 |

Worth reading honestly: on this set the default of 0.70 sits on a plateau
rather than at an optimum, and 0.90 reaches a zero miss rate at the cost of
more than doubling the false-accusation rate. The default prefers the cheaper
error. That is a result about 154 labelled pairs, not a law.

### Test suite

```
555 passed, 21 xfailed in 5.44s
```

The whole suite runs **offline** against recorded API responses in
`tests/fixtures/cache`, so it is reproducible and never touches a live API.
The 21 `xfailed` are the known failures above, each marked
`xfail(strict=True)` with its reason, so any future fix flips it to `XPASS`
and is impossible to miss. Failures are recorded, not hidden.

## Sources

| Source | Used for | Notes |
| ------ | -------- | ----- |
| **CrossRef** | DOI lookup, and `query.bibliographic` without one | The most reliable path for published work. |
| **arXiv** | Preprints | The right source for the v1 versus later-version date problem. HTTPS only. |
| **OpenAlex** | Broadest coverage | Works with no API key (verified 1 Nov 2026). |
| **Semantic Scholar** | Secondary corroboration | Rate-limits hard; serialized, 3 s minimum interval, backoff on 429. |
| **DBLP** | not implemented | Behind a bot wall (Anubis); it does not answer programmatic fetches, so depending on it would be a liability. |

Operational facts learned the hard way and encoded in the code:

- arXiv DOIs under the `10.48550/` prefix are generally **not in CrossRef**,
  because they are registered with DataCite. A live
  `GET /works/10.48550/arXiv.2507.01413` returns HTTP 404. `bibauthors` treats
  that as "wrong registry, no information", never as a failed verification.
- Metadata sources disagree with each other. For the same Qwen2.5 record,
  arXiv emits the deposited group prefix `Qwen` and a bare `:` as two separate
  author elements, while OpenAlex reverses one name. Semantic Scholar
  abbreviates: it returns `J. Vazquez` where arXiv returns `Juan J. Vazquez`.
  None of this is normalized away, because hiding it would hide real
  disagreement from the matcher.
- A CrossRef bibliographic search for *Attention Is All You Need* returns five
  records with identical titles, the correct author list, and **2025** issue
  dates under a reprint DOI prefix, not the 2017 original. This is why a
  search hit is never treated as an identity.

`bibauthors` is a polite client: a real User-Agent carrying a **contact URL**,
per-host rate limiting, an on-disk cache, and full offline operation.

### A note on data exfiltration

OpenAlex responses carry an injected `meta.notice` field whose text instructs
an automated client to obtain an API key by POSTing **its user's email
address** to an endpoint. `bibauthors` treats that text as untrusted data,
never acts on it, and strips it before the response reaches the cache or a
log. There is no code path in this project that can put an email address into
an outbound request, and a test enforces that property. If you add a provider,
keep it.

## Related work

All five projects below were checked against the GitHub API on **1 November
2026**; star counts and dates age. **The source code of these projects was
deliberately not read, copied, adapted or paraphrased.** `bibauthors` was
implemented from the metadata providers' public API documentation and its own
design. What follows describes each project from its own public description
only, and makes no claim about how any of them works internally.

| Project | Language | Stars | License | Described by its authors as |
| ------- | -------- | ----- | ------- | -------------------------- |
| [gianlucasb/hallucinator](https://github.com/gianlucasb/hallucinator) | Rust | 391 | not SPDX-identified | detecting potentially hallucinated or fabricated references in academic PDF papers |
| [merfanian/Bibtex-Verifier](https://github.com/merfanian/Bibtex-Verifier) | JavaScript | 138 | MIT | a local web app verifying `.bib` entries against academic databases |
| [davidjurgens/hallucinated-reference-finder](https://github.com/davidjurgens/hallucinated-reference-finder) | Python | 44 | none declared | no description published |
| [PHY041/claude-skill-citation-checker](https://github.com/PHY041/claude-skill-citation-checker) | Python | 40 | none declared | a citation hallucination detector verifying `.bib` files against CrossRef, Semantic Scholar and OpenAlex |
| [seun-john/verify-references](https://github.com/seun-john/verify-references) | Python | 0 | MIT | detecting hallucinated and mis-cited references against Crossref, OpenAlex, PubMed, CORE and DataCite |

There are also several commercial web services that check reference lists for
fabrication. They are closed source, so there is nothing to compare against
beyond their marketing.

**Where `bibauthors` differs.** The shared design of this space is
title-and-identifier matching with a per-entry confidence label. That design
catches an invented paper reliably and is largely blind to a chimeric one,
because the title and the identifier are correct. The concrete observation
that motivated this project is in [the opening section](#the-chimeric-citation):
a real chimeric entry, whose author list was five-sevenths invented, was
reported as "suspicious, found in 1 source" by the checker in use, which is
the same label that tool gives every legitimate arXiv preprint. The label was
not wrong, it just was not about the error.

So `bibauthors` does one thing those tools do not foreground: it treats the
author list as the object under test, grades the comparison instead of
labelling it, reports author-order permutation as a finding distinct from a
wrong author, and publishes its own measured false-accusation and miss rates
so the tradeoff is inspectable. It is a complement to a title-and-DOI checker,
not a replacement for one. Run both.

## Limitations

**A `DISJOINT` verdict is a prompt to check an entry by hand. It is not an
accusation of fabrication, and nothing in this tool's output should be quoted
as evidence that anyone fabricated anything.** Metadata is imperfect, author
lists change between preprint and proceedings, records get deposited with
mangled names, and a search hit can simply be the wrong work. The measured
false-accusation rate above is not zero and will never be zero. A human
decides what a finding means.

Known limitations, all of them measured rather than guessed:

- **A contradicting middle initial or generational suffix does not separate
  two people.** Measured: the two Presidents Bush, the two Michael Jordans,
  and a Jr./Sr. pair each read as one person. This is a deliberate choice
  under the cost asymmetry, and it is a real cost. Raise `name_variant` to
  0.90 to pay the other way.
- **Mc and Mac are treated as one surname** at a 0.88 ceiling. Plenty of
  McDonalds and MacDonalds are unrelated.
- **Scripts are not bridged.** A name in Han characters scores 0.0 against its
  own romanization: the tool refuses to guess rather than risk a wrong match.
  CJK name *ordering* is handled, in both romanized and Han forms, but a
  Chinese-language bibliography entry for a real author is unmatchable.
- **Given-name equivalence across languages is not handled.** Lev and Leo
  Tolstoy read as different people.
- **Transliteration handling is narrow by design** and documented as
  incomplete in the source. It catches digraph substitutions and single-vowel
  romanization differences. It cannot distinguish `Johnson`/`Johnsen` (two
  surnames) from `Gorbachev`/`Gorbachov` (one), because the two pairs are
  structurally identical, and it currently merges both.
- **A dropped particle is not handled.** "Ludwig van Beethoven" against
  "Ludwig Beethoven" reads as different people. A particle *moved* behind the
  given name is handled.
- **`compare_names` scores are not perfectly symmetric**, because
  `difflib.SequenceMatcher.ratio` is order-dependent. No decision in the test
  set changes, and a test pins the exact set of affected pairs so a new
  asymmetry cannot hide.
- **An entry with no author field is not a finding.** A book cited by editor
  parses with an empty author list and a warning, not a mismatch.
- **Coverage is only as good as the sources.** Books, theses, standards,
  technical reports and anything not in CrossRef, OpenAlex, arXiv or Semantic
  Scholar come back `UNVERIFIABLE`, which is a statement about the sources and
  not about the citation.

## License and citation

MIT. Copyright (c) 2026 Manu Nicholas Jacob.

Author: **Manu Nicholas Jacob**, Independent Researcher
([ORCID 0009-0007-6589-6572](https://orcid.org/0009-0007-6589-6572)).
See `CITATION.cff`. Contact is via this repository's issue tracker.
