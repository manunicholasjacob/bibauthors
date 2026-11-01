"""Command line front end.

Exit codes:

    0  clean: every entry MATCH or MATCH_WITH_VARIANTS
    1  author mismatch found (DISJOINT or PARTIAL_OVERLAP, plus whatever the
       config promotes)
    2  no mismatch, but at least one entry could not be verified
    3  usage or input error

Code 1 wins over code 2 when both apply.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .config import Config, parse_threshold_overrides
from .parsers import ParseError, load_bibliography
from .report import format_github_annotations, format_json, format_text
from .verdict import Thresholds
from .verify import Verifier

EXIT_CLEAN = 0
EXIT_MISMATCH = 1
EXIT_UNVERIFIABLE = 2
EXIT_USAGE = 3

_EPILOG = """\
exit codes:
  0  clean
  1  author mismatch found
  2  no mismatch, but some entries could not be verified
  3  usage or input error

A DISJOINT verdict is a prompt to check the entry by hand. It is not an
accusation of fabrication.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bibauthors",
        description=(
            "Verify the author list of every bibliography entry against "
            "authoritative metadata."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="bibliography files (.bib or CSL-JSON). Reads stdin when absent.",
    )
    parser.add_argument("--version", action="version", version="bibauthors " + __version__)
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=("bibtex", "csljson"),
        help="input format (sniffed by default)",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="write the machine-readable report here ('-' for stdout)",
    )
    out.add_argument(
        "--quiet", action="store_true", help="suppress the human report"
    )
    out.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show clean entries and every provider note",
    )
    out.add_argument(
        "--github",
        action="store_true",
        help="also emit GitHub Actions annotations on stdout",
    )

    net = parser.add_argument_group("sources and network")
    net.add_argument(
        "--offline",
        action="store_true",
        help="use only the on-disk cache; never touch the network",
    )
    net.add_argument(
        "--cache-dir", metavar="DIR", help="where the HTTP cache lives"
    )
    net.add_argument(
        "--providers",
        metavar="LIST",
        help="comma-separated subset of: crossref,arxiv,openalex,semanticscholar",
    )
    net.add_argument(
        "--min-interval",
        type=float,
        metavar="SECONDS",
        help="minimum delay between requests to one host (default 1.0)",
    )
    net.add_argument(
        "--timeout", type=float, metavar="SECONDS", help="per-request timeout"
    )
    net.add_argument(
        "--ask-every-provider",
        action="store_true",
        help=(
            "do not stop at the first confident identifier hit. Slower and "
            "spends more of someone else's rate limit; useful for auditing "
            "disagreement between sources."
        ),
    )

    tune = parser.add_argument_group("thresholds and policy")
    tune.add_argument(
        "--config", metavar="PATH", help="TOML config file (default: discovered)"
    )
    tune.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override one threshold; repeatable. See --list-thresholds.",
    )
    tune.add_argument(
        "--list-thresholds",
        action="store_true",
        help="print every threshold with its default and exit",
    )
    tune.add_argument(
        "--strict",
        action="store_true",
        help="also fail on ORDER_DIFFERS and TRUNCATED",
    )
    tune.add_argument(
        "--fail-on-unverifiable",
        action="store_true",
        help="treat exit code 2 as a failure (returns 1)",
    )
    tune.add_argument(
        "--no-year-check",
        action="store_true",
        help="skip the date comparison entirely",
    )
    tune.add_argument(
        "--ignore-key",
        dest="ignore_keys",
        action="append",
        default=[],
        metavar="KEY",
        help="skip this citation key; repeatable",
    )
    tune.add_argument(
        "--max-entries", type=int, metavar="N", help="stop after N entries"
    )
    return parser


def _print_thresholds(stream) -> None:
    import dataclasses

    defaults = Thresholds()
    stream.write("thresholds (use --set NAME=VALUE):\n\n")
    doc = (Thresholds.__doc__ or "").splitlines()
    descriptions = {}
    current = None
    for line in doc:
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith("        ") and stripped.replace("_", "").replace(
            " / ", ""
        ).isalnum():
            current = stripped
            descriptions[current] = []
        elif current:
            descriptions[current].append(stripped)
    for field in dataclasses.fields(defaults):
        stream.write("  %-28s %s\n" % (field.name, getattr(defaults, field.name)))
        for key, lines in descriptions.items():
            if field.name in [part.strip() for part in key.split("/")]:
                stream.write("      %s\n" % " ".join(lines))
                break


def _read_inputs(paths: Sequence[str], fmt: Optional[str]):
    entries = []
    sources: List[str] = []
    if not paths:
        text = sys.stdin.read()
        if not text.strip():
            raise ParseError("no input: give a file path or pipe a bibliography in")
        entries.extend(load_bibliography(text, fmt=fmt, is_text=True))
        sources.append("<stdin>")
        return entries, sources
    for path in paths:
        if not os.path.exists(path):
            raise ParseError("no such file: %s" % path)
        entries.extend(load_bibliography(path, fmt=fmt))
        sources.append(path)
    return entries, sources


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_thresholds:
        _print_thresholds(sys.stdout)
        return EXIT_CLEAN

    try:
        base = (
            Config.from_toml(args.config)
            if args.config
            else Config.discover(args.paths[0] if args.paths else ".")
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write("bibauthors: config error: %s\n" % exc)
        return EXIT_USAGE

    try:
        overrides = parse_threshold_overrides(args.overrides)
        if args.strict:
            overrides.setdefault("promote_order_differs", True)
            overrides.setdefault("promote_truncated", True)
        providers = (
            [p.strip() for p in args.providers.split(",") if p.strip()]
            if args.providers
            else None
        )
        config = base.with_overrides(
            offline=True if args.offline else None,
            cache_dir=args.cache_dir,
            providers=providers,
            min_interval=args.min_interval,
            timeout=args.timeout,
            max_entries=args.max_entries,
            check_years=False if args.no_year_check else None,
            stop_after_confident=False if args.ask_every_provider else None,
            ignore_keys=args.ignore_keys or None,
            fail_on_unverifiable=True if args.fail_on_unverifiable else None,
            **overrides
        )
    except ValueError as exc:
        sys.stderr.write("bibauthors: %s\n" % exc)
        return EXIT_USAGE

    try:
        entries, sources = _read_inputs(args.paths, args.fmt)
    except ParseError as exc:
        sys.stderr.write("bibauthors: %s\n" % exc)
        return EXIT_USAGE
    except OSError as exc:
        sys.stderr.write("bibauthors: %s\n" % exc)
        return EXIT_USAGE

    if not entries:
        sys.stderr.write("bibauthors: no entries found in the input\n")
        return EXIT_USAGE

    verifier = Verifier(config=config)
    report = verifier.verify_entries(entries, sources=sources)

    if not args.quiet:
        sys.stdout.write(format_text(report, verbose=args.verbose))
    if args.github:
        sys.stdout.write(format_github_annotations(report))
    if args.json_path:
        payload = format_json(report)
        if args.json_path == "-":
            sys.stdout.write(payload)
        else:
            directory = os.path.dirname(os.path.abspath(args.json_path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(args.json_path, "w", encoding="ascii") as handle:
                handle.write(payload)

    code = report.exit_code()
    if code == EXIT_UNVERIFIABLE and config.fail_on_unverifiable:
        return EXIT_MISMATCH
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
