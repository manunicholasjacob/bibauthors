"""Command line tests: exit codes, report shapes, flags.

Every test here is offline. Each run passes ``--offline --cache-dir
tests/fixtures/cache``, so a provider lookup that is not in the recorded
fixtures becomes a CacheMiss, then ProviderUnavailable, then an UNVERIFIABLE
verdict. Nothing in this file may reach the network: a test that silently
goes online would pass on someone else's server and fail in a clean checkout.

``bibauthor.cli.main`` is called in-process and the output is read with
pytest's ``capsys``, rather than spawning a subprocess. That keeps the tests
fast and, more usefully, keeps the tracebacks real when something breaks.

Run with:

    PYTHONPATH=src PYTHONIOENCODING=utf-8 python -X utf8 -m pytest tests/test_cli.py -q

This file is pure ASCII, like every other source file in the repository.
"""

from __future__ import annotations

import dataclasses
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

from bibauthor import __version__  # noqa: E402
from bibauthor.cli import main  # noqa: E402
from bibauthor.verdict import Thresholds, Verdict  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
BIB_DIR = os.path.join(FIXTURES, "bib")
CACHE_DIR = os.path.join(FIXTURES, "cache")

#: Prepended to every invocation. The cache directory is the committed one,
#: and offline mode makes a cache miss an error rather than a request.
OFFLINE = ("--offline", "--cache-dir", CACHE_DIR)

#: The two names borrowed from another paper in the chimeric fixture. The
#: human report has to name at least one of them, or a reader has no way to
#: know which entry to go and check.
FABRICATED = ("Conitzer", "Sycara")


def bib(name):
    """Absolute path to a committed .bib fixture."""
    return os.path.join(BIB_DIR, name)


def run(capsys, *argv):
    """Call main() with OFFLINE prepended, and return (code, stdout, stderr)."""
    code = main(list(OFFLINE) + [str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def write_bib(tmp_path, name, text):
    """Write a throwaway bibliography and return its path as a string."""
    path = tmp_path / name
    path.write_text(text, encoding="ascii")
    return str(path)


# The real author list of arXiv 2507.01413, which is what the recorded arXiv
# fixture holds. Used to build a bibliography that genuinely matches.
TRUE_AUTHORS_BIB = """\
@misc{collusion2025true,
  author        = {Agrawal, Kushal and Teo, Verona and Vazquez, Juan J. and
                   Kunnavakkam, Sudarsh and Srikanth, Vishak and Liu, Andy},
  title         = {Evaluating LLM Agent Collusion in Double Auctions},
  year          = {2025},
  eprint        = {2507.01413},
  archiveprefix = {arXiv},
  primaryclass  = {cs.MA}
}
"""

# The same work with one name changed to a different person who shares the
# surname. "Teo, Vincent" against "Verona Teo" scores 0.65: below the default
# name_variant of 0.70, above 0.60. That single number decides the verdict,
# which is what the --set test exercises.
NEAR_MISS_BIB = """\
@misc{collusion2025nearmiss,
  author        = {Agrawal, Kushal and Teo, Vincent and Vazquez, Juan J. and
                   Kunnavakkam, Sudarsh and Srikanth, Vishak and Liu, Andy},
  title         = {Evaluating LLM Agent Collusion in Double Auctions},
  year          = {2025},
  eprint        = {2507.01413},
  archiveprefix = {arXiv},
  primaryclass  = {cs.MA}
}
"""

# A title and authors no recorded fixture covers, so offline mode cannot
# verify it and the run must end on exit code 2 rather than claim a finding.
UNCACHED_BIB = """\
@article{nosuchwork2026,
  author  = {Quux, Barnaby and Grault, Wilhelmina},
  title   = {A Title No Recorded Fixture Covers At All Whatsoever},
  journal = {Journal of Entirely Nonexistent Things},
  year    = {2026}
}
"""


# ---------------------------------------------------------------------------
# exit code 1: the author mismatch, which is the finding the tool exists for
# ---------------------------------------------------------------------------


def test_chimeric_fixture_exits_one_and_names_a_fabricated_author(capsys):
    """The real fabricated-author-list case has to fail and say who to check."""
    code, out, err = run(capsys, bib("chimeric.bib"))

    assert code == 1, out + err
    assert Verdict.PARTIAL_OVERLAP in out
    assert any(name in out for name in FABRICATED), out
    # The report must not leave the reader thinking this is a verdict on a
    # person rather than a prompt to check the entry.
    assert "not an" in out and "accusation" in out


def test_chimeric_report_marks_the_unmatched_names(capsys):
    code, out, _ = run(capsys, bib("chimeric.bib"))
    assert code == 1
    assert "NOT ON THIS WORK" in out


# ---------------------------------------------------------------------------
# exit code 0: clean
# ---------------------------------------------------------------------------


def test_true_author_list_exits_zero(capsys, tmp_path):
    """The same work with its real author list is clean."""
    path = write_bib(tmp_path, "true.bib", TRUE_AUTHORS_BIB)
    code, out, err = run(capsys, path)

    assert code == 0, out + err
    assert Verdict.MATCH in out
    assert "RESULT: clean" in out


def test_truncated_fixture_exits_zero_with_a_year_note(capsys):
    """wrong_year.bib is TRUNCATED with a v1 year note, and TRUNCATED passes.

    This is the baseline for the --strict test below: without --strict the
    verdict is a note, not a failure.
    """
    code, out, _ = run(capsys, bib("wrong_year.bib"), "--json", "-", "--quiet")
    assert code == 0, out

    data = json.loads(out)
    entry = data["entries"][0]
    assert entry["verdict"] == Verdict.TRUNCATED, entry["verdict"]
    assert entry["is_mismatch"] is False
    kinds = [f["kind"] for f in entry["year_findings"]]
    assert "arxiv_v1_year" in kinds, kinds


# ---------------------------------------------------------------------------
# exit code 2: could not check, which is not the same as a finding
# ---------------------------------------------------------------------------


def test_uncached_entry_exits_two(capsys, tmp_path):
    path = write_bib(tmp_path, "uncached.bib", UNCACHED_BIB)
    code, out, err = run(capsys, path)

    assert code == 2, out + err
    assert Verdict.UNVERIFIABLE in out
    assert "could not be checked" in out


def test_fail_on_unverifiable_turns_two_into_one(capsys, tmp_path):
    path = write_bib(tmp_path, "uncached.bib", UNCACHED_BIB)
    code, _, _ = run(capsys, path, "--fail-on-unverifiable")
    assert code == 1


def test_mismatch_wins_over_unverifiable(capsys, tmp_path):
    """Both present: exit 1, because the mismatch is the reportable finding."""
    path = write_bib(tmp_path, "both.bib", UNCACHED_BIB)
    code, out, _ = run(capsys, bib("chimeric.bib"), path, "--json", "-", "--quiet")

    assert code == 1, out
    data = json.loads(out)
    assert data["totals"]["mismatches"] == 1, data["totals"]
    assert data["totals"]["unverifiable"] >= 1, data["totals"]


# ---------------------------------------------------------------------------
# exit code 3: usage and input errors
# ---------------------------------------------------------------------------


def test_missing_file_exits_three(capsys, tmp_path):
    missing = str(tmp_path / "there-is-no-such-file.bib")
    code, _, err = run(capsys, missing)

    assert code == 3
    assert "no such file" in err


def test_garbage_input_exits_three(capsys, tmp_path):
    path = write_bib(
        tmp_path, "garbage.bib", "this is not a bibliography at all\njust words\n"
    )
    code, _, err = run(capsys, path)

    assert code == 3
    assert err.strip(), "a usage error has to say something on stderr"


def test_unknown_threshold_exits_three(capsys):
    code, _, err = run(
        capsys, bib("clean.bib"), "--set", "nosuchthreshold=1"
    )

    assert code == 3
    assert "nosuchthreshold" in err
    # The error has to list the real names, or the user cannot recover.
    assert "name_variant" in err


def test_malformed_override_exits_three(capsys):
    code, _, err = run(capsys, bib("clean.bib"), "--set", "name_variant")
    assert code == 3
    assert "NAME=VALUE" in err


def test_out_of_range_threshold_exits_three(capsys):
    """name_variant above name_match is incoherent, and is rejected."""
    code, _, err = run(capsys, bib("clean.bib"), "--set", "name_variant=0.99")
    assert code == 3
    assert "name_variant" in err


# ---------------------------------------------------------------------------
# the JSON report
# ---------------------------------------------------------------------------


def test_json_to_stdout_parses_and_carries_the_contract(capsys):
    code, out, _ = run(capsys, bib("chimeric.bib"), "--quiet", "--json", "-")
    assert code == 1

    data = json.loads(out)

    assert data["schema_version"], sorted(data)
    assert data["tool"] == "bibauthor"
    assert data["exit_code"] == 1
    assert data["offline"] is True
    assert isinstance(data["counts"], dict) and data["counts"]
    assert data["counts"][Verdict.PARTIAL_OVERLAP] == 1, data["counts"]

    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["verdict"] == Verdict.PARTIAL_OVERLAP
    assert entry["is_mismatch"] is True
    assert entry["comparison"]["unmatched_cited"], entry["comparison"]

    unmatched = " ".join(u["name"] for u in entry["comparison"]["unmatched_cited"])
    assert any(name in unmatched for name in FABRICATED), unmatched


def test_json_report_has_every_threshold(capsys):
    code, out, _ = run(capsys, bib("chimeric.bib"), "--quiet", "--json", "-")
    assert code == 1

    data = json.loads(out)
    expected = {f.name for f in dataclasses.fields(Thresholds)}
    assert set(data["thresholds"]) == expected, sorted(data["thresholds"])


def test_json_to_a_path_writes_the_file(capsys, tmp_path):
    target = tmp_path / "nested" / "report.json"
    code, out, _ = run(capsys, bib("chimeric.bib"), "--quiet", "--json", str(target))

    assert code == 1
    assert out == "", "nothing should go to stdout with --quiet and a file target"
    assert target.is_file()

    data = json.loads(target.read_text(encoding="ascii"))
    assert data["exit_code"] == 1
    assert data["entries"][0]["verdict"] == Verdict.PARTIAL_OVERLAP


def test_json_report_is_pure_ascii(capsys, tmp_path):
    """The report has to survive any console, any CI log, any pipe."""
    target = tmp_path / "report.json"
    run(capsys, bib("unicode_names.bib"), "--quiet", "--json", str(target))

    raw = target.read_bytes()
    assert raw.decode("ascii"), "the JSON report must be ASCII"


# ---------------------------------------------------------------------------
# the no-exfiltration property
# ---------------------------------------------------------------------------


# Built from character classes rather than written out, because no file in
# this repository may contain an actual email address.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def test_json_report_carries_no_email_address_and_no_api_key(capsys, tmp_path):
    """Nothing identifying goes into a report that lands in a CI artifact.

    bibauthor never sends a contact address to a provider and never takes an
    API key, so neither may appear in the report it writes. This test is the
    guard on that property, because a report is the thing most likely to be
    uploaded, pasted into an issue, or committed.
    """
    target = tmp_path / "report.json"
    paths = [
        bib("chimeric.bib"),
        bib("clean.bib"),
        bib("wrong_year.bib"),
        bib("latex_names.bib"),
        bib("unicode_names.bib"),
    ]
    run(capsys, *(paths + ["--quiet", "--verbose", "--json", str(target)]))

    text = target.read_text(encoding="ascii")

    found = EMAIL_RE.findall(text)
    assert not found, found

    lowered = text.lower()
    for forbidden in ("api_key", "api-key", "apikey", "mailto", "api_keys", "api-keys"):
        assert forbidden not in lowered, forbidden


# ---------------------------------------------------------------------------
# GitHub Actions annotations
# ---------------------------------------------------------------------------


def test_github_annotations_carry_file_and_line(capsys):
    code, out, _ = run(capsys, bib("chimeric.bib"), "--quiet", "--github")
    assert code == 1

    errors = [line for line in out.splitlines() if line.startswith("::error ")]
    assert errors, out

    first = errors[0]
    assert "file=" in first, first
    assert "line=" in first, first
    assert "chimeric.bib" in first, first
    assert "title=bibauthor" in first, first


def test_github_annotations_are_quiet_on_a_clean_run(capsys, tmp_path):
    path = write_bib(tmp_path, "true.bib", TRUE_AUTHORS_BIB)
    code, out, _ = run(capsys, path, "--quiet", "--github")

    assert code == 0
    assert "::error" not in out
    assert "::warning" not in out


# ---------------------------------------------------------------------------
# thresholds: --set, --strict, --list-thresholds
# ---------------------------------------------------------------------------


def test_set_name_variant_changes_the_verdict(capsys, tmp_path):
    """One threshold, two verdicts, same bibliography.

    "Teo, Vincent" against the record's "Verona Teo" scores 0.65. At the
    default name_variant of 0.70 it is a different person, so one cited author
    is not on the work and the entry is PARTIAL_OVERLAP. At 0.60 it is the
    same person written differently, and the entry passes.
    """
    path = write_bib(tmp_path, "nearmiss.bib", NEAR_MISS_BIB)

    code, out, _ = run(capsys, path, "--quiet", "--json", "-")
    assert code == 1, out
    default = json.loads(out)
    assert default["entries"][0]["verdict"] == Verdict.PARTIAL_OVERLAP
    assert default["thresholds"]["name_variant"] == pytest.approx(0.70)

    code, out, _ = run(
        capsys, path, "--quiet", "--json", "-", "--set", "name_variant=0.60"
    )
    assert code == 0, out
    relaxed = json.loads(out)
    assert relaxed["entries"][0]["verdict"] == Verdict.MATCH_WITH_VARIANTS
    assert relaxed["thresholds"]["name_variant"] == pytest.approx(0.60)


def test_strict_promotes_truncated_to_a_failure(capsys):
    """wrong_year.bib passes by default and fails under --strict."""
    code, _, _ = run(capsys, bib("wrong_year.bib"), "--quiet")
    assert code == 0

    code, out, _ = run(capsys, bib("wrong_year.bib"), "--quiet", "--strict", "--json", "-")
    assert code == 1, out

    data = json.loads(out)
    entry = data["entries"][0]
    assert entry["verdict"] == Verdict.TRUNCATED, entry["verdict"]
    assert entry["is_mismatch"] is True
    assert data["thresholds"]["promote_truncated"] is True
    assert data["thresholds"]["promote_order_differs"] is True


def test_list_thresholds_prints_every_name(capsys):
    code = main(["--list-thresholds"])
    out = capsys.readouterr().out

    assert code == 0
    for field in dataclasses.fields(Thresholds):
        assert field.name in out, field.name
    assert "--set" in out, "the listing should say how to change one"


def test_ignore_key_skips_the_entry(capsys):
    """An ignored key cannot produce a mismatch, only an unverifiable entry."""
    code, out, _ = run(
        capsys,
        bib("chimeric.bib"),
        "--quiet",
        "--json",
        "-",
        "--ignore-key",
        "collusion2025",
    )
    assert code == 2, out
    data = json.loads(out)
    assert data["totals"]["mismatches"] == 0, data["totals"]
    assert data["entries"][0]["verdict"] == Verdict.UNVERIFIABLE


# ---------------------------------------------------------------------------
# --version, and the config table this repository ships
# ---------------------------------------------------------------------------


def test_version_exits_zero_and_prints_the_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out, out
    assert "bibauthor" in out, out


def test_shipped_pyproject_table_loads(capsys):
    """The documented [tool.bibauthor] example has to be loadable.

    pyproject.toml carries the project's own defaults in a [tool.bibauthor]
    table that doubles as the documented example an adopter copies. A typo in
    a key name there raises a config error, so point --config at it and check
    the run still works and still uses the defaults.
    """
    config = os.path.join(ROOT, "pyproject.toml")
    code, out, err = run(
        capsys, bib("chimeric.bib"), "--quiet", "--json", "-", "--config", config
    )

    assert code == 1, err
    data = json.loads(out)
    defaults = dataclasses.asdict(Thresholds())
    assert data["thresholds"] == pytest.approx(defaults)
    assert data["providers"] == ["crossref", "arxiv", "openalex", "semanticscholar"]
