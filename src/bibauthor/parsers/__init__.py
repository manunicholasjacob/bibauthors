"""Bibliography input: format sniffing and dispatch.

``load_bibliography`` is the only entry point the rest of the package needs.
It takes a path or a string of text, works out whether it is BibTeX or
CSL-JSON, and returns a list of :class:`bibauthor.entry.Entry`.

This file is pure ASCII.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from ..entry import Entry
from .bibtex import parse_bibtex
from .csljson import parse_csl_json

__all__ = ["ParseError", "load_bibliography", "parse_bibtex", "parse_csl_json", "sniff_format"]


class ParseError(ValueError):
    """The input could not be read as a bibliography."""


_BIBTEX_NAMES = frozenset(("bib", "bibtex", "biblatex"))
_CSL_NAMES = frozenset(("csl", "csljson", "csl-json", "csl_json", "json"))

_BIBTEX_SUFFIXES = frozenset((".bib", ".bibtex", ".biblatex"))
_CSL_SUFFIXES = frozenset((".json", ".csljson", ".csl"))


def _decode_bytes(data: bytes) -> str:
    """Decode a bibliography file, tolerating a BOM and broken encodings.

    UTF-8 first, because that is what modern tools emit; latin-1 next, because
    that is what the old ones emit; UTF-8 with replacement last, so that a file
    in neither encoding still parses rather than aborting the whole run.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("latin-1")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _first_meaningful_char(text: str) -> str:
    """The first character that is not a BOM, whitespace or a ``%`` comment."""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\ufeff" or ch in " \t\r\n":
            i += 1
            continue
        if ch == "%":
            newline = text.find("\n", i)
            if newline < 0:
                return ""
            i = newline + 1
            continue
        return ch
    return ""


def sniff_format(text: str, path: str = "") -> Optional[str]:
    """Guess ``"bibtex"`` or ``"csljson"``, or return None when unsure."""
    lead = _first_meaningful_char(text or "")
    if lead in ("[", "{"):
        return "csljson"
    if lead == "@":
        return "bibtex"
    if path:
        suffix = os.path.splitext(path)[1].lower()
        if suffix in _BIBTEX_SUFFIXES:
            return "bibtex"
        if suffix in _CSL_SUFFIXES:
            return "csljson"
    return None


def _normalize_fmt(fmt: Optional[str]) -> Optional[str]:
    if fmt is None:
        return None
    name = str(fmt).strip().lower().lstrip(".")
    if name in _BIBTEX_NAMES:
        return "bibtex"
    if name in _CSL_NAMES:
        return "csljson"
    raise ParseError("unknown bibliography format %r" % fmt)


def load_bibliography(
    path_or_text: Any, fmt: Optional[str] = None, is_text: bool = False
) -> List[Entry]:
    """Load a bibliography from a path, or from text when ``is_text`` is true.

    ``fmt`` forces the format; when it is None the format is sniffed from the
    content, then from the file extension. Raises :class:`ParseError` when the
    file cannot be read, the format cannot be determined, or the content does
    not parse.
    """
    wanted = _normalize_fmt(fmt)

    if is_text:
        if isinstance(path_or_text, bytes):
            text = _decode_bytes(path_or_text)
        else:
            text = str(path_or_text)
            if text.startswith("\ufeff"):
                text = text[1:]
        source_file = ""
        path = ""
    else:
        try:
            path = os.fspath(path_or_text)
        except TypeError as exc:
            raise ParseError("not a usable bibliography path: %s" % exc)
        if not isinstance(path, str):
            path = path.decode("utf-8", errors="replace")
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise ParseError("cannot read bibliography %r: %s" % (path, exc))
        text = _decode_bytes(data)
        source_file = path

    chosen = wanted or sniff_format(text, path)
    if chosen is None:
        where = path or "the supplied text"
        raise ParseError(
            "cannot tell whether %s is BibTeX or CSL-JSON; pass fmt explicitly"
            % where
        )

    try:
        if chosen == "bibtex":
            return parse_bibtex(text, source_file=source_file)
        return parse_csl_json(text, source_file=source_file)
    except ParseError:
        raise
    except ValueError as exc:
        where = path or "the supplied text"
        raise ParseError("could not parse %s as %s: %s" % (where, chosen, exc))
