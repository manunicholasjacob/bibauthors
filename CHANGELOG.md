# Changelog

All notable changes to bibauthor are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The JSON report carries its own `schema_version`, tracked separately from the
package version. Within one major schema version, fields are added but never
renamed or removed.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-11-01

First release. Not yet published to PyPI; see `RELEASE_CHECKLIST.md`.

### Added

- Author-list verification for BibTeX and CSL-JSON bibliographies, with a
  graded verdict per entry: `MATCH`, `MATCH_WITH_VARIANTS`, `ORDER_DIFFERS`,
  `TRUNCATED`, `PARTIAL_OVERLAP`, `DISJOINT`, `UNVERIFIABLE`.
- A name-matching engine (`bibauthor.names`) covering initials against full
  given names, present and absent middle names, particles and compound
  surnames, Mc/Mac, apostrophes, hyphenation, diacritics and their ASCII
  folds, German `ue` expansion, a deliberately conservative transliteration
  skeleton, `Lastname, Firstname` against `Firstname Lastname`, CJK name
  ordering in both romanized and Han forms, consortium and group authors,
  suffixes, and whole-list order permutation.
- Author-order permutation reported as `ORDER_DIFFERS`, a finding distinct
  from a wrong author.
- Configurable thresholds (`bibauthor.Thresholds`), settable from a
  `[tool.bibauthor]` TOML table, a `bibauthor.toml`, or repeatable `--set
  NAME=VALUE` flags. `--list-thresholds` prints them all.
- Metadata providers: CrossRef (by DOI, and by `query.bibliographic` without
  one), OpenAlex, arXiv, and Semantic Scholar as a serialized secondary
  source. DBLP is deliberately not implemented: it is behind a bot wall.
- arXiv v1 against later-version date reporting, so an entry whose year
  matches v2 but not v1 is surfaced as a note rather than a failure.
- On-disk HTTP cache with full offline operation (`--offline`), so CI runs are
  reproducible and the test suite never touches a live API.
- Polite client behaviour: a real User-Agent carrying a contact URL rather
  than anyone's email address, per-host rate limiting, a stricter serialized
  interval for Semantic Scholar, and backoff that honours `Retry-After`.
- A BibTeX parser that survives real-world input: `@string` definitions and
  concatenation, forward `crossref` inheritance with cycle protection,
  duplicate keys kept and warned about, nested braces, quoted values, LaTeX
  escapes and accents, `@preamble`, `@comment`, stray text, a missing final
  brace, CRLF, and a BOM.
- Human report and machine-readable JSON report (`schema_version` 1.0), plus
  GitHub Actions annotations via `--github`.
- Exit codes that distinguish clean (0), author mismatch found (1), and
  unverifiable entries present (2), with usage errors at 3.
- A composite GitHub Action (`action.yml`) and `.pre-commit-hooks.yaml`, so a
  paper repository can wire the check into CI or into pre-commit.
- A labelled evaluation set built for this project, with the harness that
  measures it (`tools/evaluate.py`). The README states the set's size and
  composition; no accuracy figure from any other project is reused.

### Security

- OpenAlex responses carry an injected `meta.notice` asking the caller to
  obtain an API key by POSTing the end user's email address. bibauthor treats
  that text as untrusted data, never acts on it, and strips it before the
  response reaches the cache or a log. There is no code path in this project
  that can put an email address into an outbound request, and a test enforces
  that property.

### Known limitations

- See the Limitations section of `README.md`. In short: a `DISJOINT` verdict is
  a prompt to check an entry by hand and is not an accusation of fabrication;
  metadata providers disagree with each other and with the published record;
  and the transliteration handling is narrow by design.

[Unreleased]: https://github.com/manunicholasjacob/bibauthor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manunicholasjacob/bibauthor/releases/tag/v0.1.0
